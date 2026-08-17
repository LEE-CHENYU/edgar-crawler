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

def test_rows_with_unusable_period_end_are_dropped():
    rows = rows_from_jp_frame(_df())
    assert len(rows) == 1
    assert all(r["local_id"] != "1" for r in rows)

def test_missing_stock_code_is_dropped():
    df = pd.DataFrame([{"doc_id": "D", "fiscal_year": 2016,
                        "metric_period_end": "2016-03-31", "stock_code": None,
                        "company_name": "X", "assets": 1.0}])
    assert rows_from_jp_frame(df) == []
