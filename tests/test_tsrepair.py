import os

from omnidl import tsrepair

from .conftest import FAKE_PNG


def ts_packets(n, seed=0):
    out = bytearray()
    for i in range(n):
        pkt = bytearray([0x47]) + bytes(((i + seed + k) * 7) % 251 for k in range(187))
        # 避免封包內容裡剛好出現對齊的 0x47
        out += pkt
    return bytes(out)


def test_clean_ts_untouched(tmp_path):
    p = tmp_path / 'a.ts'
    data = ts_packets(500)
    p.write_bytes(data)
    res = tsrepair.repair_file(str(p))
    assert res.kind == 'ts' and res.junk_bytes == 0 and not res.repaired
    assert p.read_bytes() == data


def test_strip_fake_png_headers(tmp_path):
    p = tmp_path / 'a.mp4'
    segs = [ts_packets(100, seed=s) for s in range(5)]
    p.write_bytes(b''.join(FAKE_PNG + s for s in segs))
    res = tsrepair.repair_file(str(p))
    assert res.repaired and res.junk_regions == 5 and res.junk_bytes == 5 * len(FAKE_PNG)
    assert p.read_bytes() == b''.join(segs)
    assert tsrepair.is_mpegts(str(p))


def test_junk_containing_sync_bytes(tmp_path):
    # 垃圾裡面夾帶 0x47 也不能誤判
    junk = b'\x89PNG' + b'\x47' * 3 + b'GGG' + os.urandom(50).replace(b'\x47', b'\x00')
    p = tmp_path / 'b.ts'
    body = ts_packets(50)
    p.write_bytes(junk + body + junk + body)
    tsrepair.repair_file(str(p))
    assert p.read_bytes() == body + body


def test_large_window_boundary(tmp_path, monkeypatch):
    monkeypatch.setattr(tsrepair, 'WINDOW_PACKETS', 7)
    p = tmp_path / 'c.ts'
    segs = [ts_packets(23, seed=s) for s in range(4)]
    p.write_bytes(b''.join(FAKE_PNG + s for s in segs))
    tsrepair.repair_file(str(p))
    assert p.read_bytes() == b''.join(segs)


def test_mp4_and_garbage_left_alone(tmp_path):
    mp4 = tmp_path / 'x.mp4'
    mp4.write_bytes(b'\x00\x00\x00\x18ftypmp42' + os.urandom(5000))
    assert tsrepair.inspect(str(mp4)).kind == 'mp4'
    img = tmp_path / 'y.jpg'
    img.write_bytes(FAKE_PNG * 3)
    res = tsrepair.repair_file(str(img))
    assert not res.repaired
    assert img.read_bytes() == FAKE_PNG * 3
