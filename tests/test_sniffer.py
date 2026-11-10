from omnidl import sniffer

from .conftest import pack_js


def test_plain_and_escaped_urls():
    page = r'''<video src="https://cdn.example.com/v/720p/video.mp4"></video>
    <script>var cfg={"hls":"https:\/\/cdn.example.com\/hls\/master.m3u8?token=abc"};</script>
    <a href="https://cdn.example.com/preview/trailer.mp4">x</a>'''
    c = sniffer.find_media_urls(page, 'https://example.com/watch/1')
    urls = [x.url for x in c]
    assert urls[0] == 'https://cdn.example.com/hls/master.m3u8?token=abc'
    assert 'https://cdn.example.com/v/720p/video.mp4' in urls
    assert urls[-1].endswith('trailer.mp4')  # 預告片分數最低


def test_packed_js():
    js = "var s='https://surrit.example.com/abc-def/playlist.m3u8';p.load(s)"
    page = f'<script>{pack_js(js)}</script>'
    assert 'playlist.m3u8' not in page
    c = sniffer.find_media_urls(page, 'https://example.com/')
    assert c[0].url == 'https://surrit.example.com/abc-def/playlist.m3u8'


def test_protocol_relative_and_relative():
    page = '''<source src="//cdn.x.com/a/b.m3u8"><script>load("/media/c.mp4")</script>'''
    urls = {x.url for x in sniffer.find_media_urls(page, 'https://site.com/p/1')}
    assert urls == {'https://cdn.x.com/a/b.m3u8', 'https://site.com/media/c.mp4'}


def test_base64():
    import base64
    enc = base64.b64encode(b'https://cdn.x.com/v/index.m3u8').decode()
    urls = [x.url for x in sniffer.find_media_urls(f'<div data-src="{enc}"></div>', 'https://a.com')]
    assert urls == ['https://cdn.x.com/v/index.m3u8']


def test_classify_and_title():
    assert sniffer.classify_m3u8('#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1\na.m3u8\n#EXT-X-STREAM-INF:BANDWIDTH=2\nb.m3u8') == (True, 2)
    assert sniffer.classify_m3u8('#EXTM3U\n#EXTINF:1,\na.ts') == (False, 0)
    assert sniffer.classify_m3u8('<html>') == (None, 0)
    assert sniffer.extract_title('<meta property="og:title" content="Hello &amp; World">') == 'Hello & World'
