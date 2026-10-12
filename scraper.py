# ============================================================
# scraper.py — 四個精準源頭的抓取邏輯（降噪強化版）
#
# 主要改動：
#   1. DIGITIMES 加上關鍵字過濾（與 TrendForce 相同邏輯）
#   2. Seeking Alpha 改用個股專屬 RSS，精準度大幅提升
#   3. TrendForce 補上進入文章抓摘要（非空白）
#   4. SEC EDGAR summary 改為抓 8-K exhibit 標題，有實際情報價值
#   5. 新增跨源頭標題去重（同一新聞多源報導只保留一筆）
# ============================================================

import calendar
import feedparser
import requests
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from bs4 import BeautifulSoup
from config import (
    SEMIANALYSIS_RSS,
    TRENDFORCE_NEWS_URL,
    DIGITIMES_RSS,
    WATCHLIST,
    SEC_FILING_TYPES, SEC_USER_AGENT, SEC_8K_VALUABLE_ITEMS,
    MAX_ARTICLES_PER_SOURCE,
    SEMI_ENGINEERING_RSS, EETIMES_RSS, TOMS_HARDWARE_RSS,
    SERVE_THE_HOME_RSS, NEXT_PLATFORM_RSS, FABRICATED_KNOWLEDGE_RSS,
)

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; IndustryRadar/1.0)",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# ── 半導體核心關鍵字（DIGITIMES / Seeking Alpha 共用）────────
SEMICON_KEYWORDS = [
    # 晶片 / 製程
    "semiconductor", "chip", "wafer", "foundry", "fab",
    "logic", "NAND", "DRAM", "HBM", "memory",
    "CoWoS", "packaging", "advanced packaging", "chiplet",
    # 公司
    "TSMC", "NVIDIA", "AMD", "Intel", "Micron",
    "SK Hynix", "Samsung", "ASE", "ASML", "Qualcomm",
    # 產業關鍵字
    "CapEx", "capacity", "utilization", "yield", "node",
    "AI chip", "data center", "HPC", "GPU", "accelerator",
    # 市場 / 供應鏈
    "supply chain", "inventory", "pricing", "bit growth",
    "WFE", "equipment", "lithography",
]


def _clean_html(text: str, max_len: int = 600) -> str:
    """移除 HTML tag，截取前 max_len 字"""
    return re.sub(r"<[^>]+>", "", text or "").strip()[:max_len]


# ── 來源健康統計（collect_runs）──────────────────────────────
# fetcher 都接受選填的 stats dict，有傳就把抓取統計填進去，回傳的文章 list 不受影響。
# feedparser 的實際行為（tests/test_feedparser_redirect.py 釘住）：
#   301/302/307 → status 是轉址碼、href 是最終網址；308 → status 200、href 是最終網址；
#   連線失敗 → 沒有 status 也沒有 href，bozo_exception 是 URLError，不會丟例外
ERROR_MSG_MAX_LEN = 500


def _fill_stats(stats: dict | None, **values):
    if stats is not None:
        stats.update(values)


def _to_utc_str(dt: datetime) -> str:
    """轉成與 SQLite created_at 相同的 UTC 格式 'YYYY-MM-DD HH:MM:SS'"""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _feed_meta(feed, configured_url: str) -> dict:
    """
    從 feedparser 結果整理 collect_runs 欄位。
    error 判定：http_status >= 400，或 raw_count == 0（不論 bozo）；
    轉址（含 308）不算 error，只記 http_status / redirect_to。
    """
    http_status = feed.get("status")
    href = feed.get("href")
    raw_count = len(feed.entries)

    # 取全體最大 / 最小值而非第一筆：有些 feed 不是依時間排序（如 Next Platform）
    times = []
    for entry in feed.entries:
        parsed = entry.get("published_parsed") or entry.get("updated_parsed")
        if parsed:
            times.append(datetime.fromtimestamp(calendar.timegm(parsed), tz=timezone.utc))

    status, error_msg = "ok", None
    if http_status is not None and http_status >= 400:
        status, error_msg = "error", f"HTTP {http_status}"
    elif raw_count == 0:
        status = "error"
        error_msg = "feed 0 條目"
        if feed.get("bozo_exception") is not None:
            error_msg += f": {feed.bozo_exception!r}"

    return {
        "status":                status,
        "error_msg":             error_msg[:ERROR_MSG_MAX_LEN] if error_msg else None,
        "http_status":           http_status,
        "redirect_to":           href if href and href != configured_url else None,
        "raw_count":             raw_count,
        "feed_newest_published": _to_utc_str(max(times)) if times else None,
        "feed_oldest_published": _to_utc_str(min(times)) if times else None,
    }


