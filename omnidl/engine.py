"""下載引擎：包裝 yt-dlp，加上嗅探備援、偽裝片段修復與 aria2c 外掛。

解析流程（智慧備援鏈）::

    yt-dlp 原生解析 ──失敗(403/Cloudflare)──▶ 瀏覽器指紋偽裝重試 (curl_cffi)
            │                                         │
            └──────── 找不到影片 / 不支援的網址 ◀───────┘
                                │
                                ▼
                     網頁嗅探 (sniffer.py) ──▶ 找出 Master Playlist ──▶ yt-dlp 下載

下載流程::

    yt-dlp 原生 HLS/DASH 下載器 (-N 片段並行)  或  aria2c 多連線
        │ 每個檔案下載完成 (progress hook: finished)
        ▼
    偽裝片段修復 (tsrepair.py，剝除假 PNG/JPEG 檔頭)
        ▼
    FFmpeg 無損合併 / 封裝成 .mp4
"""

from __future__ import annotations

import glob
import hashlib
import os
import re
import time
import urllib.parse
from typing import Callable

import yt_dlp
from yt_dlp.postprocessor.ffmpeg import FFmpegFixupPostProcessor
from yt_dlp.postprocessor.common import PostProcessor
from yt_dlp.utils import DownloadCancelled, DownloadError, ExtractorError, UnsupportedError

from . import sniffer, tools, tsrepair
from .config import Settings

PP_STAGE = {
    'Merger': 'merging',
    'FixupM3u8': 'fixing',
    'TsToMp4': 'fixing',
    'ExtractAudio': 'converting',
    'VideoConvertor': 'converting',
    'VideoRemuxer': 'converting',
    'EmbedThumbnail': 'embedding',
    'EmbedSubtitle': 'embedding',
    'Metadata': 'embedding',
    'MoveFiles': 'moving',
}


class JobControl:
    """讓下載中的工作可以被暫停 / 取消。"""

    def __init__(self):
        self.stop_reason: str | None = None   # 'pause' | 'cancel'

    def check(self):
        if self.stop_reason:
            raise DownloadCancelled(self.stop_reason)


class _Logger:
    def __init__(self, emit: Callable[[str, str], None]):
        self.emit = emit

    def debug(self, msg):
        if msg.startswith('[debug] '):
            return
        self.emit('info', msg)

    def info(self, msg):
        self.emit('info', msg)

    def warning(self, msg):
        self.emit('warning', msg)

    def error(self, msg):
        self.emit('error', msg)


class TsToMp4PP(FFmpegFixupPostProcessor):
    """保險：如果最後的 .mp4 其實是 MPEG-TS（例如交給 aria2c 下載的 HLS），封裝成真正的 MP4。"""

    @PostProcessor._restrict_to(images=False)
    def run(self, info):
        path = info.get('filepath')
        if path and info.get('ext') in ('mp4', 'm4a') and os.path.exists(path) and tsrepair.is_mpegts(path):
            args = ['-f', 'mp4']
            if self.get_audio_codec(path) == 'aac':
                args += ['-bsf:a', 'aac_adtstoasc']
            self._fixup('Remuxing MPEG-TS into MP4', path, [*self.stream_copy_opts(), *args])
        return [], info


# ---------------------------------------------------------------- 參數組裝

def temp_dir(settings: Settings, key: str) -> str:
    """每個工作有自己的暫存資料夾，同一部影片同時下載不同格式也不會互相覆蓋 .part 檔。"""
    return os.path.join(settings.download_dir, '.omnidl-temp', re.sub(r'[^\w\-]', '_', key)[:40])


def format_selector(settings: Settings, format_id: str | None = None) -> str:
    if format_id:
        return format_id
    q = settings.quality
    if q == 'audio':
        return 'ba/b'
    if q == 'best':
        return 'bv*+ba/b'
    h = int(q)
    return f'bv*[height<={h}]+ba/b[height<={h}]/bv*+ba/b'


