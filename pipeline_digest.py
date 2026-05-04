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
from datetime import datetime
from collections import defaultdict
from database import get_recent_articles, save_weekly_digest, get_last_weekly_digest
from synthesizer import synthesize_weekly, build_digest_markdown
from config import (
    EMAIL_SENDER, EMAIL_PASSWORD,
    EMAIL_RECEIVERS, SMTP_HOST, SMTP_PORT,
    DIGEST_DAYS, AI_SCORE_THRESHOLD, FULL_CONTENT_SCORE_THRESHOLD,
    SOURCE_META, SOURCE_ORDER,
)

os.makedirs("logs", exist_ok=True)
os.makedirs("digests", exist_ok=True)

logging.basicConfig(
    filename="logs/pipeline_digest.log",
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)


def _score_bar(score: int) -> str:
    """把分數轉成視覺化的分數條，例如 ████████░░ 8/10"""
    if not score:
        return ""
    filled = "█" * score
    empty  = "░" * (10 - score)
    color  = "#16a34a" if score >= 8 else "#ca8a04" if score >= 6 else "#dc2626"
    return f'<span style="font-family:monospace;color:{color};font-size:13px;">{filled}{empty}</span> <strong style="color:{color};">{score}/10</strong>'


def build_digest_html(total_collected: int, total_threshold: int, total_pack: int, digest_markdown: str = "") -> str:
    """精簡版 Email HTML：顯示統計漏斗 + 內嵌 analysis_pack"""
    date_str = datetime.now().strftime("%Y 年 %m 月 %d 日")

    pack_section = ""
    if digest_markdown:
        import html as html_module
        escaped = html_module.escape(digest_markdown)
        pack_section = f"""
        <h2 style="font-size:18px;color:#111827;border-bottom:2px solid #6366f1;padding-bottom:8px;margin:32px 0 16px;">
            📎 週報全文
        </h2>
        <pre style="white-space:pre-wrap;font-size:13px;line-height:1.6;color:#374151;background:#f9fafb;padding:16px;border-radius:8px;overflow-x:auto;">{escaped}</pre>"""

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

        {pack_section}

        <div style="text-align:center;padding:20px;border-top:1px solid #e5e7eb;color:#9ca3af;font-size:12px;margin-top:32px;">
            Signal-Source 自動生成 · AI 評分由 Groq 提供
        </div>
    </body>
    </html>"""


def send_email(html_content: str, total_articles: int):
    receivers = [r.strip() for r in EMAIL_RECEIVERS.split(",") if r.strip()]

    msg            = MIMEMultipart("alternative")
    msg["Subject"] = f"📡 Signal-Source 週報 — {datetime.now().strftime('%Y/%m/%d')} ({total_articles} 篇精選)"
    msg["From"]    = EMAIL_SENDER
    msg["To"]      = ", ".join(receivers)

    msg.attach(MIMEText(html_content, "html", "utf-8"))

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

    last_digest = get_last_weekly_digest()
    if last_digest:
        print(f"📅 找到上週週報（{last_digest['run_date']}），啟用跨週比較")
    else:
        print("📅 無歷史週報，首次生成")

    print("\n🤖 呼叫 Claude 進行跨文章合成...")
    synthesis = synthesize_weekly(pack_articles, last_digest=last_digest)
    if synthesis:
        print("✅ 合成完成")
        run_date      = datetime.now().strftime("%Y-%m-%d")
        article_titles = [a.get("title", "") for a in pack_articles]
        save_weekly_digest(run_date, synthesis, article_titles)
        print(f"💾 本週合成結果已存入 DB（{run_date}）")
    else:
        print("⚠️  合成失敗，週報將只顯示原始文章清單")

    md       = build_digest_markdown(synthesis, pack_articles, len(all_articles))
    date_str = datetime.now().strftime("%Y-%m-%d")
    md_path  = f"digests/{date_str}.md"
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md)
    print(f"📝 週報已寫入 {md_path}")

    html = build_digest_html(len(all_articles), len(filtered_articles), len(pack_articles), digest_markdown=md)
    send_email(html, len(pack_articles))

    print(f"\n🎉 Pipeline Digest 完成！")


if __name__ == "__main__":
    run()