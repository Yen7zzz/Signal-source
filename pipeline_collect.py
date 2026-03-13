# ============================================================
# pipeline_collect.py — 每天執行：抓資料 → 抓全文 → AI 評分 → 存 DB
#
# 對應 Signal-Flow 的 pipeline_a.py
# 新增流程：
#   scraper 抓標題+摘要
#   → content_fetcher 用 Jina AI 抓完整內文
#   → scorer 用 Groq 評分（1-10）+ 一句話重點
#   → 存入資料庫
# ============================================================

import logging
import os
from datetime import datetime
from database import init_db, save_article, article_exists, update_article_ai
from scraper import (
    fetch_semianalysis,
    fetch_trendforce,
    fetch_digitimes,
    fetch_sec_edgar,
)
from content_fetcher import batch_fetch
from scorer import batch_score

os.makedirs("logs", exist_ok=True)

logging.basicConfig(
    filename="logs/pipeline_collect.log",
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)


def run():
    print(f"\n{'='*55}")
    print(f"📡 Pipeline Collect 開始 — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*55}")

    init_db()

    # 所有資料源統一在這裡定義
    sources = [
        ("SemiAnalysis",  fetch_semianalysis),
        ("TrendForce",    fetch_trendforce),
        ("DIGITIMES",     fetch_digitimes),
        ("SEC EDGAR",     fetch_sec_edgar),
    ]

    total_fetched = 0
    total_new     = 0

    for source_name, fetch_fn in sources:
        print(f"\n{'─'*40}")
        print(f"📥 抓取 {source_name}...")

        try:
            articles = fetch_fn()
            print(f"   取得 {len(articles)} 篇")

            # 過濾掉已存在的文章（去重），只處理新文章
            new_articles = [a for a in articles if not article_exists(a["url"])]
            total_fetched += len(articles)
            print(f"   新文章：{len(new_articles)} 篇（重複跳過 {len(articles) - len(new_articles)} 篇）")

            if not new_articles:
                continue

            # Step 1：Jina AI 抓完整內文
            print(f"\n   🌐 Jina AI 抓取完整內文...")
            new_articles = batch_fetch(new_articles)

            # Step 2：Groq 評分
            print(f"\n   🤖 Groq AI 評分中...")
            new_articles = batch_score(new_articles)

            # Step 3：存入資料庫
            saved = 0
            for article in new_articles:
                success = save_article(**article)
                if success:
                    saved += 1
                    total_new += 1

            print(f"\n   ✅ 新增 {saved} 篇進資料庫")

        except Exception as e:
            # 單一源頭失敗不中斷整個流程
            logging.error(f"{source_name} 執行失敗: {e}")
            print(f"   ❌ {source_name} 失敗：{e}")

    print(f"\n{'='*55}")
    print(f"🎉 完成！")
    print(f"   抓取：{total_fetched} 篇")
    print(f"   新增：{total_new} 篇")
    print(f"{'='*55}")
    logging.info(f"Pipeline Collect 完成，抓取 {total_fetched} 篇，新增 {total_new} 篇")


if __name__ == "__main__":
    run()