import pandas as pd
from panel.views import check_invariants


def _ok():
    return pd.DataFrame([
        {"spine_key": "B1", "period_end": "2024-12-31", "period_type": "A",
         "fiscal_year": 2024, "currency": "JPY", "fx_rate": 0.0064,
         "fx_asof": "2026-08-17T00:00:00+00:00", "revenue_usd": 1.0},
    ])


def test_clean_panel_reports_no_violations():
    assert check_invariants(_ok()) == []


def test_duplicate_security_period_is_flagged():
    df = pd.concat([_ok(), _ok()], ignore_index=True)
    assert any("duplicate" in v for v in check_invariants(df))


def test_period_type_inconsistent_with_period_end_is_flagged():
    df = _ok()
    df.loc[0, "period_type"] = "Q"   # 12-31 must be A
    assert any("period_type" in v for v in check_invariants(df))


def test_usd_value_without_fx_rate_is_flagged():
    df = _ok()
    df.loc[0, "fx_rate"] = None
    assert any("fx_rate" in v for v in check_invariants(df))


def test_implausible_fiscal_year_is_flagged():
    df = _ok()
    df.loc[0, "fiscal_year"] = 430
    assert any("fiscal_year" in v for v in check_invariants(df))


def test_open_period_type_is_allowed():
    df = _ok()
    df.loc[0, "period_end"] = "2024-01-01"
    df.loc[0, "period_type"] = "OPEN"
    assert check_invariants(df) == []


def test_usd_check_does_not_fire_when_no_usd_columns_present():
    """A row with no *_usd columns at all is not a violation, even with fx_rate missing."""
    df = _ok().drop(columns=["revenue_usd"])
    df.loc[0, "fx_rate"] = None
    assert check_invariants(df) == []


# --- Balance sheet identity invariant ---
# total_liabilities + total_equity ~= total_assets, within 1% relative tolerance.
# Task 6 found HK rows where total_equity == total_assets while total_liabilities
# was separately non-zero (upstream stage-02 mislabeling). This check flags such
# rows; it never corrects them.

def _balance_row(**overrides):
    row = {
        "spine_key": "B1", "period_end": "2024-12-31", "period_type": "A",
        "fiscal_year": 2024, "currency": "USD",
    }
    row.update(overrides)
    return pd.DataFrame([row])


def test_balance_identity_exact_match_is_not_flagged():
    df = _balance_row(total_assets=1000, total_liabilities=600, total_equity=400)
    assert check_invariants(df) == []


def test_balance_identity_within_one_percent_tolerance_is_not_flagged():
    # off by 5 on assets=1000 -> 0.5%, within the 1% relative tolerance
    df = _balance_row(total_assets=1000, total_liabilities=600, total_equity=395)
    assert check_invariants(df) == []


def test_balance_identity_outside_tolerance_is_flagged():
    # off by 100 on assets=1000 -> 10%, outside the 1% relative tolerance
    df = _balance_row(total_assets=1000, total_liabilities=600, total_equity=300)
    violations = check_invariants(df)
    assert any("total_assets" in v for v in violations)
    assert any("B1" in v and "2024-12-31" in v for v in violations)


def test_balance_identity_hk_equity_equals_assets_pattern_is_flagged():
    # Task 6 pattern: total_equity mislabeled to equal total_assets while
    # total_liabilities is separately non-zero.
    df = _balance_row(total_assets=1000, total_liabilities=400, total_equity=1000)
    violations = check_invariants(df)
    assert any("total_assets" in v for v in violations)


def test_balance_identity_missing_liabilities_is_not_flagged():
    # Missing a value is incomplete data, not a wrong value.
    df = _balance_row(total_assets=1000, total_equity=395)
    assert check_invariants(df) == []


def test_balance_identity_zero_total_assets_is_not_flagged_and_does_not_raise():
    df = _balance_row(total_assets=0, total_liabilities=600, total_equity=300)
    assert check_invariants(df) == []


def test_balance_identity_none_total_assets_is_not_flagged_and_does_not_raise():
    df = _balance_row(total_assets=None, total_liabilities=600, total_equity=300)
    assert check_invariants(df) == []