def build_opts(settings: Settings, *, emit_log=None, progress_hook=None, pp_hook=None,
               format_id: str | None = None, extra_headers: dict | None = None,
               impersonate: bool = False, playlist_items: str | None = None, temp_key: str | None = None) -> dict:
    s = settings
    os.makedirs(s.download_dir, exist_ok=True)
    tmpl = s.output_template
    if s.playlist:
        tmpl = '%(playlist_title&{}/|)s' + tmpl
    opts: dict = {
        'paths': {'home': s.download_dir, **({'temp': temp_dir(s, temp_key)} if temp_key else {})},
        'outtmpl': {'default': tmpl},
        'format': format_selector(s, format_id),
        'concurrent_fragment_downloads': s.concurrent_fragments,
        'retries': s.retries,
        'fragment_retries': s.fragment_retries,
        'extractor_retries': 3,
        'file_access_retries': 5,
        'continuedl': True,
        'noprogress': True,
        'quiet': True,
        'no_warnings': False,
        'noplaylist': not s.playlist,
        'restrictfilenames': s.restrict_filenames,
        'windowsfilenames': True,
        'trim_file_name': 200,
        'ignoreerrors': 'only_download' if s.playlist else False,
        'socket_timeout': 30,
        'postprocessors': [],
        'http_headers': {},
    }
    if s.container in ('mp4', 'mkv') and s.quality != 'audio':
        opts['merge_output_format'] = s.container
        # 優先挑選與容器相容的編碼，同畫質下避免不必要的轉檔
        if s.container == 'mp4':
            opts['format_sort'] = ['res', 'fps', 'vcodec:h264', 'acodec:aac']
    ffloc = tools.ffmpeg_location()
    if ffloc:
        opts['ffmpeg_location'] = ffloc
    js = tools.js_runtimes()
    if js:
        opts['js_runtimes'] = js
    if not tools.ejs_available():
        opts['remote_components'] = ['ejs:github']

    # aria2c 外掛
    if s.aria2c != 'off':
        aria = tools.find_tool('aria2c')
        if aria:
            n = str(s.aria2c_connections)
            opts['external_downloader'] = (
                {'default': aria} if s.aria2c == 'all' else {'http': aria, 'https': aria})
            opts['external_downloader_args'] = {'aria2c': [
                '-x', n, '-s', n, '-k', '1M', '--file-allocation=none',
                '--summary-interval=1', '--console-log-level=warn']}
        elif emit_log:
            emit_log('warning', '找不到 aria2c，改用 yt-dlp 內建下載器')

    if s.rate_limit:
        try:
            opts['ratelimit'] = yt_dlp.parse_options(['-r', s.rate_limit]).ydl_opts['ratelimit']
        except Exception:  # noqa: BLE001
            pass
    if s.proxy:
        opts['proxy'] = s.proxy
    if s.cookies_from_browser:
        opts['cookiesfrombrowser'] = (s.cookies_from_browser, None, None, None)
    elif s.cookies_file and os.path.isfile(s.cookies_file):
        opts['cookiefile'] = s.cookies_file
    if s.user_agent:
        opts['http_headers']['User-Agent'] = s.user_agent
    if extra_headers:
        opts['http_headers'].update(extra_headers)
    if impersonate:
        from yt_dlp.networking.impersonate import ImpersonateTarget
        opts['impersonate'] = ImpersonateTarget()  # 任一可用的瀏覽器指紋
    if playlist_items:
        opts['playlist_items'] = playlist_items
    if s.download_archive:
        opts['download_archive'] = os.path.join(s.download_dir, '.omnidl-archive.txt')

    pps = opts['postprocessors']
    if s.quality == 'audio':
        pps.append({'key': 'FFmpegExtractAudio',
                    'preferredcodec': s.audio_format, 'preferredquality': '0'})
    if s.write_subs and s.quality != 'audio':
        opts['writesubtitles'] = True
        opts['subtitleslangs'] = [x.strip() for x in s.sub_langs.split(',') if x.strip()]
        if s.embed_subs:
            pps.append({'key': 'FFmpegEmbedSubtitle'})
    if s.embed_metadata:
        pps.append({'key': 'FFmpegMetadata', 'add_metadata': True, 'add_chapters': True})
    if s.embed_thumbnail and (s.quality == 'audio' or s.container != 'auto'):
        opts['writethumbnail'] = True
        pps.append({'key': 'EmbedThumbnail', 'already_have_thumbnail': False})

    if emit_log:
        opts['logger'] = _Logger(emit_log)
    if progress_hook:
        opts['progress_hooks'] = [progress_hook]
    if pp_hook:
        opts['postprocessor_hooks'] = [pp_hook]
    return opts


