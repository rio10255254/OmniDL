"""命令列介面。

    omnidl                         開啟圖形介面
    omnidl get URL [URL...]        直接下載
    omnidl info URL                列出可用畫質
    omnidl sniff URL               嗅探網頁裡的串流網址
    omnidl doctor                  檢查 FFmpeg / aria2c / yt-dlp
    omnidl install-tools           (Windows) 自動下載 FFmpeg 與 aria2c
    omnidl update                  更新 yt-dlp
"""

from __future__ import annotations

import argparse
import shutil
import socket
import sys
import threading
import time
import webbrowser

from . import __version__, tools
from .config import ARIA2_MODES, AUDIO_FORMATS, BROWSERS, QUALITY_CHOICES, SNIFF_MODES, SettingsStore

COMMANDS = ('gui', 'get', 'info', 'sniff', 'doctor', 'install-tools', 'update', 'selftest')


def _human(n):
    if n is None:
        return '?'
    for unit in ('B', 'KiB', 'MiB', 'GiB', 'TiB'):
        if abs(n) < 1024:
            return f'{n:.1f}{unit}'
        n /= 1024
    return f'{n:.1f}PiB'


def _fix_console():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding='utf-8', errors='replace')
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------- get
def _settings_from_args(args):
    s = SettingsStore().get()
    for attr, key in (('quality', 'quality'), ('audio_format', 'audio_format'), ('output', 'download_dir'),
                      ('fragments', 'concurrent_fragments'), ('aria2', 'aria2c'), ('sniff', 'sniff_mode'),
                      ('cookies_from_browser', 'cookies_from_browser'), ('cookies', 'cookies_file'),
                      ('proxy', 'proxy'), ('limit_rate', 'rate_limit'), ('template', 'output_template')):
        v = getattr(args, attr, None)
        if v is not None:
            setattr(s, key, v)
    if getattr(args, 'no_repair', False):
        s.repair_disguised = False
    if getattr(args, 'no_playlist', False):
        s.playlist = False
    if getattr(args, 'subs', False):
        s.write_subs = True
    s.validate()
    return s


def cmd_get(args) -> int:
    from yt_dlp.utils import DownloadCancelled

    from .engine import Engine, JobControl, friendly_error

    urls = list(args.urls)
    if args.batch_file:
        with open(args.batch_file, encoding='utf-8') as f:
            urls += [ln.strip() for ln in f if ln.strip() and not ln.startswith('#')]
    if not urls:
        print('請提供至少一個網址', file=sys.stderr)
        return 2
    settings = _settings_from_args(args)
    width = shutil.get_terminal_size((100, 20)).columns
    failed = 0
    for n, url in enumerate(urls, 1):
        print(f'\n[{n}/{len(urls)}] {url}')
        state = {'stage': ''}

        def log(level, msg):
            if level == 'error' or (level == 'info' and not args.verbose
                                    and not msg.startswith(('[sniff]', '[repair]', '[omnidl]'))):
                return
            print(('\r' + ' ' * (width - 1) + '\r') + msg, file=sys.stderr if level != 'info' else sys.stdout)

        def prog(d):
            pct = d.get('percent')
            parts = [f'{pct:5.1f}%' if pct is not None else '  ...']
            if d.get('total'):
                parts.append(f'{_human(d.get("downloaded"))}/{_human(d["total"])}')
            if d.get('speed'):
                parts.append(f'{_human(d["speed"])}/s')
            if d.get('eta'):
                parts.append(f'ETA {int(d["eta"]) // 60:02d}:{int(d["eta"]) % 60:02d}')
            if d.get('fragment_count'):
                parts.append(f'片段 {d.get("fragment_index")}/{d["fragment_count"]}')
            line = '  '.join(parts)
            sys.stdout.write('\r' + line[:width - 1].ljust(min(width - 1, 70)))
            sys.stdout.flush()

        def stage(st, detail=None, state=state):
            if st != state['stage']:
                state['stage'] = st
                label = STAGE_LABELS.get(st, st)
                sys.stdout.write('\r' + ' ' * (width - 1) + '\r')
                print(f'» {label}' + (f'：{detail}' if detail and st != 'downloading' else ''))

        import hashlib
        key = 'cli-' + hashlib.md5(f'{url}|{args.format}|{settings.quality}'.encode()).hexdigest()[:12]
        eng = Engine(settings, log=log, on_progress=prog, on_stage=stage, control=JobControl(), temp_key=key)
        try:
            res = eng.download(url, format_id=args.format, playlist_items=args.items)
            eng.remove_temp_dir(force=True)
            print()
            for f in res['files']:
                print(f'✔ 完成：{f}')
            if res['via'] == 'sniff':
                print('  (透過網頁嗅探取得)')
        except (KeyboardInterrupt, DownloadCancelled):
            print('\n已中斷。再次執行相同指令即可續傳。')
            return 130
        except Exception as e:  # noqa: BLE001
            print(f'\n✘ 失敗：{friendly_error(str(e))}', file=sys.stderr)
            failed += 1
    return 1 if failed else 0


