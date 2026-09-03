# ============================================================
# pipeline_digest.py — 每週執行：撈文章 → 產生 evidence pack markdown
#
# 不再呼叫任何 LLM。文章篩選與排序完全依賴 pipeline_collect.py
# 寫入的 rule_score / is_junk（規則式評分），不使用 ai_score。
#
# 這一步只做 markdown 產生：
#   get_recent_articles() → render_evidence_pack() → digests/{date}.md
# 寄信（send_email）與週報 DB 寫入（save_weekly_digest）暫緩，
# 待階段 2b 實作。
# ============================================================

import argparse
import smtplib
import logging
import os
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication
from datetime import datetime, timedelta
from collections import defaultdict
from database import get_recent_articles
from config import (
    EMAIL_SENDER, EMAIL_PASSWORD,
    EMAIL_RECEIVERS, SMTP_HOST, SMTP_PORT,
    DIGEST_DAYS, SOURCE_META, SOURCE_ORDER,
    EVIDENCE_FULL_TEXT_THRESHOLD, EVIDENCE_SUMMARY_THRESHOLD, EVIDENCE_FULL_TEXT_CHARS,
)

os.makedirs("logs", exist_ok=True)
os.makedirs("digests", exist_ok=True)

logging.basicConfig(
    filename="logs/pipeline_digest.log",
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)


def _rule_score(article: dict) -> int:
    """rule_score 可能為 None（尚未跑過規則式評分的舊資料），視為 0（併入低訊號層）"""
    score = article.get("rule_score")
    return score if score is not None else 0


def _truncate(text: str, n: int) -> str:
    text = (text or "").strip()
    if len(text) <= n:
        return text
    return text[:n].rstrip() + "…"


def _tier_split(articles: list[dict]) -> tuple[list[dict], list[dict], list[dict]]:
    """把非 tw_revenue / sec_edgar 的文章依 rule_score 分成高/中/低三層（供 render 與 email 共用）"""
    intel_articles = [a for a in articles if a.get("source_type") not in ("tw_revenue", "sec_edgar")]
    intel_sorted = sorted(intel_articles, key=_rule_score, reverse=True)
    high = [a for a in intel_sorted if _rule_score(a) >= EVIDENCE_FULL_TEXT_THRESHOLD]
    mid  = [a for a in intel_sorted if EVIDENCE_SUMMARY_THRESHOLD <= _rule_score(a) < EVIDENCE_FULL_TEXT_THRESHOLD]
    low  = [a for a in intel_sorted if _rule_score(a) < EVIDENCE_SUMMARY_THRESHOLD]
    return high, mid, low


def send_email(md_path: str, stats: dict):
    """
    寄送 evidence pack：.md 作為附件，body 只放摘要統計。
    evidence pack 是給下游 LLM 讀的原料，不是排版給人看的 HTML 週報，
    所以不內嵌內容，只附檔。
    """
    receivers = [r.strip() for r in EMAIL_RECEIVERS.split(",") if r.strip()]

    total = stats.get("total", 0)
    subject_date = datetime.now().strftime("%Y/%m/%d")

    body_lines = [
        f"Signal-Source Evidence Pack",
        f"期間：{stats.get('start', '')} ~ {stats.get('end', '')}",
        f"總篇數：{total}",
        f"高訊號：{stats.get('high_count', 0)} 篇",
        f"中訊號：{stats.get('mid_count', 0)} 篇",
        f"低訊號：{stats.get('low_count', 0)} 篇",
        f"全文覆蓋率：{stats.get('full_text_coverage_pct', 0):.0f}%",
        "",
        "各 source_type 篇數：",
    ]
    for src, n in stats.get("src_counts", {}).items():
        label = SOURCE_META.get(src, {}).get("label", src)
        body_lines.append(f"  {label}（{src}）：{n} 篇")

    body = "\n".join(body_lines)

    msg            = MIMEMultipart()
    msg["Subject"] = f"📡 Signal-Source Evidence Pack — {subject_date}（{total} 篇）"
    msg["From"]    = EMAIL_SENDER
    msg["To"]      = ", ".join(receivers)

    msg.attach(MIMEText(body, "plain", "utf-8"))

    with open(md_path, "rb") as f:
        attachment = MIMEApplication(f.read(), _subtype="markdown")
    attachment.add_header(
        "Content-Disposition", "attachment", filename=os.path.basename(md_path)
    )
    msg.attach(attachment)

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.starttls()
        server.login(EMAIL_SENDER, EMAIL_PASSWORD)
        server.sendmail(EMAIL_SENDER, receivers, msg.as_string())

    print(f"📧 evidence pack 已寄出 → {receivers}")
    logging.info(f"evidence pack 寄出成功，共 {total} 篇")