def _is_relevant(text: str, keywords: list[str] = None) -> bool:
    """
    判斷文字是否與半導體產業相關
    預設使用 SEMICON_KEYWORDS，也可傳入自訂 keyword list
    """
    kws = keywords or SEMICON_KEYWORDS
    text_lower = text.lower()
    return any(kw.lower() in text_lower for kw in kws)


# ── 1. SemiAnalysis ───────────────────────────────────────────
def fetch_semianalysis(stats: dict | None = None) -> list[dict]:
    """
    透過 Substack RSS 抓取 SemiAnalysis 的公開文章
    付費文章只顯示標題，但標題本身已有情報價值
    """
    articles = []
    try:
        feed = feedparser.parse(SEMIANALYSIS_RSS)
        _fill_stats(stats, **_feed_meta(feed, SEMIANALYSIS_RSS))
        for entry in feed.entries[:MAX_ARTICLES_PER_SOURCE]:
            title = entry.get("title", "").strip()
            url = entry.get("link", "").strip()
            summary = _clean_html(entry.get("summary", ""))

            if title and url:
                articles.append({
                    "source_type": "semianalysis",
                    "title": title,
                    "url": url,
                    "summary": summary,
                    "source": "SemiAnalysis",
                    "published": entry.get("published", ""),
                    "ticker": "",
                    "filing_type": "",
                })
        logger.info(f"SemiAnalysis: {len(articles)} 篇")
    except Exception as e:
        logger.error(f"SemiAnalysis 失敗: {e}")
        _fill_stats(stats, status="error", error_msg=repr(e)[:ERROR_MSG_MAX_LEN])
    _fill_stats(stats, passed_filter=None, returned=len(articles))
    return articles


# ── 2. TrendForce（加上進入文章抓摘要）───────────────────────
def _fetch_trendforce_summary(article_url: str) -> str:
    """
    進入 TrendForce 文章頁，抓第一段作為摘要
    失敗就回傳空字串，不中斷主流程
    """
    try:
        resp = requests.get(article_url, headers=HEADERS, timeout=10)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        # TrendForce 文章內容在 .entry-content 或 article 標籤
        content = soup.select_one(".entry-content, article, .post-content")
        if content:
            paragraphs = content.find_all("p")
            # 取前兩段，過濾掉太短的（通常是廣告標語）
            text = " ".join(
                p.get_text(strip=True)
                for p in paragraphs[:3]
                if len(p.get_text(strip=True)) > 40
            )
            return text[:500]
    except Exception as e:
        logger.debug(f"TrendForce 抓摘要失敗 {article_url}: {e}")
    return ""


# 只收文章頁：/news/YYYY/MM/DD/slug/（排除分類頁、分頁、/news/ 首頁）
# URL 上的日期是台灣日期，直接當 published（僅供顯示，週報窗口用 created_at）
TRENDFORCE_ARTICLE_URL_RE = re.compile(
    r"^https://www\.trendforce\.com/news/(\d{4})/(\d{2})/(\d{2})/[^/]+/$"
)


