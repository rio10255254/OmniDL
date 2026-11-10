"""設定與路徑管理。"""

from __future__ import annotations

import json
import os
import sys
import threading
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

APP_NAME = 'OmniDL'


def app_dir() -> Path:
    """使用者資料夾：設定檔、歷史紀錄、工具程式都放這裡。"""
    override = os.environ.get('OMNIDL_HOME')
    if override:
        p = Path(override)
    elif sys.platform == 'win32':
        p = Path(os.environ.get('APPDATA') or Path.home() / 'AppData' / 'Roaming') / APP_NAME
    elif sys.platform == 'darwin':
        p = Path.home() / 'Library' / 'Application Support' / APP_NAME
    else:
        p = Path(os.environ.get('XDG_CONFIG_HOME') or Path.home() / '.config') / APP_NAME.lower()
    p.mkdir(parents=True, exist_ok=True)
    return p


def bin_dir() -> Path:
    p = app_dir() / 'bin'
    p.mkdir(exist_ok=True)
    return p


def lib_dir() -> Path:
    """yt-dlp 自我更新後的新版本放在這裡（打包版用）。"""
    return app_dir() / 'lib'


def default_download_dir() -> str:
    return str(Path.home() / 'Downloads' / APP_NAME)


QUALITY_CHOICES = ['best', '2160', '1440', '1080', '720', '480', '360', 'audio']
AUDIO_FORMATS = ['mp3', 'm4a', 'opus', 'flac', 'wav', 'best']
CONTAINERS = ['mp4', 'mkv', 'auto']
ARIA2_MODES = ['off', 'http', 'all']
SNIFF_MODES = ['auto', 'always', 'off']
BROWSERS = ['', 'chrome', 'edge', 'firefox', 'brave', 'opera', 'vivaldi', 'chromium', 'safari']


@dataclass
class Settings:
    download_dir: str = field(default_factory=default_download_dir)
    output_template: str = '%(title).150B [%(id)s].%(ext)s'
    quality: str = 'best'
    audio_format: str = 'mp3'
    container: str = 'mp4'
    # 同時下載的 HLS/DASH 片段數 (yt-dlp -N)
    concurrent_fragments: int = 8
    # aria2c：off 不用；http 只用在一般直連檔案；all 連 HLS/DASH 片段也交給它
    aria2c: str = 'off'
    aria2c_connections: int = 16
    max_concurrent_jobs: int = 2
    retries: int = 10
    fragment_retries: int = 20
    rate_limit: str = ''          # 例如 5M、500K
    proxy: str = ''
    cookies_from_browser: str = ''
    cookies_file: str = ''
    user_agent: str = ''
    # 嗅探：auto = yt-dlp 失敗才嗅探；always = 一律先嗅探；off = 不嗅探
    sniff_mode: str = 'auto'
    # 偽裝片段修復（剝除假的 PNG/JPEG 檔頭）
    repair_disguised: bool = True
    # 遇到 403/Cloudflare 自動改用瀏覽器 TLS 指紋偽裝 (需要 curl_cffi)
    auto_impersonate: bool = True
    write_subs: bool = False
    sub_langs: str = 'zh-Hant,zh-TW,zh,en'
    embed_subs: bool = True
    embed_thumbnail: bool = True
    embed_metadata: bool = True
    playlist: bool = True
    restrict_filenames: bool = False
    download_archive: bool = False
    port: int = 8765

    @classmethod
    def from_dict(cls, data: dict) -> 'Settings':
        names = {f.name: f for f in fields(cls)}
        clean = {}
        defaults = cls()
        for k, v in (data or {}).items():
            if k not in names:
                continue
            default = getattr(defaults, k)
            try:
                if isinstance(default, bool):
                    v = v if isinstance(v, bool) else str(v).lower() in ('1', 'true', 'yes', 'on')
                elif isinstance(default, int):
                    v = int(v)
                else:
                    v = '' if v is None else str(v)
            except (TypeError, ValueError):
                continue
            clean[k] = v
        s = cls(**clean)
        s.validate()
        return s

    def validate(self):
        if self.quality not in QUALITY_CHOICES:
            self.quality = 'best'
        if self.audio_format not in AUDIO_FORMATS:
            self.audio_format = 'mp3'
        if self.container not in CONTAINERS:
            self.container = 'mp4'
        if self.aria2c not in ARIA2_MODES:
            self.aria2c = 'off'
        if self.sniff_mode not in SNIFF_MODES:
            self.sniff_mode = 'auto'
        if self.cookies_from_browser not in BROWSERS:
            self.cookies_from_browser = ''
        self.concurrent_fragments = max(1, min(self.concurrent_fragments, 64))
        self.aria2c_connections = max(1, min(self.aria2c_connections, 16))
        self.max_concurrent_jobs = max(1, min(self.max_concurrent_jobs, 10))
        self.retries = max(0, min(self.retries, 100))
        self.fragment_retries = max(0, min(self.fragment_retries, 100))
        if not self.output_template.strip():
            self.output_template = Settings.output_template
        if not self.download_dir.strip():
            self.download_dir = default_download_dir()

    def to_dict(self) -> dict:
        return asdict(self)


class SettingsStore:
    def __init__(self, path: Path | None = None):
        self.path = path or app_dir() / 'settings.json'
        self._lock = threading.Lock()
        self._settings = self._load()

    def _load(self) -> Settings:
        try:
            return Settings.from_dict(json.loads(self.path.read_text('utf-8')))
        except (OSError, ValueError):
            return Settings()

    def get(self) -> Settings:
        with self._lock:
            return Settings.from_dict(self._settings.to_dict())

    def update(self, data: dict) -> Settings:
        with self._lock:
            merged = self._settings.to_dict()
            merged.update(data or {})
            self._settings = Settings.from_dict(merged)
            tmp = self.path.with_suffix('.tmp')
            tmp.write_text(json.dumps(self._settings.to_dict(), ensure_ascii=False, indent=2), 'utf-8')
            os.replace(tmp, self.path)
            return Settings.from_dict(self._settings.to_dict())
