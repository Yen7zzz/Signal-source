# ============================================================
# scraper.py — 四個精準源頭的抓取邏輯
#
# 各源頭抓取策略：
#   SemiAnalysis  → Substack RSS（feedparser，最簡單）
#   TrendForce    → 網頁爬蟲（requests + BeautifulSoup）
#   DIGITIMES     → RSS（feedparser）
#   SEC EDGAR     → 官方 REST API（最穩定，官方支援）
#   Seeking Alpha → RSS（feedparser）
#
# 設計原則：
#   每個函式獨立，失敗不影響其他源頭
#   summary 一律保留原文，不讓 AI 改寫（讓 Gemini 自己分析）
#   所有函式回傳相同格式的 list[dict]，方便 pipeline_collect 統一處理
# ============================================================

import feedparser
import requests
import logging
import re
import time
from datetime import datetime
from bs4 import BeautifulSoup
from config import (
    SEMIANALYSIS_RSS,
    TRENDFORCE_NEWS_URL, TRENDFORCE_KEYWORDS,
    DIGITIMES_RSS,
    SEEKING_ALPHA_RSS,
    WATCHLIST,
    SEC_FILING_TYPES, SEC_USER_AGENT,
    MAX_ARTICLES_PER_SOURCE,
)

logger = logging.getLogger(__name__)

# ── 共用 HTTP Headers ─────────────────────────────────────────
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; IndustryRadar/1.0)",
    "Accept":     "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


def _clean_html(text: str, max_len: int = 500) -> str:
    """移除 HTML tag，截取前 max_len 字"""
    return re.sub(r"<[^>]+>", "", text or "").strip()[:max_len]


# ── 1. SemiAnalysis ───────────────────────────────────────────
def fetch_semianalysis() -> list[dict]:
    """
    透過 Substack RSS 抓取 SemiAnalysis 的公開文章
    
    注意：付費文章只會顯示標題，沒有摘要
    這沒關係——標題本身就有情報價值，可以觸發你手動去讀
    """
    articles = []
    try:
        feed = feedparser.parse(SEMIANALYSIS_RSS)
        for entry in feed.entries[:MAX_ARTICLES_PER_SOURCE]:
            title   = entry.get("title", "").strip()
            url     = entry.get("link", "").strip()
            summary = _clean_html(entry.get("summary", ""))

            if title and url:
                articles.append({
                    "source_type": "semianalysis",
                    "title":       title,
                    "url":         url,
                    "summary":     summary,
                    "source":      "SemiAnalysis (Dylan Patel)",
                    "published":   entry.get("published", ""),
                    "ticker":      "",
                    "filing_type": "",
                })

        logger.info(f"SemiAnalysis: 抓到 {len(articles)} 篇")
    except Exception as e:
        logger.error(f"SemiAnalysis 抓取失敗: {e}")

    return articles


