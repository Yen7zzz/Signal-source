# 離線測試：fetcher 的 stats（collect_runs 統計）與回歸——傳不傳 stats，回傳的 list 必須完全相同
import os
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

import feedparser
import pytest

import pipeline_collect
import scraper
from config import TRENDFORCE_NEWS_URL

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "trendforce")
PAGE_2_URL = "https://www.trendforce.com/news/page/2/"
NEWEST = datetime(2026, 10, 12, 3, 0, tzinfo=timezone.utc)

RSS_FETCHERS = {
    "semianalysis":         (scraper.fetch_semianalysis,         scraper.SEMIANALYSIS_RSS),
    "digitimes":            (scraper.fetch_digitimes,            scraper.DIGITIMES_RSS),
    "semi_engineering":     (scraper.fetch_semi_engineering,     scraper.SEMI_ENGINEERING_RSS),
    "eetimes":              (scraper.fetch_eetimes,              scraper.EETIMES_RSS),
    "toms_hardware":        (scraper.fetch_toms_hardware,        scraper.TOMS_HARDWARE_RSS),
    "serve_the_home":       (scraper.fetch_serve_the_home,       scraper.SERVE_THE_HOME_RSS),
    "next_platform":        (scraper.fetch_next_platform,        scraper.NEXT_PLATFORM_RSS),
    "fabricated_knowledge": (scraper.fetch_fabricated_knowledge, scraper.FABRICATED_KNOWLEDGE_RSS),
}
FILTERED = {"digitimes", "semi_engineering", "eetimes", "toms_hardware"}


def _rss(items: list[tuple[str, datetime | None]]) -> str:
    """items: [(title, published)]，published 為 None 表示該條目沒有 pubDate"""
    parts = []
    for i, (title, published) in enumerate(items):
        pub = f"<pubDate>{format_datetime(published)}</pubDate>" if published else ""
        parts.append(
            f"<item><title>{title}</title><link>https://example.com/{i}</link>"
            f"<description>summary {i}</description>{pub}</item>"
        )
    return (
        '<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>'
        + "".join(parts) + "</channel></rss>"
    )


# 25 條，依時間新到舊；偶數條相關（含 TSMC）、奇數條不相關 → 相關 13 條，超過上限 10
MIXED_FEED = _rss([
    (f"TSMC news {i}" if i % 2 == 0 else f"Garden tips {i}", NEWEST - timedelta(hours=i))
    for i in range(25)
])


@pytest.fixture
def fake_feed(monkeypatch):
    """不連網：feedparser.parse 回傳預先準備的 feed，並記錄呼叫；任何 requests.get 都直接失敗"""
    state = {"xml": MIXED_FEED, "status": 200, "href": None, "calls": 0, "raise": None}
    real_parse = feedparser.parse  # monkeypatch 改的是 feedparser 模組本身，要先留住原函式

    def fake_parse(url):
        state["calls"] += 1
        if state["raise"]:
            raise state["raise"]
        feed = real_parse(state["xml"])
        if state["status"] is not None:
            feed["status"] = state["status"]
        feed["href"] = state["href"] or url
        return feed

    def no_network(*args, **kwargs):
        raise AssertionError("unexpected network request")

    monkeypatch.setattr(scraper.feedparser, "parse", fake_parse)
    monkeypatch.setattr(scraper.requests, "get", no_network)
    return state


# 1. 回歸：每個 RSS fetcher 傳不傳 stats，回傳的 list 完全相同
@pytest.mark.parametrize("source_type", RSS_FETCHERS)
def test_rss_fetchers_same_result_with_and_without_stats(fake_feed, source_type):
    fetch, _ = RSS_FETCHERS[source_type]
    without = fetch()
    stats = {}
    with_stats = fetch(stats=stats)
    assert with_stats == without
    assert len(without) == scraper.MAX_ARTICLES_PER_SOURCE
    assert stats["returned"] == len(without)


# 2. 過濾型：passed_filter 計算整份 feed，不受上限截斷；回傳仍是前 10 條相關
@pytest.mark.parametrize("source_type", sorted(FILTERED))
def test_filtered_passed_counts_past_cap(fake_feed, source_type):
    fetch, _ = RSS_FETCHERS[source_type]
    stats = {}
    articles = fetch(stats=stats)
    assert stats["raw_count"] == 25
    assert stats["passed_filter"] == 13
    assert stats["returned"] == 10
    assert [a["title"] for a in articles] == [f"TSMC news {i}" for i in range(0, 20, 2)]


# 3. 無過濾來源：passed_filter 為 NULL
@pytest.mark.parametrize("source_type", sorted(set(RSS_FETCHERS) - FILTERED))
def test_unfiltered_passed_is_none(fake_feed, source_type):
    fetch, _ = RSS_FETCHERS[source_type]
    stats = {}
    fetch(stats=stats)
    assert stats["passed_filter"] is None
    assert stats["raw_count"] == 25
    assert stats["returned"] == 10


