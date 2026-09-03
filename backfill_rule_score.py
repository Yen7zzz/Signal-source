# ============================================================
# backfill_rule_score.py — 一次性回填歷史資料的 rule_score / is_junk
#
# 只寫 rule_score / is_junk 兩個欄位，絕對不動 ai_score / ai_summary。
# 單一 connection + executemany + 一次 commit，避免逐筆 commit 拖慢。
# ============================================================

import argparse
import sqlite3
from collections import defaultdict

from database import DB_PATH
from rule_scorer import is_junk, score_by_rules

TW_REVENUE_RULE_SCORE = 10
TW_REVENUE_IS_JUNK = 0


def compute_updates(rows: list[tuple]) -> list[tuple]:
    """
    rows: [(id, title, url, source_type), ...]
    回傳 [(rule_score, is_junk_int, id), ...]，順序對應 executemany 的 UPDATE 參數
    """
    updates = []
    for row_id, title, url, source_type in rows:
        if source_type == "tw_revenue":
            rule_score = TW_REVENUE_RULE_SCORE
            junk_int = TW_REVENUE_IS_JUNK
        else:
            junk = is_junk(title, source_type)
            rule_score = score_by_rules(title, source_type)
            junk_int = 1 if junk else 0
        updates.append((rule_score, junk_int, row_id))
    return updates


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="只計算並印統計，不寫入 DB")
    args = parser.parse_args()

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT id, title, url, source_type FROM articles")
    rows = cur.fetchall()

    total = len(rows)
    print(f"讀取到 {total} 筆文章，計算 rule_score / is_junk 中...")

    updates = compute_updates(rows)

    junk_count = sum(1 for _, junk_int, _ in updates if junk_int == 1)
    score_dist = defaultdict(int)
    for rule_score, _, _ in updates:
        score_dist[rule_score] += 1

    print(f"\n=== 統計 ===")
    print(f"總筆數：{total}")
    print(f"junk：{junk_count} 篇（{junk_count/total*100:.1f}%）")
    print(f"非 junk：{total - junk_count} 篇（{(total-junk_count)/total*100:.1f}%）")
    print(f"\nrule_score 分布：")
    for s in range(1, 11):
        if score_dist.get(s):
            print(f"  {s:2d} 分：{score_dist[s]:5d} 篇")

    if args.dry_run:
        print("\n[dry-run] 未寫入資料庫。")
        conn.close()
        return

    print(f"\n寫入 DB 中（{total} 筆，單一 connection + executemany）...")
    cur.executemany(
        "UPDATE articles SET rule_score = ?, is_junk = ? WHERE id = ?",
        updates,
    )
    conn.commit()
    conn.close()
    print("完成，已 commit。")


if __name__ == "__main__":
    main()
