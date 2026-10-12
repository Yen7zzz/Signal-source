# 釘住 feedparser 遇到轉址 / HTTP 錯誤 / 連線失敗時的實際行為（本機 http server，不連外網）
# _feed_meta 的 error 判定與 redirect_to 依賴這些行為；feedparser 或 Python 升級後若行為改變，這裡會先失敗
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import feedparser
import pytest

import scraper

RSS = (
    b'<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>'
    b"<item><title>a</title><link>http://example.com/a</link>"
    b"<pubDate>Mon, 12 Oct 2026 03:00:00 GMT</pubDate></item>"
    b"</channel></rss>"
)


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        path = self.path
        if path == "/feed":
            self.send_response(200)
            self.send_header("Content-Type", "application/rss+xml")
            self.end_headers()
            self.wfile.write(RSS)
        elif path.startswith("/redirect/"):
            self.send_response(int(path.rsplit("/", 1)[1]))
            self.send_header("Location", "/feed")
            self.end_headers()
        elif path == "/chain":
            # 301 → 302 → /feed
            self.send_response(301)
            self.send_header("Location", "/redirect/302")
            self.end_headers()
        elif path.startswith("/status/"):
            self.send_response(int(path.rsplit("/", 1)[1]))
            self.end_headers()
            self.wfile.write(b"error")
        elif path == "/empty":
            self.send_response(200)
            self.send_header("Content-Type", "application/rss+xml")
            self.end_headers()
            self.wfile.write(b'<?xml version="1.0"?><rss version="2.0"><channel><title>t</title></channel></rss>')


@pytest.fixture(scope="module")
def base_url():
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


# 1. 301 / 302 / 307：status 是轉址碼（不是最終的 200），href 是最終網址，內容正常解析
@pytest.mark.parametrize("code", [301, 302, 307])
def test_redirect_status_is_redirect_code(base_url, code):
    feed = feedparser.parse(f"{base_url}/redirect/{code}")
    assert feed.status == code
    assert feed.href == f"{base_url}/feed"
    assert len(feed.entries) == 1


# 2. 308：feedparser 不記轉址碼，status 是 200，但 href 仍是最終網址 → redirect_to 必須看 href
def test_308_status_is_200_but_href_changes(base_url):
    feed = feedparser.parse(f"{base_url}/redirect/308")
    assert feed.status == 200
    assert feed.href == f"{base_url}/feed"
    assert len(feed.entries) == 1


# 3. 多段轉址：status 是第一段的轉址碼，href 是最終網址
def test_redirect_chain_keeps_first_code(base_url):
    feed = feedparser.parse(f"{base_url}/chain")
    assert feed.status == 301
    assert feed.href == f"{base_url}/feed"


# 4. HTTP 錯誤：不丟例外，status 是錯誤碼，0 條目
@pytest.mark.parametrize("code", [404, 503])
def test_http_error_does_not_raise(base_url, code):
    feed = feedparser.parse(f"{base_url}/status/{code}")
    assert feed.status == code
    assert len(feed.entries) == 0


# 5. 連線失敗：不丟例外，沒有 status 也沒有 href，bozo_exception 記錯誤
def test_connection_failure_has_no_status_or_href():
    feed = feedparser.parse("http://127.0.0.1:1/feed")
    assert "status" not in feed
    assert "href" not in feed
    assert len(feed.entries) == 0
    assert feed.bozo


# 6. _feed_meta：轉址不算 error，只記 http_status / redirect_to
@pytest.mark.parametrize("code", [301, 302, 307, 308])
def test_feed_meta_redirect_is_ok(base_url, code):
    url = f"{base_url}/redirect/{code}"
    meta = scraper._feed_meta(feedparser.parse(url), url)
    assert meta["status"] == "ok"
    assert meta["error_msg"] is None
    assert meta["http_status"] == (200 if code == 308 else code)
    assert meta["redirect_to"] == f"{base_url}/feed"
    assert meta["raw_count"] == 1
    assert meta["feed_newest_published"] == "2026-10-12 03:00:00"


def test_feed_meta_no_redirect(base_url):
    url = f"{base_url}/feed"
    meta = scraper._feed_meta(feedparser.parse(url), url)
    assert meta["status"] == "ok"
    assert meta["http_status"] == 200
    assert meta["redirect_to"] is None


def test_feed_meta_http_error(base_url):
    url = f"{base_url}/status/503"
    meta = scraper._feed_meta(feedparser.parse(url), url)
    assert meta["status"] == "error"
    assert meta["error_msg"] == "HTTP 503"
    assert meta["http_status"] == 503


def test_feed_meta_empty_feed_is_error(base_url):
    url = f"{base_url}/empty"
    meta = scraper._feed_meta(feedparser.parse(url), url)
    assert meta["status"] == "error"
    assert meta["http_status"] == 200
    assert meta["raw_count"] == 0


def test_feed_meta_connection_failure():
    url = "http://127.0.0.1:1/feed"
    meta = scraper._feed_meta(feedparser.parse(url), url)
    assert meta["status"] == "error"
    assert meta["http_status"] is None
    assert meta["redirect_to"] is None
    assert "URLError" in meta["error_msg"]