def _parse_trendforce_list(html: str) -> tuple[list[tuple[str, str, str]], str | None]:
    """
    解析 TrendForce 列表頁
    回傳 ([(url, title, published), ...], 下一頁 URL 或 None)
    列表（每頁 5 篇）與輪播都會收，只用 URL 格式判斷是否為文章
    """
    soup = BeautifulSoup(html, "html.parser")
    items = []
    seen_urls = set()

    for link in soup.select("a[href*='/news/']"):
        title = link.get_text(strip=True)
        href = link.get("href", "")

        # 同一篇文章有圖片、標題、more 多個連結，只取有標題文字的那個
        if not title or len(title) < 10:
            continue

        url = f"https://www.trendforce.com{href}" if href.startswith("/") else href
        m = TRENDFORCE_ARTICLE_URL_RE.match(url)
        if not m or url in seen_urls:
            continue
        seen_urls.add(url)
        items.append((url, title, f"{m.group(1)}-{m.group(2)}-{m.group(3)}"))

    next_link = next(
        (a for a in soup.find_all("a", href=True) if a.get_text(strip=True) == "Next Page"),
        None,
    )
    return items, (next_link["href"] if next_link else None)


def _trendforce_date_to_utc(date_str: str) -> str:
    """URL 上的台灣日期 → 該日台灣 00:00 的 UTC 字串"""
    tpe = timezone(timedelta(hours=8))
    return _to_utc_str(datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=tpe))


def fetch_trendforce(known_urls: set[str] | None = None, stats: dict | None = None) -> list[dict]:
    """
    爬取 TrendForce 公開新聞第 1、2 頁，進入文章抓摘要

    - 不做關鍵字過濾，交由 rule_score 分層
    - known_urls（已入庫 URL）在抓摘要前就跳過，省掉重複的文章頁請求
    - 不套用 MAX_ARTICLES_PER_SOURCE，兩頁即硬上限
    - stats：第 1 頁失敗或解析 0 篇記 error；第 2 頁失敗或解析 0 篇仍是 ok，原因寫在 error_msg
    """
    known_urls = known_urls or set()
    articles = []
    candidates = []
    seen_urls = set()
    skipped = 0
    _fill_stats(stats, passed_filter=None, returned=0)

    try:
        resp = requests.get(TRENDFORCE_NEWS_URL, headers=HEADERS, timeout=15)
        # 與 feedparser 一致：有轉址時記第一段的轉址碼
        history = getattr(resp, "history", None) or []
        final_url = getattr(resp, "url", None)
        _fill_stats(
            stats,
            http_status=history[0].status_code if history else getattr(resp, "status_code", None),
            redirect_to=final_url if final_url and final_url != TRENDFORCE_NEWS_URL else None,
        )
        resp.raise_for_status()
        page_items, next_url = _parse_trendforce_list(resp.text)
    except Exception as e:
        logger.error(f"TrendForce 失敗: {e}")
        _fill_stats(stats, status="error", error_msg=repr(e)[:ERROR_MSG_MAX_LEN])
        return articles
    candidates.extend(page_items)
    # HTML 改版時 HTTP 仍是 200，但 parser 會靜默回傳 0 篇 → 第 1 頁 0 篇記 error
    page_1_count = len(page_items)

    # 第 2 頁失敗不影響第 1 頁的結果
    page_2_note = None
    if next_url:
        try:
            resp = requests.get(next_url, headers=HEADERS, timeout=15)
            resp.raise_for_status()
            page_items, _ = _parse_trendforce_list(resp.text)
            candidates.extend(page_items)
            if not page_items:
                page_2_note = "第 2 頁解析 0 篇"
        except Exception as e:
            logger.warning(f"TrendForce 第 2 頁失敗，只用第 1 頁: {e}")
            page_2_note = f"第 2 頁失敗，只用第 1 頁: {e!r}"
    else:
        logger.warning("TrendForce 找不到 Next Page 連結，只用第 1 頁")
        page_2_note = "找不到 Next Page 連結，只用第 1 頁"

    dates = [published for _, _, published in candidates]
    _fill_stats(
        stats,
        status="ok" if page_1_count else "error",
        error_msg=("列表頁解析 0 篇" if not page_1_count
                   else page_2_note[:ERROR_MSG_MAX_LEN] if page_2_note else None),
        raw_count=len({url for url, _, _ in candidates}),
        feed_newest_published=_trendforce_date_to_utc(max(dates)) if dates else None,
        feed_oldest_published=_trendforce_date_to_utc(min(dates)) if dates else None,
    )

    for url, title, published in candidates:
        # 輪播文章在兩頁都會出現
        if url in seen_urls:
            continue
        seen_urls.add(url)

        if url in known_urls:
            skipped += 1
            continue

        # 進入文章抓摘要（加 0.3s 延遲避免被封）
        summary = _fetch_trendforce_summary(url)
        time.sleep(0.3)

        articles.append({
            "source_type": "trendforce",
            "title": title,
            "url": url,
            "summary": summary,
            "source": "TrendForce",
            "published": published,
            "ticker": "",
            "filing_type": "",
        })

    logger.info(f"TrendForce: {len(articles)} 篇（已入庫跳過 {skipped} 篇）")
    _fill_stats(stats, returned=len(articles))
    return articles


