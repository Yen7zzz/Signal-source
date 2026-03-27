# ============================================================
# rescore_fallback.py — 對歷史 fallback 文章重新評分
#
# 用途：修復 GROQ_API_KEY 遺漏期間所有評分為 5 且 ai_summary
#       為空的文章（即 scorer.py 的 fallback 值）
#
# 執行一次即可，不加入日常 pipeline。
# 也可透過 .github/workflows/rescore.yml 手動觸發。
# ============================================================

import time
import logging
import sqlite3
import os

from groq import Groq
from scorer import score_article
from database import get_connection, update_article_ai
from config import GROQ_API_KEY

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

SLEEP_BETWEEN = 1   # 秒，避免 Groq rate limit


def fetch_fallback_articles() -> list[dict]:
    """撈出所有 ai_score=5 且 ai_summary 為空的文章（判定為 fallback）"""
    conn = get_connection()
    rows = conn.execute("""
        SELECT url, title, source, summary, full_content
        FROM   articles
        WHERE  ai_score = 5
          AND  source_type != 'tw_revenue'
          AND  (ai_summary IS NULL OR ai_summary = '')
        ORDER  BY created_at
    """).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def run():
    if not GROQ_API_KEY:
        print("❌ GROQ_API_KEY 未設定，中止。")
        return

    articles = fetch_fallback_articles()
    total    = len(articles)

    print(f"\n{'='*55}")
    print(f"🔄 Rescore Fallback — 共 {total} 篇需要重新評分")
    print(f"{'='*55}\n")

    if total == 0:
        print("✅ 沒有需要重評分的文章。")
        return

    client  = Groq(api_key=GROQ_API_KEY)
    updated = 0
    failed  = 0

    for i, article in enumerate(articles, 1):
        title = article.get("title", "")[:60]
        print(f"[{i:>3}/{total}] {title}...")

        score, key_point = score_article(client, article)

        if score != 5 or key_point:
            update_article_ai(
                url=article["url"],
                ai_score=score,
                ai_summary=key_point,
            )
            bar = "█" * score + "░" * (10 - score)
            print(f"         [{bar}] {score}/10 — {key_point[:45]}")
            updated += 1
        else:
            # 仍然是 5 且無摘要 → Groq 還是失敗，保留原樣
            print(f"         ⚠️  評分仍為 fallback，跳過更新")
            failed += 1

        if i < total:
            time.sleep(SLEEP_BETWEEN)

    print(f"\n{'='*55}")
    print(f"🎉 完成！更新 {updated} 篇，仍失敗 {failed} 篇")
    print(f"{'='*55}")
    logger.info(f"Rescore 完成：更新 {updated} 篇，失敗 {failed} 篇")


if __name__ == "__main__":
    run()
