# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Industry Radar** — An automated semiconductor industry intelligence system. It scrapes articles from 11 sources daily, enriches non-junk articles with full-text via Jina AI, scores every article with a zero-API rule-based scorer, stores results in SQLite, and sends a weekly evidence pack (a structured Markdown file, mailed as an attachment) for a downstream AI (with web search) to analyze. All orchestration runs on GitHub Actions.

## Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Run daily collection pipeline (scrape → dedupe → rule-score → save → fetch full text for non-junk)
python pipeline_collect.py

# Run weekly evidence pack generation (query DB → render markdown → mail as attachment)
python pipeline_digest.py

# Preview the evidence pack without mailing it. It still writes digests/{date}.md —
# git restore or delete that file after checking, so it isn't committed by mistake
python pipeline_digest.py --dry-run

# Build the pack for a past week: the window ends at the Taiwan Monday on or before
# the given date (Taiwan), e.g. to regenerate or preview an earlier evidence pack.
# Without --dry-run this also mails the pack; add --dry-run to only preview it
python pipeline_digest.py --date YYYY-MM-DD
python pipeline_digest.py --date YYYY-MM-DD --dry-run

# Debug SEC EDGAR API
python debug_sec.py

# Run offline tests (pytest is a dev-only dependency, not installed by any workflow)
pip install -r requirements-dev.txt
python -m pytest tests
```

**One-off maintenance scripts** (not run by any workflow; run manually when needed):

```bash
# Backfill rule_score / is_junk for historical articles that predate rule_scorer.py
# (used once when rule-based scoring was introduced; safe to re-run — always dry-run first)
python backfill_rule_score.py --dry-run
python backfill_rule_score.py

# Re-clean full_content for existing DB rows using the current content_fetcher
# cleaning rules (run again after tightening the Jina cleanup regexes, so
# historical rows benefit from the new rules too, not just newly scraped ones)
python clean_existing_content.py
```

**Required environment variables:**
```bash
SEC_USER_AGENT="IndustryRadar your@email.com"   # Required by SEC EDGAR API
EMAIL_SENDER=...                    # Gmail address
EMAIL_PASSWORD=...                  # 16-char Gmail App Password
EMAIL_RECEIVERS=...                 # Comma-separated recipients
```

In GitHub Actions, these are set as repository secrets. There are no LLM API keys anywhere in this project — scoring and filtering are pure Python string/keyword rules (`rule_scorer.py`), and `pipeline_digest.py` does not call any LLM either.

## Architecture

### Data Pipeline

```
scraper.py (11 sources)
    → deduplicator.py (title-similarity dedup, batch + cross-day)
    → rule_scorer.py (is_junk + score_by_rules — pure keyword rules, no API call)
    → database.py (SQLite: data/industry_radar.db)
    → content_fetcher.py (Jina AI r.jina.ai/{url}, only for non-junk articles)
    → pipeline_digest.py (Evidence Pack markdown, mailed as .md attachment)
