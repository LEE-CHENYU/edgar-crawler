import pytest
from panel.fx import FX_TICKERS, fetch_rates, to_usd

def test_fx_tickers_cover_panel_currencies():
    for cur in ("AUD", "TWD", "PHP", "KRW", "CNY", "INR", "HKD", "JPY"):
        assert cur in FX_TICKERS

def test_usd_is_identity_and_needs_no_lookup():
    rates = fetch_rates(["USD"], fetcher=lambda t: {})
    assert rates["USD"] == 1.0

def test_to_usd_multiplies_and_records_rate_and_asof():
    row = {"currency": "JPY", "total_assets": 1000.0, "revenue": None}
    out = to_usd(row, rates={"JPY": 0.0064}, asof="2026-08-17T00:00:00+00:00")
    assert out["total_assets_usd"] == pytest.approx(6.4)
    assert out["revenue_usd"] is None
    assert out["fx_rate"] == 0.0064
    assert out["fx_asof"] == "2026-08-17T00:00:00+00:00"

def test_to_usd_leaves_native_values_untouched():
    row = {"currency": "JPY", "total_assets": 1000.0}
    out = to_usd(row, rates={"JPY": 0.0064}, asof="x")
    assert out["total_assets"] == 1000.0

def test_missing_rate_yields_null_usd_and_null_rate_not_a_guess():
    row = {"currency": "XYZ", "total_assets": 10.0}
    out = to_usd(row, rates={}, asof="x")
    assert out["total_assets_usd"] is None
    assert out["fx_rate"] is None

def test_fetch_rates_skips_currencies_the_fetcher_cannot_price():
    rates = fetch_rates(["JPY", "XYZ"], fetcher=lambda t: {"JPY=X": 155.0})
    assert rates["JPY"] == pytest.approx(1 / 155.0)
    assert "XYZ" not in rates

def test_fetch_rates_rejects_negative_quote():
    rates = fetch_rates(["JPY"], fetcher=lambda t: {"JPY=X": -155.0})
    assert "JPY" not in rates

def test_fetch_rates_rejects_zero_quote():
    rates = fetch_rates(["JPY"], fetcher=lambda t: {"JPY=X": 0.0})
    assert "JPY" not in rates

def test_to_usd_on_row_with_negative_quote_filtered_currency_is_clean_miss():
    rates = fetch_rates(["JPY"], fetcher=lambda t: {"JPY=X": -155.0})
    row = {"currency": "JPY", "total_assets": 1000.0, "revenue": 500.0}
    out = to_usd(row, rates=rates, asof="x")
    assert out["fx_rate"] is None
    assert out["total_assets_usd"] is None
    assert out["revenue_usd"] is None
