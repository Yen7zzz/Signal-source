from difflib import SequenceMatcher

# Lower number = higher priority when deduplicating within a batch
SOURCE_PRIORITY = {
    "sec_edgar": 0,
    "semianalysis": 1,
    "fabricated_knowledge": 2,
}


def deduplicate_by_title(articles: list[dict], threshold: float = 0.6) -> list[dict]:
    """
    Deduplicate articles by title similarity within a batch.
    When two articles are similar, keeps the one from the higher-priority source
    (sec_edgar > semianalysis > fabricated_knowledge > others).
    Prints dropped pairs for debug.
    """
    def priority_key(a):
        return SOURCE_PRIORITY.get(a.get("source_type", ""), 99)

    sorted_articles = sorted(articles, key=priority_key)
    kept = []
    kept_pairs: list[tuple[str, str]] = []  # (normalized_title, original_title)

    for article in sorted_articles:
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
    """
    norm_db_pairs = [(t.lower().strip(), t) for t in db_titles if t]
    filtered = []

    for article in articles:
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
