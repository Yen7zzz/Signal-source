# ============================================================
# pipeline_collect.py — 每天執行：抓各源頭資料 → 存入資料庫
#
# 對應 Signal-Flow 的 pipeline_a.py
# 差異：沒有 Transformer 分類，資料源換成精準的產業源頭
# ============================================================

import logging
import os
from datetime import datetime
from database import init_db, save_article, article_exists
from scraper import (
    fetch_semianalysis,
    fetch_trendforce,
    fetch_digitimes,
    fetch_sec_edgar,
    fetch_seeking_alpha,
)

os.makedirs("logs", exist_ok=True)

logging.basicConfig(
    filename="logs/pipeline_collect.log",
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)


def run():
    print(f"\n{'='*50}")
    print(f"📡 Pipeline Collect 開始 — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*50}")

    init_db()

    # 所有資料源統一在這裡定義
    # 格式：(顯示名稱, fetch 函式)
    sources = [
        ("SemiAnalysis",  fetch_semianalysis),
        ("TrendForce",    fetch_trendforce),
        ("DIGITIMES",     fetch_digitimes),
        ("SEC EDGAR",     fetch_sec_edgar),
        ("Seeking Alpha", fetch_seeking_alpha),
    ]

    total_fetched = 0
    total_saved   = 0

    for source_name, fetch_fn in sources:
        print(f"\n📥 抓取 {source_name}...")
        try:
            articles = fetch_fn()
            total_fetched += len(articles)
            print(f"   取得 {len(articles)} 篇")

            saved = 0
            for article in articles:
                if not article_exists(article["url"]):
                    success = save_article(**article)
                    if success:
                        saved += 1
                        total_saved += 1
                        print(f"   ✅ {article['title'][:65]}...")

            print(f"   新增 {saved} 篇（重複跳過 {len(articles) - saved} 篇）")

        except Exception as e:
            # 單一源頭失敗不中斷整個流程
            logging.error(f"{source_name} 執行失敗: {e}")
            print(f"   ❌ {source_name} 失敗：{e}")

    print(f"\n{'='*50}")
    print(f"🎉 完成！抓取 {total_fetched} 篇，新增 {total_saved} 篇")
    print(f"{'='*50}")
    logging.info(f"Pipeline Collect 完成，抓取 {total_fetched} 篇，新增 {total_saved} 篇")


if __name__ == "__main__":
    run()