STAGE_LABELS = {
    'analyzing': '解析網址', 'sniffing': '網頁嗅探', 'downloading': '下載中',
    'repairing': '修復偽裝片段', 'merging': 'FFmpeg 合併', 'fixing': '修正容器',
    'converting': '轉檔', 'embedding': '嵌入資訊', 'moving': '移動檔案', 'processing': '後處理',
}


# ---------------------------------------------------------------- info / sniff
def cmd_info(args) -> int:
    from .engine import Engine, friendly_error
    eng = Engine(_settings_from_args(args), log=lambda lv, m: print(m, file=sys.stderr) if lv == 'warning' else None)
    try:
        info = eng.analyze(args.url)
    except Exception as e:  # noqa: BLE001
        print(f'✘ {friendly_error(str(e))}', file=sys.stderr)
        return 1
    print(f'標題：{info.get("title")}')
    print(f'來源：{info.get("extractor")}  (解析方式：{info.get("via")})')
    if info['type'] == 'playlist':
        print(f'播放清單，共 {info["count"]} 部')
        for e in info['entries'][:50]:
            print(f'  {e["index"]:>3}. {e.get("title") or e.get("url")}')
        return 0
    print(f'{"ID":<14}{"副檔名":<6}{"解析度":<14}{"FPS":<5}{"視訊":<14}{"音訊":<12}{"大小":>10}')
    for f in info['formats']:
        print(f'{str(f["format_id"])[:13]:<14}{f["ext"] or "":<9}{(f["resolution"] or "")[:13]:<17}'
              f'{str(f["fps"] or ""):<5}{(f["vcodec"] or "-")[:13]:<14}{(f["acodec"] or "-")[:11]:<12}'
              f'{_human(f["filesize"]) if f["filesize"] else "":>10}')
    print('\n用 omnidl get URL -f <ID> 指定格式；只有視訊的格式可寫成 "<ID>+ba" 自動配上最佳音訊。')
    return 0


def cmd_sniff(args) -> int:
    from .engine import Engine
    eng = Engine(_settings_from_args(args))
    try:
        res = eng.sniff_only(args.url)
    except Exception as e:  # noqa: BLE001
        print(f'✘ {e}', file=sys.stderr)
        return 1
    print(f'標題：{res.title}')
    if not res.candidates:
        print('找不到任何串流網址')
        return 1
    for c in res.candidates:
        print(f'[{c.score:>4}] {c.ext:<5} {c.url}' + (f'\n        {c.note}' if c.note else ''))
    return 0


