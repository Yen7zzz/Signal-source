# ============================================================
# database.py — SQLite 資料庫操作
#
# 直接參照 Signal-Flow 的 database.py，結構幾乎相同
# 差異：新增 source_type 欄位，區分不同資料源
#       新增 filing_type 欄位，區分 SEC 表單類型（8-K / 10-Q）
#       新增 ticker 欄位，對應 WATCHLIST 的公司代碼
#       新增 full_content 欄位，存 Jina AI 抓回的完整內文
#       新增 ai_score 欄位，存 Groq 評分（1-10）
#       新增 ai_summary 欄位，存 Groq 一句話重點摘要
# ============================================================

import json
import os
import sqlite3
import logging
from datetime import datetime, timedelta
from config import DB_PATH

logger = logging.getLogger(__name__)


def get_connection() -> sqlite3.Connection:
    """建立資料庫連線，回傳 Row 物件（可用欄位名稱存取）"""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """
    初始化資料庫，建立 articles 資料表
    使用 IF NOT EXISTS，安全地重複執行

    注意：若資料表已存在但缺少新欄位（舊版升級），
    會用 ALTER TABLE 補上，不會破壞現有資料
    """
    conn = get_connection()

    # 建立資料表（新專案第一次執行）
    conn.execute("""
        CREATE TABLE IF NOT EXISTS articles (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            source_type  TEXT NOT NULL,
            ticker       TEXT,
            filing_type  TEXT,
            title        TEXT NOT NULL,
            url          TEXT NOT NULL UNIQUE,
            summary      TEXT,
            full_content TEXT,
            ai_score     INTEGER,
            ai_summary   TEXT,
            source       TEXT,
            published    TEXT,
            created_at   TEXT DEFAULT (datetime('now'))
        )
    """)

    # 若是從舊版升級（資料表已存在但缺少新欄位），補上新欄位
    # SQLite 的 ALTER TABLE 不支援 IF NOT EXISTS，用 try/except 處理
    new_columns = [
        ("full_content",          "TEXT"),
        ("ai_score",              "INTEGER"),
        ("ai_summary",            "TEXT"),
        ("content_completeness",  "TEXT"),
        ("rule_score",            "INTEGER"),
        ("is_junk",               "INTEGER"),
    ]
    for col_name, col_type in new_columns:
        try:
            conn.execute(f"ALTER TABLE articles ADD COLUMN {col_name} {col_type}")
            logger.info(f"資料庫升級：新增欄位 {col_name}")
        except sqlite3.OperationalError:
            pass  # 欄位已存在，忽略

    # 台股月營收獨立資料表（結構化數據，供未來趨勢分析用）
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tw_monthly_revenue (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_id     TEXT NOT NULL,
            stock_name   TEXT NOT NULL,
            year         INTEGER NOT NULL,
            month        INTEGER NOT NULL,
            revenue      INTEGER NOT NULL,
            yoy_pct      REAL,
            mom_pct      REAL,
            created_at   TEXT DEFAULT (datetime('now')),
            UNIQUE(stock_id, year, month)
        )
    """)

    # 週報合成結果存檔表（供跨週比較使用）
    conn.execute("""
        CREATE TABLE IF NOT EXISTS weekly_digests (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            run_date         TEXT NOT NULL,
            synthesis_json   TEXT,
            article_titles   TEXT,
            created_at       TEXT DEFAULT (datetime('now'))
        )
    """)

    conn.commit()
    conn.close()
    logger.info("資料庫初始化完成")


def article_exists(url: str) -> bool:
    """去重檢查：URL 已存在則回傳 True"""
    conn = get_connection()
    row  = conn.execute(
        "SELECT 1 FROM articles WHERE url = ?", (url,)
    ).fetchone()
    conn.close()
    return row is not None


def save_article(
    source_type:  str,
    title:        str,
    url:          str,
    summary:      str  = "",
    full_content: str  = "",
    ai_score:     int  = None,
    ai_summary:   str  = "",
    source:       str  = "",
    published:    str  = "",
    ticker:       str  = "",
    filing_type:  str  = "",
) -> bool:
    """
    儲存單篇文章，回傳是否成功（重複 URL 會被忽略）

    Args:
        source_type:  資料源類型
        title:        文章標題（原文）
        url:          原始連結
        summary:      RSS 摘要（原文，500字）
        full_content: Jina AI 抓回的完整內文
        ai_score:     Groq 評分 1-10
        ai_summary:   Groq 一句話重點摘要
        source:       來源名稱，例如 "SemiAnalysis"
        published:    發布時間字串
        ticker:       對應的股票代碼（SEC 文章才有）
        filing_type:  SEC 表單類型（SEC 文章才有）
    """
    try:
        conn = get_connection()
        conn.execute("""
            INSERT OR IGNORE INTO articles
                (source_type, ticker, filing_type, title, url,
                 summary, full_content, ai_score, ai_summary, source, published)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (source_type, ticker, filing_type, title, url,
              summary, full_content, ai_score, ai_summary, source, published))
        affected = conn.total_changes
        conn.commit()
        conn.close()
        return affected > 0
    except Exception as e:
        logger.error(f"儲存文章失敗：{e} | URL: {url}")
        return False


