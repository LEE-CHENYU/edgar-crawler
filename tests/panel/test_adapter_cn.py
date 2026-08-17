import pandas as pd
from panel.adapters.cn import CN_FIELD_MAP, rows_from_cn_frames

def _bs():
    return pd.DataFrame([
        {"Stkcd": "000001", "ShortName": "深发展A", "Accper": "2024-12-31",
         "Typrep": "A", "A001000000": 100.0, "A001100000": 60.0, "A001101000": 10.0},
        {"Stkcd": "000001", "ShortName": "深发展A", "Accper": "2024-03-31",
         "Typrep": "A", "A001000000": 90.0, "A001100000": 55.0, "A001101000": 9.0},
        {"Stkcd": "000001", "ShortName": "深发展A", "Accper": "2024-01-01",
         "Typrep": "A", "A001000000": 88.0, "A001100000": 50.0, "A001101000": 8.0},
    ])

def test_field_map_targets_canonical_names():
    assert CN_FIELD_MAP["A001000000"] == "total_assets"
    assert CN_FIELD_MAP["A001101000"] == "cash_and_equivalents"

def test_quarterly_and_annual_periods_are_typed():
    rows = {r["period_end"]: r for r in rows_from_cn_frames(_bs())}
    assert rows["2024-12-31"]["period_type"] == "A"
    assert rows["2024-03-31"]["period_type"] == "Q"

def test_january_first_rows_are_open_not_quarterly():
    """Restated opening balances: 149,269 such rows exist in the live corpus."""
    rows = {r["period_end"]: r for r in rows_from_cn_frames(_bs())}
    assert rows["2024-01-01"]["period_type"] == "OPEN"

def test_open_rows_are_retained_not_dropped():
    assert len(rows_from_cn_frames(_bs())) == 3

def test_identity_and_metrics_are_mapped():
    row = [r for r in rows_from_cn_frames(_bs()) if r["period_end"] == "2024-12-31"][0]
    assert row["market"] == "cn"
    assert row["local_id"] == "000001"
    assert row["company_name"] == "深发展A"
    assert row["currency"] == "CNY"
    assert row["fiscal_year"] == 2024
    assert row["total_assets"] == 100.0
    assert row["cash_and_equivalents"] == 10.0

def test_typrep_is_recorded_so_consolidated_and_parent_are_distinguishable():
    row = rows_from_cn_frames(_bs())[0]
    assert row["cn_typrep"] == "A"

def test_unusable_accper_is_dropped():
    bad = pd.DataFrame([{"Stkcd": "1", "ShortName": "x", "Accper": "junk",
                         "Typrep": "A", "A001000000": 1.0}])
    assert rows_from_cn_frames(bad) == []

def test_missing_optional_frames_are_fine():
    assert len(rows_from_cn_frames(_bs(), income_statement=None, cash_flow=None)) == 3


def _inc():
    return pd.DataFrame([
        {"Stkcd": "000001", "Accper": "2024-12-31", "Typrep": "A", "B001100000": 500.0},
    ])


def _cf():
    return pd.DataFrame([
        {"Stkcd": "000001", "Accper": "2024-12-31", "Typrep": "A", "C001000000": 40.0},
    ])


def test_three_frame_metrics_merge_onto_same_row():
    """Metrics from balance sheet, income statement, and cash flow for the same
    Stkcd/Accper/Typrep land on one row, not three separate rows."""
    rows = {r["period_end"]: r for r in rows_from_cn_frames(_bs(), _inc(), _cf())}
    row = rows["2024-12-31"]
    assert row["total_assets"] == 100.0
    assert row["revenue"] == 500.0
    assert row["operating_cash_flow"] == 40.0


def test_period_only_in_income_statement_still_produces_a_row():
    """An outer join: a period present in income_statement but absent from
    balance_sheet must still produce a row, with balance-sheet metrics as
    None rather than a missing key or an exception."""
    inc = pd.DataFrame([
        {"Stkcd": "000001", "Accper": "2024-06-30", "Typrep": "A", "B001100000": 250.0},
    ])
    rows = {r["period_end"]: r for r in rows_from_cn_frames(_bs(), income_statement=inc)}
    assert "2024-06-30" in rows
    row = rows["2024-06-30"]
    assert row["revenue"] == 250.0
    assert row["total_assets"] is None


def test_duplicate_metric_column_across_frames_is_not_swallowed_by_dup_suffix():
    """When a metric code appears in two supplied frames for the same key, the
    value that lands on the row must be the real value (not None), and no
    '_dup'-suffixed key should leak into the emitted row dict."""
    inc_with_overlap = pd.DataFrame([
        {"Stkcd": "000001", "Accper": "2024-12-31", "Typrep": "A", "A001000000": 999.0},
    ])
    rows = rows_from_cn_frames(_bs(), income_statement=inc_with_overlap)
    row = [r for r in rows if r["period_end"] == "2024-12-31"][0]
    assert row["total_assets"] is not None
    assert not any(str(k).endswith("_dup") for k in row.keys())
