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
from database import init_db, save_article, article_exists, update_article_ai, save_tw_revenue
from scraper import (
    fetch_semianalysis,
    fetch_trendforce,
    fetch_digitimes,
    fetch_sec_edgar,
    fetch_semi_engineering,
    fetch_eetimes,
    fetch_toms_hardware,
    fetch_serve_the_home,
    fetch_next_platform,
    fetch_fabricated_knowledge,
)
from scraper_twstock import fetch_tw_revenue_all
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
    # 第三欄 skip_ai：True 表示跳過 Jina + Groq（月營收已自行計算分數）
    sources = [
        ("SemiAnalysis",           fetch_semianalysis,          False),
        ("TrendForce",             fetch_trendforce,             False),
        ("DIGITIMES",              fetch_digitimes,              False),
        ("SEC EDGAR",              fetch_sec_edgar,              False),
        ("Semiconductor Eng.",     fetch_semi_engineering,       False),
        ("EE Times",               fetch_eetimes,                False),
        ("Tom's Hardware",         fetch_toms_hardware,          False),
        ("ServeTheHome",           fetch_serve_the_home,         False),
        ("Next Platform",          fetch_next_platform,          False),
        ("Fabricated Knowledge",   fetch_fabricated_knowledge,   False),
        ("台股月營收",             fetch_tw_revenue_all,         True),
    ]

    total_fetched = 0
    total_new     = 0

    for source_name, fetch_fn, skip_ai in sources:
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

            if not skip_ai:
                # Step 1：Jina AI 抓完整內文
                print(f"\n   🌐 Jina AI 抓取完整內文...")
                new_articles = batch_fetch(new_articles)

                # Step 2：Groq 評分
                print(f"\n   🤖 Groq AI 評分中...")
                new_articles = batch_score(new_articles)

            # Step 3：存入資料庫
            saved = 0
            for article in new_articles:
                # 月營收：先抽出結構化欄位（_*），再存 articles 表
                revenue_meta = None
                if article.get("source_type") == "tw_revenue":
                    revenue_meta = {
                        k[1:]: article.pop(k)   # 去掉前綴底線
                        for k in ["_stock_name", "_year", "_month",
                                  "_revenue", "_yoy_pct", "_mom_pct"]
                    }

                success = save_article(**article)
                if success:
                    saved += 1
                    total_new += 1

                # 同時存結構化資料表（INSERT OR IGNORE，不怕重跑）
                if revenue_meta:
                    save_tw_revenue(
                        stock_id=article["ticker"],
                        **revenue_meta,
                    )

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