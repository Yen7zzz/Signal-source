# ============================================================
# synthesizer.py — Stage 2 跨文章合成
#
# synthesize_weekly : 呼叫 Claude Haiku，對精選文章做三維度分析，回傳結構化 dict
# build_digest_markdown : 把合成結果 + 原始文章清單轉成 .md 字串
# ============================================================

import json
import logging
from datetime import datetime

import anthropic

from config import ANTHROPIC_API_KEY, ANTHROPIC_MODEL, FULL_CONTENT_SCORE_THRESHOLD, SOURCE_META

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
你是資深半導體產業分析師，專長涵蓋晶圓代工、記憶體供需、AI 晶片生態系與資本支出趨勢。
你的工作是綜合多源情報，為投資人與產業從業者提煉本週最關鍵的結構性訊號。"""

_JSON_SCHEMA = """\
{
  "supply_side": {
    "summary": "2-3 句供給端趨勢摘要，聚焦產能、CapEx、晶圓代工、供應商策略",
    "key_articles": ["[8/10] 文章的完整原始標題（直接引用，不改寫）"]
  },
  "demand_side": {
    "summary": "2-3 句需求端趨勢摘要，聚焦 AI 晶片拉貨、雲端 CapEx、庫存水位",
    "key_articles": ["[8/10] 文章的完整原始標題（直接引用，不改寫）"]
  },
  "technology_nodes": {
    "summary": "2-3 句技術節點趨勢摘要，聚焦 HBM、CoWoS、先進封裝、製程進展",
    "key_articles": ["[8/10] 文章的完整原始標題（直接引用，不改寫）"]
  },
  "tracking_events": [
    {
      "event": "值得在未來 1-2 週持續追蹤的具體事件，30 字以內",
      "why": "為何值得追蹤，潛在影響或催化劑，40 字以內",
      "signal": "high | medium"
    }
  ],
  "weekly_headline": "本週最重要的單一訊號，15 字以內"
}"""


def _source_label(article: dict) -> str:
    meta = SOURCE_META.get(article.get("source_type", ""), {})
    return meta.get("label", article.get("source_type", ""))


def _build_material(article: dict) -> str:
    score       = article.get("ai_score", "?")
    title       = article.get("title", "")
    source      = _source_label(article)
    published   = (article.get("published") or "")[:10]
    ai_summary  = article.get("ai_summary", "")
    completeness = article.get("content_completeness")

    if completeness == "full":
        full_content = (article.get("full_content") or "")[:2000]
        return (
            f"### [{score}/10] {title}\n"
            f"來源：{source} | 日期：{published}\n"
            f"重點：{ai_summary}\n"
            f"\n[內文]\n{full_content}\n[/內文]"
        )
    else:
        summary = article.get("summary", "")
        return (
            f"### [{score}/10] {title}\n"
            f"來源：{source} | 日期：{published}\n"
            f"重點：{ai_summary}\n"
            f"摘要：{summary}"
        )


def synthesize_weekly(articles: list[dict]) -> dict:
    """
    接收精選文章 list，呼叫 Claude Haiku 做三維度跨文章合成。
    回傳結構化 dict；JSON parse 失敗或 API 錯誤時回傳空 dict。
    """
    if not articles:
        logger.warning("synthesize_weekly：無文章，跳過合成")
        return {}

    if not ANTHROPIC_API_KEY:
        logger.error("ANTHROPIC_API_KEY 未設定，無法執行合成")
        return {}

    materials = "\n\n---\n\n".join(_build_material(a) for a in articles)

    user_prompt = (
        f"以下是本週精選的 {len(articles)} 篇半導體產業情報"
        f"（AI 評分 ≥ {FULL_CONTENT_SCORE_THRESHOLD}）。\n\n"
        "請綜合分析，輸出以下 JSON 結構，不要加任何說明文字或 markdown code block：\n\n"
        f"{_JSON_SCHEMA}\n\n"
        "分析原則：\n"
        "- 摘要聚焦「結構性變化」，不是個別事件重述\n"
        "- key_articles 只引用有實際支撐該維度論點的文章，格式為 \"[分數/10] 原始標題\"，至少 1 篇最多 5 篇\n"
        "- tracking_events 優先選「已有早期信號但方向未定」的事件，列 2–4 個\n"
        "- 如果某個維度本週沒有相關文章，summary 寫「本週無顯著訊號」，key_articles 給空 list，不要硬湊\n"
        "- 所有文字使用繁體中文\n\n"
        "---\n\n"
        f"{materials}"
    )

    try:
        client   = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        response = client.messages.create(
            model=ANTHROPIC_MODEL,
            max_tokens=4096,
            temperature=0.3,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )

        raw = response.content[0].text.strip()
        raw = raw.replace("```json", "").replace("```", "").strip()
        return json.loads(raw)

    except json.JSONDecodeError as e:
        logger.error(f"synthesize_weekly：JSON parse 失敗 — {e}")
        return {}
    except Exception as e:
        logger.error(f"synthesize_weekly：API 呼叫失敗 — {e}")
        return {}


# ── Markdown builder ──────────────────────────────────────────────────────────

_SIGNAL_ICON = {"high": "🔴 高", "medium": "🟡 中"}


def _render_dimension(title: str, icon: str, dimension: dict) -> str:
    summary      = dimension.get("summary", "本週無顯著訊號")
    key_articles = dimension.get("key_articles") or []

    lines = [f"## {icon} {title}", "", summary]
    if key_articles:
        lines += ["", "**相關情報：**"]
        for ref in key_articles:
            lines.append(f"- {ref}")
    lines.append("")
    return "\n".join(lines)


def build_digest_markdown(
    synthesis: dict,
    articles: list[dict],
    total_collected: int,
) -> str:
    date_str      = datetime.now().strftime("%Y-%m-%d")
    selected_count = len(articles)
    sorted_articles = sorted(articles, key=lambda a: a.get("ai_score") or 0, reverse=True)

    header = (
        f"# Signal-Source 週報 · {date_str}\n\n"
        f"> 本週收集 {total_collected} 篇 → 精選 {selected_count} 篇"
        + (" | Claude Haiku 合成分析" if synthesis else " | ⚠️ 合成失敗，僅顯示原始文章")
        + "\n"
    )

    sections = [header]

    if synthesis:
        # 核心訊號
        headline = synthesis.get("weekly_headline", "")
        if headline:
            sections.append(f"## 🔑 本週核心訊號\n\n**{headline}**\n")

        # 三個維度
        sections.append(_render_dimension(
            "供給端", "📦", synthesis.get("supply_side", {})))
        sections.append("---\n")
        sections.append(_render_dimension(
            "需求端", "📈", synthesis.get("demand_side", {})))
        sections.append("---\n")
        sections.append(_render_dimension(
            "技術節點", "⚙️", synthesis.get("technology_nodes", {})))
        sections.append("---\n")

        # 追蹤事件 table
        events = synthesis.get("tracking_events") or []
        if events:
            table_rows = ["## 🔭 追蹤事件（未來 1–2 週）", "",
                          "| 事件 | 為何追蹤 | 重要度 |",
                          "|------|----------|--------|"]
            for ev in events:
                icon  = _SIGNAL_ICON.get(ev.get("signal", "medium"), "🟡 中")
                table_rows.append(
                    f"| {ev.get('event', '')} | {ev.get('why', '')} | {icon} |"
                )
            sections.append("\n".join(table_rows) + "\n")
            sections.append("---\n")

    # 精選文章列表
    article_lines = [f"## 📰 本週精選（{selected_count} 篇）", ""]
    for a in sorted_articles:
        score       = a.get("ai_score", "?")
        title       = a.get("title", "")
        url         = a.get("url", "")
        published   = (a.get("published") or "")[:10]
        ai_summary  = a.get("ai_summary", "")
        ticker      = a.get("ticker", "")
        label       = _source_label(a)
        ticker_tag  = f" · {ticker}" if ticker else ""

        article_lines.append(f"### [{score}/10] {title}")
        article_lines.append(f"*{published} · {label}{ticker_tag}*")
        if ai_summary:
            article_lines.append(f"\n{ai_summary}")
        if url:
            article_lines.append(f"\n[原文連結 →]({url})")
        article_lines.append("")

    sections.append("\n".join(article_lines))

    footer = (
        f"\n---\n*由 Signal-Source × {ANTHROPIC_MODEL} 自動合成 · {date_str}*\n"
    )
    sections.append(footer)

    return "\n".join(sections)