# ── 2. TrendForce ─────────────────────────────────────────────
def fetch_trendforce() -> list[dict]:
    """
    爬取 TrendForce 公開新聞頁，並用關鍵字過濾相關文章
    
    TrendForce 網站結構相對穩定，但如果某天格式改了會抓不到
    失敗時系統繼續跑，只是這個源頭當天沒資料
    """
    articles = []
    try:
        resp = requests.get(TRENDFORCE_NEWS_URL, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        # TrendForce 新聞列表的文章連結
        # 格式：/news/xxxxxx.html
        news_links = soup.select("a[href*='/news/']")
        seen_urls  = set()

        for link in news_links:
            title = link.get_text(strip=True)
            href  = link.get("href", "")

            # 過濾掉非文章連結（導航、分類頁等）
            if not title or len(title) < 10:
                continue

            # 補完整 URL
            if href.startswith("/"):
                url = f"https://www.trendforce.com{href}"
            elif href.startswith("http"):
                url = href
            else:
                continue

            if url in seen_urls:
                continue
            seen_urls.add(url)

            # 關鍵字過濾：標題包含任一關鍵字才保留
            title_lower = title.lower()
            if not any(kw.lower() in title_lower for kw in TRENDFORCE_KEYWORDS):
                continue

            articles.append({
                "source_type": "trendforce",
                "title":       title,
                "url":         url,
                "summary":     "",      # TrendForce 列表頁沒有摘要，留空
                "source":      "TrendForce",
                "published":   datetime.now().strftime("%Y-%m-%d"),
                "ticker":      "",
                "filing_type": "",
            })

            if len(articles) >= MAX_ARTICLES_PER_SOURCE:
                break

        logger.info(f"TrendForce: 抓到 {len(articles)} 篇")
    except Exception as e:
        logger.error(f"TrendForce 抓取失敗: {e}")

    return articles


# ── 3. DIGITIMES ──────────────────────────────────────────────
def fetch_digitimes() -> list[dict]:
    """透過 RSS 抓取 DIGITIMES 公開文章"""
    articles = []
    try:
        feed = feedparser.parse(DIGITIMES_RSS)
        for entry in feed.entries[:MAX_ARTICLES_PER_SOURCE]:
            title   = entry.get("title", "").strip()
            url     = entry.get("link", "").strip()
            summary = _clean_html(entry.get("summary", ""))

            if title and url:
                articles.append({
                    "source_type": "digitimes",
                    "title":       title,
                    "url":         url,
                    "summary":     summary,
                    "source":      "DIGITIMES",
                    "published":   entry.get("published", ""),
                    "ticker":      "",
                    "filing_type": "",
                })

        logger.info(f"DIGITIMES: 抓到 {len(articles)} 篇")
    except Exception as e:
        logger.error(f"DIGITIMES 抓取失敗: {e}")

    return articles


# ── 4. SEC EDGAR ──────────────────────────────────────────────
def _get_sec_filings(ticker: str, company_info: dict) -> list[dict]:
    """
    用 SEC EDGAR REST API 抓取單一公司的最新法說會與季報
    
    API 文件：https://efts.sec.gov/LATEST/search-index?q=...
    完全免費，官方支援，不會突然掛掉
    
    SEC 要求 User-Agent 包含聯絡 email，否則會被 rate limit
    """
    cik = company_info.get("sec_cik", "")
    if not cik:
        return []   # 無 CIK 的公司（如 SK Hynix）跳過

    articles = []
    sec_headers = {"User-Agent": SEC_USER_AGENT}

    try:
        # 用 CIK 查最新 filings
        # EDGAR API 的 CIK 格式：去掉前綴零，例如 "0001045810" → "1045810"
        cik_clean = str(int(cik))
        url = (
            f"https://data.sec.gov/submissions/CIK{cik_clean.zfill(10)}.json"
        )

        resp = requests.get(url, headers=sec_headers, timeout=15)
        resp.raise_for_status()
        data = resp.json()

        recent = data.get("filings", {}).get("recent", {})
        forms       = recent.get("form", [])
        dates       = recent.get("filingDate", [])
        accessions  = recent.get("accessionNumber", [])
        descriptions= recent.get("primaryDocument", [])

        company_name = company_info["name"]

        for form, date, accession, doc in zip(forms, dates, accessions, descriptions):
            if form not in SEC_FILING_TYPES:
                continue

            # 組出 EDGAR 文件連結
            accession_clean = accession.replace("-", "")
            filing_url = (
                f"https://www.sec.gov/Archives/edgar/full-index/"
                f"{date[:4]}/{date[5:7]}/{accession_clean}/{doc}"
            )

            # 直接用 EDGAR viewer 連結，比較好讀
            viewer_url = (
                f"https://www.sec.gov/cgi-bin/browse-edgar?"
                f"action=getcompany&CIK={cik_clean}"
                f"&type={form}&dateb=&owner=include&count=10"
            )

            articles.append({
                "source_type": "sec_edgar",
                "title":       f"[{form}] {company_name} — {date}",
                "url":         viewer_url,
                "summary":     f"{company_name} 提交 {form} 表單，日期：{date}",
                "source":      "SEC EDGAR",
                "published":   date,
                "ticker":      ticker,
                "filing_type": form,
            })

            # 每間公司最多抓 3 份文件（避免季報太多)
            if len(articles) >= 3:
                break

        # SEC 有 rate limit，每間公司之間等一下
        time.sleep(0.5)

        logger.info(f"SEC EDGAR [{ticker}]: 抓到 {len(articles)} 份文件")
    except Exception as e:
        logger.error(f"SEC EDGAR [{ticker}] 抓取失敗: {e}")

    return articles


def fetch_sec_edgar() -> list[dict]:
    """
    批次抓取 WATCHLIST 所有公司的 SEC 文件
    只處理有 sec_cik 的公司
    """
    all_articles = []
    for ticker, info in WATCHLIST.items():
        if info.get("sec_cik"):
            filings = _get_sec_filings(ticker, info)
            all_articles.extend(filings)
    return all_articles


# ── 5. Seeking Alpha ──────────────────────────────────────────
def fetch_seeking_alpha() -> list[dict]:
    """
    透過 RSS 抓取 Seeking Alpha 的免費文章
    
    注意：Seeking Alpha RSS 主要是一般財經新聞
    真正有價值的法說會逐字稿是在 SEC EDGAR（上面已處理）
    這裡抓的是補充性的市場分析文章
    """
    articles = []
    try:
        feed = feedparser.parse(SEEKING_ALPHA_RSS)

        # 用 WATCHLIST 的公司名稱做過濾
        company_names = [info["name"].lower() for info in WATCHLIST.values()]
        tickers_lower = [t.lower() for t in WATCHLIST.keys()]

        for entry in feed.entries:
            title   = entry.get("title", "").strip()
            url     = entry.get("link", "").strip()
            summary = _clean_html(entry.get("summary", ""))

            if not title or not url:
                continue

            # 只保留跟 WATCHLIST 公司相關的文章
            title_lower = title.lower()
            is_relevant = (
                any(name in title_lower for name in company_names) or
                any(t in title_lower for t in tickers_lower)
            )

            if not is_relevant:
                continue

            articles.append({
                "source_type": "seeking_alpha",
                "title":       title,
                "url":         url,
                "summary":     summary,
                "source":      "Seeking Alpha",
                "published":   entry.get("published", ""),
                "ticker":      "",
                "filing_type": "",
            })

            if len(articles) >= MAX_ARTICLES_PER_SOURCE:
                break

        logger.info(f"Seeking Alpha: 抓到 {len(articles)} 篇")
    except Exception as e:
        logger.error(f"Seeking Alpha 抓取失敗: {e}")

    return articles
