# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Industry Radar** — An automated semiconductor industry intelligence system. It scrapes articles from 5 sources daily, enriches them with full-text via Jina AI and AI scoring via Groq, stores results in SQLite, and sends a weekly HTML digest email. All orchestration runs on GitHub Actions.

## Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Run daily collection pipeline (scrape → fetch → score → save)
python pipeline_collect.py

# Run weekly digest (query DB → build HTML email → send via Gmail)
python pipeline_digest.py

# Debug SEC EDGAR API
python debug_sec.py
```

**Required environment variables:**
```bash
GROQ_API_KEY=...                    # Groq LLaMA inference
SEC_USER_AGENT="IndustryRadar your@email.com"   # Required by SEC EDGAR API
EMAIL_SENDER=...                    # Gmail address
EMAIL_PASSWORD=...                  # 16-char Gmail App Password
EMAIL_RECEIVERS=...                 # Comma-separated recipients
```

In GitHub Actions, these are set as repository secrets.

## Architecture

### Data Pipeline

```
scraper.py (5 sources)
    → content_fetcher.py (Jina AI r.jina.ai/{url})
    → scorer.py (Groq LLaMA 3.3-70B)
    → database.py (SQLite: data/industry_radar.db)
    → pipeline_digest.py (HTML email via SMTP Gmail)
```

### Key Modules

- **`config.py`** — Single source of truth: API keys, WATCHLIST (8 tickers with SEC CIK numbers), scoring threshold, email settings, and per-source scraping params.
- **`scraper.py`** — Fetches from SemiAnalysis (RSS), TrendForce (HTML scrape), DIGITIMES (RSS), SEC EDGAR (official API, 8-K/10-Q), and Seeking Alpha (per-ticker RSS). Each fetcher returns a list of dicts with `title`, `url`, `summary`, `source_type`, etc.
- **`content_fetcher.py`** — Calls `https://r.jina.ai/{url}` to get clean Markdown text. SEC docs use direct requests + BeautifulSoup instead. Rate-limited to 2s between calls. Truncates to 3000 chars for Groq context.
- **`scorer.py`** — Sends title + summary + full_content to Groq. Returns JSON with `score` (1–10), `reason`, and `key_point` (50-char summary in Traditional Chinese). Temperature 0.1 for consistency; fallback score 5 on failure.
- **`database.py`** — SQLite with auto-migration (`ALTER TABLE` for new columns). Deduplication by URL (`INSERT OR IGNORE`). Main query: `get_recent_articles(days, min_score)`.
- **`pipeline_collect.py`** — Orchestrates daily run: init DB → for each source: fetch → deduplicate → batch_fetch → batch_score → save.
- **`pipeline_digest.py`** — Queries last 7 days above score threshold, groups by source, builds HTML email with score bars (█░ characters), and sends via SMTP.

### GitHub Actions

- **`collect.yml`**: Runs `pipeline_collect.py` daily at UTC 00:00, then commits `data/industry_radar.db` back to the repo. Requires `contents: write` permission.
- **`digest.yml`**: Runs `pipeline_digest.py` weekly Monday UTC 01:00. No commit step — only sends email.

### Database Schema (SQLite)

Table `articles`: `id`, `source_type`, `ticker`, `filing_type`, `title`, `url` (UNIQUE), `summary`, `full_content`, `ai_score`, `ai_summary`, `source`, `published`, `created_at`.

## Key Design Decisions

- **Free-tier services only:** Jina AI (free reader API), Groq (free tier LLaMA), SEC EDGAR (public API), RSS feeds.
- **Database in git:** `data/industry_radar.db` is committed after each collection run — no external DB needed.
- **Fail-safe design:** Errors in any single source or article don't abort the pipeline; failures are logged and skipped.
- **Korean stocks (SK Hynix, Samsung) have no SEC CIK** — they're in WATCHLIST for Seeking Alpha RSS but skipped in SEC EDGAR fetching.
- **Scoring threshold** (`SCORE_THRESHOLD = 6` in config) filters the digest. Scores below threshold are stored but not emailed.