# ---------------------------------------------------------------- doctor / tools
def cmd_doctor(args) -> int:
    st = tools.status()
    ok = lambda b: '✔' if b else '✘'  # noqa: E731
    print(f'OmniDL {__version__}  /  Python {st["python"]}  /  {st["platform"]}')
    print(f'{ok(True)} yt-dlp     {st["yt_dlp"]["version"]}  {st["yt_dlp"]["path"]}')
    for name in ('ffmpeg', 'ffprobe', 'aria2c'):
        t = st[name]
        print(f'{ok(t["path"])} {name:<10} {t["version"] or "未安裝"}  {t["path"] or ""}')
    js = st['js_runtime']
    print(f'{ok(js["name"])} JS 執行環境 {(js["name"] or "") + " " + (js["version"] or "") if js["name"] else "未安裝（YouTube 需要 Deno）"}')
    print(f'{ok(st["curl_cffi"])} curl_cffi  {"可用（可偽裝瀏覽器指紋突破 Cloudflare）" if st["curl_cffi"] else "未安裝"}')
    if not (st['ffmpeg']['path'] and st['aria2c']['path']):
        print('\n' + st['install_hint'])
    return 0 if st['ffmpeg']['path'] else 1


def cmd_install_tools(args) -> int:
    if not tools.IS_WIN:
        print(tools.install_hint())
        return 1

    def prog(name, done, total):
        sys.stdout.write(f'\r下載 {name} {done * 100 // total if total else 0}%   ')
        sys.stdout.flush()
    res = tools.install_windows_tools(progress=prog)
    print(f'\n完成：{res}  →  {tools.bin_dir()}')
    return 0


def cmd_update(args) -> int:
    print(tools.update_ytdlp())
    return 0


# ---------------------------------------------------------------- gui
def _free_port(preferred: int) -> int:
    for port in (preferred, 0):
        with socket.socket() as s:
            try:
                s.bind(('127.0.0.1', port))
                return s.getsockname()[1]
            except OSError:
                continue
    raise RuntimeError('找不到可用的連接埠')


def cmd_gui(args) -> int:
    import uvicorn

    from . import desktop
    from .server import create_app

    store = SettingsStore()
    port = _free_port(args.port or store.get().port)
    server_ref = {}

    def stop():
        srv = server_ref.get('server')
        if srv:
            srv.should_exit = True

    app = create_app(store, on_shutdown=stop)
    url = f'http://127.0.0.1:{port}/'
    config = uvicorn.Config(app, host='127.0.0.1', port=port, log_level='warning', access_log=False)
    server = uvicorn.Server(config)
    server_ref['server'] = server

    # 伺服器一律在背景執行緒啟動，介面用哪一種方式開都不影響它
    t = threading.Thread(target=server.run, daemon=True, name='omnidl-server')
    t.start()
    for _ in range(200):
        if server.started or not t.is_alive():
            break
        time.sleep(0.05)
    if not server.started:
        desktop.log_error(f'介面伺服器無法啟動 (port {port})')
        print(f'✘ 介面伺服器無法啟動 (port {port})', file=sys.stderr)
        return 1

    def shutdown() -> int:
        app.state.manager.pause_all()
        server.should_exit = True
        t.join(timeout=5)
        return 0

    want_window = args.window or (desktop.FROZEN and not args.browser)
    if want_window:
        # 第 1 層：pywebview 獨立視窗
        if desktop.try_webview(url, 'OmniDL 萬用影片下載器'):
            return shutdown()
        # 第 2 層：Edge / Chrome 應用程式模式（沒有網址列的獨立視窗）
        proc = desktop.open_app_window(url)
        if proc is not None:
            started = time.monotonic()
            while proc.poll() is None and not server.should_exit:
                time.sleep(0.3)
            # 很快就結束代表瀏覽器把視窗交給了已開啟的程序，改成等伺服器結束
            if proc.poll() is not None and time.monotonic() - started > 5:
                return shutdown()

    # 第 3 層：預設瀏覽器
    print(f'OmniDL {__version__} 已啟動：{url}')
    print('在介面按「結束程式」，或在這裡按 Ctrl+C 即可結束。')
    if not args.no_browser and not (want_window and server.should_exit):
        webbrowser.open(url)
    try:
        while not server.should_exit and t.is_alive():
            time.sleep(0.3)
    except KeyboardInterrupt:
        pass
    return shutdown()


