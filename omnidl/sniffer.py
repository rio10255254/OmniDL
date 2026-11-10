"""網頁嗅探器 (Page sniffer).

當 yt-dlp 沒有對應網站的解析器、通用解析器也找不到影片時，
抓取網頁原始碼並找出藏在裡面的串流網址：

* 直接寫在 HTML / JSON / JS 裡的 .m3u8 / .mpd / .mp4 網址
* 經過 `\\/`、`\\u002F`、HTML entity 跳脫的網址
* 以 Dean Edwards packer (`eval(function(p,a,c,k,e,d)...`) 混淆的 JS
* Base64 包起來的網址

找到候選後會實際讀取 m3u8 內容，判斷哪一個是「總菜單」
(Master Playlist，含 #EXT-X-STREAM-INF)，讓 yt-dlp 自動挑最高畫質。
"""

from __future__ import annotations

import base64
import html
import re
import urllib.parse
from dataclasses import dataclass, field

from yt_dlp.utils import PACKED_CODES_RE, decode_packed_codes

MEDIA_EXT = ('m3u8', 'mpd', 'mp4', 'webm', 'mkv', 'flv', 'mov', 'm4v')

_URL_RE = re.compile(
    r'''(?P<url>(?:https?:)?//[^\s"'<>()\\`]+?\.(?P<ext>%s)(?:\?[^\s"'<>()\\`]*)?)(?=$|[\s"'<>()\\`,;])'''
    % '|'.join(MEDIA_EXT), re.I)
_REL_RE = re.compile(
    r'''["'](?P<url>/[^\s"'<>()\\`]+?\.(?P<ext>m3u8|mpd|mp4)(?:\?[^\s"'<>()\\`]*)?)["']''', re.I)
_B64_RE = re.compile(r'''["']((?:aHR0c|Ly9)[A-Za-z0-9+/=]{16,})["']''')
_TITLE_RES = (
    re.compile(r'''<meta[^>]+property=["']og:title["'][^>]+content=["']([^"']+)''', re.I),
    re.compile(r'''<meta[^>]+content=["']([^"']+)["'][^>]+property=["']og:title''', re.I),
    re.compile(r'<title[^>]*>([^<]+)</title>', re.I),
)
_THUMB_RE = re.compile(r'''<meta[^>]+property=["']og:image["'][^>]+content=["']([^"']+)''', re.I)

# 檔名裡常見的畫質線索
_QUALITY_RE = re.compile(r'(?<!\d)(2160|1440|1080|720|480|360|240)p?(?!\d)')


@dataclass
class Candidate:
    url: str
    ext: str
    score: int = 0
    is_master: bool | None = None
    variants: int = 0
    note: str = ''

    def to_dict(self):
        return {'url': self.url, 'ext': self.ext, 'score': self.score,
                'is_master': self.is_master, 'variants': self.variants, 'note': self.note}


@dataclass
class SniffResult:
    page_url: str
    title: str | None = None
    thumbnail: str | None = None
    candidates: list[Candidate] = field(default_factory=list)

    @property
    def best(self) -> Candidate | None:
        return self.candidates[0] if self.candidates else None

    def to_dict(self):
        return {'page_url': self.page_url, 'title': self.title, 'thumbnail': self.thumbnail,
                'candidates': [c.to_dict() for c in self.candidates]}


def _unescape(text: str) -> str:
    text = text.replace('\\/', '/')
    text = re.sub(r'\\u([0-9a-fA-F]{4})', lambda m: chr(int(m.group(1), 16)), text)
    text = re.sub(r'\\x([0-9a-fA-F]{2})', lambda m: chr(int(m.group(1), 16)), text)
    return html.unescape(text)


def _unpack_all(text: str) -> list[str]:
    """解開所有 p,a,c,k,e,d 混淆區塊（可能有多層）。"""
    out = []
    pending = [text]
    for _ in range(5):  # 最多 5 層
        nxt = []
        for chunk in pending:
            for m in re.finditer(r"eval\(function\(p,a,c,k,e,[rd]\).*?\.split\('\|'\).*?\)\)", chunk, re.S):
                block = m.group(0)
                if not re.search(PACKED_CODES_RE, block):
                    continue
                try:
                    decoded = decode_packed_codes(block)
                except Exception:
                    continue
                out.append(decoded)
                nxt.append(decoded)
        if not nxt:
            break
        pending = nxt
    return out


