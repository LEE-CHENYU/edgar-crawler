"""Tests for the canonical_metrics_wide adapter (in_bse, au, tw, ph, kr)."""
from collections import Counter

import pandas as pd
import pytest

from panel.adapters.in_bse import rows_from_canonical_metrics


def _df():
    return pd.DataFrame([
        {"ticker": "533089", "filing_date": "2013", "fiscal_year": 2013,
         "doc_id": "5330890313", "n_metrics": 9, "revenue": 100.0,
         "total_assets": 500.0, "net_income": 12.0},
        {"ticker": "533089", "filing_date": "2014", "fiscal_year": 430,
         "doc_id": "5330890314", "n_metrics": 3, "revenue": 1.0},
    ])


def test_rows_map_identity_and_metrics():
    rows = rows_from_canonical_metrics(_df(), market="in_bse", currency="INR")
    # 2 rows, not 1: the second row's fiscal_year=430 is now RESCUED from its
    # filing_date (spec Sec 5.1) instead of being dropped.
    assert len(rows) == 2
    r = rows[0]
    assert r["market"] == "in_bse" and r["local_id"] == "533089"
    assert r["currency"] == "INR"
    assert r["fiscal_year"] == 2013
    assert r["period_end"] == "2013-12-31"
    assert r["period_type"] == "A"
    assert r["revenue"] == 100.0 and r["total_assets"] == 500.0


def test_implausible_fiscal_year_is_never_kept_as_is():
    rows = rows_from_canonical_metrics(_df(), market="in_bse", currency="INR")
    assert all(r["fiscal_year"] != 430 for r in rows)


def test_doc_id_is_recorded_for_traceability():
    rows = rows_from_canonical_metrics(_df(), market="in_bse", currency="INR")
    assert rows[0]["source_doc_id"] == "5330890313"


def test_duplicate_ticker_doc_id_pairs_raise():
    """A repeated (ticker, doc_id) means two rows for one company from one
    document, which would silently skip work at panel level. Bare doc_id
    repetition does NOT raise -- one ASX filing legitimately covers several
    stapled tickers (see test below)."""
    df = pd.DataFrame([
        {"ticker": "1", "fiscal_year": 2013, "doc_id": "D", "revenue": 1.0},
        {"ticker": "1", "fiscal_year": 2014, "doc_id": "D", "revenue": 2.0},
    ])
    with pytest.raises(ValueError, match=r"duplicate \(ticker, doc_id\)"):
        rows_from_canonical_metrics(df, market="in_bse", currency="INR")


def test_missing_metric_columns_are_omitted_not_zeroed():
    rows = rows_from_canonical_metrics(_df(), market="in_bse", currency="INR")
    assert rows[0].get("inventories") is None


def test_non_numeric_metric_value_becomes_none():
    """Non-numeric strings like 'N/A' should degrade to None, not raise."""
    df = pd.DataFrame([
        {"ticker": "533089", "fiscal_year": 2013,
         "doc_id": "5330890313", "revenue": "N/A", "total_assets": 500.0},
    ])
    rows = rows_from_canonical_metrics(df, market="in_bse", currency="INR")
    assert len(rows) == 1
    assert rows[0]["revenue"] is None
    assert rows[0]["total_assets"] == 500.0


def test_metric_with_thousands_separator_is_parsed():
    """Thousands separators like '1,234.5' should parse correctly."""
    df = pd.DataFrame([
        {"ticker": "533089", "fiscal_year": 2013,
         "doc_id": "5330890313", "revenue": "1,234.5", "total_assets": 500.0},
    ])
    rows = rows_from_canonical_metrics(df, market="in_bse", currency="INR")
    assert len(rows) == 1
    assert rows[0]["revenue"] == 1234.5
    assert rows[0]["total_assets"] == 500.0


# --- Final fix wave FIX 2: this adapter now serves au/tw/ph/kr too ---


