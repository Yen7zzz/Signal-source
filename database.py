# ============================================================
# database.py — SQLite 資料庫操作
#
# 直接參照 Signal-Flow 的 database.py，結構幾乎相同
# 差異：新增 source_type 欄位，區分不同資料源
#       新增 filing_type 欄位，區分 SEC 表單類型（8-K / 10-Q）
#       新增 ticker 欄位，對應 WATCHLIST 的公司代碼
# ============================================================

import sqlite3
import logging
from datetime import datetime, timedelta
from config import DB_PATH

logger = logging.getLogger(__name__)


def get_connection() -> sqlite3.Connection:
    """建立資料庫連線，回傳 Row 物件（可用欄位名稱存取）"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """
    初始化資料庫，建立 articles 資料表
    使用 IF NOT EXISTS，安全地重複執行
    """
    conn = get_connection()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS articles (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            source_type  TEXT NOT NULL,   -- 'semianalysis' | 'trendforce' | 'digitimes' | 'sec_edgar' | 'seeking_alpha'
            ticker       TEXT,            -- 對應 WATCHLIST 的公司代碼，SEC 文章才有
            filing_type  TEXT,            -- SEC 專用：'8-K' | '10-Q' | '10-K'
            title        TEXT NOT NULL,
            url          TEXT NOT NULL UNIQUE,
            summary      TEXT,
            source       TEXT,            -- 來源名稱，例如 "TrendForce"
            published    TEXT,
            created_at   TEXT DEFAULT (datetime('now'))
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
    source_type: str,
    title:       str,
    url:         str,
    summary:     str  = "",
    source:      str  = "",
    published:   str  = "",
    ticker:      str  = "",
    filing_type: str  = "",
) -> bool:
    """
    儲存單篇文章，回傳是否成功（重複 URL 會被忽略）
    
    Args:
        source_type: 資料源類型，對應 scraper.py 的各個 fetch 函式
        title:       文章標題（原文，不改寫）
        url:         原始連結
        summary:     文章摘要（原文前 500 字，不讓 AI 改寫）
        source:      來源名稱，例如 "SemiAnalysis"
        published:   發布時間字串
        ticker:      對應的股票代碼（SEC 文章才有）
        filing_type: SEC 表單類型（SEC 文章才有）
    """
    try:
        conn = get_connection()
        conn.execute("""
            INSERT OR IGNORE INTO articles
                (source_type, ticker, filing_type, title, url, summary, source, published)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (source_type, ticker, filing_type, title, url, summary, source, published))
        affected = conn.total_changes
        conn.commit()
        conn.close()
        return affected > 0
    except Exception as e:
        logger.error(f"儲存文章失敗：{e} | URL: {url}")
        return False


def get_recent_articles(days: int = 7) -> list[dict]:
    """
    撈取最近 N 天的文章，供 pipeline_digest.py 生成週報
    
    Returns:
        按 source_type 排序的文章清單，每筆是 dict
    """
    since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    conn  = get_connection()
    rows  = conn.execute("""
        SELECT *
        FROM   articles
        WHERE  created_at >= ?
        ORDER  BY source_type, published DESC
    """, (since,)).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_articles_by_source(source_type: str, days: int = 7) -> list[dict]:
    """
    撈取特定來源的最近文章
    用於 pipeline_digest.py 分源頭組裝 Email
    """
    since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    conn  = get_connection()
    rows  = conn.execute("""
        SELECT *
        FROM   articles
        WHERE  source_type = ?
          AND  created_at >= ?
        ORDER  BY published DESC
    """, (source_type, since)).fetchall()
    conn.close()
    return [dict(row) for row in rows]
