# ============================================================
# pipeline_digest.py — 每週執行：撈資料 → 整理 HTML → 寄信
#
# 對應 Signal-Flow 的 pipeline_b.py
# 關鍵差異：
#   不做 AI 分析！保留原文，讓你用 Gemini 自己分析
#   Email 格式針對「餵給 Gemini」做優化
#   分源頭呈現，而不是混在一起
# ============================================================

import smtplib
import logging
import os
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime
from collections import defaultdict
from database import get_recent_articles
from config import (
    EMAIL_SENDER, EMAIL_PASSWORD,
    EMAIL_RECEIVERS, SMTP_HOST, SMTP_PORT,
    DIGEST_DAYS,
)

os.makedirs("logs", exist_ok=True)

logging.basicConfig(
    filename="logs/pipeline_digest.log",
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

# 各源頭的顯示設定
SOURCE_META = {
    "semianalysis":  {"label": "SemiAnalysis",       "icon": "🔬", "color": "#6366f1"},
    "trendforce":    {"label": "TrendForce",          "icon": "📊", "color": "#0ea5e9"},
    "digitimes":     {"label": "DIGITIMES",           "icon": "📡", "color": "#10b981"},
    "sec_edgar":     {"label": "SEC EDGAR 法說會/季報","icon": "📋", "color": "#f59e0b"},
    "seeking_alpha": {"label": "Seeking Alpha",       "icon": "💰", "color": "#ef4444"},
}

# Email 中各源頭的排列順序
SOURCE_ORDER = ["sec_edgar", "semianalysis", "trendforce", "digitimes", "seeking_alpha"]


def build_article_html(article: dict) -> str:
    """
    單篇文章的 HTML 區塊
    
    設計原則：
    - 標題 + 摘要盡量保留原文（方便你複製貼到 Gemini）
    - 明確標示來源和日期（幫助 Gemini 理解資訊時序）
    - SEC 文件特別標示 ticker 和表單類型
    """
    title       = article.get("title", "")
    url         = article.get("url", "#")
    summary     = article.get("summary", "")
    published   = article.get("published", "")
    ticker      = article.get("ticker", "")
    filing_type = article.get("filing_type", "")

    # SEC 文件特別加上 badge
    badge = ""
    if ticker:
        badge = f'<span style="background:#fef3c7;color:#92400e;padding:2px 8px;border-radius:4px;font-size:12px;font-weight:bold;margin-right:6px;">{ticker}</span>'
    if filing_type:
        badge += f'<span style="background:#dbeafe;color:#1e40af;padding:2px 8px;border-radius:4px;font-size:12px;font-weight:bold;">{filing_type}</span>'

    summary_html = f'<p style="margin:8px 0 0;color:#374151;line-height:1.6;font-size:14px;">{summary}</p>' if summary else ""
    badge_html   = f'<div style="margin-bottom:6px;">{badge}</div>' if badge else ""
    date_html    = f'<span style="color:#9ca3af;font-size:12px;">  {published[:10]}</span>' if published else ""

    return f"""
    <div style="margin-bottom:16px;padding:14px 16px;background:#f9fafb;border-left:3px solid #d1d5db;border-radius:4px;">
        {badge_html}
        <a href="{url}" style="font-size:15px;font-weight:600;color:#111827;text-decoration:none;line-height:1.4;">
            {title}
        </a>
        {date_html}
        {summary_html}
        <div style="margin-top:8px;">
            <a href="{url}" style="font-size:12px;color:#6366f1;">原文連結 →</a>
        </div>
    </div>"""


def build_email_html(articles_by_source: dict) -> str:
    """
    組裝完整的 HTML Email
    
    結構：
    Header → [各源頭區塊] → 使用提示 → Footer
    
    「使用提示」區塊是重點：
    直接提示你如何把這封 Email 的內容貼給 Gemini
    """
    date_str      = datetime.now().strftime("%Y 年 %m 月 %d 日")
    sections_html = ""

    for source_type in SOURCE_ORDER:
        articles = articles_by_source.get(source_type, [])
        if not articles:
            continue

        meta  = SOURCE_META.get(source_type, {"label": source_type, "icon": "📰", "color": "#6b7280"})
        items = "".join(build_article_html(a) for a in articles)

        sections_html += f"""
        <div style="margin-bottom:36px;">
            <h2 style="font-size:18px;color:#111827;border-bottom:2px solid {meta['color']};padding-bottom:8px;margin-bottom:16px;">
                {meta['icon']} {meta['label']}
                <span style="font-size:13px;font-weight:normal;color:#6b7280;margin-left:8px;">({len(articles)} 篇)</span>
            </h2>
            {items}
        </div>"""

    # 使用提示區塊：告訴你怎麼用這封信
    gemini_tip = """
    <div style="margin:32px 0;padding:20px;background:#fffbeb;border:1px solid #fcd34d;border-radius:8px;">
        <h3 style="margin:0 0 12px;font-size:15px;color:#92400e;">💡 Gemini 分析提示</h3>
        <p style="margin:0 0 8px;font-size:14px;color:#78350f;line-height:1.6;">
            將上方內容複製後，用以下 Prompt 貼給 Gemini：
        </p>
        <div style="background:#fff;padding:12px;border-radius:4px;font-family:monospace;font-size:13px;color:#374151;line-height:1.7;">
            「以下是本週半導體產業的原始情報，來源包含 SEC 法說會、SemiAnalysis 深度分析、TrendForce 研調。
            請從供給端（產能、CapEx）、需求端（AI 晶片拉貨）、技術節點（HBM、CoWoS）三個維度，
            找出本週最重要的結構性變化，並指出可能的投資機會或風險。」
        </div>
    </div>"""

    total = sum(len(v) for v in articles_by_source.values())

    return f"""
    <html>
    <head><meta charset="UTF-8"></head>
    <body style="font-family:-apple-system,Arial,sans-serif;max-width:700px;margin:auto;padding:24px;color:#111827;">

        <div style="text-align:center;padding:28px 0;border-bottom:1px solid #e5e7eb;margin-bottom:32px;">
            <h1 style="font-size:24px;color:#111827;margin:0;">📡 Industry Radar 週報</h1>
            <p style="color:#6b7280;margin-top:8px;font-size:14px;">
                {date_str} · 共 {total} 篇原始情報 · 未經 AI 改寫
            </p>
        </div>

        {sections_html}

        {gemini_tip}

        <div style="text-align:center;padding:20px;border-top:1px solid #e5e7eb;color:#9ca3af;font-size:12px;">
            本郵件由 Industry Radar 自動生成 · 所有內容均為原始資料，未經 AI 摘要
        </div>
    </body>
    </html>"""


def send_email(html_content: str, total_articles: int):
    """寄出 HTML Email，對應 Signal-Flow 的 send_email()"""
    receivers = [r.strip() for r in EMAIL_RECEIVERS.split(",") if r.strip()]

    msg            = MIMEMultipart("alternative")
    msg["Subject"] = f"📡 Industry Radar 週報 — {datetime.now().strftime('%Y/%m/%d')} ({total_articles} 篇情報)"
    msg["From"]    = EMAIL_SENDER
    msg["To"]      = ", ".join(receivers)
    msg.attach(MIMEText(html_content, "html", "utf-8"))

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.starttls()
        server.login(EMAIL_SENDER, EMAIL_PASSWORD)
        server.sendmail(EMAIL_SENDER, receivers, msg.as_string())

    print(f"📧 週報已寄出 → {receivers}")
    logging.info(f"週報寄出成功 → {receivers}，共 {total_articles} 篇")


def run():
    print(f"\n{'='*50}")
    print(f"📊 Pipeline Digest 開始 — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*50}")

    # 撈最近 7 天的文章
    all_articles = get_recent_articles(days=DIGEST_DAYS)
    print(f"\n📦 共撈到 {len(all_articles)} 篇文章")

    if not all_articles:
        print("⚠️  沒有文章可以生成週報，請先執行 pipeline_collect.py")
        return

    # 按源頭分組
    by_source = defaultdict(list)
    for article in all_articles:
        by_source[article["source_type"]].append(article)

    # 顯示各源頭文章數
    for source, articles in by_source.items():
        meta = SOURCE_META.get(source, {})
        print(f"   {meta.get('icon','📰')} {meta.get('label', source)}: {len(articles)} 篇")

    # 組裝並寄出
    html = build_email_html(dict(by_source))
    send_email(html, len(all_articles))

    print(f"\n🎉 Pipeline Digest 完成！")


if __name__ == "__main__":
    run()