def test_shared_doc_id_across_different_tickers_is_allowed():
    """Live ASX case: doc_id 02969892 covers HDN/HCW/DGT/HMC, 01203318 covers
    INV/ARC. Raising on bare doc_id repetition would have dropped all 27,665
    AU rows."""
    df = pd.DataFrame([
        {"ticker": "HDN", "fiscal_year": 2025, "doc_id": "02969892", "revenue": 1.0},
        {"ticker": "HCW", "fiscal_year": 2025, "doc_id": "02969892", "revenue": 2.0},
    ])
    rows = rows_from_canonical_metrics(df, market="au", currency="AUD")
    assert len(rows) == 2
    assert {r["local_id"] for r in rows} == {"HDN", "HCW"}


def test_fiscal_year_rescued_from_filing_date_is_marked_derived():
    """PH live case: 2 rows carry fiscal_year=430 with filing_date 20250502."""
    df = pd.DataFrame([
        {"ticker": "BKR", "filing_date": "20250502", "fiscal_year": 430,
         "revenue": 5.0},
    ])
    drops = Counter()
    rows = rows_from_canonical_metrics(df, market="ph", currency="PHP", drops=drops)
    assert len(rows) == 1
    assert rows[0]["fiscal_year"] == 2025
    assert rows[0]["period_end"] == "2025-12-31"
    assert rows[0]["fiscal_year_source"] == "derived:filing_date"
    assert drops["fiscal_year_derived_from_filing_date"] == 1


def test_row_is_dropped_and_counted_when_neither_year_works():
    df = pd.DataFrame([
        {"ticker": "X", "filing_date": None, "fiscal_year": 430, "revenue": 5.0},
    ])
    drops = Counter()
    assert rows_from_canonical_metrics(df, market="ph", currency="PHP", drops=drops) == []
    assert drops["implausible_fiscal_year"] == 1


def test_reported_fiscal_year_is_marked_reported():
    rows = rows_from_canonical_metrics(_df(), market="in_bse", currency="INR")
    assert rows[0]["fiscal_year_source"] == "reported"


def test_missing_ticker_is_dropped_and_counted():
    """PH has 169 null tickers of 447 rows."""
    df = pd.DataFrame([
        {"ticker": None, "filing_date": "20250502", "fiscal_year": 2025},
        {"ticker": "OK", "filing_date": "20250502", "fiscal_year": 2025},
    ])
    drops = Counter()
    rows = rows_from_canonical_metrics(df, market="ph", currency="PHP", drops=drops)
    assert len(rows) == 1
    assert drops["missing_ticker"] == 1


def test_kr_schema_aliases_stock_code_and_bsns_year():
    """KR's DART parquet has no ticker/fiscal_year columns at all."""
    df = pd.DataFrame([
        {"stock_code": "005930", "company_name": "\uc0bc\uc131\uc804\uc790", "bsns_year": 2022,
         "fs_div": "CFS", "total_assets": 4.484245e14, "revenue": 3.022314e14},
    ])
    rows = rows_from_canonical_metrics(df, market="kr", currency="KRW")
    assert len(rows) == 1
    assert rows[0]["local_id"] == "005930"
    assert rows[0]["fiscal_year"] == 2022
    assert rows[0]["fs_div"] == "CFS"
    assert rows[0]["total_assets"] == 4.484245e14
    assert rows[0]["company_name"]


def test_kr_null_fs_div_is_carried_as_none_not_invented():
    df = pd.DataFrame([
        {"stock_code": "005930", "bsns_year": 2012, "fs_div": None},
    ])
    rows = rows_from_canonical_metrics(df, market="kr", currency="KRW")
    assert rows[0]["fs_div"] is None


def test_source_artifact_records_the_real_path_when_given():
    rows = rows_from_canonical_metrics(
        _df(), market="au", currency="AUD",
        source_artifact="/x/ASX_FINANCIALS/canonical_metrics_wide.parquet",
    )
    assert rows[0]["source_artifact"].endswith("canonical_metrics_wide.parquet")
