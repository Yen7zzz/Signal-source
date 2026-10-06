import re
from difflib import SequenceMatcher

from config import TITLE_SIMILARITY_EXEMPT_PATTERNS

# Lower number = higher priority when deduplicating within a batch
SOURCE_PRIORITY = {
    "sec_edgar": 0,
    "semianalysis": 1,
    "fabricated_knowledge": 2,
}

# 標題是格式化字串（非自然語言）的來源：跳過標題相似度比對，一律放行。
# 去重已由 database.py 的 URL 唯一性（sec_edgar）/ 年月唯一性（tw_revenue）保證。
TITLE_SIMILARITY_EXEMPT_SOURCES = {"sec_edgar", "tw_revenue"}

# 固定格式的系列文（例如每週 Memory Spot Price Update）：依標題開頭豁免，清單在 config.py
_EXEMPT_TITLE_RES = [re.compile(p) for p in TITLE_SIMILARITY_EXEMPT_PATTERNS]


def _is_exempt(article: dict) -> bool:
    if article.get("source_type") in TITLE_SIMILARITY_EXEMPT_SOURCES:
        return True
    title = article.get("title", "")
    return any(r.match(title) for r in _EXEMPT_TITLE_RES)


def deduplicate_by_title(articles: list[dict], threshold: float = 0.6) -> list[dict]:
    """
    Deduplicate articles by title similarity within a batch.
    When two articles are similar, keeps the one from the higher-priority source
    (sec_edgar > semianalysis > fabricated_knowledge > others).
    Prints dropped pairs for debug.

    Articles from TITLE_SIMILARITY_EXEMPT_SOURCES, or whose title matches
    TITLE_SIMILARITY_EXEMPT_PATTERNS, are always kept as-is: their titles are
    formatted strings, not prose, so similarity comparison is meaningless for them.
    """
    def priority_key(a):
        return SOURCE_PRIORITY.get(a.get("source_type", ""), 99)

    sorted_articles = sorted(articles, key=priority_key)
    kept = []
    kept_pairs: list[tuple[str, str]] = []  # (normalized_title, original_title)

    for article in sorted_articles:
        if _is_exempt(article):
            kept.append(article)
            continue

        norm = article.get("title", "").lower().strip()
        best_ratio = 0.0
        matched_orig = None

        for kn, ko in kept_pairs:
            r = SequenceMatcher(None, norm, kn).ratio()
            if r > threshold and r > best_ratio:
                best_ratio = r
                matched_orig = ko

        if matched_orig:
            print(f"   [批次去重] {best_ratio:.2f} 「{article['title']}」≈「{matched_orig}」")
        else:
            kept.append(article)
            kept_pairs.append((norm, article.get("title", "")))

    return kept


def filter_against_db_titles(
    articles: list[dict], db_titles: list[str], threshold: float = 0.6
) -> list[dict]:
    """
    Filter out articles whose title is highly similar to recently stored DB titles.
    Prints dropped pairs for debug.

    Articles from TITLE_SIMILARITY_EXEMPT_SOURCES, or whose title matches
    TITLE_SIMILARITY_EXEMPT_PATTERNS, are always kept as-is: their titles are
    formatted strings, not prose, so similarity comparison is meaningless for them.
    """
    norm_db_pairs = [(t.lower().strip(), t) for t in db_titles if t]
    filtered = []

    for article in articles:
        if _is_exempt(article):
            filtered.append(article)
            continue

        norm = article.get("title", "").lower().strip()
        best_ratio = 0.0
        matched_db = None

        for nd, orig in norm_db_pairs:
            r = SequenceMatcher(None, norm, nd).ratio()
            if r > threshold and r > best_ratio:
                best_ratio = r
                matched_db = orig

        if matched_db:
            print(f"   [跨天去重] {best_ratio:.2f} 「{article['title']}」≈ DB「{matched_db}」")
        else:
            filtered.append(article)

    return filtered
