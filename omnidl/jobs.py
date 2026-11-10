"""下載佇列：多工排程、暫停 / 續傳 / 取消、歷史紀錄。"""

from __future__ import annotations

import collections
import json
import os
import threading
import time
import traceback
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from yt_dlp.utils import DownloadCancelled

from .config import Settings, SettingsStore, app_dir
from .engine import Engine, JobControl, friendly_error

ACTIVE = ('analyzing', 'sniffing', 'downloading', 'repairing', 'merging', 'fixing',
          'converting', 'embedding', 'moving', 'processing')
JOB_OPTION_KEYS = ('format_id', 'quality', 'audio_format', 'sniff_url', 'playlist_items',
                   'download_dir', 'aria2c', 'concurrent_fragments')


@dataclass
class Job:
    url: str
    options: dict = field(default_factory=dict)
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:10])
    status: str = 'queued'      # queued | running | paused | finished | error | cancelled
    stage: str = ''
    stage_detail: str | None = None
    title: str | None = None
    progress: dict = field(default_factory=dict)
    error: str | None = None
    files: list = field(default_factory=list)
    via: str | None = None
    created: float = field(default_factory=time.time)
    finished: float | None = None
    log: collections.deque = field(default_factory=lambda: collections.deque(maxlen=300))
    control: JobControl = field(default_factory=JobControl)

    def snapshot(self, with_log=False) -> dict:
        d = {k: getattr(self, k) for k in ('id', 'url', 'options', 'status', 'stage', 'stage_detail', 'title',
                                           'progress', 'error', 'files', 'via', 'created', 'finished')}
        if with_log:
            d['log'] = list(self.log)
        return d

    def persist(self) -> dict:
        return {k: getattr(self, k) for k in ('id', 'url', 'options', 'status', 'title', 'error', 'created')}