```

### Key Modules

- **`config.py`** — Single source of truth: WATCHLIST (18 tickers; 16 have a `sec_cik`, while `000660.KS` and `005930.KS` have none), email settings, per-source scraping params, and the three Evidence Pack thresholds (`EVIDENCE_FULL_TEXT_THRESHOLD`, `EVIDENCE_SUMMARY_THRESHOLD`, `EVIDENCE_FULL_TEXT_CHARS`).
- **`scraper.py`** — Fetches from SemiAnalysis (RSS, `https://newsletter.semianalysis.com/feed`), TrendForce (HTML scrape), SEC EDGAR (official API, 8-K/10-Q), DIGITIMES / Semiconductor Engineering / EE Times / Tom's Hardware (RSS, keyword-filtered with `_is_relevant()` on title + summary), ServeTheHome / Next Platform / Fabricated Knowledge (RSS, unfiltered). Each fetcher returns a list of dicts with `title`, `url`, `summary`, `source_type`, etc. Note: `fetch_seeking_alpha()` also exists in this file but is **not** wired into `pipeline_collect.py` — it's dead code left over from an earlier source list.
  - **TrendForce** (`fetch_trendforce(known_urls=None)`) scrapes list pages 1 and 2 of `TRENDFORCE_NEWS_URL` (page 2's URL is parsed from page 1's "Next Page" link; if page 2 fails, page 1's results are still returned). Each page holds 5 chronological posts plus a curated carousel; both are kept, and only links matching `TRENDFORCE_ARTICLE_URL_RE` (`/news/YYYY/MM/DD/slug/`) count as articles, so category and pagination pages are never collected. URLs are stored exactly as in the page's `href` (no normalization). `published` is the date in the URL, which is a Taiwan date. There is no keyword filter (TrendForce relies on `rule_score` tiering instead), and `MAX_ARTICLES_PER_SOURCE` does not apply — two pages are the hard limit. URLs in `known_urls` are skipped before the per-article summary request.
- **`scraper_twstock.py`** — `fetch_tw_revenue_all()`, Taiwan monthly-revenue data (FinMind API). Bypasses rule scoring and Jina entirely (`skip_ai=True` in `pipeline_collect.py`'s source list); gets a fixed `rule_score=10, is_junk=0` when backfilled or written directly.
- **`deduplicator.py`** — `deduplicate_by_title()` (within-batch) and `filter_against_db_titles()` (cross-day, vs. recent DB titles) both use `difflib.SequenceMatcher` title-similarity (threshold 0.6). Both functions exempt articles via `_is_exempt()` and let them through unconditionally: either `source_type in TITLE_SIMILARITY_EXEMPT_SOURCES` (`sec_edgar`, `tw_revenue`), or a title matching one of `config.py`'s `TITLE_SIMILARITY_EXEMPT_PATTERNS` (regexes anchored at the start of the title via `re.match`; currently only `^\[Insights\] Memory Spot Price Update:` — a weekly TrendForce series whose consecutive titles score 0.47–0.74 against each other and were being dropped as duplicates). The exempt sources' titles are formatted strings (`[8-K] {company} — {date}`, `[月營收] {company} {year}年{month}月 ...`), not prose, so two *different* filings/months naturally score high on character similarity and would otherwise be misdetected as duplicates. Real dedup for exempt articles comes from `database.py`'s URL uniqueness (SEC accession numbers are unique per filing; pattern-exempt series rely on this alone) and, for `tw_revenue`, `tw_revenue_exists()` (year+month uniqueness).
- **`rule_scorer.py`** — Zero-API rule-based scorer, two functions:
  - `is_junk(title, source_type) -> bool` — an *exclusion* classifier (not a scorer). Five rules, any hit → junk: **A** (title has `$` plus an off/save/deal/discount phrase, unless it also mentions billion/million/trillion/acquisition/merger/partnership — those are business news, not a sale), **C** (contains " review", unless "under review"/"in review"/"reviewing"), **D** (contains "prime day"), **E** (hands-on / best of / buying guide / msrp / now just / all-time low / giveaway), **F** (title mixes a consumer-product word — rtx/ryzen/gaming/motherboard/etc. — with a transaction word — price/deal/sale/cheap/etc.). `sec_edgar` and `tw_revenue` are exempt (`JUNK_EXEMPT_SOURCES`) — their titles are formatted strings, not prose. Validated against ~3,900 pre-2026-08-17 Haiku-scored articles: exclusion misfire rate (junk flagged but Haiku had scored it ≥7) is 0.26% (1/383).
  - `score_by_rules(title, source_type) -> int` (1–10) — `SOURCE_BASE[source_type]` (a per-source prior, e.g. `sec_edgar`/`semianalysis`=7 down to `toms_hardware`/`serve_the_home`=3) plus up to +4 for `POSITIVE_KEYWORDS` hits (HBM, DRAM, CoWoS, TSMC, capex, 8-K, etc.) minus up to −4 for `NEGATIVE_KEYWORDS` hits (review, rtx, gaming, deal, etc.), then hard-capped to ≤3 if the title trips `_hard_cap_hit()` (a stricter, older $-plus-deal/review/prime-day check kept only for the numeric score, separate from `is_junk`'s five rules).
- **`content_fetcher.py`** — Calls `https://r.jina.ai/{url}` to get clean Markdown text. SEC docs use direct requests + BeautifulSoup instead. Rate-limited to 2s between calls (`JINA_DELAY_SECONDS`). Truncates to 3000 chars (`MAX_CONTENT_LENGTH`) to keep DB rows and the evidence pack a reasonable size. Only called for articles where `is_junk` is `False` — junk articles never get a Jina request.
- **`database.py`** — SQLite with auto-migration (`ALTER TABLE` for new columns). Deduplication by URL (`INSERT OR IGNORE`). `update_rule_score(url, rule_score, is_junk)` and `update_full_content(url, full_content)` are the write paths used by the current pipeline (they touch only `rule_score`/`is_junk`/`full_content`, never `ai_score`/`ai_summary`). `get_urls_by_source(source_type)` returns the set of stored URLs for one source (used to pass `known_urls` to `fetch_trendforce`). Main read query: `get_recent_articles(days=7, min_score=None, include_junk=False, min_rule_score=None, since_utc=None, until_utc=None)` — `since_utc` (inclusive) / `until_utc` (exclusive) are UTC `'YYYY-MM-DD HH:MM:SS'` strings compared against `created_at`; when `since_utc` is given, `days` is ignored. `include_junk=False` by default excludes `is_junk=1` rows; `min_score` is legacy (Haiku-era `ai_score` filter, `(ai_score >= N OR ai_score IS NULL)` — confusing semantics, not used by `pipeline_digest.py` anymore, kept only for backward compatibility).
- **`pipeline_collect.py`** — Orchestrates daily run: `init_db()` → for each of the 11 sources: fetch (TrendForce is called with `known_urls=get_urls_by_source("trendforce")`) → URL-dedupe (`article_exists`) → collect non-`tw_revenue` articles into one batch → cross-day title dedupe (`filter_against_db_titles`) → batch-internal title dedupe (`deduplicate_by_title`) → save to DB → run `is_junk`/`score_by_rules` on every article and write `rule_score`/`is_junk` (`ai_score`/`ai_summary` stay `NULL` for all new articles) → Jina-fetch full text only for `not is_junk` articles → `update_full_content` → `classify_completeness` per article.
- **`pipeline_digest.py`** — `_period_bounds()` computes a half-open window `[period_start, period_end)` in Taiwan time: `period_end` is Monday 00:00 (Taiwan) of the reference week (Taiwan today by default, or `--date YYYY-MM-DD`), and `period_start = period_end − DIGEST_DAYS` (7 days). Both are converted to UTC strings `since_utc` / `until_utc` and passed to `get_recent_articles(since_utc=..., until_utc=...)` (junk excluded by default), so consecutive weekly windows tile without gaps regardless of cron delay. Then `render_evidence_pack()` builds a Markdown document with: 台股月營收 section, SEC Filings section, and a "情報" section split into three tiers by `rule_score` — **高訊號** (`>= EVIDENCE_FULL_TEXT_THRESHOLD`, full_content embedded up to `EVIDENCE_FULL_TEXT_CHARS`, falls back to summary + "⚠️ 無全文" if no full text was fetched), **中訊號** (`>= EVIDENCE_SUMMARY_THRESHOLD`, summary only, truncated to 300 chars), **低訊號** (below that, one-line title only) — plus a stats section (per-source counts, `content_completeness` distribution, `rule_score` distribution). Writes `digests/{date}.md`, where `{date}` is `period_end` (the Taiwan Monday). With `--dry-run`, prints the first 80 lines + line/char counts and skips mailing, but still writes `digests/{date}.md` (restore or delete it afterwards so it isn't committed by mistake). Without it, calls `send_email(md_path, stats)`, which mails the `.md` file as an attachment (not inlined HTML) with a plain-text summary body — the evidence pack is raw material for a downstream AI to read, not a human-facing report.
- **`backfill_rule_score.py`** — One-off script: reads every row in `articles`, computes `is_junk`/`score_by_rules` (or fixed `rule_score=10, is_junk=0` for `tw_revenue`), writes all of them back with a single connection + `executemany` + one commit. Used once to backfill historical rows collected before `rule_scorer.py` existed; supports `--dry-run` to check the junk ratio before writing.
- **`clean_existing_content.py`** — One-off script: re-runs `content_fetcher._clean_jina_content()` against every existing `full_content` row (skipping `sec_edgar`/`tw_revenue`, which aren't Jina-sourced), to retroactively apply cleanup-regex improvements to already-stored content. Not called by any pipeline or workflow; rerun manually whenever the Jina cleaning rules change.

### GitHub Actions

- **`collect.yml`**: Runs `pipeline_collect.py` daily on cron `0 0 * * *` (UTC 00:00 = Taiwan 08:00). GitHub's scheduler often starts it about 4 hours late (observed start times 03:57–05:03 UTC on 2026-10-02 to 10-06). Then it commits `data/industry_radar.db` back to the repo. Env: only `SEC_USER_AGENT`. Requires `contents: write` permission.
- **`digest.yml`**: Runs `pipeline_digest.py` weekly on cron `17 16 * * 0` (UTC Sunday 16:17 = Taiwan Monday 00:17), which mails the evidence pack. Env: `EMAIL_SENDER`/`EMAIL_PASSWORD`/`EMAIL_RECEIVERS`. Also commits the generated `digests/{date}.md` back to the repo (`git add -f digests/` — deliberately *not* `data/`, to avoid racing `collect.yml`'s DB commit). Requires `contents: write` permission.

### Database Schema (SQLite)

Table `articles`: `id`, `source_type`, `ticker`, `filing_type`, `title`, `url` (UNIQUE), `summary`, `full_content`, `ai_score`, `ai_summary`, `source`, `published`, `created_at`, `content_completeness`, `rule_score`, `is_junk`.

- **`ai_score` / `ai_summary`** — Haiku-era historical data, frozen. Contains a large number of fallback values (`ai_score = 5`) from periods when the Groq/Haiku API failed; semantically unreliable, kept only for retrospective comparison. The current pipeline never writes to these two columns for new articles — they stay `NULL`.
- **`rule_score` / `is_junk`** — The current scoring path, written by `rule_scorer.py` via `pipeline_collect.py` (and backfilled for historical rows by `backfill_rule_score.py`). `is_junk` is `0`/`1` (SQLite has no boolean). All new logic (deduplication exemptions, digest tiering, full-text fetch gating) reads these two columns, never `ai_score`.

## Key Design Decisions

- **Zero-LLM by design:** Neither scoring nor evidence-pack generation calls any LLM API. The evidence pack is structured raw material meant for a downstream AI conversation (one with web-search capability) to read and judge — not a human-facing analysis report. This system's job is collection, noise filtering, and tiered layout; judgment is deliberately left to whatever reads the evidence pack afterward.
- **Free-tier services only:** Jina AI (free reader API), SEC EDGAR (public API), RSS feeds — plus a pure-Python rule-based scorer with no API dependency at all.
- **Database in git:** `data/industry_radar.db` is committed after each collection run — no external DB needed.
- **Fail-safe design:** Errors in any single source or article don't abort the pipeline; failures are logged and skipped.
- **Korean stocks (SK Hynix, Samsung) have no SEC CIK** — they're in WATCHLIST for reference but skipped in SEC EDGAR fetching (no equivalent "per-ticker RSS" source is currently wired in — see the `fetch_seeking_alpha()` note above).
- **Evidence pack tiering** (`EVIDENCE_FULL_TEXT_THRESHOLD = 7`, `EVIDENCE_SUMMARY_THRESHOLD = 4` in `config.py`) replaces the old single `AI_SCORE_THRESHOLD` digest filter: instead of dropping low-score articles from the weekly output entirely, every non-junk article is kept and placed into one of three tiers (full text / summary / title-only) by `rule_score`, so the downstream reader sees the full picture with signal strength made explicit rather than pre-filtered away.


## Branch 規則

- `dev` 是預設 branch，也是唯一在運作的 branch。GitHub Actions（collect.yml、digest.yml）只在 `dev` 上執行，bot 每天把 DB commit 回 `dev`。
- Cloud session 只推到 session 被允許的 branch，不要嘗試推其他 branch，也不要用 API 繞過限制。如果允許的 branch 不是 `dev`，完成後由使用者建立 PR，合併到 `dev`。
- PR 不能包含 `data/industry_radar.db` 的變更（二進位檔，合併時選錯一邊就會被舊版本覆蓋）。需要修改 DB 時，另外寫腳本，由使用者在本機、在 UTC 12:00～23:00 之間執行。
- `main` 已經存檔為 tag `archive/main-2026-03-27`（commit 49af4ec），不要重新建立 `main`。
