# ============================================================
# content_fetcher.py — 用 Jina AI Reader 抓取文章完整內文
#
# Jina AI Reader 的原理：
# 你把任何網址丟給它，它回傳乾淨的 Markdown 純文字
# 完全免費，無需 API Key，對大多數公開網頁有效
#
# 使用方式：https://r.jina.ai/{原始網址}
# 例如：https://r.jina.ai/https://www.trendforce.com/news/xxx
#
# 限制：
#   - 付費牆後面的內容抓不到（只會拿到登入頁）
#   - 有 rate limit，每篇之間要等一下
#   - 失敗時回傳空字串，不中斷主流程
# ============================================================

import requests
import logging
import time
from bs4 import BeautifulSoup
from config import SEC_USER_AGENT

logger = logging.getLogger(__name__)

JINA_BASE_URL = "https://r.jina.ai/"
JINA_HEADERS  = {
    "Accept":     "text/plain",   # 要求回傳純文字，不是 HTML
    "User-Agent": "Mozilla/5.0 (compatible; IndustryRadar/1.0)",
}

# 每篇文章抓取後等待的秒數（避免觸發 rate limit）
JINA_DELAY_SECONDS = 2

# 抓回來的內文最多保留幾個字（太長 Groq 會超出 context window）
MAX_CONTENT_LENGTH = 3000


def _fetch_sec_content(url: str) -> str:
    """
    SEC EDGAR 文件直接用 requests 抓取純文字
    必須使用 config.py 裡設定的 SEC_USER_AGENT，
    格式：「IndustryRadar your@email.com」
    SEC 會拒絕不符合格式的 User-Agent
    """
    sec_headers = {
        "User-Agent": SEC_USER_AGENT,
        "Accept":     "text/html, text/plain",
    }
    try:
        resp = requests.get(url, headers=sec_headers, timeout=20)
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "lxml")
        for tag in soup(["script", "style", "table"]):
            tag.decompose()

        text  = soup.get_text(separator="\n", strip=True)
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        text  = "\n".join(lines)

        if len(text) < 100:
            logger.warning(f"SEC 內文過短（{len(text)} 字）：{url}")
            return ""

        logger.info(f"SEC 內文抓取成功：{len(text)} 字")
        return text[:MAX_CONTENT_LENGTH]

    except Exception as e:
        logger.error(f"SEC 文件抓取失敗：{e} | URL: {url}")
        return ""


def fetch_full_content(url: str) -> str:
    """
    用 Jina AI Reader 抓取單篇文章的完整內文

    Args:
        url: 文章原始連結

    Returns:
        乾淨的 Markdown 純文字，失敗時回傳空字串
    """
    # SEC EDGAR 文件改用專屬抓取函式，不走 Jina
    if "sec.gov" in url:
        return _fetch_sec_content(url)

    jina_url = f"{JINA_BASE_URL}{url}"

    try:
        resp = requests.get(jina_url, headers=JINA_HEADERS, timeout=20)
        resp.raise_for_status()

        content = resp.text.strip()

        # 如果內容太短，可能是被擋了（登入頁、錯誤頁）
        if len(content) < 100:
            logger.warning(f"內文過短，可能是付費牆：{url}")
            return ""

        # 截斷到最大長度，避免超出 Groq context window
        return content[:MAX_CONTENT_LENGTH]

    except requests.exceptions.Timeout:
        logger.warning(f"Jina 抓取逾時：{url}")
        return ""
    except Exception as e:
        logger.error(f"Jina 抓取失敗：{e} | URL: {url}")
        return ""
    finally:
        # 不管成功失敗，都等一下再繼續
        time.sleep(JINA_DELAY_SECONDS)


def batch_fetch(articles: list[dict]) -> list[dict]:
    """
    批次抓取一批文章的完整內文

    Args:
        articles: save_article 格式的文章清單

    Returns:
        同一份清單，每筆新增 full_content 欄位
    """
    total = len(articles)
    for i, article in enumerate(articles):
        url   = article.get("url", "")
        title = article.get("title", "")[:50]

        print(f"   🌐 [{i+1}/{total}] 抓取內文：{title}...")
        content = fetch_full_content(url)

        if content:
            article["full_content"] = content
            print(f"      ✅ 抓到 {len(content)} 字")
        else:
            article["full_content"] = ""
            print(f"      ⚠️  無法抓取（付費牆或逾時）")

    return articles