def _decode_b64(text: str) -> list[str]:
    out = []
    for m in _B64_RE.finditer(text):
        s = m.group(1)
        try:
            dec = base64.b64decode(s + '=' * (-len(s) % 4)).decode('utf-8', 'ignore')
        except Exception:
            continue
        if '.m3u8' in dec or '.mp4' in dec or '.mpd' in dec:
            out.append(dec)
    return out


def extract_title(page: str) -> str | None:
    for rx in _TITLE_RES:
        m = rx.search(page)
        if m:
            t = html.unescape(m.group(1)).strip()
            if t:
                return t
    return None


def score_url(url: str, ext: str) -> int:
    u = url.lower()
    score = {'m3u8': 50, 'mpd': 40, 'mp4': 30, 'webm': 25, 'm4v': 25, 'mkv': 20, 'mov': 20, 'flv': 10}.get(ext, 0)
    if ext == 'm3u8':
        if re.search(r'(master|playlist|index)\.m3u8', u) or '/master' in u:
            score += 15
    if any(k in u for k in ('preview', 'trailer', 'thumb', 'sprite', 'ad.', '/ads/', 'promo')):
        score -= 40
    m = _QUALITY_RE.search(u)
    if m:
        score += int(m.group(1)) // 120
    return score


def find_media_urls(page: str, base_url: str) -> list[Candidate]:
    texts = [page, _unescape(page)]
    for unpacked in _unpack_all(page):
        texts += [unpacked, _unescape(unpacked)]
    texts += _decode_b64(page)

    seen: dict[str, Candidate] = {}
    scheme = urllib.parse.urlparse(base_url).scheme or 'https'
    for text in texts:
        for rx in (_URL_RE, _REL_RE):
            for m in rx.finditer(text):
                url, ext = m.group('url'), m.group('ext').lower()
                if url.startswith('//'):
                    url = f'{scheme}:{url}'
                elif url.startswith('/'):
                    url = urllib.parse.urljoin(base_url, url)
                if url not in seen:
                    seen[url] = Candidate(url=url, ext=ext, score=score_url(url, ext))
    return sorted(seen.values(), key=lambda c: -c.score)


def classify_m3u8(content: str) -> tuple[bool | None, int]:
    if '#EXTM3U' not in content:
        return None, 0
    variants = content.count('#EXT-X-STREAM-INF')
    return variants > 0, variants


def sniff(ydl, page_url: str, headers: dict | None = None, probe_limit: int = 6) -> SniffResult:
    """抓取網頁並找出最可能的影片串流。

    `ydl` 是一個 yt_dlp.YoutubeDL 物件，使用它的網路層可以自動套用
    cookies、代理、以及（若有安裝 curl_cffi）瀏覽器偽裝。
    """
    from yt_dlp.networking import Request

    req_headers = {'Referer': page_url}
    req_headers.update(headers or {})
    page = _fetch_text(ydl, Request(page_url, headers=req_headers))
    result = SniffResult(page_url=page_url, title=extract_title(page))
    m = _THUMB_RE.search(page)
    if m:
        result.thumbnail = html.unescape(m.group(1))

    cands = find_media_urls(page, page_url)
    # 實際讀取 m3u8，找出真正的總菜單
    probed = 0
    for c in cands:
        if c.ext != 'm3u8' or probed >= probe_limit:
            continue
        probed += 1
        try:
            body = _fetch_text(ydl, Request(c.url, headers=req_headers), limit=256 * 1024)
        except Exception as e:  # noqa: BLE001
            c.score -= 30
            c.note = f'無法讀取: {e.__class__.__name__}'
            continue
        c.is_master, c.variants = classify_m3u8(body)
        if c.is_master is None:
            c.score -= 30
            c.note = '不是有效的 m3u8'
        elif c.is_master:
            c.score += 40 + c.variants
            c.note = f'總菜單 (Master Playlist)，{c.variants} 種畫質'
        else:
            c.note = '單一畫質播放清單'
    result.candidates = sorted(cands, key=lambda c: -c.score)
    return result


def _fetch_text(ydl, req, limit: int | None = None) -> str:
    with ydl.urlopen(req) as resp:
        data = resp.read(limit) if limit else resp.read()
    return data.decode('utf-8', 'replace')
