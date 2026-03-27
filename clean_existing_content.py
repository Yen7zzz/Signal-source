"""
clean_existing_content.py — 一次性腳本：清洗 DB 裡現有的 Jina full_content

跳過 sec_edgar / tw_revenue（非 Jina 抓取，有自己的清理邏輯）
"""

import sqlite3
from config import DB_PATH
from content_fetcher import _clean_jina_content

SKIP_SOURCES = {"sec_edgar", "tw_revenue"}


def run():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    rows = conn.execute("""
        SELECT url, source_type, full_content
        FROM   articles
        WHERE  full_content IS NOT NULL
          AND  full_content != ''
    """).fetchall()

    print(f"撈到 {len(rows)} 筆有 full_content 的文章")

    updated   = 0
    unchanged = 0
    emptied   = 0

    for row in rows:
        url         = row["url"]
        source_type = row["source_type"]
        original    = row["full_content"]

        if source_type in SKIP_SOURCES:
            continue

        cleaned = _clean_jina_content(original)

        if cleaned == original:
            unchanged += 1
        elif cleaned == "":
            conn.execute("UPDATE articles SET full_content = '' WHERE url = ?", (url,))
            emptied += 1
        else:
            conn.execute("UPDATE articles SET full_content = ? WHERE url = ?", (cleaned, url))
            updated += 1

    conn.commit()
    conn.close()

    total_changed = updated + emptied
    print(f"\n結果：")
    print(f"  清洗有變化：{updated} 篇")
    print(f"  清洗後變空：{emptied} 篇（原本就是垃圾）")
    print(f"  無變化：    {unchanged} 篇")
    print(f"  合計異動：  {total_changed} 筆 UPDATE")


if __name__ == "__main__":
    run()
