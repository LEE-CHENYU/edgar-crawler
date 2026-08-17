"""Live FX for panel snapshots.

Rates are fetched at snapshot time and recorded on every row, so a snapshot is
reproducible rather than silently re-rated on a later read.
"""
from __future__ import annotations

from typing import Callable, Dict, Iterable, Optional

from panel.schema import METRIC_COLUMNS

# yfinance-style tickers quote units of local currency per USD.
FX_TICKERS = {
    "AUD": "AUD=X", "TWD": "TWD=X", "PHP": "PHP=X", "KRW": "KRW=X",
    "CNY": "CNY=X", "INR": "INR=X", "HKD": "HKD=X", "JPY": "JPY=X",
    "EUR": "EUR=X", "GBP": "GBP=X",
}


def _yf_fetcher(tickers) -> Dict[str, float]:
    import yfinance as yf

    out: Dict[str, float] = {}
    for ticker in tickers:
        try:
            hist = yf.Ticker(ticker).history(period="1d")
            if len(hist):
                out[ticker] = float(hist["Close"].iloc[-1])
        except Exception:
            continue
    return out


def fetch_rates(
    currencies: Iterable[str],
    fetcher: Optional[Callable[[Iterable[str]], Dict[str, float]]] = None,
) -> Dict[str, float]:
    """currency -> USD per unit of that currency."""
    wanted = {c for c in currencies if c}
    rates: Dict[str, float] = {}
    if "USD" in wanted:
        rates["USD"] = 1.0
        wanted.discard("USD")
    tickers = {FX_TICKERS[c]: c for c in wanted if c in FX_TICKERS}
    if not tickers:
        return rates
    quotes = (fetcher or _yf_fetcher)(list(tickers))
    for ticker, value in (quotes or {}).items():
        currency = tickers.get(ticker)
        if currency and value:
            rates[currency] = 1.0 / float(value)
    return rates


def to_usd(row: dict, rates: Dict[str, float], asof: str) -> dict:
    out = dict(row)
    rate = rates.get(row.get("currency"))
    out["fx_rate"] = rate
    out["fx_asof"] = asof
    for metric in METRIC_COLUMNS:
        value = row.get(metric)
        out[f"{metric}_usd"] = (
            None if (rate is None or value is None) else float(value) * rate
        )
    return out
