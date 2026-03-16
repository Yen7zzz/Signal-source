# ============================================================
# scorer.py — 用 Groq 對每篇文章評分（1-10）並生成一句話重點
#
# 評分標準（Prompt 裡定義）：
#   10 分：直接影響半導體供需結構的重大事件（如法說會CapEx大幅削減）
#    7-9 分：有明確產業意義的技術或市場變化
#    4-6 分：一般產業動態，背景資訊
#    1-3 分：與追蹤主題關聯性低
#
# 設計原則：
#   用 summary 或 full_content 來評分（優先用 full_content）
#   評分失敗時給預設分數 5（不讓文章消失，只是降低優先度）
#   批次處理，每篇之間不需要等（Groq 速度夠快）
# ============================================================

import json
import logging
from groq import Groq
from config import GROQ_API_KEY, GROQ_MODEL

logger = logging.getLogger(__name__)

# 評分失敗時的預設分數（讓文章還是能進週報，但排在後面）
DEFAULT_SCORE_ON_FAILURE = 5


def _build_prompt(article: dict) -> str:
    """
    組裝評分 Prompt

    優先用 full_content（Jina 抓回的完整內文）
    若沒有 full_content，退回用 summary
    """
    title   = article.get("title", "")
    source  = article.get("source", "")
    content = article.get("full_content", "") or article.get("summary", "")

    return f"""你是一位專業的半導體產業分析師。
請評估以下文章對「半導體供需結構、記憶體（HBM/DRAM/NAND）、AI 晶片、先進封裝（CoWoS）、資本支出（CapEx）」的情報價值。

來源：{source}
標題：{title}
內容：{content}

請用以下 JSON 格式回覆，不要有其他說明：
{{
  "score": <1到10的整數>,
  "reason": "<一句話說明評分原因，繁體中文，30字以內>",
  "key_point": "<這篇文章最重要的一個情報點，繁體中文，50字以內>"
}}

評分標準：
10 分：直接揭露供需結構改變（如：法說會宣布削減 CapEx、HBM 配比重大異動）
7-9 分：有明確產業意義的技術或市場變化（如：新製程進展、供應商轉換）
4-6 分：一般產業動態、市場分析（有參考價值但不緊急）
1-3 分：與核心主題關聯性低的一般新聞"""


def score_article(client: Groq, article: dict) -> tuple[int, str]:
    """
    對單篇文章評分，回傳 (score, key_point)

    Args:
        client:  Groq client
        article: 文章 dict

    Returns:
        (ai_score, ai_summary) — 失敗時回傳預設值
    """
    try:
        response = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[{"role": "user", "content": _build_prompt(article)}],
            temperature=0.1,   # 低溫度讓評分穩定
            max_tokens=200,
        )

        raw  = response.choices[0].message.content.strip()
        raw  = raw.replace("```json", "").replace("```", "").strip()
        data = json.loads(raw)

        score     = int(data.get("score", DEFAULT_SCORE_ON_FAILURE))
        key_point = data.get("key_point", "")

        # 確保分數在合理範圍內
        score = max(1, min(10, score))

        return score, key_point

    except json.JSONDecodeError:
        logger.warning(f"Groq 回傳格式錯誤：{article.get('title', '')[:40]}")
        return DEFAULT_SCORE_ON_FAILURE, ""
    except Exception as e:
        logger.error(f"Groq 評分失敗：{e} | 標題：{article.get('title', '')[:40]}")
        return DEFAULT_SCORE_ON_FAILURE, ""


def batch_score(articles: list[dict]) -> list[dict]:
    """
    批次對一批文章評分

    Args:
        articles: 已含 full_content 的文章清單

    Returns:
        同一份清單，每筆新增 ai_score 和 ai_summary 欄位
    """
    if not GROQ_API_KEY:
        logger.warning("GROQ_API_KEY 未設定，跳過評分")
        for article in articles:
            article["ai_score"]   = DEFAULT_SCORE_ON_FAILURE
            article["ai_summary"] = ""
        return articles

    client = Groq(api_key=GROQ_API_KEY)
    total  = len(articles)

    for i, article in enumerate(articles):
        title = article.get("title", "")[:50]
        print(f"   🤖 [{i+1}/{total}] 評分中：{title}...")

        score, key_point = score_article(client, article)

        article["ai_score"]   = score
        article["ai_summary"] = key_point

        # 顯示評分結果
        bar = "█" * score + "░" * (10 - score)
        print(f"      [{bar}] {score}/10 — {key_point[:40]}")

    return articles
