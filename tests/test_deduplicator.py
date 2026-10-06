from deduplicator import deduplicate_by_title, filter_against_db_titles

# 實際標題：9/30 這篇在 10/01 的 run 被跨天去重以 0.65 誤擋
SPOT_0924 = "[Insights] Memory Spot Price Update: DRAM Spot Prices Hold Cautious Tone; DDR4 2Gx8 Drops 3.6%"
SPOT_0930 = "[Insights] Memory Spot Price Update: DRAM Market Quiet Amid Holidays; NAND Buying Stays Weak"
NEWS = "[News] Micron Taiwan Investment Tops NT$1.6T as Four-Site Expansion Accelerates"


def _tf(title: str) -> dict:
    return {"source_type": "trendforce", "title": title, "url": f"https://example.com/{hash(title)}"}


# 10. Memory Spot 系列文在跨天與批次去重都放行
def test_series_title_exempt():
    kept = filter_against_db_titles([_tf(SPOT_0930)], [SPOT_0924])
    assert [a["title"] for a in kept] == [SPOT_0930]

    kept = deduplicate_by_title([_tf(SPOT_0924), _tf(SPOT_0930)])
    assert {a["title"] for a in kept} == {SPOT_0924, SPOT_0930}


# 11. 豁免不擴大：一般新聞照擋；片語不在標題開頭不豁免
def test_exemption_not_widened():
    assert filter_against_db_titles([_tf(NEWS)], [NEWS]) == []
    assert len(deduplicate_by_title([_tf(NEWS), _tf(NEWS + "!")])) == 1

    not_prefix = "[News] Recap of [Insights] Memory Spot Price Update: DRAM Spot Prices Hold Cautious Tone"
    assert filter_against_db_titles([_tf(not_prefix)], [not_prefix]) == []
