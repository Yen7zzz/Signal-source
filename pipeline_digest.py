# ============================================================
# pipeline_digest.py — 每週執行：撈高分文章 → 整理 HTML → 寄信
#
# 對應 Signal-Flow 的 pipeline_b.py
# 關鍵差異：
#   只寄出 ai_score >= AI_SCORE_THRESHOLD 的文章
#   每篇文章顯示 AI 評分條 + Groq 的一句話重點
#   附上 Gemini 分析用的 Prompt 模板（Email 底部）
# ============================================================

import smtplib
import logging
import os
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from datetime import datetime
from collections import defaultdict
from database import get_recent_articles
from config import (
    EMAIL_SENDER, EMAIL_PASSWORD,
    EMAIL_RECEIVERS, SMTP_HOST, SMTP_PORT,
    DIGEST_DAYS, AI_SCORE_THRESHOLD, FULL_CONTENT_SCORE_THRESHOLD,
)

os.makedirs("logs", exist_ok=True)

logging.basicConfig(
    filename="logs/pipeline_digest.log",
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

# 各源頭的顯示設定
SOURCE_META = {
    "semianalysis":        {"label": "SemiAnalysis",           "icon": "🔬", "color": "#6366f1"},
    "trendforce":          {"label": "TrendForce",              "icon": "📊", "color": "#0ea5e9"},
    "digitimes":           {"label": "DIGITIMES",               "icon": "📡", "color": "#10b981"},
    "sec_edgar":           {"label": "SEC EDGAR 法說會/季報",   "icon": "📋", "color": "#f59e0b"},
    "tw_revenue":          {"label": "台股月營收",               "icon": "🇹🇼", "color": "#e11d48"},
    "semi_engineering":    {"label": "Semiconductor Engineering","icon": "⚙️", "color": "#7c3aed"},
    "eetimes":             {"label": "EE Times",                "icon": "⚡", "color": "#b45309"},
    "toms_hardware":       {"label": "Tom's Hardware",          "icon": "🖥️", "color": "#0369a1"},
    "serve_the_home":      {"label": "ServeTheHome",            "icon": "🗄️", "color": "#0f766e"},
    "next_platform":       {"label": "Next Platform",           "icon": "🚀", "color": "#7e22ce"},
    "fabricated_knowledge":{"label": "Fabricated Knowledge",    "icon": "🏭", "color": "#be123c"},
}

SOURCE_ORDER = [
    "sec_edgar", "tw_revenue",
    "semianalysis", "fabricated_knowledge",
    "semi_engineering", "next_platform",
    "trendforce", "digitimes",
    "eetimes", "toms_hardware", "serve_the_home",
]


def _score_bar(score: int) -> str:
    """把分數轉成視覺化的分數條，例如 ████████░░ 8/10"""
    if not score:
        return ""
    filled = "█" * score
    empty  = "░" * (10 - score)
    color  = "#16a34a" if score >= 8 else "#ca8a04" if score >= 6 else "#dc2626"
    return f'<span style="font-family:monospace;color:{color};font-size:13px;">{filled}{empty}</span> <strong style="color:{color};">{score}/10</strong>'


def build_analysis_pack() -> str:
    """產出給 LLM 分析用的 Markdown pack（非人類閱讀）"""
    all_articles  = get_recent_articles(days=DIGEST_DAYS)
    pack_articles = get_recent_articles(days=DIGEST_DAYS, min_score=FULL_CONTENT_SCORE_THRESHOLD)
    pack_articles = sorted(pack_articles, key=lambda a: a.get("ai_score") or 0, reverse=True)

    date_str = datetime.now().strftime("%Y-%m-%d")
    lines = [
        f"# Signal-Source Analysis Pack — {date_str}",
        "",
        f"本週收集：{len(all_articles)} 篇 | AI 評分 ≥ {FULL_CONTENT_SCORE_THRESHOLD} 精選：{len(pack_articles)} 篇",
        "",
        "---",
    ]

    for article in pack_articles:
        score        = article.get("ai_score")
        title        = article.get("title", "")
        source_type  = article.get("source_type", "")
        ticker       = article.get("ticker", "")
        published    = article.get("published", "")
        ai_summary   = article.get("ai_summary", "")
        full_content = article.get("full_content", "")

        score_label = f"{score}/10" if score is not None else "?/10"
        meta_label  = SOURCE_META.get(source_type, {}).get("label", source_type)

        lines.append("")
        lines.append(f"## [{score_label}] {title}")
        lines.append("")
        lines.append(f"- 來源：{meta_label}")
        if ticker:
            lines.append(f"- 公司：{ticker}")
        if published:
            lines.append(f"- 日期：{published[:10]}")
        if ai_summary:
            lines.append("")
            lines.append(f"**重點：** {ai_summary}")
        if full_content:
            lines.append("")
            lines.append("### 全文")
            lines.append("")
            lines.append(full_content)
        lines.append("")
        lines.append("---")

    return "\n".join(lines)


def build_article_html(article: dict) -> str:
    """單篇文章的 HTML 區塊，含 AI 評分和重點摘要"""
    title       = article.get("title", "")
    url         = article.get("url", "#")
    ai_summary  = article.get("ai_summary", "")
    published   = article.get("published", "")
    ticker      = article.get("ticker", "")
    filing_type = article.get("filing_type", "")
    ai_score    = article.get("ai_score")

    # Badge（SEC 文件才有）
    badge = ""
    if ticker:
        badge += f'<span style="background:#fef3c7;color:#92400e;padding:2px 8px;border-radius:4px;font-size:12px;font-weight:bold;margin-right:6px;">{ticker}</span>'
    if filing_type:
        badge += f'<span style="background:#dbeafe;color:#1e40af;padding:2px 8px;border-radius:4px;font-size:12px;font-weight:bold;">{filing_type}</span>'

    badge_html   = f'<div style="margin-bottom:6px;">{badge}</div>' if badge else ""
    date_html    = f'<span style="color:#9ca3af;font-size:12px;margin-left:8px;">{published[:10]}</span>' if published else ""
    score_html   = f'<div style="margin:6px 0;">{_score_bar(ai_score)}</div>' if ai_score else ""
    summary_html = f'<p style="margin:6px 0 0;color:#374151;line-height:1.6;font-size:14px;border-left:3px solid #e5e7eb;padding-left:10px;">{ai_summary}</p>' if ai_summary else ""

    return f"""
    <div style="margin-bottom:20px;padding:16px;background:#f9fafb;border-left:3px solid #d1d5db;border-radius:6px;">
        {badge_html}
        <a href="{url}" style="font-size:15px;font-weight:600;color:#111827;text-decoration:none;line-height:1.5;">
            {title}
        </a>
        {date_html}
        {score_html}
        {summary_html}
        <div style="margin-top:10px;">
            <a href="{url}" style="font-size:12px;color:#6366f1;text-decoration:none;">原文連結 →</a>
        </div>
    </div>"""


def build_digest_html(total_collected: int, total_threshold: int, total_pack: int) -> str:
    """精簡版 Email HTML：只顯示統計漏斗，正文詳見附件"""
    date_str = datetime.now().strftime("%Y 年 %m 月 %d 日")
    filename = f"analysis_pack_{datetime.now().strftime('%Y-%m-%d')}.md"
    return f"""
    <html>
    <head><meta charset="UTF-8"></head>
    <body style="font-family:-apple-system,Arial,sans-serif;max-width:700px;margin:auto;padding:24px;color:#111827;">

        <div style="text-align:center;padding:28px 0;border-bottom:1px solid #e5e7eb;margin-bottom:32px;">
            <h1 style="font-size:24px;color:#111827;margin:0;">📡 Signal-Source 週報</h1>
            <p style="color:#6b7280;margin-top:8px;font-size:14px;">{date_str} · AI 預評分篩選</p>
            <p style="color:#9ca3af;font-size:13px;margin-top:4px;">
                本週收集 {total_collected} 篇
                → ≥{AI_SCORE_THRESHOLD} 分 {total_threshold} 篇
                → ≥{FULL_CONTENT_SCORE_THRESHOLD} 分精選 {total_pack} 篇
            </p>
        </div>

        <div style="padding:20px;background:#f0f9ff;border:1px solid #bae6fd;border-radius:8px;text-align:center;">
            <p style="margin:0;font-size:15px;color:#0369a1;">
                詳細情報請見附件 <strong>{filename}</strong>
            </p>
        </div>

        <div style="text-align:center;padding:20px;border-top:1px solid #e5e7eb;color:#9ca3af;font-size:12px;margin-top:32px;">
            Signal-Source 自動生成 · AI 評分由 Groq 提供
        </div>
    </body>
    </html>"""


def build_email_html(articles_by_source: dict, total: int, filtered: int) -> str:
    """組裝完整 HTML Email"""
    date_str      = datetime.now().strftime("%Y 年 %m 月 %d 日")
    sections_html = ""

    for source_type in SOURCE_ORDER:
        articles = articles_by_source.get(source_type, [])
        if not articles:
            continue

        meta  = SOURCE_META.get(source_type, {"label": source_type, "icon": "📰", "color": "#6b7280"})
        items = "".join(build_article_html(a) for a in articles)

        sections_html += f"""
        <div style="margin-bottom:40px;">
            <h2 style="font-size:18px;color:#111827;border-bottom:2px solid {meta['color']};padding-bottom:8px;margin-bottom:16px;">
                {meta['icon']} {meta['label']}
                <span style="font-size:13px;font-weight:normal;color:#6b7280;margin-left:8px;">({len(articles)} 篇)</span>
            </h2>
            {items}
        </div>"""

    # Gemini 分析提示區塊
    gemini_prompt = (
        "以下是本週半導體產業的精選情報（已由 AI 預評分篩選），"
        "來源包含 SEC 法說會、SemiAnalysis 深度分析、TrendForce 研調、DIGITIMES 供應鏈消息。"
        "請從以下三個維度分析本週最重要的結構性變化：\\n"
        "1. 供給端：產能、CapEx、供應商策略\\n"
        "2. 需求端：AI 晶片拉貨、庫存水位\\n"
        "3. 技術節點：HBM、CoWoS、先進封裝\\n"
        "最後指出一個你認為最值得追蹤的潛在機會或風險。"
    )

    gemini_tip = f"""
    <div style="margin:32px 0;padding:20px;background:#fffbeb;border:1px solid #fcd34d;border-radius:8px;">
        <h3 style="margin:0 0 12px;font-size:15px;color:#92400e;">💡 Gemini 分析提示</h3>
        <p style="margin:0 0 10px;font-size:13px;color:#78350f;">複製上方情報後，搭配以下 Prompt 使用：</p>
        <div style="background:#fff;padding:14px;border-radius:6px;font-size:13px;color:#374151;line-height:1.8;white-space:pre-wrap;">{gemini_prompt}</div>
    </div>"""

    return f"""
    <html>
    <head><meta charset="UTF-8"></head>
    <body style="font-family:-apple-system,Arial,sans-serif;max-width:700px;margin:auto;padding:24px;color:#111827;">

        <div style="text-align:center;padding:28px 0;border-bottom:1px solid #e5e7eb;margin-bottom:32px;">
            <h1 style="font-size:24px;color:#111827;margin:0;">📡 Signal-Source 週報</h1>
            <p style="color:#6b7280;margin-top:8px;font-size:14px;">
                {date_str} · AI 預評分篩選
            </p>
            <p style="color:#9ca3af;font-size:13px;margin-top:4px;">
                本週收集 {filtered} 篇 → AI 評分 ≥ {AI_SCORE_THRESHOLD} 分保留 {total} 篇
            </p>
        </div>

        {sections_html}

        {gemini_tip}

        <div style="text-align:center;padding:20px;border-top:1px solid #e5e7eb;color:#9ca3af;font-size:12px;margin-top:32px;">
            Signal-Source 自動生成 · AI 評分由 Groq 提供
        </div>
    </body>
    </html>"""


def send_email(html_content: str, total_articles: int, analysis_pack: str = ""):
    receivers = [r.strip() for r in EMAIL_RECEIVERS.split(",") if r.strip()]

    msg            = MIMEMultipart("mixed")
    msg["Subject"] = f"📡 Signal-Source 週報 — {datetime.now().strftime('%Y/%m/%d')} ({total_articles} 篇精選)"
    msg["From"]    = EMAIL_SENDER
    msg["To"]      = ", ".join(receivers)

    # HTML 包在 alternative 子結構裡（標準 mixed + alternative 巢狀）
    alt = MIMEMultipart("alternative")
    alt.attach(MIMEText(html_content, "html", "utf-8"))
    msg.attach(alt)

    if analysis_pack:
        filename    = f"analysis_pack_{datetime.now().strftime('%Y-%m-%d')}.md"
        attachment  = MIMEBase("application", "octet-stream")
        attachment.set_payload(analysis_pack.encode("utf-8"))
        encoders.encode_base64(attachment)
        attachment.add_header("Content-Disposition", "attachment", filename=filename)
        msg.attach(attachment)

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.starttls()
        server.login(EMAIL_SENDER, EMAIL_PASSWORD)
        server.sendmail(EMAIL_SENDER, receivers, msg.as_string())

    print(f"📧 週報已寄出 → {receivers}")
    logging.info(f"週報寄出成功，共 {total_articles} 篇")


def run():
    print(f"\n{'='*55}")
    print(f"📊 Pipeline Digest 開始 — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*55}")

    all_articles      = get_recent_articles(days=DIGEST_DAYS)
    filtered_articles = get_recent_articles(days=DIGEST_DAYS, min_score=AI_SCORE_THRESHOLD)
    pack_articles     = get_recent_articles(days=DIGEST_DAYS, min_score=FULL_CONTENT_SCORE_THRESHOLD)

    print(f"\n📦 本週收集：{len(all_articles)} 篇")
    print(f"✅ AI 評分 ≥ {AI_SCORE_THRESHOLD} 分：{len(filtered_articles)} 篇")
    print(f"⭐ AI 評分 ≥ {FULL_CONTENT_SCORE_THRESHOLD} 分精選：{len(pack_articles)} 篇")

    if not pack_articles:
        print("⚠️  沒有符合精選門檻的文章，請先執行 pipeline_collect.py 或降低 FULL_CONTENT_SCORE_THRESHOLD")
        return

    pack = build_analysis_pack()
    html = build_digest_html(len(all_articles), len(filtered_articles), len(pack_articles))
    send_email(html, len(pack_articles), analysis_pack=pack)

    print(f"\n🎉 Pipeline Digest 完成！")


if __name__ == "__main__":
    run()