class JobManager:
    def __init__(self, store: SettingsStore, data_dir: Path | None = None):
        self.store = store
        self.dir = data_dir or app_dir()
        self.jobs: dict[str, Job] = {}
        self.order: list[str] = []
        self.lock = threading.RLock()
        self.listeners: list[Callable[[dict], None]] = []
        self.history_path = self.dir / 'history.json'
        self.jobs_path = self.dir / 'jobs.json'
        self._load_jobs()

    # ---------------------------------------------------------------- 事件
    def subscribe(self, fn):
        self.listeners.append(fn)
        return lambda: self.listeners.remove(fn) if fn in self.listeners else None

    def _emit(self, kind: str, payload):
        for fn in list(self.listeners):
            try:
                fn({'type': kind, 'data': payload})
            except Exception:  # noqa: BLE001
                pass

    def _changed(self, job: Job, persist=True):
        self._emit('job', job.snapshot())
        if persist:
            self._save_jobs()

    # ---------------------------------------------------------------- 持久化
    def _load_jobs(self):
        try:
            data = json.loads(self.jobs_path.read_text('utf-8'))
        except (OSError, ValueError):
            return
        for d in data:
            job = Job(url=d['url'], options=d.get('options') or {}, id=d['id'], title=d.get('title'),
                      error=d.get('error'), created=d.get('created') or time.time())
            # 上次關閉時還沒下載完的工作 → 暫停狀態，可以按「繼續」續傳
            job.status = d.get('status') if d.get('status') in ('paused', 'error', 'queued') else 'paused'
            if job.status == 'queued':
                job.status = 'paused'
            self.jobs[job.id] = job
            self.order.append(job.id)

    def _save_jobs(self):
        with self.lock:
            data = [self.jobs[i].persist() for i in self.order
                    if self.jobs[i].status not in ('finished', 'cancelled')]
        tmp = self.jobs_path.with_suffix('.tmp')
        try:
            tmp.write_text(json.dumps(data, ensure_ascii=False), 'utf-8')
            os.replace(tmp, self.jobs_path)
        except OSError:
            pass

    def history(self) -> list:
        try:
            return json.loads(self.history_path.read_text('utf-8'))
        except (OSError, ValueError):
            return []

    def _add_history(self, job: Job):
        with self.lock:
            hist = self.history()
            hist.insert(0, {'id': job.id, 'url': job.url, 'title': job.title, 'files': job.files,
                            'via': job.via, 'finished': job.finished})
            hist = hist[:500]
            try:
                self.history_path.write_text(json.dumps(hist, ensure_ascii=False), 'utf-8')
            except OSError:
                pass
        self._emit('history', None)

    def clear_history(self):
        with self.lock:
            try:
                self.history_path.unlink()
            except OSError:
                pass
        self._emit('history', None)

    # ---------------------------------------------------------------- 操作
    def list(self) -> list[dict]:
        with self.lock:
            return [self.jobs[i].snapshot() for i in self.order]

    def get(self, job_id: str) -> Job | None:
        return self.jobs.get(job_id)

    def add(self, url: str, options: dict | None = None, title: str | None = None) -> Job:
        opts = {k: v for k, v in (options or {}).items() if k in JOB_OPTION_KEYS and v not in (None, '')}
        job = Job(url=url.strip(), options=opts, title=title)
        with self.lock:
            self.jobs[job.id] = job
            self.order.append(job.id)
        self._changed(job)
        self._pump()
        return job

    def pause(self, job_id):
        job = self.jobs.get(job_id)
        if not job:
            return
        if job.status == 'running':
            job.control.stop_reason = 'pause'
        elif job.status == 'queued':
            job.status = 'paused'
            self._changed(job)

    def resume(self, job_id):
        job = self.jobs.get(job_id)
        if job and job.status in ('paused', 'error', 'cancelled'):
            job.status, job.error, job.stage = 'queued', None, ''
            job.control = JobControl()
            self._changed(job)
            self._pump()

    def cancel(self, job_id):
        job = self.jobs.get(job_id)
        if not job:
            return
        if job.status == 'running':
            job.control.stop_reason = 'cancel'
        elif job.status in ('queued', 'paused', 'error'):
            job.status = 'cancelled'
            self._changed(job)

    def remove(self, job_id):
        with self.lock:
            job = self.jobs.get(job_id)
            if not job or job.status == 'running':
                return False
            del self.jobs[job_id]
            self.order.remove(job_id)
        self._emit('removed', job_id)
        self._save_jobs()
        return True

    def clear_done(self):
        with self.lock:
            done = [i for i in self.order if self.jobs[i].status in ('finished', 'cancelled')]
        for i in done:
            self.remove(i)

    def pause_all(self):
        for i in list(self.order):
            self.pause(i)

    def resume_all(self):
        for i in list(self.order):
            if self.jobs[i].status == 'paused':
                self.resume(i)

    # ---------------------------------------------------------------- 排程
    def _pump(self):
        limit = self.store.get().max_concurrent_jobs
        with self.lock:
            running = sum(1 for j in self.jobs.values() if j.status == 'running')
            for i in self.order:
                if running >= limit:
                    break
                job = self.jobs[i]
                if job.status == 'queued':
                    job.status = 'running'
                    running += 1
                    threading.Thread(target=self._run, args=(job,), daemon=True, name=f'job-{job.id}').start()

    def _settings_for(self, job: Job) -> Settings:
        data = self.store.get().to_dict()
        for k in ('quality', 'audio_format', 'download_dir', 'aria2c', 'concurrent_fragments'):
            if k in job.options:
                data[k] = job.options[k]
        return Settings.from_dict(data)

    def _run(self, job: Job):
        job.progress, job.files, job.error = {}, [], None
        self._changed(job)

        def log(level, msg):
            job.log.append({'t': time.time(), 'level': level, 'msg': msg})
            if level != 'info':
                self._emit('log', {'id': job.id, 'level': level, 'msg': msg})

        def on_progress(data):
            job.progress.update(data)
            self._changed(job, persist=False)

        def on_stage(stage, detail=None):
            job.stage = stage
            if stage == 'downloading' and detail and not job.title:
                job.title = detail
            job.stage_detail = detail
            self._changed(job, persist=False)

        eng = Engine(self._settings_for(job), log=log, on_progress=on_progress, on_stage=on_stage,
                     control=job.control, temp_key=job.id)
        o = job.options
        try:
            res = eng.download(job.url, format_id=o.get('format_id'), sniff_url=o.get('sniff_url'),
                               playlist_items=o.get('playlist_items'), title=job.title)
            eng.remove_temp_dir(force=True)  # 先清暫存，再回報完成
            job.title = res.get('title') or job.title
            job.via, job.files = res.get('via'), res.get('files') or []
            job.status, job.stage = 'finished', 'done'
            job.finished = time.time()
            job.progress.update({'percent': 100.0, 'speed': None, 'eta': 0})
            self._add_history(job)
        except DownloadCancelled:
            if job.control.stop_reason == 'cancel':
                eng.cleanup_partials()
                job.status = 'cancelled'
            else:
                job.status = 'paused'
            job.stage = ''
        except Exception as e:  # noqa: BLE001
            job.status, job.stage = 'error', ''
            job.error = friendly_error(str(e))
            log('error', traceback.format_exc(limit=3))
        finally:
            self._changed(job)
            self._pump()

