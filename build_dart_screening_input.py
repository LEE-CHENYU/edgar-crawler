"""Korea (DART) screening-input adapter.

canonical_metrics_wide.parquet (OpenDART) -> the shared Morningstar
SCREENING_COLUMNS CSV the gates engine (run_screening_pipeline.py) consumes.

Deterministic core here: USD-convert monetary fields (live KRW/USD FX), compute
ratios. Ticker exchange-suffix (.KS/.KQ) + live price/market-cap/multiples are an
enrichment layer (need a KRX board map + yfinance) applied on top.
"""
from __future__ import annotations
import math
from pathlib import Path
from typing import Optional

# Monetary canonical metric -> SCREENING_COLUMNS name (all USD-converted).
_MONEY_MAP = {
    "total_assets": "Total Assets",
    "total_liabilities": "Total Liabilities",
    "total_equity": "Total Equity",
    "current_assets": "Current Assets",
    "current_liabilities": "Current Liabilities",
    "cash_and_equivalents": "Cash (Balance Sheet)",
    "revenue": "Revenue",
    "operating_profit": "Operating Profit",
    "net_income": "Net Income",
}
NAN = float("nan")


def _g(rec: dict, k: str) -> Optional[float]:
    v = rec.get(k)
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    return float(v)


def _div(a: Optional[float], b: Optional[float]) -> float:
    """Safe ratio; NaN when either side missing or denom zero."""
    if a is None or b is None or b == 0:
        return NAN
    return a / b


def build_row(rec: dict, fx: float) -> dict:
    """One canonical_metrics row -> a SCREENING_COLUMNS dict (USD + ratios)."""
    row: dict[str, object] = {}
    # Identifiers (KEY_COLUMNS the gates engine expects)
    code = str(rec.get("stock_code") or "")
    row["Ticker"] = code                       # bare KRX code; .KS/.KQ suffix = price-stage enrichment
    row["Symbol"] = code
    row["Company Name"] = rec.get("company_name") or ""
    row["Exchange"] = "KRX"
    row["Sector"] = ""
    row["Industry Group"] = ""                 # empty -> routes to non-financial track (HK precedent)
    row["Stock Style"] = ""
    row["Currency"] = "USD"
    row["bsns_year"] = rec.get("bsns_year")
    # Monetary fields -> USD
    for src, col in _MONEY_MAP.items():
        v = _g(rec, src)
        row[col] = v * fx if v is not None else NAN
    # Ratios (unitless — computed from raw KRW, FX cancels)
    rev = _g(rec, "revenue")
    row["Operating Margin"] = _div(_g(rec, "operating_profit"), rev)
    row["Gross Margin"] = _div(_g(rec, "gross_profit"), rev)
    row["Net Margin 1 Year Avg"] = _div(_g(rec, "net_income"), rev)
    row["Current Ratio"] = _div(_g(rec, "current_assets"), _g(rec, "current_liabilities"))
    row["Debt/Equity Total"] = _div(_g(rec, "total_liabilities"), _g(rec, "total_equity"))
    row["Return on Equity"] = _div(_g(rec, "net_income"), _g(rec, "total_equity"))
    row["Return on Assets"] = _div(_g(rec, "net_income"), _g(rec, "total_assets"))
    row["Cash Flow Yield"] = NAN  # needs market cap (enrichment)
    return row


def get_live_krw_usd_fx() -> float:
    """Live KRW->USD (i.e. USD per 1 KRW). Falls back to a flagged approximate."""
    try:
        import yfinance as yf
        rate = yf.Ticker("KRW=X").fast_info.get("last_price")  # KRW per USD
        if rate and rate > 0:
            return 1.0 / float(rate)
    except Exception:
        pass
    return 1.0 / 1350.0   # flagged stale fallback (CLAUDE.md live-FX guardrail)


def build_screening_input(parquet_path: Path, output_path: Path,
                          fx: Optional[float] = None) -> int:
    import pandas as pd
    df = pd.read_parquet(parquet_path)
    df = df[df["n_metrics"] > 0].copy()
    # latest fiscal year per company
    df = df.sort_values("bsns_year").groupby("corp_code", as_index=False).last()
    if fx is None:
        fx = get_live_krw_usd_fx()
    rows = [build_row(r, fx) for r in df.to_dict("records")]
    out = pd.DataFrame(rows)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(output_path, index=False)
    return len(out)