def new_ydl(opts: dict) -> yt_dlp.YoutubeDL:
    ydl = yt_dlp.YoutubeDL(opts)
    if tools.ffmpeg_location() or ydl.params.get('ffmpeg_location'):
        ydl.add_post_processor(TsToMp4PP(ydl), when='post_process')
    return ydl


def _extract_strict(ydl, url, process=False):
    """解析階段一律讓錯誤丟出來（播放清單的 ignoreerrors 只套用在下載階段）。"""
    saved = ydl.params.get('ignoreerrors')
    ydl.params['ignoreerrors'] = False
    try:
        ie = ydl.extract_info(url, download=False, process=process)
    finally:
        ydl.params['ignoreerrors'] = saved
    if not ie:
        raise ExtractorError('無法解析此網址', expected=True)
    return ie


# ---------------------------------------------------------------- 錯誤分類

def _root_exc(err: BaseException) -> BaseException:
    exc = getattr(err, 'exc_info', None)
    if exc and exc[1] is not None:
        return exc[1]
    return err


def is_blocked_error(err: BaseException) -> bool:
    """403 / Cloudflare / 需要偽裝的錯誤。"""
    text = str(err).lower()
    if any(k in text for k in ('403', 'cloudflare', 'impersonat', 'captcha', 'forbidden')):
        return True
    root = _root_exc(err)
    cause = getattr(root, 'cause', None)
    return getattr(cause, 'status', None) in (403, 429, 503)


def is_unsupported_error(err: BaseException) -> bool:
    root = _root_exc(err)
    if isinstance(root, UnsupportedError):
        return True
    text = str(err).lower()
    return any(k in text for k in ('unsupported url', 'no video formats', 'no media found',
                                   'unable to extract', 'no video could be found'))


# ---------------------------------------------------------------- 主要流程

