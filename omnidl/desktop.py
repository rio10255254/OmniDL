"""桌面視窗：pywebview → Edge/Chrome 應用程式模式 → 預設瀏覽器，三層備援。

Windows 從瀏覽器下載的 zip 解壓後，檔案會帶有「網路下載標記」
(Mark of the Web，NTFS 的 Zone.Identifier 資料流)。.NET Framework
會拒絕載入帶標記的 DLL，pywebview 依賴的 pythonnet 因此失敗：

    RuntimeError: Failed to resolve Python.Runtime.Loader.Initialize ...

啟動時先移除程式資料夾內 DLL 的標記（等同「內容 → 解除封鎖」）；
即使仍然失敗，也會自動改用其他方式開啟介面，不會讓程式當掉。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import traceback
from pathlib import Path

from .config import app_dir

IS_WIN = sys.platform == 'win32'
FROZEN = bool(getattr(sys, 'frozen', False))
UNBLOCK_SUFFIXES = {'.dll', '.exe', '.pyd'}


def log_error(context: str, exc: BaseException | None = None):
    """無主控台的 exe 看不到錯誤，寫到使用者資料夾的 omnidl.log。"""
    try:
        with open(app_dir() / 'omnidl.log', 'a', encoding='utf-8') as f:
            f.write(f'[{time.strftime("%Y-%m-%d %H:%M:%S")}] {context}\n')
            if exc is not None:
                f.write(''.join(traceback.format_exception(type(exc), exc, exc.__traceback__)))
            f.write('\n')
    except OSError:
        pass


def bundle_dir() -> Path:
    return Path(sys.executable).parent if FROZEN else Path(__file__).parent


def unblock_bundle(root: Path | None = None) -> int:
    """移除程式資料夾內 DLL / EXE / PYD 的網路下載標記，回傳處理的檔案數。"""
    if not IS_WIN:
        return 0
    root = root or bundle_dir()
    count = 0
    for p in root.rglob('*'):
        if p.suffix.lower() not in UNBLOCK_SUFFIXES:
            continue
        try:
            os.remove(f'{p}:Zone.Identifier')
            count += 1
        except OSError:
            pass  # 沒有標記，或沒有權限（例如裝在 Program Files）
    return count


def has_zone_mark(path: Path) -> bool:
    if not IS_WIN:
        return False
    return os.path.exists(f'{path}:Zone.Identifier')


# ---------------------------------------------------------------- 第 1 層：pywebview
def try_webview(url: str, title: str) -> bool:
    """以 pywebview 開啟獨立視窗並阻塞到視窗關閉。成功回傳 True；任何失敗回傳 False。"""
    try:
        import webview  # type: ignore
    except Exception as e:  # noqa: BLE001
        log_error('pywebview 無法載入', e)
        return False
    try:
        webview.create_window(title, url, width=1180, height=820, min_size=(760, 560))
        webview.start()
        return True
    except Exception as e:  # noqa: BLE001  # pythonnet / WebView2 載入失敗等
        log_error('pywebview 視窗開啟失敗，改用其他方式', e)
        return False


# ---------------------------------------------------------------- 第 2 層：Edge / Chrome 應用程式模式
def find_app_browser() -> str | None:
    candidates = []
    if IS_WIN:
        for env in ('PROGRAMFILES(X86)', 'PROGRAMFILES', 'LOCALAPPDATA'):
            base = os.environ.get(env)
            if base:
                candidates += [
                    os.path.join(base, 'Microsoft', 'Edge', 'Application', 'msedge.exe'),
                    os.path.join(base, 'Google', 'Chrome', 'Application', 'chrome.exe'),
                ]
    elif sys.platform == 'darwin':
        candidates += ['/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
                       '/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge']
    else:
        candidates += [shutil.which(n) or '' for n in ('microsoft-edge', 'google-chrome', 'chromium', 'chromium-browser')]
    return next((c for c in candidates if c and os.path.isfile(c)), None)


def open_app_window(url: str) -> subprocess.Popen | None:
    """用 Edge/Chrome 的 --app 模式開一個沒有網址列的視窗。

    使用獨立的設定檔資料夾，讓這個瀏覽器程序不會併入使用者已開啟的瀏覽器，
    這樣才能偵測視窗何時被關閉。
    """
    exe = find_app_browser()
    if not exe:
        return None
    profile = app_dir() / 'app-window'
    try:
        return subprocess.Popen([
            exe, f'--app={url}', f'--user-data-dir={profile}', '--window-size=1180,820',
            '--no-first-run', '--no-default-browser-check', '--disable-features=Translate',
        ])
    except OSError as e:
        log_error('無法啟動應用程式模式視窗', e)
        return None


# ---------------------------------------------------------------- 自我檢測（CI 用）
def selftest(unblock: bool = True, report: str | None = None) -> int:
    """檢查打包版能否載入 pythonnet（pywebview 在 Windows 上的依賴）。"""
    result: dict = {'platform': sys.platform, 'frozen': FROZEN, 'unblock': unblock}
    if IS_WIN:
        dll = next(bundle_dir().rglob('Python.Runtime.dll'), None)
        result['runtime_dll'] = str(dll) if dll else None
        result['marked_before'] = bool(dll and has_zone_mark(dll))
        if unblock:
            result['unblocked_files'] = unblock_bundle()
        result['marked_after'] = bool(dll and has_zone_mark(dll))
        try:
            import clr  # type: ignore  # noqa: F401  (pythonnet)
            from System import Environment  # type: ignore
            result['clr'] = f'ok (.NET {Environment.Version})'
            ok = True
        except Exception as e:  # noqa: BLE001
            result['clr'] = f'失敗：{type(e).__name__}: {e}'
            ok = False
    else:
        result['clr'] = '略過（非 Windows）'
        ok = True
    result['app_browser'] = find_app_browser()
    if report:
        Path(report).write_text(json.dumps(result, ensure_ascii=False, indent=2), 'utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if ok else 1