def cmd_selftest(args) -> int:
    from . import desktop
    return desktop.selftest(unblock=not args.no_unblock, report=args.report)


# ---------------------------------------------------------------- main
def build_parser():
    p = argparse.ArgumentParser(prog='omnidl', description='OmniDL 萬用影片下載器 (yt-dlp + FFmpeg + aria2c)')
    p.add_argument('--version', action='version', version=f'OmniDL {__version__}')
    sub = p.add_subparsers(dest='cmd')

    g = sub.add_parser('gui', help='開啟圖形介面（預設）')
    g.add_argument('--port', type=int)
    g.add_argument('--no-browser', action='store_true', help='不要自動開啟瀏覽器')
    g.add_argument('--window', action='store_true', help='用獨立視窗開啟 (需要 pywebview)')
    g.add_argument('--browser', action='store_true', help='強制用瀏覽器開啟')

    def common(sp):
        sp.add_argument('-o', '--output', help='下載資料夾')
        sp.add_argument('-N', '--fragments', type=int, help='同時下載的片段數（預設 8）')
        sp.add_argument('--aria2', choices=ARIA2_MODES, help='aria2c 外掛：off / http (只用於直連檔) / all')
        sp.add_argument('--sniff', choices=SNIFF_MODES, help='網頁嗅探：auto / always / off')
        sp.add_argument('--cookies-from-browser', choices=[b for b in BROWSERS if b])
        sp.add_argument('--cookies', help='cookies.txt 檔案')
        sp.add_argument('--proxy')
        sp.add_argument('-v', '--verbose', action='store_true')

    d = sub.add_parser('get', help='下載影片')
    d.add_argument('urls', nargs='*')
    d.add_argument('-q', '--quality', choices=QUALITY_CHOICES, help='畫質上限，audio = 只要音訊')
    d.add_argument('-a', '--audio-format', choices=AUDIO_FORMATS)
    d.add_argument('-f', '--format', help='指定 yt-dlp 格式 ID 或格式選擇語法')
    d.add_argument('-i', '--batch-file', help='從文字檔讀取網址（一行一個）')
    d.add_argument('--items', help='播放清單項目，例如 1-5,8')
    d.add_argument('-t', '--template', help='檔名樣板 (yt-dlp outtmpl)')
    d.add_argument('-r', '--limit-rate', help='限速，例如 5M')
    d.add_argument('--no-repair', action='store_true', help='停用偽裝片段修復')
    d.add_argument('--no-playlist', action='store_true')
    d.add_argument('--subs', action='store_true', help='下載並嵌入字幕')
    common(d)

    i = sub.add_parser('info', help='列出可用畫質')
    i.add_argument('url')
    common(i)
    s = sub.add_parser('sniff', help='嗅探網頁中的串流網址')
    s.add_argument('url')
    common(s)
    sub.add_parser('doctor', help='檢查相依工具')
    sub.add_parser('install-tools', help='(Windows) 下載 FFmpeg 與 aria2c')
    sub.add_parser('update', help='更新 yt-dlp')
    st = sub.add_parser('selftest', help=argparse.SUPPRESS)
    st.add_argument('--no-unblock', action='store_true')
    st.add_argument('--report')
    return p


def main(argv=None) -> int:
    _fix_console()
    argv = list(sys.argv[1:] if argv is None else argv)
    # `omnidl URL` 等同 `omnidl get URL`
    if argv and argv[0] not in COMMANDS and not argv[0].startswith('-') and '://' in argv[0]:
        argv.insert(0, 'get')
    args = build_parser().parse_args(argv)
    if not args.cmd:
        args = build_parser().parse_args(['gui', *argv])
    handler = {
        'gui': cmd_gui, 'get': cmd_get, 'info': cmd_info, 'sniff': cmd_sniff, 'doctor': cmd_doctor,
        'install-tools': cmd_install_tools, 'update': cmd_update, 'selftest': cmd_selftest,
    }[args.cmd]
    return handler(args) or 0