class Engine:
    def __init__(self, settings: Settings, *, log=None, on_progress=None, on_stage=None,
                 control: JobControl | None = None, temp_key: str | None = None):
        self.s = settings
        self.temp_key = temp_key
        self.log = log or (lambda level, msg: None)
        self.on_progress = on_progress or (lambda data: None)
        self.on_stage = on_stage or (lambda stage, detail=None: None)
        self.control = control or JobControl()
        self.files: list[str] = []
        self.tmpfiles: set[str] = set()
        self.sniff_url: str | None = None
        self._last_emit = 0.0

    # ---- hooks
    def _progress_hook(self, d):
        self.control.check()
        st = d.get('status')
        if d.get('tmpfilename'):
            self.tmpfiles.add(d['tmpfilename'])
        if st == 'downloading':
            now = time.monotonic()
            if now - self._last_emit < 0.25:
                return
            self._last_emit = now
            total = d.get('total_bytes') or d.get('total_bytes_estimate')
            done = d.get('downloaded_bytes') or 0
            frag_i, frag_n = d.get('fragment_index'), d.get('fragment_count')
            pct = None
            if total:
                pct = min(100.0, done * 100.0 / total)
            elif frag_i and frag_n:
                pct = min(100.0, frag_i * 100.0 / frag_n)
            info = d.get('info_dict') or {}
            self.on_progress({
                'percent': pct, 'downloaded': done, 'total': total,
                'speed': d.get('speed'), 'eta': d.get('eta'),
                'fragment_index': frag_i, 'fragment_count': frag_n,
                'filename': os.path.basename(d.get('filename') or ''),
                'format': info.get('format_id'),
                'playlist_index': info.get('playlist_index'),
                'n_entries': info.get('n_entries'),
            })
        elif st == 'finished':
            fn = d.get('filename')
            self.on_progress({'percent': 100.0, 'speed': None, 'eta': 0,
                              'filename': os.path.basename(fn or '')})
            if fn and self.s.repair_disguised and os.path.exists(fn):
                self._maybe_repair(fn)

    def _maybe_repair(self, path: str):
        try:
            res = tsrepair.inspect(path)
            if res.needs_repair:
                self.on_stage('repairing', os.path.basename(path))
                self.log('info', f'[repair] 偵測到偽裝片段：{res.junk_regions} 段共 {res.junk_bytes} bytes 垃圾資料，修復中…')
                res = tsrepair.repair_file(path)
                self.log('info', f'[repair] 完成，保留 {res.packets} 個 TS 封包')
                self.on_stage('downloading')
        except Exception as e:  # noqa: BLE001
            self.log('warning', f'[repair] 修復失敗：{e}')

    def _pp_hook(self, d):
        name = d.get('postprocessor') or ''
        if d.get('status') == 'started':
            self.control.check()
            self.on_stage(PP_STAGE.get(name, 'processing'), name)

    def _new(self, **kw) -> yt_dlp.YoutubeDL:
        ydl = new_ydl(build_opts(self.s, emit_log=self.log, progress_hook=self._progress_hook,
                                 pp_hook=self._pp_hook, temp_key=self.temp_key, **kw))
        ydl.add_post_hook(self._post_hook)
        return ydl

    def _post_hook(self, filename):
        if filename and filename not in self.files:
            self.files.append(filename)

    # ---- 解析
    def resolve(self, url: str, *, format_id=None, sniff_url=None, playlist_items=None, process=False, title=None):
        """回傳 (ydl, ie_result, via)。via: 'native' | 'impersonate' | 'sniff'。"""
        if sniff_url:
            return self._from_sniff(url, sniff_url, format_id=format_id, title=title)
        if self.s.sniff_mode == 'always':
            try:
                return self._sniff(url, format_id=format_id)
            except Exception as e:  # noqa: BLE001
                self.log('warning', f'[sniff] 嗅探失敗，改用 yt-dlp：{e}')

        attempts = [False]
        if self.s.auto_impersonate and tools.impersonate_available():
            attempts.append(True)
        last_err: Exception | None = None
        for imp in attempts:
            if imp:
                self.log('info', '[omnidl] 改用瀏覽器 TLS 指紋偽裝重試…')
            ydl = self._new(format_id=format_id, impersonate=imp, playlist_items=playlist_items)
            try:
                ie = _extract_strict(ydl, url, process=process)
                return ydl, ie, 'impersonate' if imp else 'native'
            except DownloadCancelled:
                raise
            except (DownloadError, ExtractorError) as e:
                last_err = e
                ydl.close()
                if not is_blocked_error(e):
                    break
        if self.s.sniff_mode != 'off' and last_err is not None and (
                is_unsupported_error(last_err) or is_blocked_error(last_err)):
            self.log('info', '[sniff] yt-dlp 無法直接解析，啟動網頁嗅探…')
            try:
                return self._sniff(url, format_id=format_id)
            except Exception as e:  # noqa: BLE001
                self.log('error', f'[sniff] 嗅探也失敗：{e}')
        raise last_err  # type: ignore[misc]

    def sniff_only(self, url: str) -> sniffer.SniffResult:
        imp = self.s.auto_impersonate and tools.impersonate_available()
        ydl = self._new(impersonate=imp)
        try:
            return sniffer.sniff(ydl, url, headers=self._origin_headers(url))
        finally:
            ydl.close()

    @staticmethod
    def _origin_headers(page_url: str) -> dict:
        p = urllib.parse.urlparse(page_url)
        return {'Referer': page_url, 'Origin': f'{p.scheme}://{p.netloc}'}

    def _sniff(self, url: str, format_id=None):
        self.on_stage('sniffing')
        res = self.sniff_only(url)
        if not res.best:
            raise ExtractorError('網頁中找不到任何影片串流 (m3u8/mpd/mp4)', expected=True)
        best = res.best
        self.log('info', f'[sniff] 選用 {best.url} ({best.note or best.ext})')
        return self._from_sniff(url, best.url, format_id=format_id, title=res.title, thumbnail=res.thumbnail)

    def _from_sniff(self, page_url: str, media_url: str, *, format_id=None, title=None, thumbnail=None):
        self.sniff_url = media_url
        imp = self.s.auto_impersonate and tools.impersonate_available()
        headers = self._origin_headers(page_url)
        ydl = self._new(format_id=format_id, extra_headers=headers, impersonate=imp)
        ie = _extract_strict(ydl, media_url)
        last = urllib.parse.urlparse(page_url).path.strip('/').split('/')[-1]
        last = re.sub(r'\.(html?|php|aspx?)$', '', last, flags=re.I)
        slug = re.sub(r'[^\w\-]+', '-', last).strip('-')
        ie['id'] = slug[:60] or hashlib.md5(page_url.encode()).hexdigest()[:10]
        if title:
            ie['title'] = title
        elif not ie.get('title') or ie.get('title') in ('playlist', 'index', 'master'):
            ie['title'] = ie['id']
        if thumbnail and not ie.get('thumbnail'):
            ie['thumbnail'] = thumbnail
        ie['webpage_url'] = page_url
        ie['original_url'] = page_url
        ie['extractor'] = ie['extractor_key'] = 'OmniDLSniffer'
        return ydl, ie, 'sniff'

    # ---- 分析（給 GUI 顯示格式清單）
    def analyze(self, url: str) -> dict:
        self.on_stage('analyzing')
        ydl, ie, via = self.resolve(url, process=False)
        try:
            if ie.get('_type') in ('playlist', 'multi_video'):
                entries = []
                for i, e in enumerate(ie.get('entries') or []):
                    if i >= 500:
                        break
                    if not e:
                        continue
                    entries.append({'index': i + 1, 'title': e.get('title'), 'url': e.get('url') or e.get('webpage_url'),
                                    'duration': e.get('duration')})
                return {'type': 'playlist', 'via': via, 'title': ie.get('title'), 'id': ie.get('id'),
                        'uploader': ie.get('uploader'), 'webpage_url': ie.get('webpage_url') or url,
                        'extractor': ie.get('extractor_key'), 'entries': entries,
                        'count': ie.get('playlist_count') or len(entries)}
            info = ydl.process_ie_result(ie, download=False)
            out = summarize(info, via, url)
            out['sniff_url'] = self.sniff_url if via == 'sniff' else None
            return out
        finally:
            ydl.close()

    # ---- 下載
    def download(self, url: str, *, format_id=None, sniff_url=None, playlist_items=None, title=None) -> dict:
        self.on_stage('analyzing')
        ydl, ie, via = self.resolve(url, format_id=format_id, sniff_url=sniff_url, playlist_items=playlist_items,
                                    title=title)
        try:
            title = ie.get('title')
            self.on_stage('downloading', title)
            try:
                info = ydl.process_ie_result(ie, download=True)
            except DownloadCancelled:
                raise
            except (DownloadError, ExtractorError) as e:
                # 原生解析成功但根本沒有可下載的格式 → 嗅探
                if via == 'native' and self.s.sniff_mode != 'off' and is_unsupported_error(e) and not self.files:
                    self.log('info', '[sniff] 解析結果沒有影片，改用網頁嗅探…')
                    ydl.close()
                    ydl, ie, via = self._sniff(url, format_id=format_id)
                    info = ydl.process_ie_result(ie, download=True)
                else:
                    raise
            if ydl._download_retcode and not self.files:
                raise DownloadError('下載失敗，請查看紀錄')
            return {'title': (info or {}).get('title') or title, 'via': via, 'files': self.files}
        finally:
            ydl.close()

    def remove_temp_dir(self, force=False):
        """完成或取消後清掉這個工作的暫存資料夾。"""
        if not self.temp_key:
            return
        import shutil
        d = temp_dir(self.s, self.temp_key)
        if force:
            shutil.rmtree(d, ignore_errors=True)
        else:
            try:
                os.rmdir(d)
            except OSError:
                pass
        try:
            os.rmdir(os.path.dirname(d))  # 只有空的時候才會成功
        except OSError:
            pass

    def cleanup_partials(self):
        """取消時刪除暫存檔。"""
        for tmp in list(self.tmpfiles):
            for p in glob.glob(glob.escape(tmp) + '*') + glob.glob(glob.escape(tmp.removesuffix('.part')) + '.part*'):
                try:
                    os.remove(p)
                except OSError:
                    pass
            ytdl = tmp.removesuffix('.part') + '.ytdl'
            if os.path.exists(ytdl):
                try:
                    os.remove(ytdl)
                except OSError:
                    pass
        self.remove_temp_dir(force=True)


