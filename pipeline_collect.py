# ============================================================
# pipeline_collect.py — 每天執行：抓資料 → 規則式評分 → 存 DB → 非 junk 才抓全文
#
# 流程：
#   scraper 抓標題+摘要 → 存 DB
#   → 規則式評分（is_junk + score_by_rules，純標題比對，無 API 呼叫）
#   → update DB rule_score / is_junk（ai_score / ai_summary 對新文章一律留 NULL）
#   → not junk 的文章才用 Jina 抓全文
#   → update DB full_content
#   → 每個來源寫一列 collect_runs（抓取統計，供來源健康檢查；在 finally 統一寫入）
# ============================================================

import logging
import os
from collections import Counter
from datetime import datetime, timezone
from database import (
    init_db, save_article, article_exists, get_urls_by_source,
    save_tw_revenue, get_recent_titles, update_content_completeness,
    update_rule_score, update_full_content, save_collect_runs,
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
    ERROR_MSG_MAX_LEN,
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


def _new_run_row(run_at: str, source_type: str) -> dict:
    """collect_runs 一列的預設值；fetcher 透過 stats 填入抓取統計，其餘由 pipeline 補上"""
    return {
        "run_at": run_at, "source_type": source_type, "status": "ok", "error_msg": None,
        "http_status": None, "redirect_to": None, "raw_count": None, "passed_filter": None,
        "returned": None, "after_url_dedup": None, "after_title_dedup": None, "inserted": None,
        "feed_newest_published": None, "feed_oldest_published": None,
    }


def _count_only(fetch_fn):
    """不接 stats 的 fetcher（SEC EDGAR、台股月營收）：只記回傳篇數"""
    def wrapped(stats: dict) -> list[dict]:
        articles = fetch_fn()
        stats["raw_count"] = stats["returned"] = len(articles)
        return articles
    return wrapped


def run():
    print(f"\n{'='*55}")
    print(f"📡 Pipeline Collect 開始 — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*55}")

    init_db()

    # 同一次執行的所有 collect_runs 列共用 run_at，格式與 created_at 相同（UTC）
    run_at   = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    run_rows = {}  # source_type → collect_runs 一列

    try:
        _collect(run_at, run_rows)
    finally:
        # 不論中途是否出錯都寫入；寫入失敗只 log，不中斷 collect
        if run_rows and not save_collect_runs(list(run_rows.values())):
            print("   ⚠️ collect_runs 寫入失敗（詳見 log）")


def _collect(run_at: str, run_rows: dict):
    # 所有資料源統一在這裡定義，fetch_fn 都接收 stats dict（collect_runs 統計）
    # 第四欄 skip_ai：True 表示跳過 Jina 全文抓取 + 規則式評分（月營收已自行計算分數）
    sources = [
        ("SemiAnalysis",           "semianalysis",         fetch_semianalysis,          False),
        # TrendForce 傳入已入庫 URL，在抓文章頁摘要前就先跳過
        ("TrendForce",             "trendforce",
            lambda stats: fetch_trendforce(known_urls=get_urls_by_source("trendforce"), stats=stats), False),
        ("DIGITIMES",              "digitimes",            fetch_digitimes,             False),
        ("SEC EDGAR",              "sec_edgar",            _count_only(fetch_sec_edgar), False),
        ("Semiconductor Eng.",     "semi_engineering",     fetch_semi_engineering,      False),
        ("EE Times",               "eetimes",              fetch_eetimes,               False),
        ("Tom's Hardware",         "toms_hardware",        fetch_toms_hardware,         False),
        ("ServeTheHome",           "serve_the_home",       fetch_serve_the_home,        False),
        ("Next Platform",          "next_platform",        fetch_next_platform,         False),
        ("Fabricated Knowledge",   "fabricated_knowledge", fetch_fabricated_knowledge,  False),
        ("台股月營收",             "tw_revenue",           _count_only(fetch_tw_revenue_all), True),
    ]

    total_fetched    = 0
    total_new        = 0
    all_new_articles = []  # 非 skip_ai 來源統一收集，後續批次去重+評分

    for source_name, source_type, fetch_fn, skip_ai in sources:
        print(f"\n{'─'*40}")
        print(f"📥 抓取 {source_name}...")

        row = run_rows[source_type] = _new_run_row(run_at, source_type)
        try:
            stats = {}
            try:
                articles = fetch_fn(stats=stats)
            finally:
                row.update({k: v for k, v in stats.items() if k in row})
            print(f"   取得 {len(articles)} 篇")
            if row["status"] == "error":
                print(f"   ⚠️ {source_name} 抓取異常：{row['error_msg']}")

            # URL 去重：過濾掉已存在的文章
            new_articles = [a for a in articles if not article_exists(a["url"])]
            total_fetched += len(articles)
            print(f"   新文章：{len(new_articles)} 篇（重複跳過 {len(articles) - len(new_articles)} 篇）")

            row["after_url_dedup"] = len(new_articles)
            row["inserted"] = 0
            # 月營收不走標題去重，after_title_dedup 留 NULL 表示不適用
            if not skip_ai:
                row["after_title_dedup"] = 0

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
                        row["inserted"] += 1

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
            row["status"] = "error"
            row["error_msg"] = repr(e)[:ERROR_MSG_MAX_LEN]

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

        for source_type, n in Counter(a["source_type"] for a in all_new_articles).items():
            if source_type in run_rows:
                run_rows[source_type]["after_title_dedup"] = n

        if all_new_articles:
            # Step 1：存入 DB
            saved = 0
            for article in all_new_articles:
                success = save_article(**article)
                if success:
                    saved += 1
                    total_new += 1
                    if article["source_type"] in run_rows:
                        run_rows[article["source_type"]]["inserted"] += 1
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