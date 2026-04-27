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

import re
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


_JINA_META_RE   = re.compile(r'^(Title|URL Source|Published Time|Markdown Content):.*$', re.MULTILINE)
_NAV_LINK_RE    = re.compile(r'^\s*\[.+?\]\(.+?\)\s*$')
_BULLET_LINK_RE = re.compile(r'^\s*\*\s*\[.+?\]\(.+?\)\s*$')  # * [text](url) 純 bullet 連結
_MULTI_LINK_RE  = re.compile(r'\[.*?\]\(.*?\)')                # 用於計算行內連結數
_IMAGE_RE       = re.compile(r'!\[.*?\]\(.*?\)')
_NOISE_KEYWORDS = re.compile(
    r'Advertisement|\s+AD\b|SUBSCRIBE|Sign in|My account|Skip to main content'
    r'|Trending Issues|AspenCore|MULTIMEDIA|EVENT\+'
    # Footer / site chrome
    r'|Share this article|Related stories|Popular Keywords'
    r'|Member Center|SIGN OUT|NEXTPLATFORM AD|cnv\.asp',
    re.IGNORECASE,
)
# Footer labels that appear as standalone lines ("Categories", "Tags", "Companies")
_FOOTER_EXACT_RE = re.compile(
    r'^\s*(Categories|Tags|Companies)\s*$',
    re.IGNORECASE,
)
# DIGITIMES realtime-news sidebar trigger
_REALTIME_NEWS_RE = re.compile(r'REALTIME\s+NEWS', re.IGNORECASE)
# Login / paywall form keywords
_LOGIN_KW_RE = re.compile(
    r'Email address|\bPassword\b|Keep me signed in|\bSign in\b'
    r'|Create account|Enterprise first-time login|Forgot password',
    re.IGNORECASE,
)
_INLINE_EMPTY_LINK_RE = re.compile(r'!?\[\]\([^)]*\)')  # [](url) 或 ![](url)
_ANY_LINK_RE          = re.compile(r'\[.+?\]\(.+?\)')   # 行內是否含有連結（非空）


def _paragraph_link_density_filter(lines: list[str]) -> list[str]:
    """
    段落級連結密度過濾：將行切成段落，
    若段落有 ≥4 非空行且 >60% 含有 [text](url)，整段丟棄。
    針對 EE Times 類型的 promo block（文字行 + 連結行交替）。
    """
    result   = []
    paragraph = []

    def _flush(para: list[str]) -> None:
        non_empty = [l for l in para if l.strip()]
        if len(non_empty) >= 4:
            link_lines = sum(1 for l in non_empty if _ANY_LINK_RE.search(l))
            if link_lines / len(non_empty) > 0.6:
                return  # 高密度段落，整段捨棄
        result.extend(para)

    for line in lines:
        if line.strip() == '':
            _flush(paragraph)
            paragraph = []
            result.append(line)
        else:
            paragraph.append(line)
    _flush(paragraph)
    return result


def _remove_realtime_news_blocks(lines: list[str]) -> list[str]:
    """
    Remove DIGITIMES "REALTIME NEWS" sidebar blocks.
    From the trigger line, remove everything until 2+ consecutive empty lines
    or a long paragraph (>100 chars), whichever comes first.
    """
    result: list[str] = []
    i = 0
    while i < len(lines):
        if _REALTIME_NEWS_RE.search(lines[i]):
            i += 1
            consecutive_empty = 0
            while i < len(lines):
                stripped = lines[i].strip()
                if not stripped:
                    consecutive_empty += 1
                    if consecutive_empty >= 2:
                        break
                    i += 1
                elif len(stripped) > 100:
                    break
                else:
                    consecutive_empty = 0
                    i += 1
        else:
            result.append(lines[i])
            i += 1
    return result


def _remove_login_blocks(lines: list[str]) -> list[str]:
    """
    Remove login / paywall form blocks.
    When 3+ login-keyword lines are found in a contiguous window (separated only by
    blank lines or short non-keyword lines), the entire window is dropped.
    A long paragraph (>100 chars) or 3+ consecutive non-keyword lines ends the window.
    """
    result: list[str] = []
    i = 0
    while i < len(lines):
        if not _LOGIN_KW_RE.search(lines[i]):
            result.append(lines[i])
            i += 1
            continue

        j = i
        login_count = 0
        consecutive_non_login = 0
        while j < len(lines):
            stripped = lines[j].strip()
            if _LOGIN_KW_RE.search(lines[j]):
                login_count += 1
                consecutive_non_login = 0
                j += 1
            elif not stripped:
                j += 1  # blank lines don't break the window
            elif len(stripped) > 100:
                break   # real paragraph ends the window
            else:
                consecutive_non_login += 1
                if consecutive_non_login >= 3:
                    break
                j += 1

        if login_count >= 3:
            i = j   # drop the block
        else:
            result.extend(lines[i:j])
            i = j
    return result


def _clean_jina_content(raw: str) -> str:
    # 0. inline 清除空 markdown 連結 [](url) / ![](url)
    text = _INLINE_EMPTY_LINK_RE.sub('', raw)

    # 1. Jina meta header
    text = _JINA_META_RE.sub('', text)

    # 2. 導航列：連續 ≥3 行純連結的區塊整段移除
    lines  = text.splitlines()
    result = []
    buffer = []
    for line in lines:
        if _NAV_LINK_RE.match(line):
            buffer.append(line)
        else:
            if len(buffer) < 3:
                result.extend(buffer)
            buffer = []
            result.append(line)
    if len(buffer) < 3:
        result.extend(buffer)

    # 2b. 段落級連結密度過濾（EE Times 類型 promo block）
    result = _paragraph_link_density_filter(result)

    # 3. DIGITIMES REALTIME NEWS 側邊欄整段移除
    result = _remove_realtime_news_blocks(result)

    # 4. 登入 / 付費牆表單整段移除
    result = _remove_login_blocks(result)

    # 5. 廣告/噪訊關鍵字 + 頁尾 label + bullet 連結 + 多連結行 + 圖片 markdown
    cleaned = []
    for line in result:
        if _NOISE_KEYWORDS.search(line):
            continue
        if _FOOTER_EXACT_RE.match(line):
            continue
        if _BULLET_LINK_RE.match(line):
            continue
        if len(_MULTI_LINK_RE.findall(line)) >= 3:
            continue
        cleaned.append(_IMAGE_RE.sub('', line))

    text = '\n'.join(cleaned)

    # 6. 連續空行壓縮
    text = re.sub(r'\n{3,}', '\n\n', text).strip()

    # 7. 清理後內容不足 50 字視為無效
    if len(text) < 50:
        return ''
    return text


def classify_completeness(full_content: str, summary: str = "") -> str:
    """
    Classify how complete an article's content is.
    - full:          full_content >= 500 chars (real article body)
    - partial:       full_content 50–499 chars (paywall truncation)
    - headline_only: full_content absent or < 50 chars
    """
    length = len((full_content or "").strip())
    if length >= 500:
        return "full"
    if length >= 50:
        return "partial"
    return "headline_only"


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

        content = _clean_jina_content(resp.text.strip())

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