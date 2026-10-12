# 離線測試：collect_runs 建表 / 寫入，以及 pipeline_collect 逐來源統計（暫存 DB，不碰 data/industry_radar.db）
import hashlib
import sqlite3

import pytest

import database
import pipeline_collect

SOURCE_FETCHERS = [
    ("fetch_semianalysis", "semianalysis"),
    ("fetch_digitimes", "digitimes"),
    ("fetch_semi_engineering", "semi_engineering"),
    ("fetch_eetimes", "eetimes"),
    ("fetch_toms_hardware", "toms_hardware"),
    ("fetch_serve_the_home", "serve_the_home"),
    ("fetch_next_platform", "next_platform"),
    ("fetch_fabricated_knowledge", "fabricated_knowledge"),
]


@pytest.fixture
def tmp_db(monkeypatch, tmp_path):
    path = tmp_path / "data" / "test.db"
    monkeypatch.setattr(database, "DB_PATH", str(path))
    database.init_db()
    return path


def _rows(path) -> list[dict]:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute("SELECT * FROM collect_runs ORDER BY id")]
    conn.close()
    return rows


def _title(key: str) -> str:
    """互不相似的標題（兩兩相似度 <= 0.4），避免假資料被標題去重誤刪"""
    return hashlib.sha1(key.encode()).hexdigest()


def _article(source_type: str, title: str, url: str) -> dict:
    return {
        "source_type": source_type, "title": title, "url": url, "summary": "",
        "source": source_type, "published": "", "ticker": "", "filing_type": "",
    }


# 1. init_db 建表可重複執行，不影響 articles 表
def test_init_db_creates_collect_runs_idempotent(tmp_db):
    database.save_article(**_article("digitimes", "TSMC capex", "https://x/1"))
    database.init_db()
    conn = sqlite3.connect(tmp_db)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(collect_runs)")]
    n_articles = conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
    conn.close()
    assert cols[1:] == list(database.COLLECT_RUN_COLUMNS)
    assert n_articles == 1


# 2. save_collect_runs 寫入，缺的欄位為 NULL
def test_save_collect_runs_roundtrip(tmp_db):
    ok = database.save_collect_runs([
        {"run_at": "2026-10-12 04:00:00", "source_type": "digitimes", "status": "ok", "raw_count": 35},
        {"run_at": "2026-10-12 04:00:00", "source_type": "eetimes", "status": "error", "error_msg": "HTTP 503"},
    ])
    assert ok
    rows = _rows(tmp_db)
    assert [(r["source_type"], r["status"], r["raw_count"]) for r in rows] == [
        ("digitimes", "ok", 35), ("eetimes", "error", None)]


# 3. 寫入失敗不往外丟，只回傳 False
def test_save_collect_runs_failure_returns_false(monkeypatch, tmp_path):
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "missing" / "no_table.db"))
    # 沒跑 init_db → 沒有 collect_runs 表
    assert database.save_collect_runs([{"run_at": "x", "source_type": "y", "status": "ok"}]) is False


# ── pipeline_collect 整合（所有 fetcher 換成假資料，Jina 換成 no-op）──

@pytest.fixture
def fake_sources(monkeypatch, tmp_db):
    """預設每個 RSS 來源回傳 2 篇；可用 overrides[fn_name] 換成自訂函式"""
    overrides = {}

    def make(fn_name, source_type):
        def fetch(stats=None):
            if fn_name in overrides:
                return overrides[fn_name](stats)
            if stats is not None:
                stats.update(status="ok", raw_count=20, returned=2, http_status=200,
                             feed_newest_published="2026-10-12 03:00:00")
            return [_article(source_type, _title(f"{source_type}-{i}"),
                             f"https://{source_type}/{i}") for i in range(2)]
        return fetch

    for fn_name, source_type in SOURCE_FETCHERS:
        monkeypatch.setattr(pipeline_collect, fn_name, make(fn_name, source_type))
    monkeypatch.setattr(pipeline_collect, "fetch_trendforce",
                        lambda known_urls=None, stats=None: overrides.get("fetch_trendforce", lambda s: [])(stats))
    monkeypatch.setattr(pipeline_collect, "fetch_sec_edgar", lambda: [])
    monkeypatch.setattr(pipeline_collect, "fetch_tw_revenue_all", lambda: [])
    monkeypatch.setattr(pipeline_collect, "batch_fetch", lambda articles: articles)
    return overrides


