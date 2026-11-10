"""外部工具 (FFmpeg / aria2c) 的偵測、安裝，以及 yt-dlp 自我更新。"""

from __future__ import annotations

import io
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

from .config import bin_dir, lib_dir

IS_WIN = sys.platform == 'win32'
EXE = '.exe' if IS_WIN else ''

FFMPEG_WIN_URL = 'https://github.com/yt-dlp/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip'
ARIA2_WIN_URL = 'https://github.com/aria2/aria2/releases/download/release-1.37.0/aria2-1.37.0-win-64bit-build1.zip'
DENO_WIN_URL = 'https://github.com/denoland/deno/releases/latest/download/deno-x86_64-pc-windows-msvc.zip'
JS_RUNTIMES = ('deno', 'node', 'bun')

# Windows 下呼叫子程序時不要跳出黑色主控台視窗
NO_WINDOW = {'creationflags': 0x08000000} if IS_WIN else {}


def find_tool(name: str) -> str | None:
    """先找程式自己的 bin 資料夾，再找 PATH。"""
    local = bin_dir() / f'{name}{EXE}'
    if local.is_file():
        return str(local)
    if getattr(sys, 'frozen', False):
        bundled = Path(sys.executable).parent / 'bin' / f'{name}{EXE}'
        if bundled.is_file():
            return str(bundled)
    return shutil.which(name)


def tool_version(path: str | None, args=('-version',)) -> str | None:
    if not path:
        return None
    try:
        out = subprocess.run([path, *args], capture_output=True, text=True, timeout=10,
                             encoding='utf-8', errors='replace', **NO_WINDOW).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    first = out.strip().splitlines()[0] if out.strip() else ''
    m = re.search(r'version\s+(\S+)', first)
    return m.group(1) if m else first[:60] or None


def ffmpeg_location() -> str | None:
    p = find_tool('ffmpeg')
    return str(Path(p).parent) if p else None


def js_runtimes() -> dict:
    """YouTube 需要 JavaScript 執行環境 (Deno / Node / Bun) 才能解出完整格式。"""
    found = {}
    for name in JS_RUNTIMES:
        p = find_tool(name)
        if p:
            found[name] = {'path': p}
    return found


def ejs_available() -> bool:
    try:
        import yt_dlp_ejs  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


def impersonate_available() -> bool:
    try:
        import curl_cffi  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


def status() -> dict:
    import yt_dlp
    ffmpeg = find_tool('ffmpeg')
    ffprobe = find_tool('ffprobe')
    aria2c = find_tool('aria2c')
    js = js_runtimes()
    js_name = next(iter(js), None)
    return {
        'platform': sys.platform,
        'python': sys.version.split()[0],
        'frozen': bool(getattr(sys, 'frozen', False)),
        'yt_dlp': {'version': yt_dlp.version.__version__, 'path': os.path.dirname(yt_dlp.__file__)},
        'ffmpeg': {'path': ffmpeg, 'version': tool_version(ffmpeg)},
        'ffprobe': {'path': ffprobe, 'version': tool_version(ffprobe)},
        'aria2c': {'path': aria2c, 'version': tool_version(aria2c, ('--version',))},
        'curl_cffi': impersonate_available(),
        'js_runtime': {'name': js_name, 'path': js[js_name]['path'] if js_name else None,
                       'version': tool_version(js[js_name]['path'], ('--version',)) if js_name else None},
        'can_auto_install': IS_WIN,
        'install_hint': install_hint(),
        'bin_dir': str(bin_dir()),
    }


def install_hint() -> str:
    if IS_WIN:
        return '按下「自動安裝工具」即可下載 FFmpeg、aria2c 與 Deno 到程式資料夾。'
    if sys.platform == 'darwin':
        return '請在終端機執行： brew install ffmpeg aria2 deno'
    return ('請在終端機執行： sudo apt install ffmpeg aria2 ，'
            'Deno 可用 curl -fsSL https://deno.land/install.sh | sh 安裝')


