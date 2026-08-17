"""Generic screening-input adapter for the PDF markets (AU / TW / PH).

Same canonical_metrics -> SCREENING_COLUMNS mapping as the Korea (DART) adapter,
parameterised by market (currency, exchange, live FX). Reuses build_dart_screening_input.build_row's
ratio/USD logic; only the identifier columns + FX source differ per market.
"""
from __future__ import annotations
import argparse
from pathlib import Path

import build_dart_screening_input as kr  # reuse build_row (metric map + ratios)

MARKETS = {
    "au": {"exchange": "ASX", "fx_ticker": "AUDUSD=X", "fallback": 0.66,
           "parquet": "derived/ASX_FINANCIALS/canonical_metrics_wide.parquet"},
    "tw": {"exchange": "TWSE", "fx_ticker": "TWD=X", "fallback": 1 / 32.0, "invert": True,
           "parquet": "derived/TWSE_FINANCIALS/canonical_metrics_wide.parquet"},
    "ph": {"exchange": "PSE", "fx_ticker": "PHP=X", "fallback": 1 / 57.0, "invert": True,
           "parquet": "derived/PSE_FINANCIALS/canonical_metrics_wide.parquet"},
    # India/BSE: stage-02 completed 2026-08-17 over 67,977 PDFs (61,830 with
    # metrics). INR=X quotes INR per USD, so it inverts like TWD/PHP.
    "in": {"exchange": "BSE", "fx_ticker": "INR=X", "fallback": 1 / 88.0, "invert": True,
           "parquet": "derived/IN_BSE_FINANCIALS/canonical_metrics_wide.parquet"},
}
ROOT = Path("/Volumes/OWC Express 1M2/datasets/MARKET_FILINGS")


def live_fx(cfg: dict) -> float:
    """USD per 1 local unit. AUDUSD=X is already USD/AUD; TWD=X/PHP=X are local/USD -> invert."""
    try:
        import yfinance as yf
        r = yf.Ticker(cfg["fx_ticker"]).fast_info.get("last_price")
        if r and r > 0:
            return 1.0 / float(r) if cfg.get("invert") else float(r)
    except Exception:
        pass
    return cfg["fallback"]


def build(market: str) -> int:
    import pandas as pd
    cfg = MARKETS[market]
    df = pd.read_parquet(ROOT / cfg["parquet"])
    df = df[df["n_metrics"] > 0].copy()
    df = df.sort_values("fiscal_year").groupby("ticker", as_index=False).last()
    fx = live_fx(cfg)
    rows = []
    for rec in df.to_dict("records"):
        # adapt the PDF-market record shape into build_row's expected keys
        rec = {**rec, "stock_code": rec.get("ticker"),
               "company_name": rec.get("company_name", ""), "bsns_year": rec.get("fiscal_year")}
        row = kr.build_row(rec, fx)
        row["Exchange"] = cfg["exchange"]
        rows.append(row)
    out = pd.DataFrame(rows)
    out_path = ROOT / cfg["parquet"].rsplit("/", 1)[0] / f"{market}_screening_input.csv"
    out.to_csv(out_path, index=False)
    print(f"{market}: {len(out)} rows -> {out_path} (fx={fx:.5f})")
    return len(out)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("markets", nargs="*", default=list(MARKETS), help="au tw ph")
    args = p.parse_args()
    for m in (args.markets or MARKETS):
        build(m)
