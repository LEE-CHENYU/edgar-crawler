"""Tests for IN_BSE adapter."""
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
    assert len(rows) == 1
    r = rows[0]
    assert r["market"] == "in_bse" and r["local_id"] == "533089"
    assert r["currency"] == "INR"
    assert r["fiscal_year"] == 2013
    assert r["period_end"] == "2013-12-31"
    assert r["period_type"] == "A"
    assert r["revenue"] == 100.0 and r["total_assets"] == 500.0


def test_implausible_fiscal_year_is_dropped():
    rows = rows_from_canonical_metrics(_df(), market="in_bse", currency="INR")
    assert all(r["fiscal_year"] != 430 for r in rows)


def test_doc_id_is_recorded_for_traceability():
    rows = rows_from_canonical_metrics(_df(), market="in_bse", currency="INR")
    assert rows[0]["source_doc_id"] == "5330890313"


def test_duplicate_doc_ids_raise():
    """doc_id collisions would silently skip work at panel level."""
    df = pd.DataFrame([
        {"ticker": "1", "fiscal_year": 2013, "doc_id": "D", "revenue": 1.0},
        {"ticker": "1", "fiscal_year": 2014, "doc_id": "D", "revenue": 2.0},
    ])
    with pytest.raises(ValueError, match="duplicate doc_id"):
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