def _download(url: str, progress=None) -> bytes:
    req = urllib.request.Request(url, headers={'User-Agent': 'OmniDL'})
    with urllib.request.urlopen(req, timeout=60) as resp:
        total = int(resp.headers.get('Content-Length') or 0)
        buf = io.BytesIO()
        while True:
            chunk = resp.read(1 << 16)
            if not chunk:
                break
            buf.write(chunk)
            if progress:
                progress(buf.tell(), total)
    return buf.getvalue()


def _extract(zdata: bytes, wanted: list[str], dest: Path) -> list[str]:
    got = []
    with zipfile.ZipFile(io.BytesIO(zdata)) as zf:
        for info in zf.infolist():
            base = os.path.basename(info.filename)
            if base in wanted:
                with zf.open(info) as src, open(dest / base, 'wb') as dst:
                    shutil.copyfileobj(src, dst)
                got.append(base)
    return got


def install_windows_tools(which=('ffmpeg', 'aria2c', 'deno'), progress=None) -> dict:
    """在 Windows 上下載 FFmpeg / aria2c / Deno 可攜版到 bin 資料夾。"""
    if not IS_WIN:
        raise RuntimeError(install_hint())
    dest = bin_dir()
    done = {}
    if 'ffmpeg' in which:
        data = _download(FFMPEG_WIN_URL, progress and (lambda d, t: progress('ffmpeg', d, t)))
        done['ffmpeg'] = _extract(data, ['ffmpeg.exe', 'ffprobe.exe'], dest)
    if 'aria2c' in which:
        data = _download(ARIA2_WIN_URL, progress and (lambda d, t: progress('aria2c', d, t)))
        done['aria2c'] = _extract(data, ['aria2c.exe'], dest)
    if 'deno' in which and not js_runtimes():
        data = _download(DENO_WIN_URL, progress and (lambda d, t: progress('deno', d, t)))
        done['deno'] = _extract(data, ['deno.exe'], dest)
    return done


# ---------------------------------------------------------------- yt-dlp 更新

def update_ytdlp() -> str:
    """更新 yt-dlp。

    * 原始碼執行：用 pip 升級
    * 打包成 exe：從 PyPI 下載最新的 wheel 解壓到使用者資料夾，
      下次啟動時優先載入（見 install_override_importer）
    """
    if not getattr(sys, 'frozen', False):
        r = subprocess.run([sys.executable, '-m', 'pip', 'install', '-U', 'yt-dlp[default,curl-cffi]'],
                           capture_output=True, text=True, **NO_WINDOW)
        if r.returncode != 0:
            raise RuntimeError(r.stderr.strip()[-800:] or 'pip 失敗')
        m = re.findall(r'Successfully installed .*?yt-dlp-(\S+)', r.stdout)
        return f'已更新到 {m[-1]}，請重新啟動程式' if m else '已經是最新版本'

    with urllib.request.urlopen('https://pypi.org/pypi/yt-dlp/json', timeout=30) as resp:
        meta = json.load(resp)
    version = meta['info']['version']
    wheel = next(u for u in meta['urls'] if u['packagetype'] == 'bdist_wheel')
    data = _download(wheel['url'])
    target = lib_dir()
    tmp = target.with_name('lib.new')
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        for name in zf.namelist():
            if name.startswith('yt_dlp/'):
                zf.extract(name, tmp)
    shutil.rmtree(target, ignore_errors=True)
    os.replace(tmp, target)
    return f'已下載 yt-dlp {version}，請重新啟動程式'


def install_override_importer():
    """讓 lib/ 裡較新的 yt-dlp 優先於打包在 exe 內的版本。"""
    import importlib.abc
    import importlib.machinery

    root = lib_dir()
    if not (root / 'yt_dlp' / '__init__.py').is_file():
        return False

    class _Finder(importlib.abc.MetaPathFinder):
        def find_spec(self, fullname, path, target=None):
            if fullname != 'yt_dlp' and not fullname.startswith('yt_dlp.'):
                return None
            search = [str(root)] if fullname == 'yt_dlp' else path
            return importlib.machinery.PathFinder.find_spec(fullname, search)

    sys.meta_path.insert(0, _Finder())
    return True
