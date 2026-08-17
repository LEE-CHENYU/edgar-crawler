import pandas as pd
import pytest
from panel.adapters.jp import JP_COLUMN_MAP, rows_from_jp_frame

def _df():
    return pd.DataFrame([
        {"doc_id": "S10075EH", "filing_date": "2016-06-24", "fiscal_year": 2016,
         "metric_period_end": "2016-03-31", "company_name": "SUBARU CORP",
         "stock_code": "7270", "security_code": "72700", "edinet_code": "E02144",
         "accounting_standards": "JPGAAP", "has_consolidated_statements": True,
         "assets": 3000.0, "basic_eps": 55.0, "net_sales": 3300.0},
        {"doc_id": "BAD", "filing_date": "2016-06-24", "fiscal_year": 430,
         "metric_period_end": "junk", "company_name": "X", "stock_code": "1",
         "security_code": "1", "edinet_code": "E1", "accounting_standards": "JPGAAP",
         "has_consolidated_statements": True, "assets": 1.0},
    ])

def test_column_map_targets_canonical_names():
    assert JP_COLUMN_MAP["assets"] == "total_assets"
    assert JP_COLUMN_MAP["net_sales"] == "revenue"
    assert JP_COLUMN_MAP["basic_eps"] == "basic_eps"
    assert JP_COLUMN_MAP["income_before_taxes"] == "profit_before_tax"

def test_operating_income_is_not_mapped_to_profit_before_tax():
    """operating_income (営業利益) excludes non-operating items and is not
    pretax profit; income_before_taxes (税引前当期純利益) is the true
    counterpart of canonical profit_before_tax."""
    assert "operating_income" not in JP_COLUMN_MAP
    assert JP_COLUMN_MAP.get("income_before_taxes") == "profit_before_tax"

def test_profit_before_tax_comes_from_income_before_taxes_not_operating_income():
    df = pd.DataFrame([
        {"doc_id": "S1", "fiscal_year": 2016, "metric_period_end": "2016-03-31",
         "company_name": "X", "stock_code": "7270",
         "operating_income": 999.0, "income_before_taxes": 500.0},
    ])
    row = rows_from_jp_frame(df)[0]
    assert row["profit_before_tax"] == 500.0

def test_period_end_comes_from_metric_period_end_not_fiscal_year():
    """Japanese fiscal years commonly end 31 March; assuming 12-31 is wrong."""
    rows = rows_from_jp_frame(_df())
    assert rows[0]["period_end"] == "2016-03-31"

def test_march_year_end_is_typed_annual_not_quarterly():
    rows = rows_from_jp_frame(_df())
    assert rows[0]["period_type"] == "A"

def test_identity_and_metrics_are_mapped():
    r = rows_from_jp_frame(_df())[0]
    assert r["market"] == "jp"
    assert r["local_id"] == "7270"
    assert r["company_name"] == "SUBARU CORP"
    assert r["currency"] == "JPY"
    assert r["total_assets"] == 3000.0
    assert r["revenue"] == 3300.0
    assert r["basic_eps"] == 55.0

def test_edinet_and_standards_are_recorded():
    r = rows_from_jp_frame(_df())[0]
    assert r["jp_edinet_code"] == "E02144"
    assert r["jp_accounting_standards"] == "JPGAAP"


def test_has_consolidated_statements_is_recorded():
    """Live data has 2 rows (stock_code 85950, period 2021-03-31) sharing a
    doc_id-less key: one filing with has_consolidated_statements=True (full
    figures) and one with False (parent-company-only, much smaller figures).
    Not an amendment -- a genuine dual reporting basis, same pattern as CN's
    Typrep A/B. This field is what lets the panel distinguish them instead of
    colliding as a duplicate key."""
    r = rows_from_jp_frame(_df())[0]
    assert r["jp_has_consolidated_statements"] is True

def test_rows_with_unusable_period_end_are_dropped():
    rows = rows_from_jp_frame(_df())
    assert len(rows) == 1
    assert all(r["local_id"] != "1" for r in rows)

def test_missing_stock_code_is_dropped():
    df = pd.DataFrame([{"doc_id": "D", "fiscal_year": 2016,
                        "metric_period_end": "2016-03-31", "stock_code": None,
                        "company_name": "X", "assets": 1.0}])
    assert rows_from_jp_frame(df) == []


# --- FIX 4: drops are counted, never silent ---

def test_jp_drop_reasons_are_counted():
    from collections import Counter

    drops = Counter()
    df = pd.DataFrame([
        {"stock_code": None, "metric_period_end": "2021-03-31", "fiscal_year": 2021},
        {"stock_code": "7203", "metric_period_end": "nope", "fiscal_year": 2021},
        {"stock_code": "7203", "metric_period_end": "2021-03-31", "fiscal_year": 430},
    ])
    assert rows_from_jp_frame(df, drops=drops) == []
    assert drops["missing_stock_code"] == 1
    assert drops["unusable_period_end"] == 1
    assert drops["implausible_fiscal_year"] == 1
