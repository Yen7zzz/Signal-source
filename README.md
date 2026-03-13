# 📡 Industry Radar

半導體產業情報自動收集系統，每天抓取精準源頭，每週寄出原始情報週報供 Gemini 分析。

## 架構

```
pipeline_collect.py   每天執行，抓取四個源頭存入 SQLite
pipeline_digest.py    每週一執行，整理原始情報寄出 Email
```

## 資料源

| 來源 | 類型 | 說明 |
|------|------|------|
| SemiAnalysis | RSS | Dylan Patel 的深度半導體分析 |
| TrendForce | 爬蟲 | 記憶體產業研調，含現貨價與產能稼動率 |
| DIGITIMES | RSS | 亞洲供應鏈最即時的產業新聞 |
| SEC EDGAR | 官方 API | WATCHLIST 公司的法說會（8-K）與季報（10-Q） |
| Seeking Alpha | RSS | 補充性市場分析 |

## GitHub Secrets 設定

在 repo 的 Settings → Secrets and variables → Actions 新增：

| Secret 名稱 | 說明 |
|-------------|------|
| `EMAIL_SENDER` | 寄件 Gmail 帳號 |
| `EMAIL_PASSWORD` | Gmail App Password（16 碼）|
| `EMAIL_RECEIVERS` | 收件信箱，多個用逗號分隔 |
| `SEC_USER_AGENT` | 格式：`IndustryRadar your@email.com` |

## 新增追蹤公司

1. 去 [SEC EDGAR](https://www.sec.gov/cgi-bin/browse-edgar) 搜尋公司名稱
2. 複製 CIK（10 位數字）
3. 在 `config.py` 的 `WATCHLIST` 新增一行：

```python
"AAPL": {"name": "Apple", "sec_cik": "0000320193"},
```

非美股公司（如 SK Hynix）沒有 SEC CIK，`sec_cik` 留空字串，系統仍會從其他源頭追蹤。

## 手動入口（付費文章）

SemiAnalysis 的核心報告是付費內容。如果你有訂閱，可以把關鍵段落整理後，
直接手動存入資料庫，下週的週報就會包含這份情報：

```python
from database import save_article

save_article(
    source_type = "semianalysis",
    title       = "Memory Mania: HBM Supercycle Begins",
    url         = "https://www.semianalysis.com/p/...",
    summary     = "（貼上你整理的關鍵段落）",
    source      = "SemiAnalysis (手動)",
    published   = "2026-03-13",
)
```

## 本地測試

```bash
# 安裝套件
pip install -r requirements.txt

# 測試收集
python pipeline_collect.py

# 測試週報（需先跑過 collect）
python pipeline_digest.py
```