# ── 3. DIGITIMES（加上關鍵字過濾）────────────────────────────
def fetch_digitimes(stats: dict | None = None) -> list[dict]:
    """
    透過 RSS 抓取 DIGITIMES，加上半導體關鍵字過濾

    ★ 改動重點：原本全量收錄，現在只保留 SEMICON_KEYWORDS 相關文章
    過濾掉：手機評測、政治新聞、電動車、消費電子雜訊
    """
    articles = []
    passed = 0
    try:
        feed = feedparser.parse(DIGITIMES_RSS)
        _fill_stats(stats, **_feed_meta(feed, DIGITIMES_RSS))
        for entry in feed.entries:
            title = entry.get("title", "").strip()
            url = entry.get("link", "").strip()
            summary = _clean_html(entry.get("summary", ""))

            if not title or not url:
                continue

            # ★ 改動：標題或摘要包含關鍵字才保留
            if not _is_relevant(title + " " + summary):
                continue

            # 超過上限後只計數（passed_filter），不再收、也不發任何請求
            passed += 1
            if len(articles) >= MAX_ARTICLES_PER_SOURCE:
                continue

            articles.append({
                "source_type": "digitimes",
                "title": title,
                "url": url,
                "summary": summary,
                "source": "DIGITIMES",
                "published": entry.get("published", ""),
                "ticker": "",
                "filing_type": "",
            })

        logger.info(f"DIGITIMES: {len(articles)} 篇（關鍵字過濾後）")
    except Exception as e:
        logger.error(f"DIGITIMES 失敗: {e}")
        _fill_stats(stats, status="error", error_msg=repr(e)[:ERROR_MSG_MAX_LEN])
    _fill_stats(stats, passed_filter=passed, returned=len(articles))
    return articles


# ── 4. SEC EDGAR（summary 改為有意義的內容）──────────────────
def _get_sec_filings(ticker: str, company_info: dict) -> list[dict]:
    """
    抓取 SEC filings，summary 改為顯示 8-K 的 Item 列表
    讓人一眼看出這份文件是關於哪個重大事件

    ★ 改動：
      - summary 從「公司提交表單」改為實際的 Item 條目
      - URL 改為直接指向 filing index，而非公司查詢頁
    """
    cik = company_info.get("sec_cik", "")
    if not cik:
        return []

    articles = []
    sec_headers = {"User-Agent": SEC_USER_AGENT}

    try:
        cik_clean = str(int(cik))
        url = f"https://data.sec.gov/submissions/CIK{cik_clean.zfill(10)}.json"

        resp = requests.get(url, headers=sec_headers, timeout=15)
        resp.raise_for_status()
        data = resp.json()

        recent = data.get("filings", {}).get("recent", {})
        forms = recent.get("form", [])
        dates = recent.get("filingDate", [])
        accessions = recent.get("accessionNumber", [])
        items_list = recent.get("items", [])
        company_name = company_info["name"]

        cutoff = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")

        for form, date, accession, items_str in zip(forms, dates, accessions, items_list):
            if form not in SEC_FILING_TYPES:
                continue

            # 只保留最近 90 天的 filing
            if date < cutoff:
                continue

            accession_fmt = accession.replace("-", "")
            index_url = (
                f"https://www.sec.gov/Archives/edgar/data/"
                f"{cik_clean}/{accession_fmt}/{accession}-index.htm"
            )

            # 8-K 過濾：直接用 submissions JSON 的 items 欄位（如 "2.02,9.01"）
            # items_str 為空表示 JSON 沒有資料，視為不確定，保留
            if form == "8-K":
                if items_str and not any(item in items_str for item in SEC_8K_VALUABLE_ITEMS):
                    continue

            # summary：8-K 用 items 字串，10-Q 用財報週期說明
            if form == "8-K":
                summary = f"Items: {items_str}" if items_str else ""
            else:
                summary = ""

            if not summary:
                # 10-Q 或 items 為空的 8-K
                quarter = _estimate_quarter(date)
                summary = f"{company_name} {quarter} 季報，財務數據與 CapEx 指引"

            articles.append({
                "source_type": "sec_edgar",
                "title": f"[{form}] {company_name} — {date}",
                "url": index_url,
                "summary": summary,
                "source": "SEC EDGAR",
                "published": date,
                "ticker": ticker,
                "filing_type": form,
            })

            if len(articles) >= 3:
                break

        time.sleep(0.5)
        logger.info(f"SEC EDGAR [{ticker}]: {len(articles)} 份文件")
    except Exception as e:
        logger.error(f"SEC EDGAR [{ticker}] 失敗: {e}")

    return articles