def update_content_completeness(url: str, status: str):
    """更新單篇文章的 content_completeness 欄位（full / partial / headline_only）"""
    try:
        conn = get_connection()
        conn.execute(
            "UPDATE articles SET content_completeness = ? WHERE url = ?",
            (status, url),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"更新 content_completeness 失敗：{e} | URL: {url}")


def update_article_ai(url: str, ai_score: int, ai_summary: str, full_content: str = ""):
    """
    更新單篇文章的 AI 評分和摘要
    用於 pipeline_collect.py 在存檔後非同步更新評分
    """
    try:
        conn = get_connection()
        conn.execute("""
            UPDATE articles
            SET ai_score = ?, ai_summary = ?, full_content = ?
            WHERE url = ?
        """, (ai_score, ai_summary, full_content, url))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"更新 AI 評分失敗：{e} | URL: {url}")


def update_full_content(url: str, full_content: str) -> bool:
    """
    只更新單篇文章的 full_content 欄位，不動 ai_score / ai_summary。
    供規則式評分流程於 Jina 抓完全文後回寫使用。
    """
    try:
        conn = get_connection()
        conn.execute("""
            UPDATE articles
            SET full_content = ?
            WHERE url = ?
        """, (full_content, url))
        affected = conn.total_changes
        conn.commit()
        conn.close()
        return affected > 0
    except Exception as e:
        logger.error(f"更新 full_content 失敗：{e} | URL: {url}")
        return False


def update_rule_score(url: str, rule_score: int, is_junk: bool) -> bool:
    """
    更新單篇文章的規則式評分結果（rule_score / is_junk）

    與 ai_score / ai_summary（Haiku 時期歷史資料）完全分開存放，
    不覆寫、不混用。
    """
    try:
        conn = get_connection()
        conn.execute("""
            UPDATE articles
            SET rule_score = ?, is_junk = ?
            WHERE url = ?
        """, (rule_score, 1 if is_junk else 0, url))
        affected = conn.total_changes
        conn.commit()
        conn.close()
        return affected > 0
    except Exception as e:
        logger.error(f"更新 rule_score 失敗：{e} | URL: {url}")
        return False


def tw_revenue_exists(stock_id: str, year: int, month: int) -> bool:
    """去重：該月份的月營收是否已存在於結構化資料表"""
    conn = get_connection()
    row  = conn.execute(
        "SELECT 1 FROM tw_monthly_revenue WHERE stock_id = ? AND year = ? AND month = ?",
        (stock_id, year, month)
    ).fetchone()
    conn.close()
    return row is not None


