# 離線測試：fixture 為 2026-10-06 存下的 TrendForce 列表頁第 1、2 頁
import os

import pytest
import requests

import scraper
from config import TRENDFORCE_NEWS_URL

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "trendforce")
PAGE_2_URL = "https://www.trendforce.com/news/page/2/"


def _read(name: str) -> str:
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as f:
        return f.read()


class _FakeResponse:
    def __init__(self, text: str):
        self.text = text

    def raise_for_status(self):
        pass


@pytest.fixture
def summary_calls(monkeypatch):
    """不連網：列表頁回 fixture，文章頁摘要改成記錄呼叫"""
    calls = []
    pages = {TRENDFORCE_NEWS_URL: _read("news_p1.html"), PAGE_2_URL: _read("news_p2.html")}

    def fake_get(url, headers=None, timeout=None):
        if url not in pages:
            raise AssertionError(f"unexpected request: {url}")
        return _FakeResponse(pages[url])

    monkeypatch.setattr(scraper.requests, "get", fake_get)
    monkeypatch.setattr(scraper, "_fetch_trendforce_summary", lambda url: calls.append(url) or "")
    monkeypatch.setattr(scraper.time, "sleep", lambda s: None)
    return calls


# 1. 選擇器只收 /news/YYYY/MM/DD/slug/，第 1 頁 = 列表 5 篇 + 輪播 3 篇
def test_selector_only_article_urls():
    items, _ = scraper._parse_trendforce_list(_read("news_p1.html"))
    urls = [u for u, _, _ in items]
    assert len(urls) == 8
    assert all(scraper.TRENDFORCE_ARTICLE_URL_RE.match(u) for u in urls)
    assert not any("/category/" in u or "/page/" in u for u in urls)
    assert TRENDFORCE_NEWS_URL not in urls


# 2. 不做 URL 正規化：與頁面 href 原字串一致（含結尾斜線）
def test_urls_match_href_verbatim():
    html = _read("news_p1.html")
    items, _ = scraper._parse_trendforce_list(html)
    for url, _, _ in items:
        assert f'href="{url}"' in html
        assert url.endswith("/")


# 3. published 取自 URL（台灣日期）：date_gmt 10/05 23:30 的文章，URL 為 /2026/10/06/
def test_published_from_url():
    items, _ = scraper._parse_trendforce_list(_read("news_p1.html"))
    published = {u: p for u, _, p in items}
    url = ("https://www.trendforce.com/news/2026/10/06/news-micron-sees-tighter-memory-supply-"
           "through-fy28-when-will-the-big-threes-new-capacity-arrive/")
    assert published[url] == "2026-10-06"


# 4. 兩頁合併，輪播在兩頁重複出現只收一次 → 12 個 URL
def test_two_pages_merged_and_deduped(summary_calls):
    articles = scraper.fetch_trendforce()
    urls = [a["url"] for a in articles]
    assert len(urls) == len(set(urls)) == 12


# 5. 第 2 頁網址從第 1 頁的 Next Page 連結解析
def test_next_page_url_parsed():
    _, next_url = scraper._parse_trendforce_list(_read("news_p1.html"))
    assert next_url == PAGE_2_URL


# 6. 第 2 頁失敗時仍回傳第 1 頁的結果
def test_page_2_failure_keeps_page_1(summary_calls, monkeypatch):
    p1 = _read("news_p1.html")

    def fake_get(url, headers=None, timeout=None):
        if url == PAGE_2_URL:
            raise requests.HTTPError("503")
        return _FakeResponse(p1)

    monkeypatch.setattr(scraper.requests, "get", fake_get)
    articles = scraper.fetch_trendforce()
    assert len(articles) == 8


# 7. known_urls 在抓摘要前就跳過
def test_known_urls_skip_summary(summary_calls):
    items, _ = scraper._parse_trendforce_list(_read("news_p1.html"))
    known = {u for u, _, _ in items[:3]}
    articles = scraper.fetch_trendforce(known_urls=known)
    assert len(articles) == 12 - 3
    assert len(summary_calls) == 12 - 3
    assert not known & set(summary_calls)
    assert not known & {a["url"] for a in articles}


# 8. 不做關鍵字過濾：不在舊 TRENDFORCE_KEYWORDS 內的標題也會收
def test_no_keyword_filter(summary_calls):
    titles = [a["title"] for a in scraper.fetch_trendforce()]
    assert any("CS Microelectronics" in t for t in titles)


# 9. 不套用 MAX_ARTICLES_PER_SOURCE：超過 10 篇不截斷
def test_no_max_articles_cap(summary_calls):
    assert scraper.MAX_ARTICLES_PER_SOURCE == 10
    assert len(scraper.fetch_trendforce()) > scraper.MAX_ARTICLES_PER_SOURCE