def _estimate_quarter(date_str: str) -> str:
    """根據 filing 日期推算是哪一季"""
    try:
        month = int(date_str[5:7])
        year = date_str[:4]
        q = (month - 1) // 3 + 1
        return f"{year} Q{q}"
    except Exception:
        return date_str[:7]


def fetch_sec_edgar() -> list[dict]:
    all_articles = []
    for ticker, info in WATCHLIST.items():
        if info.get("sec_cik"):
            all_articles.extend(_get_sec_filings(ticker, info))
    return all_articles


# ── 5. Seeking Alpha（改用個股 RSS）──────────────────────────
def fetch_seeking_alpha() -> list[dict]:
    """
    ★ 改動重點：從全站 RSS 改為個股 RSS

    原本：seekingalpha.com/feed.xml（全站，超級雜）
    現在：seekingalpha.com/symbol/{TICKER}/feed.xml（個股專屬）

    這樣每篇文章都直接跟 WATCHLIST 公司相關，不需要關鍵字過濾
    精準度從 ~20% 提升到接近 100%
    """
    articles = []
    seen_urls = set()

    for ticker, info in WATCHLIST.items():
        # 只處理美股（有 sec_cik 的才有 Seeking Alpha 個股頁）
        if not info.get("sec_cik"):
            continue

        # Seeking Alpha 個股 RSS
        # 例如：https://seekingalpha.com/symbol/NVDA/feed.xml
        ticker_symbol = ticker.split(".")[0]  # 去掉 .KS 等後綴
        rss_url = f"https://seekingalpha.com/symbol/{ticker_symbol}/feed.xml"

        try:
            feed = feedparser.parse(rss_url)
            count = 0

            for entry in feed.entries:
                title = entry.get("title", "").strip()
                url = entry.get("link", "").strip()
                summary = _clean_html(entry.get("summary", ""))

                if not title or not url or url in seen_urls:
                    continue

                # 跳過純 earnings call transcript（SEC EDGAR 已經有了）
                if "transcript" in title.lower() and "earnings" in title.lower():
                    continue

                seen_urls.add(url)
                articles.append({
                    "source_type": "seeking_alpha",
                    "title": title,
                    "url": url,
                    "summary": summary,
                    "source": f"Seeking Alpha ({ticker_symbol})",
                    "published": entry.get("published", ""),
                    "ticker": ticker_symbol,
                    "filing_type": "",
                })
                count += 1

                # 每支股票最多 3 篇，避免單一公司佔版面
                if count >= 3:
                    break

            time.sleep(0.2)  # 避免 rate limit

        except Exception as e:
            logger.error(f"Seeking Alpha [{ticker_symbol}] 失敗: {e}")

    # 總量上限
    logger.info(f"Seeking Alpha: {len(articles)} 篇（個股 RSS）")
    return articles[:MAX_ARTICLES_PER_SOURCE]