ERROR_HINTS = (
    (('sign in to confirm', 'not a bot'), '網站要求驗證身分：請到「設定 → 從瀏覽器讀取 Cookies」選擇你已登入該網站的瀏覽器。'),
    (('logged-in', 'login required', 'cookies', 'members-only', 'private video', 'age'),
     '此影片需要登入：請到「設定 → 從瀏覽器讀取 Cookies」選擇已登入的瀏覽器。'),
    (('drm',), '此影片受 DRM 保護，基於法律與技術原因無法下載。'),
    (('429', 'too many requests'), '請求太頻繁被網站暫時封鎖，請稍後再試，或設定代理伺服器 / Cookies。'),
    (('geo', 'not available in your country'), '此影片有地區限制，請設定對應地區的代理伺服器。'),
    (('ffmpeg', 'ffprobe') , '缺少 FFmpeg：請到「環境檢查」安裝。'),
    (('javascript runtime', 'js runtime'), '缺少 JavaScript 執行環境：請到「環境檢查」安裝 Deno。'),
    (('unsupported url', 'no video formats', '找不到任何影片串流'),
     '找不到影片：網站可能需要登入或改版。可試試「更新 yt-dlp」，或在設定中把網頁嗅探改為「一律優先嗅探」。'),
    (('403', 'forbidden'), '伺服器拒絕存取 (403)：可嘗試設定 Cookies 或代理伺服器。'),
)