# 4. 超過上限後繼續計數，但只呼叫一次 feedparser.parse、不發任何其他請求（requests.get 會直接失敗）
@pytest.mark.parametrize("source_type", RSS_FETCHERS)
def test_no_extra_requests_past_cap(fake_feed, source_type):
    fetch, _ = RSS_FETCHERS[source_type]
    stats = {}
    fetch(stats=stats)
    assert fake_feed["calls"] == 1
    assert stats["status"] == "ok"


# 5. 非時序排序的 feed：newest / oldest 取全體最大 / 最小值；沒有 pubDate 的條目略過
def test_newest_oldest_not_first_last(fake_feed):
    fake_feed["xml"] = _rss([
        ("TSMC a", NEWEST - timedelta(days=3)),
        ("TSMC b", NEWEST),
        ("TSMC c", None),
        ("TSMC d", NEWEST - timedelta(days=30)),
        ("TSMC e", NEWEST - timedelta(days=1)),
    ])
    stats = {}
    scraper.fetch_next_platform(stats=stats)
    assert stats["feed_newest_published"] == "2026-10-12 03:00:00"
    assert stats["feed_oldest_published"] == "2026-09-12 03:00:00"


# 6. error 判定：raw_count == 0（不論 bozo）
def test_empty_feed_is_error(fake_feed):
    fake_feed["xml"] = _rss([])
    stats = {}
    assert scraper.fetch_digitimes(stats=stats) == []
    assert stats["status"] == "error"
    assert stats["raw_count"] == 0
    assert stats["passed_filter"] == 0
    assert stats["returned"] == 0


# 7. bozo 但有條目（DIGITIMES 平常就是編碼宣告不符）→ ok
def test_bozo_with_entries_is_ok(fake_feed, monkeypatch):
    real_parse = scraper.feedparser.parse

    def bozo_parse(url):
        feed = real_parse(url)
        feed["bozo"] = True
        feed["bozo_exception"] = feedparser.CharacterEncodingOverride("us-ascii")
        return feed

    monkeypatch.setattr(scraper.feedparser, "parse", bozo_parse)
    stats = {}
    scraper.fetch_digitimes(stats=stats)
    assert stats["status"] == "ok"
    assert stats["raw_count"] == 25


# 8. error 判定：http_status >= 400，即使有條目
def test_http_error_with_entries_is_error(fake_feed):
    fake_feed["status"] = 500
    stats = {}
    scraper.fetch_eetimes(stats=stats)
    assert stats["status"] == "error"
    assert stats["http_status"] == 500


# 9. 轉址：不算 error，記 http_status 與 redirect_to
def test_redirect_recorded_not_error(fake_feed):
    fake_feed["status"] = 301
    fake_feed["href"] = "https://example.com/new-feed"
    stats = {}
    scraper.fetch_semianalysis(stats=stats)
    assert stats["status"] == "ok"
    assert stats["http_status"] == 301
    assert stats["redirect_to"] == "https://example.com/new-feed"


def test_no_redirect_leaves_redirect_to_null(fake_feed):
    stats = {}
    scraper.fetch_semianalysis(stats=stats)
    assert stats["redirect_to"] is None


# 10. error 判定：有例外 → fetcher 照舊回傳 []，stats 記 error
@pytest.mark.parametrize("source_type", RSS_FETCHERS)
def test_exception_is_error(fake_feed, source_type):
    fake_feed["raise"] = RuntimeError("boom")
    fetch, _ = RSS_FETCHERS[source_type]
    stats = {}
    assert fetch(stats=stats) == []
    assert stats["status"] == "error"
    assert "boom" in stats["error_msg"]
    assert stats["returned"] == 0


# ── TrendForce ───────────────────────────────────────────────

def _read(name: str) -> str:
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as f:
        return f.read()


class _FakeResponse:
    def __init__(self, text: str, url: str, status_code: int = 200, history=None):
        self.text = text
        self.url = url
        self.status_code = status_code
        self.history = history or []

    def raise_for_status(self):
        if self.status_code >= 400:
            raise scraper.requests.HTTPError(f"{self.status_code} error")


@pytest.fixture
def trendforce_pages(monkeypatch):
    pages = {TRENDFORCE_NEWS_URL: _read("news_p1.html"), PAGE_2_URL: _read("news_p2.html")}
    state = {"pages": pages, "fail": set(), "redirect_to": None}

    def fake_get(url, headers=None, timeout=None):
        if url in state["fail"]:
            if url == TRENDFORCE_NEWS_URL:
                return _FakeResponse("", url, status_code=503)
            raise scraper.requests.ConnectionError("page down")
        if url not in state["pages"]:
            raise AssertionError(f"unexpected request: {url}")
        if url == TRENDFORCE_NEWS_URL and state["redirect_to"]:
            return _FakeResponse(state["pages"][url], state["redirect_to"],
                                 history=[_FakeResponse("", url, status_code=301)])
        return _FakeResponse(state["pages"][url], url)

    monkeypatch.setattr(scraper.requests, "get", fake_get)
    monkeypatch.setattr(scraper, "_fetch_trendforce_summary", lambda url: "")
    monkeypatch.setattr(scraper.time, "sleep", lambda s: None)
    return state


