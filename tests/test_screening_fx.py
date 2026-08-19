"""FX must come from the wire, and a fallback must be loud.

Incident 2026-08-18: yfinance `fast_info["last_price"]` began returning None for
every FX ticker, so build_pdf_market_screening_input silently used its hardcoded
fallbacks. The errors are material -- INR fallback 1/88 against a live 95.67 is
8.7%, AUD 0.66 against 0.7076 is 7.2% -- and every USD figure in the affected
screening inputs carries them. Nothing in the output said "fallback"; the run
printed a number and looked healthy.

The project rule is to fetch live FX for any cross-currency conversion and never
use a remembered rate. A silent fallback is a remembered rate.
"""

import pytest

from build_pdf_market_screening_input import fx_with_source


class _FastInfoOnly:
    """fast_info works -- the historical happy path."""
    def __init__(self, price): self._p = price
    @property
    def fast_info(self): return {"last_price": self._p}
    def history(self, period="5d"): raise AssertionError("should not be reached")


class _HistoryOnly:
    """fast_info returns None, as it does live today."""
    def __init__(self, close): self._c = close
    @property
    def fast_info(self): return {"last_price": None}
    def history(self, period="5d"):
        import pandas as pd
        return pd.DataFrame({"Close": [self._c]})


class _Dead:
    @property
    def fast_info(self): raise RuntimeError("network down")
    def history(self, period="5d"): raise RuntimeError("network down")


def test_fast_info_is_used_when_it_works():
    rate, source = fx_with_source({"fx_ticker": "X=X", "fallback": 0.5}, ticker_factory=lambda t: _FastInfoOnly(2.0))
    assert rate == 2.0 and source == "live:fast_info"


def test_history_is_used_when_fast_info_returns_none():
    """The live case: fast_info gives None for every FX ticker."""
    rate, source = fx_with_source({"fx_ticker": "X=X", "fallback": 0.5}, ticker_factory=lambda t: _HistoryOnly(4.0))
    assert rate == 4.0 and source == "live:history"


def test_inversion_applies_to_the_history_path_too():
    """TWD=X/INR=X quote local-per-USD and must invert; a fix that inverted only
    the fast_info path would have produced rates 1000x off."""
    rate, source = fx_with_source({"fx_ticker": "INR=X", "fallback": 1 / 88.0, "invert": True},
                                  ticker_factory=lambda t: _HistoryOnly(95.67))
    assert rate == pytest.approx(1 / 95.67)
    assert source == "live:history"


def test_a_dead_source_falls_back_but_says_so():
    rate, source = fx_with_source({"fx_ticker": "X=X", "fallback": 0.25}, ticker_factory=lambda t: _Dead())
    assert rate == 0.25
    assert source == "FALLBACK"


def test_a_nonsense_rate_is_rejected():
    rate, source = fx_with_source({"fx_ticker": "X=X", "fallback": 0.25}, ticker_factory=lambda t: _HistoryOnly(0.0))
    assert source == "FALLBACK"