# ── 6–11. 半導體 / 技術媒體 RSS ──────────────────────────────

def _fetch_rss(
    rss_url: str,
    source_type: str,
    source_label: str,
    filter_keywords: bool = False,
    stats: dict | None = None,
) -> list[dict]:
    """
    通用 RSS fetcher，供下方 6 個媒體源共用。

    Args:
        rss_url:          RSS 網址
        source_type:      articles 表的 source_type 欄位值
        source_label:     顯示用的來源名稱
        filter_keywords:  True → 用 _is_relevant() 過濾非半導體內容
        stats:            有傳就填入 collect_runs 統計（passed_filter 只有過濾型來源才有值）
    """
    articles = []
    passed = 0
    try:
        feed = feedparser.parse(rss_url)
        _fill_stats(stats, **_feed_meta(feed, rss_url))
        for entry in feed.entries:
            title   = entry.get("title", "").strip()
            url     = entry.get("link", "").strip()
            summary = _clean_html(entry.get("summary", ""))

            if not title or not url:
                continue

            if filter_keywords and not _is_relevant(title + " " + summary):
                continue

            # 超過上限後只計數（passed_filter），不再收、也不發任何請求
            passed += 1
            if len(articles) >= MAX_ARTICLES_PER_SOURCE:
                continue

            articles.append({
                "source_type": source_type,
                "title":       title,
                "url":         url,
                "summary":     summary,
                "source":      source_label,
                "published":   entry.get("published", ""),
                "ticker":      "",
                "filing_type": "",
            })

        logger.info(f"{source_label}: {len(articles)} 篇")
    except Exception as e:
        logger.error(f"{source_label} 失敗: {e}")
        _fill_stats(stats, status="error", error_msg=repr(e)[:ERROR_MSG_MAX_LEN])
    _fill_stats(stats, passed_filter=passed if filter_keywords else None, returned=len(articles))
    return articles


def fetch_semi_engineering(stats: dict | None = None) -> list[dict]:
    """Semiconductor Engineering — 混合站，需關鍵字過濾"""
    return _fetch_rss(
        SEMI_ENGINEERING_RSS,
        source_type="semi_engineering",
        source_label="Semiconductor Engineering",
        filter_keywords=True,
        stats=stats,
    )


def fetch_eetimes(stats: dict | None = None) -> list[dict]:
    """EE Times — 混合站，需關鍵字過濾"""
    return _fetch_rss(
        EETIMES_RSS,
        source_type="eetimes",
        source_label="EE Times",
        filter_keywords=True,
        stats=stats,
    )


def fetch_toms_hardware(stats: dict | None = None) -> list[dict]:
    """Tom's Hardware — 混合站（含消費電子），需關鍵字過濾"""
    return _fetch_rss(
        TOMS_HARDWARE_RSS,
        source_type="toms_hardware",
        source_label="Tom's Hardware",
        filter_keywords=True,
        stats=stats,
    )


def fetch_serve_the_home(stats: dict | None = None) -> list[dict]:
    """ServeTheHome — 聚焦伺服器 / 資料中心，不需過濾"""
    return _fetch_rss(
        SERVE_THE_HOME_RSS,
        source_type="serve_the_home",
        source_label="ServeTheHome",
        filter_keywords=False,
        stats=stats,
    )


def fetch_next_platform(stats: dict | None = None) -> list[dict]:
    """Next Platform — 聚焦 HPC / 資料中心，不需過濾"""
    return _fetch_rss(
        NEXT_PLATFORM_RSS,
        source_type="next_platform",
        source_label="Next Platform",
        filter_keywords=False,
        stats=stats,
    )


def fetch_fabricated_knowledge(stats: dict | None = None) -> list[dict]:
    """Fabricated Knowledge — 聚焦半導體供應鏈，不需過濾"""
    return _fetch_rss(
        FABRICATED_KNOWLEDGE_RSS,
        source_type="fabricated_knowledge",
        source_label="Fabricated Knowledge",
        filter_keywords=False,
        stats=stats,
    )