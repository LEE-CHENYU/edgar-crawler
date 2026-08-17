import gzip, json
from panel.adapters.hk import (
    HK_METRIC_ALIASES, iter_hk_fact_records, rows_from_hk_facts,
)

def _rec(metric, value, fiscal_year=2012, code="00158", vi=0):
    return {"market": "HKEX", "filing_id": "1562416", "filing_date": "2013-01-02",
            "company_name": "MELBOURNE ENT", "stock_code": code,
            "title": "Annual Report 2012", "section": "income_statement",
            "metric": metric, "raw_label": metric, "value": value,
            "value_index": vi, "fiscal_year": fiscal_year, "unit_text": "HKD'000"}

def test_aliases_map_hk_names_onto_canonical():
    assert HK_METRIC_ALIASES["profit_for_year"] == "net_income"

def test_aliases_do_not_claim_net_assets_is_total_equity():
    """net_assets and total_equity are not interchangeable; do not alias."""
    assert HK_METRIC_ALIASES.get("net_assets") is None

def test_long_records_pivot_to_one_row_per_security_year():
    rows = rows_from_hk_facts([
        _rec("revenue", 100.0), _rec("total_assets", 500.0), _rec("profit_for_year", 12.0),
    ])
    assert len(rows) == 1
    r = rows[0]
    assert r["market"] == "hk" and r["local_id"] == "00158"
    assert r["fiscal_year"] == 2012
    assert r["period_end"] == "2012-12-31"
    assert r["period_type"] == "A"
    assert r["revenue"] == 100.0
    assert r["total_assets"] == 500.0
    assert r["net_income"] == 12.0

def test_separate_years_become_separate_rows():
    rows = rows_from_hk_facts([_rec("revenue", 1.0, 2012), _rec("revenue", 2.0, 2013)])
    assert {r["fiscal_year"] for r in rows} == {2012, 2013}

def test_first_value_index_wins_on_duplicates():
    """value_index orders repeated extractions; index 0 is the primary."""
    rows = rows_from_hk_facts([
        _rec("revenue", 99.0, vi=1), _rec("revenue", 100.0, vi=0),
    ])
    assert rows[0]["revenue"] == 100.0

def test_unknown_metrics_are_ignored_not_crashing():
    rows = rows_from_hk_facts([_rec("some_hk_only_line", 5.0), _rec("revenue", 1.0)])
    assert rows[0]["revenue"] == 1.0
    assert "some_hk_only_line" not in rows[0]

def test_implausible_fiscal_year_is_dropped():
    assert rows_from_hk_facts([_rec("revenue", 1.0, fiscal_year=430)]) == []

def test_non_numeric_values_are_skipped():
    rows = rows_from_hk_facts([_rec("revenue", "n/a"), _rec("total_assets", 5.0)])
    assert rows[0].get("revenue") is None
    assert rows[0]["total_assets"] == 5.0

def test_iter_reads_gzipped_jsonl(tmp_path):
    d = tmp_path / "20130102"
    d.mkdir()
    with gzip.open(d / "1562416.jsonl.gz", "wt", encoding="utf-8") as f:
        f.write(json.dumps(_rec("revenue", 1.0)) + "\n")
    got = list(iter_hk_fact_records(tmp_path))
    assert got[0]["metric"] == "revenue"

def test_iter_skips_unreadable_lines(tmp_path):
    d = tmp_path / "20130103"
    d.mkdir()
    with gzip.open(d / "x.jsonl.gz", "wt", encoding="utf-8") as f:
        f.write("{not json}\n")
        f.write(json.dumps(_rec("revenue", 2.0)) + "\n")
    got = list(iter_hk_fact_records(tmp_path))
    assert len(got) == 1