def test_trendforce_same_result_with_and_without_stats(trendforce_pages):
    known = {a["url"] for a in scraper.fetch_trendforce()[:2]}
    without = scraper.fetch_trendforce(known_urls=known)
    stats = {}
    assert scraper.fetch_trendforce(known_urls=known, stats=stats) == without
    assert stats["returned"] == len(without) == stats["raw_count"] - 2


def test_trendforce_stats(trendforce_pages):
    stats = {}
    articles = scraper.fetch_trendforce(stats=stats)
    assert stats["status"] == "ok"
    assert stats["error_msg"] is None
    assert stats["http_status"] == 200
    assert stats["redirect_to"] is None
    assert stats["passed_filter"] is None
    # 兩頁候選去重後的數量；known_urls 為空，所以全部回傳
    assert stats["raw_count"] == len(articles)
    dates = sorted(a["published"] for a in articles)
    # URL 上是台灣日期 → 當日台灣 00:00 的 UTC（前一天 16:00）
    newest = datetime.strptime(dates[-1], "%Y-%m-%d") - timedelta(hours=8)
    assert stats["feed_newest_published"] == newest.strftime("%Y-%m-%d %H:%M:%S")


def test_trendforce_known_urls_reduce_returned_not_raw(trendforce_pages):
    all_urls = {a["url"] for a in scraper.fetch_trendforce()}
    stats = {}
    assert scraper.fetch_trendforce(known_urls=all_urls, stats=stats) == []
    assert stats["raw_count"] == len(all_urls)
    assert stats["returned"] == 0
    assert stats["status"] == "ok"


def test_trendforce_page_2_failure_is_ok_with_note(trendforce_pages):
    trendforce_pages["fail"].add(PAGE_2_URL)
    stats = {}
    assert scraper.fetch_trendforce(stats=stats)
    assert stats["status"] == "ok"
    assert "第 2 頁失敗" in stats["error_msg"]


def test_trendforce_page_1_failure_is_error(trendforce_pages):
    trendforce_pages["fail"].add(TRENDFORCE_NEWS_URL)
    stats = {}
    assert scraper.fetch_trendforce(stats=stats) == []
    assert stats["status"] == "error"
    assert stats["http_status"] == 503
    assert stats["returned"] == 0


# HTML 改版：HTTP 200 但 parser 解析 0 篇 → 第 1 頁記 error，第 2 頁只在 error_msg 註記
EMPTY_PAGE = "<html><body><p>redesigned</p></body></html>"
EMPTY_PAGE_WITH_NEXT = f'<html><body><a href="{PAGE_2_URL}">Next Page</a></body></html>'


def test_trendforce_page_1_zero_candidates_is_error(trendforce_pages):
    trendforce_pages["pages"][TRENDFORCE_NEWS_URL] = EMPTY_PAGE
    without = scraper.fetch_trendforce()
    stats = {}
    assert scraper.fetch_trendforce(stats=stats) == without == []
    assert stats["status"] == "error"
    assert stats["error_msg"] == "列表頁解析 0 篇"
    assert stats["http_status"] == 200
    assert stats["raw_count"] == 0


def test_trendforce_page_1_zero_but_page_2_parses_is_still_error(trendforce_pages):
    trendforce_pages["pages"][TRENDFORCE_NEWS_URL] = EMPTY_PAGE_WITH_NEXT
    without = scraper.fetch_trendforce()
    stats = {}
    articles = scraper.fetch_trendforce(stats=stats)
    assert articles == without
    assert articles  # 回傳行為不變：第 2 頁的文章照收
    assert stats["status"] == "error"
    assert stats["error_msg"] == "列表頁解析 0 篇"
    assert stats["raw_count"] == len(articles)


def test_trendforce_page_2_zero_candidates_is_ok_with_note(trendforce_pages):
    trendforce_pages["pages"][PAGE_2_URL] = EMPTY_PAGE
    stats = {}
    articles = scraper.fetch_trendforce(stats=stats)
    assert articles
    assert stats["status"] == "ok"
    assert stats["error_msg"] == "第 2 頁解析 0 篇"


def test_trendforce_redirect_recorded(trendforce_pages):
    trendforce_pages["redirect_to"] = "https://www.trendforce.com/news/moved/"
    stats = {}
    scraper.fetch_trendforce(stats=stats)
    assert stats["status"] == "ok"
    assert stats["http_status"] == 301
    assert stats["redirect_to"] == "https://www.trendforce.com/news/moved/"


# ── SEC EDGAR / 台股月營收：fetcher 不變，pipeline 用 _count_only 包裝 ──

def test_count_only_same_result_and_counts():
    articles = [{"url": "a"}, {"url": "b"}]
    wrapped = pipeline_collect._count_only(lambda: articles)
    stats = {}
    assert wrapped(stats=stats) is articles
    assert stats == {"raw_count": 2, "returned": 2}