def save_tw_revenue(
    stock_id:   str,
    stock_name: str,
    year:       int,
    month:      int,
    revenue:    int,
    yoy_pct:    float = None,
    mom_pct:    float = None,
) -> bool:
    """儲存月營收結構化數據到 tw_monthly_revenue 表（重複會被忽略）"""
    try:
        conn = get_connection()
        conn.execute("""
            INSERT OR IGNORE INTO tw_monthly_revenue
                (stock_id, stock_name, year, month, revenue, yoy_pct, mom_pct)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (stock_id, stock_name, year, month, revenue, yoy_pct, mom_pct))
        affected = conn.total_changes
        conn.commit()
        conn.close()
        return affected > 0
    except Exception as e:
        logger.error(f"儲存月營收失敗：{e} | {stock_id} {year}/{month}")
        return False


def get_recent_titles(days: int = 7) -> list[str]:
    """撈取最近 N 天的文章標題，供跨天標題去重使用"""
    since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    conn = get_connection()
    rows = conn.execute(
        "SELECT title FROM articles WHERE created_at >= ?", (since,)
    ).fetchall()
    conn.close()
    return [row["title"] for row in rows if row["title"]]


def save_weekly_digest(run_date: str, synthesis: dict, article_titles: list[str]) -> bool:
    """儲存本週合成結果，供下週跨週比較使用"""
    try:
        conn = get_connection()
        conn.execute("""
            INSERT INTO weekly_digests (run_date, synthesis_json, article_titles)
            VALUES (?, ?, ?)
        """, (run_date, json.dumps(synthesis, ensure_ascii=False),
              json.dumps(article_titles, ensure_ascii=False)))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logger.error(f"儲存 weekly_digest 失敗：{e}")
        return False


def get_last_weekly_digest() -> dict | None:
    """撈取最近一筆週報合成結果，回傳 None 若無資料或 JSON 解析失敗"""
    try:
        conn = get_connection()
        row = conn.execute("""
            SELECT run_date, synthesis_json, article_titles
            FROM   weekly_digests
            ORDER  BY run_date DESC
            LIMIT  1
        """).fetchone()
        conn.close()
        if row is None:
            return None
        return {
            "run_date":       row["run_date"],
            "synthesis":      json.loads(row["synthesis_json"]),
            "article_titles": json.loads(row["article_titles"]),
        }
    except Exception as e:
        logger.error(f"讀取 last_weekly_digest 失敗：{e}")
        return None


def get_recent_articles(
    days: int = 7,
    min_score: int = None,
    include_junk: bool = False,
    min_rule_score: int | None = None,
) -> list[dict]:
    """
    撈取最近 N 天的文章，供 pipeline_digest.py 生成週報

    Args:
        days:           撈幾天內的文章
        min_score:      最低 AI 評分門檻（None 表示不過濾，維持原行為不動）
        include_junk:   是否包含 is_junk = 1 的文章（預設 False，排除規則式判定的雜訊）
        min_rule_score: 最低 rule_score 門檻（None 表示不過濾）

    Returns:
        按 ai_score 降序排列的文章清單（含 rule_score、is_junk 欄位）
    """
    since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    conn  = get_connection()

    where_clauses = ["created_at >= ?"]
    params = [since]

    if min_score is not None:
        where_clauses.append("(ai_score >= ? OR ai_score IS NULL)")
        params.append(min_score)

    if not include_junk:
        where_clauses.append("(is_junk IS NULL OR is_junk != 1)")

    if min_rule_score is not None:
        where_clauses.append("(rule_score >= ? OR rule_score IS NULL)")
        params.append(min_rule_score)

    where_sql = " AND ".join(where_clauses)

    if min_score is not None:
        order_sql = "ai_score DESC, source_type, published DESC"
    else:
        order_sql = "source_type, published DESC"

    rows = conn.execute(f"""
        SELECT *
        FROM   articles
        WHERE  {where_sql}
        ORDER  BY {order_sql}
    """, params).fetchall()

    conn.close()
    return [dict(row) for row in rows]