def friendly_error(msg: str) -> str:
    msg = (msg or '').replace('ERROR: ', '').strip()
    low = msg.lower()
    for keys, hint in ERROR_HINTS:
        if any(k in low for k in keys):
            return f'{hint}\n（原始訊息：{msg[:400]}）'
    return msg[:600]


def _fmt_summary(f: dict) -> dict:
    v, a = f.get('vcodec'), f.get('acodec')
    # yt-dlp 會把「確定沒有」的串流標成 'none'；None 代表未知（例如 HLS 總菜單沒寫 codecs），視為有
    has_v = v != 'none' and bool(v or f.get('height') or f.get('width') or a in (None, 'none'))
    has_a = a != 'none'
    kind = 'av' if has_v and has_a else 'video' if has_v else 'audio'
    return {
        'kind': kind,
        'format_id': f.get('format_id'), 'ext': f.get('ext'),
        'resolution': f.get('resolution') or ('audio only' if v == 'none' else None),
        'height': f.get('height'), 'fps': f.get('fps'),
        'vcodec': None if v in (None, 'none') else v, 'acodec': None if a in (None, 'none') else a,
        'tbr': f.get('tbr'), 'filesize': f.get('filesize') or f.get('filesize_approx'),
        'protocol': f.get('protocol'), 'note': f.get('format_note'),
    }


def summarize(info: dict, via: str, url: str) -> dict:
    fmts = [_fmt_summary(f) for f in info.get('formats') or [] if f.get('format_id')
            and not (f.get('vcodec') == 'none' and f.get('acodec') == 'none')]
    fmts.reverse()  # yt-dlp 排序是由差到好
    return {
        'type': 'video', 'via': via, 'id': info.get('id'), 'title': info.get('title'),
        'uploader': info.get('uploader'), 'duration': info.get('duration'),
        'thumbnail': info.get('thumbnail'), 'extractor': info.get('extractor_key'),
        'webpage_url': info.get('webpage_url') or url, 'formats': fmts,
        'selected': info.get('format_id'),
    }
