# ============================================================
# scorer.py — 用 Anthropic Claude Haiku 對每篇文章評分（1-10）並生成一句話重點
#
# 評分標準（Prompt 裡定義）：
#   10 分：直接影響半導體供需結構的重大事件（如法說會CapEx大幅削減）
#    7-9 分：有明確產業意義的技術或市場變化
#    4-6 分：一般產業動態，背景資訊
#    1-3 分：與追蹤主題關聯性低
#
# 設計原則：
#   只用 title + summary 評分（省 token；全文由 pipeline 在高分後才抓）
#   評分失敗時給預設分數 5（不讓文章消失，只是降低優先度）
#   批次處理，每篇之間不需要等
# ============================================================

import json
import logging
import anthropic
from config import ANTHROPIC_API_KEY, ANTHROPIC_MODEL

logger = logging.getLogger(__name__)

# 評分失敗時的預設分數（讓文章還是能進週報，但排在後面）
DEFAULT_SCORE_ON_FAILURE = 5


def _build_prompt(article: dict) -> str:
    """
    組裝評分 Prompt，只用 title + summary（全文由 pipeline 在高分後才抓）
    """
    title   = article.get("title", "")
    source  = article.get("source", "")
    content = article.get("summary", "")

    return f"""你是一位專業的半導體產業分析師。
請評估以下文章對「半導體供需結構、記憶體（HBM/DRAM/NAND）、AI 晶片、先進封裝（CoWoS）、資本支出（CapEx）」的情報價值。

來源：{source}
標題：{title}
內容：{content}

請用以下 JSON 格式回覆，不要有其他說明：
{{
  "score": <1到10的整數>,
  "relevance": "<high|medium|low>",
  "reason": "<一句話說明評分原因，繁體中文，30字以內>",
  "key_point": "<這篇文章最重要的一個情報點，繁體中文，50字以內>"
}}

relevance 定義：
high：直接涉及半導體供需、CapEx、產能、製程、記憶體價格
medium：間接相關（如 hyperscaler 財報提到 AI 支出，但未提及具體晶片）
low：與核心主題關聯性弱

評分標準：
10 分：直接揭露供需結構改變（如：法說會宣布削減 CapEx、HBM 配比重大異動）
7-9 分：有明確產業意義的技術或市場變化（如：新製程進展、供應商轉換）
4-6 分：一般產業動態、市場分析（有參考價值但不緊急）
1-3 分：與核心主題關聯性低的一般新聞

扣分情境（強制上限，不得超過該上限）：
純 AI 模型安全/倫理/政策討論，未涉及硬體供應鏈 → 最高 4 分
消費電子產品評測（手機、耳機、遊戲主機）→ 最高 3 分
純軟體/應用層新聞（App 更新、SaaS 產品）→ 最高 3 分"""


def score_article(client: anthropic.Anthropic, article: dict) -> tuple[int, str, str]:
    """
    對單篇文章評分，回傳 (score, key_point, relevance)

    Args:
        client:  Anthropic client
        article: 文章 dict

    Returns:
        (ai_score, ai_summary, relevance) — 失敗時回傳預設值
    """
    try:
        response = client.messages.create(
            model=ANTHROPIC_MODEL,
            max_tokens=300,
            temperature=0.1,
            messages=[{"role": "user", "content": _build_prompt(article)}],
        )

        raw  = response.content[0].text.strip()
        raw  = raw.replace("```json", "").replace("```", "").strip()
        data = json.loads(raw)

        score     = int(data.get("score", DEFAULT_SCORE_ON_FAILURE))
        key_point = data.get("key_point", "")
        relevance = data.get("relevance", "medium")

        score = max(1, min(10, score))
        if relevance not in ("high", "medium", "low"):
            relevance = "medium"

        return score, key_point, relevance

    except json.JSONDecodeError:
        logger.warning(f"Claude 回傳格式錯誤：{article.get('title', '')[:40]}")
        return DEFAULT_SCORE_ON_FAILURE, "", "medium"
    except Exception as e:
        logger.error(f"Claude 評分失敗：{e} | 標題：{article.get('title', '')[:40]}")
        return DEFAULT_SCORE_ON_FAILURE, "", "medium"


def batch_score(articles: list[dict]) -> list[dict]:
    """
    批次對一批文章評分（只用 title + summary）

    Args:
        articles: 文章清單（含 title、summary 即可）

    Returns:
        同一份清單，每筆新增 ai_score 和 ai_summary 欄位
    """
    if not ANTHROPIC_API_KEY:
        logger.warning("ANTHROPIC_API_KEY 未設定，跳過評分")
        for article in articles:
            article["ai_score"]   = DEFAULT_SCORE_ON_FAILURE
            article["ai_summary"] = ""
        return articles

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    total  = len(articles)

    for i, article in enumerate(articles):
        title = article.get("title", "")[:50]
        print(f"   🤖 [{i+1}/{total}] 評分中：{title}...")

        score, key_point, relevance = score_article(client, article)

        article["ai_score"]   = score
        article["ai_summary"] = key_point
        article["relevance"]  = relevance

        bar = "█" * score + "░" * (10 - score)
        print(f"      [{bar}] {score}/10 [{relevance}] — {key_point[:40]}")

    return articles
