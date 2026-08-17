import pytest
from asx_financials_extract import CANONICAL_METRICS
from panel.schema import (
    METRIC_COLUMNS, PANEL_COLUMNS, is_plausible_fiscal_year,
    normalize_period_end, period_type_for, to_number,
)

def test_metric_columns_match_canonical_vocabulary():
    assert METRIC_COLUMNS[0] == "revenue"
    assert "operating_cash_flow" in METRIC_COLUMNS
    assert len(METRIC_COLUMNS) == 20
    assert METRIC_COLUMNS == CANONICAL_METRICS

def test_panel_columns_lead_with_identity_then_metrics():
    assert PANEL_COLUMNS[:6] == [
        "spine_key", "market", "local_id", "period_end", "period_type", "fiscal_year",
    ]
    for m in METRIC_COLUMNS:
        assert m in PANEL_COLUMNS
    for c in ("currency", "fx_rate", "fx_asof", "source_artifact"):
        assert c in PANEL_COLUMNS

def test_period_type_annual_for_december_year_end():
    assert period_type_for("2024-12-31", cadence="annual") == "A"

def test_period_type_january_first_is_open():
    """CN year-start rows are restated opening balances, never a quarter."""
    assert period_type_for("2024-01-01", cadence="quarterly") == "OPEN"

def test_period_type_quarterly_marks_q_ends():
    for d in ("2024-03-31", "2024-06-30", "2024-09-30"):
        assert period_type_for(d, cadence="quarterly") == "Q"

def test_period_type_semiannual_marks_h():
    assert period_type_for("2024-06-30", cadence="semiannual") == "H"

def test_period_type_december_is_annual_even_when_cadence_quarterly():
    assert period_type_for("2024-12-31", cadence="quarterly") == "A"

def test_period_type_rejects_unknown_cadence():
    with pytest.raises(ValueError):
        period_type_for("2024-12-31", cadence="weekly")

def test_normalize_period_end_accepts_common_forms():
    assert normalize_period_end("20241231") == "2024-12-31"
    assert normalize_period_end("2024-12-31") == "2024-12-31"
    assert normalize_period_end("2024-12-31 00:00:00") == "2024-12-31"

def test_normalize_period_end_returns_none_on_junk():
    for bad in ("", None, "not-a-date", "430"):
        assert normalize_period_end(bad) is None

def test_plausible_fiscal_year_rejects_the_pse_outliers():
    """PSE emitted fiscal_year=430 on 2 rows."""
    assert is_plausible_fiscal_year(430) is False
    assert is_plausible_fiscal_year(2024) is True
    assert is_plausible_fiscal_year(1989) is False
    assert is_plausible_fiscal_year(None) is False


def test_to_number_accepts_plain_numeric_string():
    assert to_number("100") == 100.0

def test_to_number_strips_thousands_separators():
    assert to_number("1,234.5") == 1234.5

def test_to_number_accepts_numeric_types():
    assert to_number(42) == 42.0

def test_to_number_returns_none_on_empty_string():
    assert to_number("") is None

def test_to_number_returns_none_on_none():
    assert to_number(None) is None

def test_to_number_returns_none_on_non_numeric_text():
    assert to_number("n/a") is None

def test_to_number_returns_none_on_whitespace_only():
    assert to_number("  ") is None

def test_to_number_returns_none_on_bool_true():
    assert to_number(True) is None

def test_to_number_returns_none_on_bool_false():
    assert to_number(False) is None
