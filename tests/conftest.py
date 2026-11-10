"""測試用的本機假影片網站。

模擬實際常見的防護手法：
* 影片網址藏在 p,a,c,k,e,d 混淆過的 JS 裡
* Master Playlist + 兩種畫質 (720p / 360p)
* 片段偽裝成 .jpg，且每個片段前面塞了一段真的 PNG 檔頭
* m3u8 需要正確的 Referer 才能讀取
"""

from __future__ import annotations

import http.server
import os
import re
import shutil
import subprocess
import threading
from functools import partial

import pytest

FFMPEG = shutil.which('ffmpeg')
needs_ffmpeg = pytest.mark.skipif(not FFMPEG, reason='需要 ffmpeg')

# 1x1 PNG
FAKE_PNG = bytes.fromhex(
    '89504e470d0a1a0a0000000d4948445200000001000000010806000000'
    '1f15c4890000000d49444154789c6360000002000154a24f5f0000000049454e44ae426082')


def pack_js(js: str) -> str:
    """簡易 Dean Edwards packer，產生 eval(function(p,a,c,k,e,d){...}) 格式。"""
    def enc(n):
        digits = '0123456789abcdefghijklmnopqrstuvwxyz'
        s = ''
        while True:
            s = digits[n % 36] + s
            n //= 36
            if not n:
                return s
    words = []
    for w in re.findall(r'\b\w+\b', js):
        if w not in words:
            words.append(w)
    idx = {w: enc(i) for i, w in enumerate(words)}
    payload = re.sub(r'\b\w+\b', lambda m: idx[m.group(0)], js).replace("'", "\\'")
    body = ("eval(function(p,a,c,k,e,d){e=function(c){return c.toString(36)};"
            "if(!''.replace(/^/,String)){while(c--){d[c.toString(a)]=k[c]||c.toString(a)}"
            "k=[function(e){return d[e]}];e=function(){return'\\\\w+'};c=1};"
            "while(c--){if(k[c]){p=p.replace(new RegExp('\\\\b'+e(c)+'\\\\b','g'),k[c])}}return p}")
    return f"{body}('{payload}',36,{len(words)},'{'|'.join(words)}'.split('|'),0,{{}}))"


def _make_variant(root, name, size, seconds=4):
    vdir = os.path.join(root, 'hls', name)
    os.makedirs(vdir)
    subprocess.run([
        FFMPEG, '-v', 'error', '-f', 'lavfi', '-i', f'testsrc=size={size}:rate=25:duration={seconds}',
        '-f', 'lavfi', '-i', f'sine=frequency=440:duration={seconds}',
        '-c:v', 'libx264', '-preset', 'ultrafast', '-g', '25', '-c:a', 'aac', '-shortest',
        '-f', 'hls', '-hls_time', '1', '-hls_list_size', '0',
        '-hls_segment_filename', os.path.join(vdir, 'seg%03d.ts'), os.path.join(vdir, 'index.m3u8'),
    ], check=True)
    # 偽裝：改名成 .jpg 並在前面塞 PNG 檔頭
    playlist = open(os.path.join(vdir, 'index.m3u8')).read()
    for fn in sorted(os.listdir(vdir)):
        if fn.endswith('.ts'):
            data = open(os.path.join(vdir, fn), 'rb').read()
            os.remove(os.path.join(vdir, fn))
            new = fn.replace('.ts', '.jpg')
            with open(os.path.join(vdir, new), 'wb') as f:
                f.write(FAKE_PNG + data)
            playlist = playlist.replace(fn, new)
    with open(os.path.join(vdir, 'index.m3u8'), 'w') as f:
        f.write(playlist)


class _Handler(http.server.SimpleHTTPRequestHandler):
    delay = 0.0  # 測試暫停/取消時用來放慢片段下載

    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.delay and self.path.endswith('.jpg'):
            import time
            time.sleep(self.delay)
        if self.path.split('?')[0].endswith('.m3u8'):
            ref = self.headers.get('Referer', '')
            if '/watch/' not in ref:
                self.send_error(403, 'Referer required')
                return
        return super().do_GET()


@pytest.fixture(scope='session')
def fake_site(tmp_path_factory):
    if not FFMPEG:
        pytest.skip('需要 ffmpeg')
    root = str(tmp_path_factory.mktemp('site'))
    _make_variant(root, '720p', '1280x720')
    _make_variant(root, '360p', '640x360')
    with open(os.path.join(root, 'hls', 'master.m3u8'), 'w') as f:
        f.write('#EXTM3U\n'
                '#EXT-X-STREAM-INF:BANDWIDTH=2000000,RESOLUTION=1280x720\n720p/index.m3u8\n'
                '#EXT-X-STREAM-INF:BANDWIDTH=600000,RESOLUTION=640x360\n360p/index.m3u8\n')

    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), partial(_Handler, directory=root))
    port = server.server_address[1]
    base = f'http://127.0.0.1:{port}'
    js = f"var player=new Player();player.source='{base}/hls/master.m3u8';player.start()"
    os.makedirs(os.path.join(root, 'watch'))
    with open(os.path.join(root, 'watch', 'abc-123.html'), 'w', encoding='utf-8') as f:
        f.write(f'''<!doctype html><html><head><title>測試影片 Test Video</title>
<meta property="og:image" content="{base}/thumb.jpg"></head>
<body><div class="player" data-thumb="{base}/thumb.jpg"></div>
<script>{pack_js(js)}</script></body></html>''')
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    yield {'base': base, 'page': f'{base}/watch/abc-123.html', 'root': root, 'handler': _Handler}
    server.shutdown()


@pytest.fixture()
def omnidl_home(tmp_path, monkeypatch):
    monkeypatch.setenv('OMNIDL_HOME', str(tmp_path / 'home'))
    return tmp_path
