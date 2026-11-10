"""端對端測試：嗅探 → Master Playlist → 下載偽裝片段 → 修復 → FFmpeg 合併成 MP4。"""

import json
import os
import shutil
import subprocess

import pytest

from omnidl.config import Settings
from omnidl.engine import Engine
from .conftest import needs_ffmpeg

pytestmark = needs_ffmpeg


def probe(path):
    out = subprocess.run(['ffprobe', '-v', 'error', '-show_format', '-show_streams', '-of', 'json', path],
                         capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def make_engine(tmp_path, **kw):
    s = Settings(download_dir=str(tmp_path / 'dl'), embed_thumbnail=False, **kw)
    logs = []
    stages = []
    eng = Engine(s, log=lambda lv, m: logs.append((lv, m)), on_stage=lambda st, d=None: stages.append(st))
    return eng, logs, stages


def test_sniff_finds_master(fake_site, omnidl_home, tmp_path):
    eng, logs, _ = make_engine(tmp_path)
    res = eng.sniff_only(fake_site['page'])
    assert res.title == '測試影片 Test Video'
    assert res.best.url.endswith('/hls/master.m3u8')
    assert res.best.is_master and res.best.variants == 2


def test_analyze_via_sniff(fake_site, omnidl_home, tmp_path):
    eng, logs, _ = make_engine(tmp_path)
    info = eng.analyze(fake_site['page'])
    assert info['via'] == 'sniff'
    heights = sorted(f['height'] for f in info['formats'] if f['height'])
    assert heights == [360, 720]
    assert {f['kind'] for f in info['formats']} == {'av'}
    assert info['sniff_url'].endswith('master.m3u8')


@pytest.mark.parametrize('aria2', ['off', 'all'])
def test_download_disguised_hls(fake_site, omnidl_home, tmp_path, aria2):
    if aria2 != 'off' and not shutil.which('aria2c'):
        pytest.skip('需要 aria2c')
    eng, logs, stages = make_engine(tmp_path, aria2c=aria2)
    result = eng.download(fake_site['page'])
    assert result['via'] == 'sniff'
    files = [f for f in result['files'] if f.endswith('.mp4')]
    assert len(files) == 1, (result, logs)
    meta = probe(files[0])
    assert meta['format']['format_name'].startswith('mov,mp4')
    v = [s for s in meta['streams'] if s['codec_type'] == 'video'][0]
    assert v['height'] == 720  # 自動選最高畫質
    assert any(s['codec_type'] == 'audio' for s in meta['streams'])
    assert float(meta['format']['duration']) > 3.5
    assert '測試影片' in os.path.basename(files[0])
    assert 'repairing' in stages


def test_quality_limit(fake_site, omnidl_home, tmp_path):
    eng, logs, _ = make_engine(tmp_path, quality='480')
    result = eng.download(fake_site['page'])
    meta = probe(result['files'][0])
    v = [s for s in meta['streams'] if s['codec_type'] == 'video'][0]
    assert v['height'] == 360


def test_audio_only(fake_site, omnidl_home, tmp_path):
    eng, logs, _ = make_engine(tmp_path, quality='audio', audio_format='mp3')
    result = eng.download(fake_site['page'])
    assert result['files'][0].endswith('.mp3'), (result, logs)
    meta = probe(result['files'][0])
    assert [s['codec_type'] for s in meta['streams']] == ['audio']
