# ============================================================
# config.py — 所有設定集中在這裡
#
# 使用方式：
# 1. 填入 WATCHLIST（公司代碼 + SEC CIK）
# 2. 填入 Email 設定
# 3. GitHub Actions 會從 Secrets 自動注入 API Keys
#
# SEC CIK 查詢：https://www.sec.gov/cgi-bin/browse-edgar
# 搜尋公司名稱 → 找到後複製 CIK 號碼填入
# ============================================================

import os

# ── AI 設定 ───────────────────────────────────────────────────
# 測試階段用 Groq（免費），之後換 Gemini 只需改這裡
GROQ_API_KEY  = os.environ.get("GROQ_API_KEY", "")
GROQ_MODEL    = "llama-3.3-70b-versatile"

# 評分門檻：低於此分數的文章不會出現在週報中
AI_SCORE_THRESHOLD = 6

# 全文抓取門檻：ai_score >= 此值才用 Jina AI 抓完整內文
FULL_CONTENT_SCORE_THRESHOLD = 7

# ── Email 設定 ────────────────────────────────────────────────
EMAIL_SENDER    = os.environ.get("EMAIL_SENDER", "")
EMAIL_PASSWORD  = os.environ.get("EMAIL_PASSWORD", "")       # Gmail App Password
EMAIL_RECEIVERS = os.environ.get("EMAIL_RECEIVERS", "")      # 逗號分隔，例如 "a@gmail.com,b@gmail.com"
SMTP_HOST       = "smtp.gmail.com"
SMTP_PORT       = 587

# ── 資料庫 ────────────────────────────────────────────────────
DB_PATH = "data/industry_radar.db"

# ── 台股月營收追蹤名單 ────────────────────────────────────────
# 來源：FinMind API（免費，不需 Token）
# 格式：股票代號 → 公司名稱
TW_WATCHLIST = {
    "2330": "台積電",
    "3711": "日月光投控",
    "2408": "南亞科",
    "3037": "欣興",
}

# ── 追蹤公司名單 ──────────────────────────────────────────────
# 新增公司步驟：
# 1. 去 https://www.sec.gov/cgi-bin/browse-edgar 搜尋公司名
# 2. 複製 CIK（純數字，前面補零到 10 位）
# 3. 貼到下方
#
# 注意：非美股（如 SK Hynix）沒有 SEC CIK，留空字串即可
# 系統仍會從其他源頭（TrendForce、SemiAnalysis）抓取相關報導
WATCHLIST = {
    # ── AI 晶片 ──────────────────────────────────────────────
    "NVDA": {"name": "NVIDIA",           "sec_cik": "0001045810"},
    "AMD":  {"name": "AMD",              "sec_cik": "0000002488"},
    "INTC": {"name": "Intel",            "sec_cik": "0000050863"},
    "AVGO": {"name": "Broadcom",         "sec_cik": "0001649338"},
    "QCOM": {"name": "Qualcomm",         "sec_cik": "0000804328"},
    "MRVL": {"name": "Marvell Technology","sec_cik": "0001058057"},

    # ── 記憶體 ───────────────────────────────────────────────
    "MU":   {"name": "Micron",           "sec_cik": "0000723125"},

    # ── 晶圓代工 / 封裝 ──────────────────────────────────────
    "TSM":  {"name": "TSMC",             "sec_cik": "0001046179"},
    "ASX":  {"name": "ASE Technology",   "sec_cik": "0001060349"},

    # ── 需求端（Hyperscaler / AI CapEx 驅動）────────────────
    "MSFT": {"name": "Microsoft",        "sec_cik": "0000789019"},
    "GOOGL":{"name": "Alphabet",         "sec_cik": "0001652044"},
    "AMZN": {"name": "Amazon",           "sec_cik": "0001018724"},
    "META": {"name": "Meta Platforms",   "sec_cik": "0001326801"},

    # ── 設備端（半導體設備 / CapEx leading indicator）────────
    "ASML": {"name": "ASML Holding",     "sec_cik": "0000937966"},
    "AMAT": {"name": "Applied Materials","sec_cik": "0000006951"},
    "LRCX": {"name": "Lam Research",     "sec_cik": "0000707549"},

    # ── 亞洲廠商（無 SEC，靠 TrendForce / SemiAnalysis 追蹤）─
    "000660.KS": {"name": "SK Hynix",    "sec_cik": ""},
    "005930.KS": {"name": "Samsung",     "sec_cik": ""},
}

# ── 資料來源設定 ──────────────────────────────────────────────

# SemiAnalysis — Substack 公開 RSS
# 只有免費文章，付費文章需手動餵入（見 README）
SEMIANALYSIS_RSS = "https://www.semianalysis.com/feed"

# ── 半導體 / 技術媒體 RSS（新增）─────────────────────────────
# 需要關鍵字過濾（混合內容站）
SEMI_ENGINEERING_RSS   = "https://semiengineering.com/feed/"
EETIMES_RSS            = "https://www.eetimes.com/feed/"
TOMS_HARDWARE_RSS      = "https://www.tomshardware.com/feeds/all"

# 聚焦站，不需過濾
SERVE_THE_HOME_RSS     = "https://www.servethehome.com/feed/"
NEXT_PLATFORM_RSS      = "https://www.nextplatform.com/feed/"
FABRICATED_KNOWLEDGE_RSS = "https://www.fabricatedknowledge.com/feed"

# TrendForce — 公開新聞頁面（爬蟲）
TRENDFORCE_NEWS_URL = "https://www.trendforce.com/news/"
TRENDFORCE_KEYWORDS = [
    "HBM", "DRAM", "NAND", "memory", "semiconductor",
    "CoWoS", "packaging", "wafer", "CapEx", "capacity",
    "Micron", "Samsung", "SK Hynix", "TSMC", "NVIDIA",
]

# DIGITIMES — RSS
DIGITIMES_RSS = "https://www.digitimes.com/rss/daily.xml"

# SEC EDGAR — 官方 API（免費，無需 API Key）
# 追蹤的表單類型：
#   8-K  → 重大事件公告（法說會、重大合約）
#   10-Q → 季報（財務數據、CapEx）
#   10-K → 年報
SEC_FILING_TYPES = ["8-K", "10-Q"]
SEC_USER_AGENT   = os.environ.get("SEC_USER_AGENT", "IndustryRadar jason071491@gmail.com")
# SEC EDGAR 要求 User-Agent 格式：「公司名稱 email」
# 請把 GitHub Secret SEC_USER_AGENT 設為你的 email，例如：
# "IndustryRadar your@email.com"

# ── 週報設定 ──────────────────────────────────────────────────
# 每個來源最多抓幾篇文章（避免 Email 過長）
MAX_ARTICLES_PER_SOURCE = 10

# 撈幾天內的文章生成週報
DIGEST_DAYS = 7