def render_evidence_pack(articles: list[dict], stats: dict) -> str:
    """
    把文章清單整理成給人 / 給下游 LLM 讀的 evidence pack markdown。
    不做任何摘要或合成，純粹依 rule_score 分層排版。
    """
    lines = []

    date_str = stats.get("date", datetime.now().strftime("%Y-%m-%d"))
    start    = stats.get("start", "")
    end      = stats.get("end", "")
    total    = stats.get("total", len(articles))
    coverage = stats.get("full_text_coverage_pct", 0)

    lines.append(f"# Signal-Source Evidence Pack · {date_str}")
    lines.append(f"> 期間：{start} ~ {end} | 收錄 {total} 篇 | 全文覆蓋 {coverage:.0f}%")
    lines.append("")

    tw_articles  = [a for a in articles if a.get("source_type") == "tw_revenue"]
    sec_articles = [a for a in articles if a.get("source_type") == "sec_edgar"]

    # ── 台股月營收 ──────────────────────────────────────────
    lines.append("## 台股月營收")
    lines.append("")
    if tw_articles:
        for a in sorted(tw_articles, key=lambda a: a.get("ticker") or ""):
            lines.append(f"- {a.get('ticker', '')} {a.get('title', '')}")
    else:
        lines.append("（本期無資料）")
    lines.append("")

    # ── SEC Filings ─────────────────────────────────────────
    lines.append("## SEC Filings")
    lines.append("")
    if sec_articles:
        for a in sorted(sec_articles, key=lambda a: (a.get("ticker") or "", a.get("published") or "")):
            lines.append(
                f"- **{a.get('ticker', '')}** {a.get('filing_type', '')} · "
                f"{a.get('published', '')} · [原文]({a.get('url', '')})"
            )
    else:
        lines.append("（本期無資料）")
    lines.append("")

    # ── 情報（依 rule_score 降序，分三層）─────────────────────
    lines.append("## 情報（依 rule_score 降序）")
    lines.append("")

    high, mid, low = _tier_split(articles)

    lines.append(f"### 高訊號（rule_score >= {EVIDENCE_FULL_TEXT_THRESHOLD}）")
    lines.append("")
    if high:
        for a in high:
            label = SOURCE_META.get(a.get("source_type"), {}).get("label", a.get("source_type", ""))
            lines.append(f"#### [{_rule_score(a)}] {a.get('title', '')}")
            lines.append(f"{a.get('published', '')} · {label} · [原文]({a.get('url', '')})")
            lines.append("")
            full_content = a.get("full_content") or ""
            if full_content:
                lines.append(_truncate(full_content, EVIDENCE_FULL_TEXT_CHARS))
            else:
                lines.append("⚠️ 無全文")
                summary = a.get("summary") or ""
                if summary:
                    lines.append(_truncate(summary, EVIDENCE_FULL_TEXT_CHARS))
            lines.append("")
    else:
        lines.append("（本期無資料）")
        lines.append("")

    lines.append(f"### 中訊號（{EVIDENCE_SUMMARY_THRESHOLD} <= rule_score < {EVIDENCE_FULL_TEXT_THRESHOLD}）")
    lines.append("")
    if mid:
        for a in mid:
            label   = SOURCE_META.get(a.get("source_type"), {}).get("label", a.get("source_type", ""))
            summary = _truncate(a.get("summary") or "", 300)
            lines.append(
                f"- **[{_rule_score(a)}] {a.get('title', '')}** · {label} · "
                f"{a.get('published', '')} · [原文]({a.get('url', '')})"
            )
            if summary:
                lines.append(f"  {summary}")
    else:
        lines.append("（本期無資料）")
    lines.append("")

    lines.append(f"### 低訊號（rule_score < {EVIDENCE_SUMMARY_THRESHOLD}）")
    lines.append("")
    if low:
        for a in low:
            label = SOURCE_META.get(a.get("source_type"), {}).get("label", a.get("source_type", ""))
            lines.append(f"- [{_rule_score(a)}] {a.get('title', '')} · {label} · [原文]({a.get('url', '')})")
    else:
        lines.append("（本期無資料）")
    lines.append("")

    # ── 統計 ────────────────────────────────────────────────
    lines.append("## 統計")
    lines.append("")

    lines.append("### 各 source_type 篇數")
    src_counts = defaultdict(int)
    for a in articles:
        src_counts[a.get("source_type") or "unknown"] += 1
    for src in SOURCE_ORDER:
        if src in src_counts:
            label = SOURCE_META.get(src, {}).get("label", src)
            lines.append(f"- {label}（{src}）：{src_counts[src]} 篇")
    for src, n in src_counts.items():
        if src not in SOURCE_ORDER:
            lines.append(f"- {src}：{n} 篇")
    lines.append("")

    lines.append("### content_completeness 分布")
    cc_counts = defaultdict(int)
    for a in articles:
        cc_counts[a.get("content_completeness") or "unknown"] += 1
    for k in ("full", "partial", "headline_only", "unknown"):
        if k in cc_counts:
            lines.append(f"- {k}：{cc_counts[k]} 篇")
    lines.append("")

    lines.append("### rule_score 分布")
    rs_counts = defaultdict(int)
    for a in articles:
        rs_counts[_rule_score(a)] += 1
    for s in range(0, 11):
        if s in rs_counts:
            suffix = "（含尚未規則式評分的舊資料）" if s == 0 else ""
            lines.append(f"- {s} 分：{rs_counts[s]} 篇{suffix}")
    lines.append("")

    return "\n".join(lines)


