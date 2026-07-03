"""TDD for the Korea (DART) screening-input adapter core.

Maps a canonical_metrics_wide row -> the shared Morningstar SCREENING_COLUMNS
schema: USD-convert monetary fields, compute the deterministic ratios. Live
market data (price/market cap/multiples) is a separate enrichment layer.
"""
import build_dart_screening_input as b

# KRW->USD ~ 1/1350; monetary fields are in KRW (won). Use a round FX for testing.
FX = 1.0 / 1350.0

REC = {
    "corp_code": "00126380", "stock_code": "005930", "bsns_year": 2023,
    "revenue": 258_935_494_000_000.0, "cost_of_revenue": 180_000_000_000_000.0,
    "gross_profit": 78_935_494_000_000.0, "operating_profit": 6_566_976_000_000.0,
    "net_income": 15_487_100_000_000.0,
    "total_assets": 455_905_980_000_000.0, "total_liabilities": 92_228_115_000_000.0,
    "total_equity": 363_677_865_000_000.0,
    "current_assets": 195_936_557_000_000.0, "current_liabilities": 75_000_000_000_000.0,
    "cash_and_equivalents": 69_080_893_000_000.0,
    "operating_cash_flow": 44_137_427_000_000.0,
}


def test_usd_conversion_of_monetary_fields():
    row = b.build_row(REC, FX)
    # Total Assets in USD ≈ 455.9T KRW / 1350 ≈ 337.7B USD
    assert abs(row["Total Assets"] - 455_905_980_000_000.0 * FX) < 1
    assert abs(row["Revenue"] - 258_935_494_000_000.0 * FX) < 1
    assert row["Currency"] == "USD"

def test_computed_ratios_are_unitless():
    row = b.build_row(REC, FX)
    assert abs(row["Operating Margin"] - 6_566_976 / 258_935_494) < 1e-6
    assert abs(row["Net Margin 1 Year Avg"] - 15_487_100 / 258_935_494) < 1e-6
    assert abs(row["Current Ratio"] - 195_936_557_000_000 / 75_000_000_000_000) < 1e-6  # ~2.61
    assert abs(row["Debt/Equity Total"] - 92_228_115 / 363_677_865) < 1e-6
    assert abs(row["Return on Equity"] - 15_487_100 / 363_677_865) < 1e-6
    assert abs(row["Return on Assets"] - 15_487_100 / 455_905_980) < 1e-6

def test_key_identifier_columns():
    row = b.build_row(REC, FX)
    assert row["Symbol"] == "005930"
    assert row["Exchange"] == "KRX"

def test_missing_metric_yields_nan_not_crash():
    import math
    thin = {"corp_code": "x", "stock_code": "000660", "bsns_year": 2023,
            "total_assets": 1000.0}  # no revenue
    row = b.build_row(thin, FX)
    assert math.isnan(row["Operating Margin"])   # revenue missing -> NaN, no ZeroDivision
    assert abs(row["Total Assets"] - 1000.0 * FX) < 1e-9
