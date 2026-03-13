# ============================================================
# debug_sec.py — 測試 SEC EDGAR EFTS 搜尋 API
# ============================================================

import requests
from config import SEC_USER_AGENT

headers = {"User-Agent": SEC_USER_AGENT}
print(f"SEC_USER_AGENT = '{SEC_USER_AGENT}'\n")

# 用 CIK 精準搜尋 Micron 的 8-K（近 90 天）
# CIK: 0000723125 → 723125
search_url = (
    "https://efts.sec.gov/LATEST/search-index"
    "?q=%22Micron%22"
    "&dateRange=custom"
    "&startdt=2025-12-01"
    "&category=form-type"
    "&forms=8-K,10-Q"
    "&entity=723125"
)

print(f"搜尋 URL:\n{search_url}\n")

try:
    resp = requests.get(search_url, headers=headers, timeout=15)
    print(f"HTTP 狀態碼: {resp.status_code}")
    data = resp.json()
    hits = data.get("hits", {}).get("hits", [])
    total = data.get("hits", {}).get("total", {}).get("value", 0)
    print(f"總計找到: {total} 筆，顯示前 {len(hits)} 筆\n")

    for hit in hits[:5]:
        src  = hit.get("_source", {})
        aid  = hit.get("_id", "")
        print(f"  表單：{src.get('form_type')}")
        print(f"  日期：{src.get('file_date','')[:10]}")
        print(f"  名稱：{src.get('display_names')}")
        print(f"  _id：{aid}")
        # 組出 viewer URL
        cik = "723125"
        acc_clean = aid.replace(":", "").replace("-", "")
        acc_fmt   = aid.replace(":", "-")
        viewer    = f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc_clean}/{acc_fmt}-index.htm"
        print(f"  URL：{viewer}")
        print()

except Exception as e:
    print(f"❌ 失敗: {e}")