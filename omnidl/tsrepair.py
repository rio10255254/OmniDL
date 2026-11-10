"""偽裝片段修復 (Disguised HLS segment repair).

有些網站會把 HLS 的 .ts 片段偽裝成圖片（副檔名改成 .jpeg/.png，
甚至在每個片段前面塞一段假的 PNG/JPEG 檔頭），讓下載器與 FFmpeg
誤判格式。yt-dlp 會照常把所有片段接起來，但結果檔案的開頭與每個
片段交界處都夾雜著垃圾位元組，FFmpeg 探測時會把它當成圖片而合併失敗。

MPEG-TS 由固定 188 bytes 的封包組成，每個封包都以 0x47 (同步位元組)
開頭。本模組沿著檔案掃描：只保留「連續對齊」的 TS 封包，遇到不對齊的
區段就視為垃圾跳過，直到重新找到連續 3 個同步位元組為止。
如此即可在不重新編碼的情況下把偽裝檔頭全部剝除。
"""

from __future__ import annotations

import mmap
import os
from dataclasses import dataclass

TS_PACKET = 188
SYNC = 0x47
SYNC_BYTE = b'\x47'
# 判定「重新同步」需要連續幾個對齊的同步位元組
RESYNC_RUN = 3
# 每次以 C 層級切片檢查的封包數，避免一次複製整個大檔
WINDOW_PACKETS = 1 << 16

MP4_BRANDS = (b'ftyp', b'moov', b'mdat', b'free', b'skip', b'wide', b'styp', b'sidx', b'moof')


@dataclass
class RepairResult:
    kind: str            # 'mp4' | 'ts' | 'unknown'
    junk_bytes: int = 0  # 被剝除的垃圾位元組數
    junk_regions: int = 0
    packets: int = 0
    repaired: bool = False

    @property
    def needs_repair(self) -> bool:
        return self.kind == 'ts' and self.junk_bytes > 0


def sniff_container(head: bytes) -> str:
    """依檔頭判斷容器格式。"""
    if len(head) >= 8 and head[4:8] in MP4_BRANDS:
        return 'mp4'
    if head[:4] == b'\x1a\x45\xdf\xa3':
        return 'mkv'
    return 'unknown'


def _is_sync_run(buf, pos: int, size: int, run: int = RESYNC_RUN) -> bool:
    """pos 起算是否有 `run` 個對齊的同步位元組（檔尾不足的部分視為通過）。"""
    for k in range(run):
        p = pos + k * TS_PACKET
        if p >= size:
            # 檔尾：至少要有一個完整封包才算
            return k > 0 and pos + TS_PACKET <= size
        if buf[p] != SYNC:
            return False
    return True


def _find_resync(buf, start: int, size: int) -> int:
    """從 start 開始找下一個可重新同步的位置，找不到回傳 -1。"""
    pos = start
    while True:
        pos = buf.find(SYNC_BYTE, pos, size)
        if pos < 0:
            return -1
        if _is_sync_run(buf, pos, size):
            return pos
        pos += 1


def _scan(buf, size: int, out=None) -> RepairResult:
    """掃描（並選擇性寫出）乾淨的 TS 封包。"""
    res = RepairResult(kind='ts')
    pos = _find_resync(buf, 0, size)
    if pos < 0:
        return RepairResult(kind='unknown')
    if pos > 0:
        res.junk_bytes += pos
        res.junk_regions += 1

    while pos < size:
        # 計算從 pos 開始有多少個連續對齊封包（以 C 切片加速）
        limit = min(size, pos + TS_PACKET * WINDOW_PACKETS)
        syncs = buf[pos:limit:TS_PACKET]
        n = len(syncs) - len(syncs.lstrip(SYNC_BYTE))
        # 最後一個封包必須完整
        while n and pos + n * TS_PACKET > size:
            n -= 1
        if n:
            if out is not None:
                out.write(buf[pos:pos + n * TS_PACKET])
            res.packets += n
            pos += n * TS_PACKET
            if limit < size and n == len(syncs):
                continue  # 整個視窗都乾淨，繼續下一個視窗
        if pos >= size:
            break
        # 不對齊：跳過垃圾直到重新同步
        nxt = _find_resync(buf, pos + 1, size)
        if nxt < 0:
            res.junk_bytes += size - pos
            res.junk_regions += 1
            break
        res.junk_bytes += nxt - pos
        res.junk_regions += 1
        pos = nxt
    return res


def inspect(path: str) -> RepairResult:
    """只檢查不修改。"""
    size = os.path.getsize(path)
    if size == 0:
        return RepairResult(kind='unknown')
    with open(path, 'rb') as f:
        head = f.read(16)
        kind = sniff_container(head)
        if kind != 'unknown':
            return RepairResult(kind=kind)
        with mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as mm:
            res = _scan(mm, size)
    # 太多垃圾代表它根本不是 TS（例如真的圖片），不要亂動
    if res.kind == 'ts' and res.packets * TS_PACKET < res.junk_bytes:
        return RepairResult(kind='unknown', junk_bytes=res.junk_bytes)
    return res


def repair_file(path: str, dest: str | None = None) -> RepairResult:
    """剝除偽裝檔頭與片段間的垃圾。dest 為 None 時原地取代。

    檔案本身乾淨或不是 TS 時不做任何事。
    """
    res = inspect(path)
    if not res.needs_repair:
        return res
    tmp = (dest or path) + '.repair.tmp'
    size = os.path.getsize(path)
    with open(path, 'rb') as f, mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as mm, \
            open(tmp, 'wb') as out:
        res = _scan(mm, size, out)
    os.replace(tmp, dest or path)
    res.repaired = True
    return res


def is_mpegts(path: str) -> bool:
    try:
        with open(path, 'rb') as f:
            head = f.read(TS_PACKET * RESYNC_RUN)
    except OSError:
        return False
    return len(head) >= TS_PACKET and _is_sync_run(head, 0, len(head))
