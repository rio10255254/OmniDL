import time

import pytest
from fastapi.testclient import TestClient

from omnidl.config import SettingsStore
from omnidl.jobs import JobManager
from omnidl.server import create_app

from .conftest import needs_ffmpeg


@pytest.fixture()
def app_client(omnidl_home, tmp_path):
    store = SettingsStore()
    store.update({'download_dir': str(tmp_path / 'dl'), 'embed_thumbnail': False})
    manager = JobManager(store)
    app = create_app(store, manager, token='T0KEN')
    client = TestClient(app, base_url='http://127.0.0.1:8765')
    client.headers['X-OmniDL-Token'] = 'T0KEN'
    return client, manager, store


def describe(job):
    """失敗時印出錯誤與最後幾行紀錄（pytest 預設會截斷 dict）。"""
    tail = '\n'.join(f"[{x['level']}] {x['msg']}" for x in list(job.log)[-25:])
    return f'status={job.status} error={job.error!r}\n{tail}'


def wait_for(manager, job_id, statuses, timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        job = manager.get(job_id)
        if job and job.status in statuses:
            return job
        time.sleep(0.1)
    raise AssertionError(f'timeout: {manager.get(job_id).snapshot(with_log=True)}')


def test_security_guard(app_client):
    client, *_ = app_client
    assert client.get('/api/state').status_code == 200
    assert client.get('/api/state', headers={'X-OmniDL-Token': 'nope'}).status_code == 403
    assert client.get('/api/state', headers={'Host': 'evil.example.com'}).status_code == 403
    page = client.get('/')
    assert 'T0KEN' in page.text and '__OMNIDL_TOKEN__' not in page.text


def test_settings_roundtrip(app_client):
    client, _, store = app_client
    r = client.put('/api/settings', json={'concurrent_fragments': 999, 'quality': 'bogus', 'aria2c': 'all'})
    data = r.json()
    assert data['concurrent_fragments'] == 64 and data['quality'] == 'best' and data['aria2c'] == 'all'
    assert SettingsStore(store.path).get().aria2c == 'all'


def test_open_restricted(app_client):
    client, *_ = app_client
    assert client.post('/api/open', json={'path': '/etc/passwd'}).status_code == 403


@needs_ffmpeg
def test_job_lifecycle(app_client, fake_site):
    client, manager, _ = app_client
    r = client.post('/api/jobs', json={'urls': [fake_site['page']], 'options': {'quality': '720'}})
    job_id = r.json()['jobs'][0]['id']
    job = wait_for(manager, job_id, ('finished', 'error'))
    assert job.status == 'finished', describe(job)
    assert job.via == 'sniff' and job.files[0].endswith('.mp4')
    hist = client.get('/api/history').json()
    assert hist[0]['id'] == job_id
    # 從「解析」面板加入（帶 sniff_url 與標題）時，檔名要用網頁標題
    r = client.post('/api/jobs', json={'url': fake_site['page'], 'title': '自訂標題',
                                       'options': {'sniff_url': fake_site['base'] + '/hls/master.m3u8', 'quality': 'audio'}})
    job2 = wait_for(manager, r.json()['jobs'][0]['id'], ('finished', 'error'))
    assert job2.status == 'finished' and '自訂標題' in job2.files[0], describe(job2)
    log = client.get(f'/api/jobs/{job_id}/log').json()['log']
    assert any('[repair]' in line['msg'] for line in log)


@needs_ffmpeg
def test_pause_resume_cancel(app_client, fake_site):
    client, manager, store = app_client
    store.update({'concurrent_fragments': 1})
    fake_site['handler'].delay = 0.4
    try:
        job_id = client.post('/api/jobs', json={'url': fake_site['page']}).json()['jobs'][0]['id']
        # 等到開始下載片段
        end = time.time() + 30
        while time.time() < end and not manager.get(job_id).progress.get('fragment_index'):
            time.sleep(0.05)
        client.post(f'/api/jobs/{job_id}/pause')
        job = wait_for(manager, job_id, ('paused', 'finished', 'error'))
        assert job.status == 'paused', describe(job)
        fake_site['handler'].delay = 0
        client.post(f'/api/jobs/{job_id}/resume')
        job = wait_for(manager, job_id, ('finished', 'error'))
        assert job.status == 'finished', describe(job)

        fake_site['handler'].delay = 0.4
        store.update({'download_dir': store.get().download_dir + '2'})
        job_id = client.post('/api/jobs', json={'url': fake_site['page']}).json()['jobs'][0]['id']
        end = time.time() + 30
        while time.time() < end and not manager.get(job_id).progress.get('fragment_index'):
            time.sleep(0.05)
        client.post(f'/api/jobs/{job_id}/cancel')
        job = wait_for(manager, job_id, ('cancelled', 'finished', 'error'))
        assert job.status == 'cancelled'
        import os
        leftovers = [f for f in os.listdir(store.get().download_dir) if 'part' in f or f.endswith('.ytdl')]
        assert leftovers == []
    finally:
        fake_site['handler'].delay = 0


@needs_ffmpeg
def test_same_video_two_formats_concurrently(app_client, fake_site):
    """同一部影片同時下載影片與音訊，暫存檔不能互相覆蓋。"""
    import os
    client, manager, store = app_client
    fake_site['handler'].delay = 0.05
    try:
        a = client.post('/api/jobs', json={'url': fake_site['page']}).json()['jobs'][0]['id']
        b = client.post('/api/jobs', json={'url': fake_site['page'], 'options': {'quality': 'audio'}}).json()['jobs'][0]['id']
        ja, jb = wait_for(manager, a, ('finished', 'error')), wait_for(manager, b, ('finished', 'error'))
    finally:
        fake_site['handler'].delay = 0
    assert ja.status == 'finished' and jb.status == 'finished', describe(ja) + describe(jb)
    names = sorted(os.listdir(store.get().download_dir))
    assert [n.rsplit('.', 1)[1] for n in names] == ['mp3', 'mp4'], names  # 暫存資料夾也已清除
