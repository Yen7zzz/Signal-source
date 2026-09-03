# ============================================================
# pipeline_collect.py — 每天執行：抓資料 → 規則式評分 → 存 DB → 非 junk 才抓全文
#
# 流程：
#   scraper 抓標題+摘要 → 存 DB
#   → 規則式評分（is_junk + score_by_rules，純標題比對，無 API 呼叫）
#   → update DB rule_score / is_junk（ai_score / ai_summary 對新文章一律留 NULL）
#   → not junk 的文章才用 Jina 抓全文
#   → update DB full_content
# ============================================================

import logging
import os
from datetime import datetime
from database import (
    init_db, save_article, article_exists,
    save_tw_revenue, get_recent_titles, update_content_completeness,
    update_rule_score, update_full_content,
)
from deduplicator import deduplicate_by_title, filter_against_db_titles
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
from content_fetcher import batch_fetch, classify_completeness
from rule_scorer import is_junk, score_by_rules

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
    # 第三欄 skip_ai：True 表示跳過 Jina 全文抓取 + 規則式評分（月營收已自行計算分數）
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

    total_fetched    = 0
    total_new        = 0
    all_new_articles = []  # 非 skip_ai 來源統一收集，後續批次去重+評分

    for source_name, fetch_fn, skip_ai in sources:
        print(f"\n{'─'*40}")
        print(f"📥 抓取 {source_name}...")

        try:
            articles = fetch_fn()
            print(f"   取得 {len(articles)} 篇")

            # URL 去重：過濾掉已存在的文章
            new_articles = [a for a in articles if not article_exists(a["url"])]
            total_fetched += len(articles)
            print(f"   新文章：{len(new_articles)} 篇（重複跳過 {len(articles) - len(new_articles)} 篇）")

            if not new_articles:
                continue

            if skip_ai:
                # 台股月營收：維持現有邏輯，直接存入 DB，不做評分或全文抓取
                saved = 0
                for article in new_articles:
                    revenue_meta = None
                    if article.get("source_type") == "tw_revenue":
                        revenue_meta = {
                            k[1:]: article.pop(k)
                            for k in ["_stock_name", "_year", "_month",
                                      "_revenue", "_yoy_pct", "_mom_pct"]
                        }

                    success = save_article(**article)
                    if success:
                        saved += 1
                        total_new += 1

                    if revenue_meta:
                        save_tw_revenue(
                            stock_id=article["ticker"],
                            **revenue_meta,
                        )
                print(f"\n   ✅ 新增 {saved} 篇進資料庫")
            else:
                # 收集到統一 list，批次去重後再統一評分
                all_new_articles.extend(new_articles)
                print(f"   📋 收集 {len(new_articles)} 篇待批次去重")

        except Exception as e:
            logging.error(f"{source_name} 執行失敗: {e}")
            print(f"   ❌ {source_name} 失敗：{e}")

    # ── 批次處理所有非 skip_ai 來源 ──────────────────────────
    if all_new_articles:
        print(f"\n{'─'*40}")
        print(f"🔍 標題去重（共 {len(all_new_articles)} 篇）...")

        # 跨天去重：過濾與 DB 近 7 天既有文章標題高度相似的
        db_titles = get_recent_titles(days=7)
        print(f"   對比 DB 近 7 天 {len(db_titles)} 篇既有標題")
        before_cross = len(all_new_articles)
        all_new_articles = filter_against_db_titles(all_new_articles, db_titles)
        print(f"   跨天去重：移除 {before_cross - len(all_new_articles)} 篇")

        # 批次內去重：同批中相似標題保留優先級較高的來源
        before_intra = len(all_new_articles)
        all_new_articles = deduplicate_by_title(all_new_articles)
        print(f"   批次內去重：移除 {before_intra - len(all_new_articles)} 篇，剩 {len(all_new_articles)} 篇")

        if all_new_articles:
            # Step 1：存入 DB
            saved = 0
            for article in all_new_articles:
                success = save_article(**article)
                if success:
                    saved += 1
                    total_new += 1
            print(f"\n   ✅ 新增 {saved} 篇進資料庫")

            # Step 2：規則式評分（純標題比對，不呼叫任何 AI API）
            print(f"\n   📐 規則式評分中...")
            score_dist = {i: 0 for i in range(1, 11)}
            junk_count = 0
            not_junk_count = 0
            for article in all_new_articles:
                junk = is_junk(article["title"], article["source_type"])
                rule_score = score_by_rules(article["title"], article["source_type"])
                article["_rule_score"] = rule_score
                article["_is_junk"] = junk
                score_dist[rule_score] += 1
                if junk:
                    junk_count += 1
                else:
                    not_junk_count += 1

            # Step 3：將評分寫回 DB（ai_score / ai_summary 對新文章一律留 NULL，不寫入）
            for article in all_new_articles:
                update_rule_score(article["url"], article["_rule_score"], article["_is_junk"])

            print(f"      junk：{junk_count} 篇 | 非 junk：{not_junk_count} 篇")
            dist_str = " | ".join(f"{s}分:{score_dist[s]}" for s in range(1, 11) if score_dist[s] > 0)
            print(f"      rule_score 分布：{dist_str}")

            # Step 4：非 junk 的文章才用 Jina 抓全文（LLM token 成本已消失，全文覆蓋率才是瓶頸）
            not_junk_articles = [a for a in all_new_articles if not a["_is_junk"]]
            if not_junk_articles:
                print(f"\n   🌐 Jina AI 抓取非 junk 文章全文（{len(not_junk_articles)}/{len(all_new_articles)} 篇）...")
                fetched = batch_fetch(not_junk_articles)
                for article in fetched:
                    if article.get("full_content"):
                        update_full_content(article["url"], article["full_content"])

            # Step 5：標記 content_completeness（all_new_articles 已含 full_content by batch_fetch）
            cc_stats = {"full": 0, "partial": 0, "headline_only": 0}
            for article in all_new_articles:
                status = classify_completeness(
                    article.get("full_content", ""),
                    article.get("summary", ""),
                )
                update_content_completeness(article["url"], status)
                cc_stats[status] += 1
            print(
                f"\n   📊 內容完整度："
                f"full {cc_stats['full']} | "
                f"partial {cc_stats['partial']} | "
                f"headline_only {cc_stats['headline_only']}"
            )

    print(f"\n{'='*55}")
    print(f"🎉 完成！")
    print(f"   抓取：{total_fetched} 篇")
    print(f"   新增：{total_new} 篇")
    print(f"{'='*55}")
    logging.info(f"Pipeline Collect 完成，抓取 {total_fetched} 篇，新增 {total_new} 篇")


if __name__ == "__main__":
    run()