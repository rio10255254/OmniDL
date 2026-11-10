"""本機網頁介面 (FastAPI)。只監聽 127.0.0.1，並以隨機 token 防止其他網站跨站呼叫。"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import subprocess
import sys
import threading
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from . import __version__, tools
from .config import SettingsStore
from .engine import Engine, friendly_error
from .jobs import JobManager

WEB_DIR = Path(__file__).parent / 'web'


def open_path(path: str):
    if sys.platform == 'win32':
        os.startfile(path)  # noqa: S606
    elif sys.platform == 'darwin':
        subprocess.Popen(['open', path])
    else:
        subprocess.Popen(['xdg-open', path])


def reveal_file(path: str):
    if sys.platform == 'win32':
        subprocess.Popen(['explorer', '/select,', os.path.normpath(path)])
    elif sys.platform == 'darwin':
        subprocess.Popen(['open', '-R', path])
    else:
        open_path(os.path.dirname(path))


def create_app(store: SettingsStore | None = None, manager: JobManager | None = None,
               token: str | None = None, on_shutdown=None) -> FastAPI:
    store = store or SettingsStore()
    manager = manager or JobManager(store)
    token = token or secrets.token_urlsafe(24)
    app = FastAPI(title='OmniDL', docs_url=None, redoc_url=None, openapi_url=None)
    app.state.token, app.state.store, app.state.manager = token, store, manager
    tool_state = {'busy': False, 'message': ''}

    @app.middleware('http')
    async def guard(request: Request, call_next):
        host = (request.headers.get('host') or '').split(':')[0]
        if host not in ('127.0.0.1', 'localhost'):
            return JSONResponse({'detail': 'forbidden host'}, status_code=403)
        if request.url.path.startswith('/api/'):
            given = request.headers.get('x-omnidl-token') or request.query_params.get('token')
            if not given or not secrets.compare_digest(given, token):
                return JSONResponse({'detail': 'bad token'}, status_code=403)
        return await call_next(request)

    @app.get('/', response_class=HTMLResponse)
    async def index():
        html = (WEB_DIR / 'index.html').read_text('utf-8')
        return html.replace('__OMNIDL_TOKEN__', token).replace('__OMNIDL_VERSION__', __version__)

    app.mount('/static', StaticFiles(directory=WEB_DIR), name='static')

    async def body(request: Request) -> dict:
        try:
            data = await request.json()
        except Exception:  # noqa: BLE001
            data = {}
        return data if isinstance(data, dict) else {}

    # ------------------------------------------------------------ 狀態 / 事件
    @app.get('/api/state')
    async def state():
        return {'version': __version__, 'settings': store.get().to_dict(), 'jobs': manager.list()}

    @app.get('/api/events')
    async def events(request: Request):
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue(maxsize=2000)

        def push(ev):
            def put():
                if not queue.full():
                    queue.put_nowait(ev)
            loop.call_soon_threadsafe(put)

        unsubscribe = manager.subscribe(push)

        async def stream():
            try:
                yield 'retry: 2000\n\n'
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        ev = await asyncio.wait_for(queue.get(), timeout=15)
                    except asyncio.TimeoutError:
                        yield ': ping\n\n'
                        continue
                    yield f'data: {json.dumps(ev, ensure_ascii=False)}\n\n'
            finally:
                unsubscribe()

        return StreamingResponse(stream(), media_type='text/event-stream',
                                 headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})

    # ------------------------------------------------------------ 解析
    @app.post('/api/analyze')
    async def analyze(request: Request):
        data = await body(request)
        url = (data.get('url') or '').strip()
        if not url:
            raise HTTPException(400, '請輸入網址')
        logs = []
        eng = Engine(store.get(), log=lambda lv, m: logs.append(m) if lv != 'info' else None)
        try:
            return await run_in_threadpool(eng.analyze, url)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(422, friendly_error(str(e))) from None

    @app.post('/api/sniff')
    async def sniff(request: Request):
        data = await body(request)
        url = (data.get('url') or '').strip()
        if not url:
            raise HTTPException(400, '請輸入網址')
        eng = Engine(store.get())
        try:
            res = await run_in_threadpool(eng.sniff_only, url)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(422, str(e)[:600]) from None
        return res.to_dict()

    # ------------------------------------------------------------ 工作
    @app.post('/api/jobs')
    async def add_jobs(request: Request):
        data = await body(request)
        urls = data.get('urls') or ([data['url']] if data.get('url') else [])
        urls = [u.strip() for u in urls if isinstance(u, str) and u.strip()]
        if not urls:
            raise HTTPException(400, '請輸入網址')
        opts = data.get('options') or {}
        title = data.get('title') if len(urls) == 1 else None
        jobs = [manager.add(u, opts, title=title).snapshot() for u in urls]
        return {'jobs': jobs}

    @app.get('/api/jobs/{job_id}/log')
    async def job_log(job_id: str):
        job = manager.get(job_id)
        if not job:
            raise HTTPException(404)
        return job.snapshot(with_log=True)

    @app.post('/api/jobs/{job_id}/{action}')
    async def job_action(job_id: str, action: str):
        if not manager.get(job_id):
            raise HTTPException(404)
        fn = {'pause': manager.pause, 'resume': manager.resume, 'retry': manager.resume,
              'cancel': manager.cancel, 'remove': manager.remove}.get(action)
        if not fn:
            raise HTTPException(400, 'unknown action')
        fn(job_id)
        return {'ok': True}

    @app.post('/api/bulk/{action}')
    async def bulk(action: str):
        fn = {'pause_all': manager.pause_all, 'resume_all': manager.resume_all,
              'clear_done': manager.clear_done}.get(action)
        if not fn:
            raise HTTPException(400, 'unknown action')
        fn()
        return {'ok': True}

    # ------------------------------------------------------------ 設定 / 歷史
    @app.get('/api/settings')
    async def get_settings():
        return store.get().to_dict()

    @app.put('/api/settings')
    async def put_settings(request: Request):
        return store.update(await body(request)).to_dict()

    @app.get('/api/history')
    async def history():
        return manager.history()

    @app.delete('/api/history')
    async def clear_history():
        manager.clear_history()
        return {'ok': True}

    @app.post('/api/open')
    async def open_(request: Request):
        """只允許開啟下載資料夾，或歷史/佇列中記錄的檔案。"""
        data = await body(request)
        target = data.get('path')
        allowed = {store.get().download_dir}
        for j in manager.list():
            allowed.update(j['files'])
        for h in manager.history():
            allowed.update(h.get('files') or [])
        if not target:
            target = store.get().download_dir
            os.makedirs(target, exist_ok=True)
        if target not in allowed:
            raise HTTPException(403, '不允許開啟此路徑')
        if not os.path.exists(target):
            raise HTTPException(404, '檔案已不存在')
        if os.path.isdir(target):
            open_path(target)
        else:
            reveal_file(target)
        return {'ok': True}

    # ------------------------------------------------------------ 工具
    @app.get('/api/doctor')
    async def doctor():
        st = await run_in_threadpool(tools.status)
        st['task'] = dict(tool_state)
        return st

    def _run_tool_task(fn, label):
        def run():
            tool_state.update(busy=True, message=f'{label}中…')
            manager._emit('tool', dict(tool_state))
            try:
                msg = fn()
                tool_state.update(busy=False, message=msg if isinstance(msg, str) else f'{label}完成')
            except Exception as e:  # noqa: BLE001
                tool_state.update(busy=False, message=f'{label}失敗：{e}')
            manager._emit('tool', dict(tool_state))
        if tool_state['busy']:
            raise HTTPException(409, '已有工作在執行')
        threading.Thread(target=run, daemon=True).start()

    @app.post('/api/tools/install')
    async def install_tools():
        if not tools.IS_WIN:
            raise HTTPException(400, tools.install_hint())

        def task():
            def prog(name, done, total):
                tool_state['message'] = f'下載 {name}… {done * 100 // total if total else 0}%'
                manager._emit('tool', dict(tool_state))
            tools.install_windows_tools(progress=prog)
            return 'FFmpeg 與 aria2c 安裝完成'
        _run_tool_task(task, '安裝工具')
        return {'ok': True}

    @app.post('/api/tools/update-ytdlp')
    async def update_ytdlp():
        _run_tool_task(tools.update_ytdlp, '更新 yt-dlp')
        return {'ok': True}

    @app.post('/api/shutdown')
    async def shutdown():
        manager.pause_all()
        if on_shutdown:
            threading.Timer(0.5, on_shutdown).start()
        return {'ok': True}

    return app