def run(dry_run: bool = False):
    print(f"\n{'='*55}")
    print(f"📊 Pipeline Digest 開始 — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*55}")

    articles = get_recent_articles(days=DIGEST_DAYS)
    print(f"\n📦 本期收集：{len(articles)} 篇（已排除 is_junk=1，未傳 min_score）")

    today = datetime.now().strftime("%Y-%m-%d")
    since = (datetime.now() - timedelta(days=DIGEST_DAYS)).strftime("%Y-%m-%d")

    full_count = sum(1 for a in articles if a.get("content_completeness") == "full")
    coverage   = (full_count / len(articles) * 100) if articles else 0.0

    high, mid, low = _tier_split(articles)

    src_counts = defaultdict(int)
    for a in articles:
        src_counts[a.get("source_type") or "unknown"] += 1
    ordered_src_counts = {src: src_counts[src] for src in SOURCE_ORDER if src in src_counts}
    for src, n in src_counts.items():
        if src not in SOURCE_ORDER:
            ordered_src_counts[src] = n

    stats = {
        "date":  today,
        "start": since,
        "end":   today,
        "total": len(articles),
        "full_text_coverage_pct": coverage,
        "high_count": len(high),
        "mid_count": len(mid),
        "low_count": len(low),
        "src_counts": ordered_src_counts,
    }

    md = render_evidence_pack(articles, stats)

    md_path = f"digests/{today}.md"
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md)

    if dry_run:
        preview_lines = md.splitlines()
        print("\n" + "\n".join(preview_lines[:80]))
        print(f"\n... (共 {len(preview_lines)} 行，{len(md)} 字元)")
        print(f"\n📝 evidence pack 已寫入 {md_path}（dry-run，跳過寄信與 save_weekly_digest）")
    else:
        print(f"\n📝 evidence pack 已寫入 {md_path}")
        send_email(md_path, stats)

    print(f"\n🎉 Pipeline Digest 完成！")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="只產生 markdown 預覽，不寄信、不寫 DB")
    args = parser.parse_args()
    run(dry_run=args.dry_run)
