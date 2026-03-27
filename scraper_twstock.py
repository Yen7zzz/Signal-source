# ============================================================
# scraper_twstock.py — 台股月營收抓取（FinMind 公開 API）
#
# 資料來源：FinMind TaiwanStockMonthRevenue
# 網址：https://api.finmindtrade.com/api/v4/data
# 免費、無需 API Token，每天一次不會觸發 rate limit
#
# 回傳格式與 scraper.py 其他 fetcher 一致（articles list）
# 額外帶 _* 欄位供 pipeline_collect.py 存入結構化資料表
# ============================================================

import time
import logging
import requests
from datetime import datetime, timedelta
from config import TW_WATCHLIST

logger = logging.getLogger(__name__)

FINMIND_URL = "https://api.finmindtrade.com/api/v4/data"


def _score_from_yoy(yoy_pct: float) -> int:
    """根據 YoY 幅度決定 AI 分數（不送 Groq）"""
    if abs(yoy_pct) > 30:
        return 9
    if abs(yoy_pct) > 15:
        return 7
    return 5


def fetch_tw_revenue(stock_id: str, stock_name: str) -> dict | None:
    """
    撈單一公司 13 個月月營收，計算 YoY / MoM，回傳最新月份的 article dict。

    回傳的 dict 包含：
    - 標準 article 欄位（可直接傳入 save_article()，需先 pop _* 欄位）
    - _stock_name / _year / _month / _revenue / _yoy_pct / _mom_pct
      供 pipeline_collect.py 存入 tw_monthly_revenue 結構化資料表
    """
    start_date = (datetime.now() - timedelta(days=400)).strftime("%Y-%m-%d")

    try:
        resp = requests.get(
            FINMIND_URL,
            params={
                "dataset":    "TaiwanStockMonthRevenue",
                "data_id":    stock_id,
                "start_date": start_date,
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json().get("data", [])
    except Exception as e:
        logger.error(f"FinMind 抓取失敗 {stock_id}: {e}")
        return None

    if not data:
        logger.warning(f"FinMind 回傳空資料：{stock_id}")
        return None

    # 按年月排序，確保最新的在最後
    data.sort(key=lambda x: (x["revenue_year"], x["revenue_month"]))

    latest      = data[-1]
    cur_revenue = latest["revenue"]
    cur_year    = latest["revenue_year"]
    cur_month   = latest["revenue_month"]
    announce_dt = latest["date"]          # 公告日（次月 1 日）

    # YoY：找去年同月
    yoy_record = next(
        (r for r in data
         if r["revenue_year"] == cur_year - 1 and r["revenue_month"] == cur_month),
        None,
    )
    yoy_pct = (
        round((cur_revenue / yoy_record["revenue"] - 1) * 100, 1)
        if yoy_record else None
    )

    # MoM：前一筆
    prev_record = data[-2] if len(data) >= 2 else None
    mom_pct = (
        round((cur_revenue / prev_record["revenue"] - 1) * 100, 1)
        if prev_record else None
    )

    # 轉成億（整數）
    revenue_yi = round(cur_revenue / 1e8)

    # ── 組標題（英文縮寫 YoY/MoM，方便一眼掃描）─────────────
    yoy_str = f"YoY {yoy_pct:+.1f}%" if yoy_pct is not None else "YoY N/A"
    mom_str = f"MoM {mom_pct:+.1f}%" if mom_pct is not None else "MoM N/A"
    title   = f"[月營收] {stock_name} {cur_year}年{cur_month}月 營收 {revenue_yi:,}億 {yoy_str} {mom_str}"

    # ── 組摘要（中文，供 Email 顯示）────────────────────────
    yoy_part = (
        f"年{'增' if yoy_pct >= 0 else '減'} {abs(yoy_pct):.1f}%"
        if yoy_pct is not None else "年增 N/A"
    )
    mom_part = (
        f"月{'增' if mom_pct >= 0 else '減'} {abs(mom_pct):.1f}%"
        if mom_pct is not None else "月增 N/A"
    )
    summary = f"月營收 {revenue_yi:,}億元，{yoy_part}，{mom_part}"

    score = _score_from_yoy(yoy_pct) if yoy_pct is not None else 5

    return {
        # ── 標準 article 欄位 ──────────────────────────────
        "source_type":  "tw_revenue",
        "ticker":       stock_id,
        "filing_type":  "月營收",
        "title":        title,
        "url":          f"finmind://{stock_id}/{cur_year}/{cur_month:02d}",
        "summary":      summary,
        "full_content": "",
        "ai_score":     score,
        "ai_summary":   summary,
        "source":       "台股月營收",
        "published":    announce_dt,
        # ── 結構化欄位（pipeline_collect.py 存 tw_monthly_revenue 用）─
        "_stock_name":  stock_name,
        "_year":        cur_year,
        "_month":       cur_month,
        "_revenue":     cur_revenue,
        "_yoy_pct":     yoy_pct,
        "_mom_pct":     mom_pct,
    }


def fetch_tw_revenue_all() -> list[dict]:
    """撈 TW_WATCHLIST 所有公司的最新月營收，回傳 articles 格式 list"""
    results = []
    items   = list(TW_WATCHLIST.items())

    for i, (stock_id, stock_name) in enumerate(items):
        article = fetch_tw_revenue(stock_id, stock_name)
        if article:
            results.append(article)
        # FinMind rate limit 保護（最後一筆不需要等）
        if i < len(items) - 1:
            time.sleep(1)

    return results