def test_pipeline_writes_one_row_per_source(fake_sources, tmp_db):
    pipeline_collect.run()
    rows = _rows(tmp_db)
    assert len(rows) == 11
    assert len({r["run_at"] for r in rows}) == 1
    by_src = {r["source_type"]: r for r in rows}
    d = by_src["digitimes"]
    assert (d["status"], d["raw_count"], d["returned"], d["after_url_dedup"],
            d["after_title_dedup"], d["inserted"]) == ("ok", 20, 2, 2, 2, 2)
    assert d["feed_newest_published"] == "2026-10-12 03:00:00"
    # 不接 stats 的來源：_count_only 記回傳篇數
    assert (by_src["sec_edgar"]["raw_count"], by_src["sec_edgar"]["returned"]) == (0, 0)
    # 月營收不走標題去重
    assert by_src["tw_revenue"]["after_title_dedup"] is None


def test_pipeline_counts_url_and_title_dedup(fake_sources, tmp_db):
    pipeline_collect.run()  # 第一次：全部入庫

    def eetimes_second_run(stats):
        stats.update(status="ok", raw_count=3, returned=3)
        return [
            _article("eetimes", _title("eetimes-0"), "https://eetimes/0"),          # URL 已存在
            _article("eetimes", _title("eetimes-1") + "x", "https://eetimes/new1"),  # 標題近似 → 跨日去重
            _article("eetimes", _title("eetimes-new"), "https://eetimes/new2"),
        ]
    fake_sources["fetch_eetimes"] = eetimes_second_run
    pipeline_collect.run()

    second = [r for r in _rows(tmp_db) if r["source_type"] == "eetimes"][-1]
    assert (second["returned"], second["after_url_dedup"],
            second["after_title_dedup"], second["inserted"]) == (3, 2, 1, 1)
    # 其他來源第二次全是重複 URL
    other = [r for r in _rows(tmp_db) if r["source_type"] == "digitimes"][-1]
    assert (other["after_url_dedup"], other["after_title_dedup"], other["inserted"]) == (0, 0, 0)


def test_pipeline_fetch_exception_records_error_and_continues(fake_sources, tmp_db):
    def boom(stats):
        stats.update(raw_count=5)
        raise RuntimeError("network down")
    fake_sources["fetch_digitimes"] = boom
    pipeline_collect.run()
    by_src = {r["source_type"]: r for r in _rows(tmp_db)}
    assert by_src["digitimes"]["status"] == "error"
    assert "network down" in by_src["digitimes"]["error_msg"]
    assert by_src["digitimes"]["raw_count"] == 5      # 例外前已填的 stats 保留
    assert by_src["digitimes"]["after_url_dedup"] is None
    assert by_src["eetimes"]["inserted"] == 2         # 其他來源照常


def test_pipeline_fetcher_reported_error(fake_sources, tmp_db):
    def empty(stats):
        stats.update(status="error", error_msg="feed 0 條目", raw_count=0, returned=0)
        return []
    fake_sources["fetch_semianalysis"] = empty
    pipeline_collect.run()
    row = {r["source_type"]: r for r in _rows(tmp_db)}["semianalysis"]
    assert (row["status"], row["error_msg"], row["after_url_dedup"], row["inserted"]) == (
        "error", "feed 0 條目", 0, 0)


def test_pipeline_survives_save_failure(fake_sources, tmp_db, monkeypatch):
    monkeypatch.setattr(pipeline_collect, "save_collect_runs", lambda rows: False)
    pipeline_collect.run()  # 不丟例外
    conn = sqlite3.connect(tmp_db)
    assert conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0] == 16
    conn.close()


def test_pipeline_writes_rows_even_if_batch_step_crashes(fake_sources, tmp_db, monkeypatch):
    def crash(days=7):
        raise RuntimeError("db locked")
    monkeypatch.setattr(pipeline_collect, "get_recent_titles", crash)
    with pytest.raises(RuntimeError):
        pipeline_collect.run()
    rows = _rows(tmp_db)
    assert len(rows) == 11
    assert {r["source_type"]: r for r in rows}["digitimes"]["after_url_dedup"] == 2
