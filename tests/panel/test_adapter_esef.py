"""ESEF adapter: 27 European markets from inline XBRL.

The first source in this panel where nothing has to be inferred. period_end is
READ from the XBRL context, currency is READ from the unit, and the decimal
scale was already applied during extraction. Every other market required a
convention, a derivation or an assumption for at least one of those.

Two things the adapter must get right, both learned expensively elsewhere:

  1. period_end_source must say "reported" -- and it is the FIRST market that
     legitimately can. Fabricating or mislabelling this misdated 37,669 JP rows
     and 83,144 HK rows earlier in this program.
  2. unit_scale is 1 because the iXBRL `scale` attribute was already applied,
     which is NOT the same claim as "unknown". Recording unknown would exclude
     these rows from level comparisons for no reason; recording a bare 1 with no
     provenance is what let lakh figures pass as units.
"""

import pandas as pd
import pytest

from panel.adapters.esef import rows_from_esef


def _frame(**over):
    row = {"market": "uk", "lei": "213800X8FBGL5UP2JR58", "period_end": "2025-09-30",
           "currency": "GBP", "currency_source": "declared", "n_metrics": 18,
           "total_assets": 3.4241e9, "revenue": 3.6385e9, "net_income": -24000000.0,
           "source_path": "/x/markets/uk/01_raw/.../a.zip"}
    row.update(over)
    return pd.DataFrame([row])


def test_storage_stays_per_country_even_though_the_market_is_namespaced():
    """The canonical STORAGE path is markets/uk/02_structured (per CLAUDE.md's
    layout); only the panel market KEY is namespaced, to keep LEI-keyed ESEF
    rows out of the ticker-keyed Nordic markets."""
    rows = rows_from_esef(_frame())
    assert rows[0]["market"] == "esef_uk"
    assert "markets/uk/02_structured" in rows[0]["source_artifact"]


def test_the_lei_is_the_local_identifier():
    """ESEF filings carry no ticker; the LEI is the issuer identity."""
    rows = rows_from_esef(_frame())
    assert rows[0]["local_id"] == "213800X8FBGL5UP2JR58"


def test_period_end_is_reported_not_derived():
    """The first market in this panel that can honestly claim this."""
    rows = rows_from_esef(_frame())
    assert rows[0]["period_end"] == "2025-09-30"
    assert rows[0]["period_end_source"] == "reported"


def test_currency_is_carried_per_filing():
    """499 UK filers report USD and 54 report RUB. A country default would
    mislabel every one."""
    rows = rows_from_esef(_frame(currency="USD"))
    assert rows[0]["currency"] == "USD"


def test_unit_scale_is_one_because_the_ixbrl_scale_was_applied():
    """Distinct from 'unknown': the scale is KNOWN and already folded into the
    values, so these rows belong in level comparisons."""
    rows = rows_from_esef(_frame())
    assert rows[0]["unit_scale"] == 1
    assert rows[0]["unit_scale_source"] == "applied:ixbrl-scale"


def test_fiscal_year_comes_from_the_period_end():
    rows = rows_from_esef(_frame(period_end="2025-09-30"))
    assert rows[0]["fiscal_year"] == 2025


def test_rows_without_a_period_end_are_dropped_and_counted():
    from collections import Counter
    drops = Counter()
    assert rows_from_esef(_frame(period_end=None), drops=drops) == []
    assert drops["missing_period_end"] == 1


def test_rows_without_an_lei_are_dropped():
    from collections import Counter
    drops = Counter()
    assert rows_from_esef(_frame(lei=None), drops=drops) == []
    assert drops["missing_lei"] == 1


def test_rows_with_no_metrics_are_dropped():
    from collections import Counter
    drops = Counter()
    assert rows_from_esef(_frame(n_metrics=0), drops=drops) == []
    assert drops["no_metrics"] == 1


def test_an_opening_balance_period_is_typed_open_not_annual():
    """A 01-01 period end is an opening balance in every cadence -- counting it
    as a year would double-count the prior close."""
    rows = rows_from_esef(_frame(period_end="2023-01-01"))
    assert rows[0]["period_type"] == "OPEN"


def test_esef_markets_are_namespaced_to_avoid_colliding_with_nordic():
    """Sweden appears in BOTH corpora: the Nasdaq Nordic sweep emits market "se"
    with a TICKER local_id and a valid exchange code, while ESEF emits Swedish
    filings keyed by LEI. Sharing the key "se" would mix two identifier spaces
    in one market and force the ticker-keyed rows to be treated as surrogate-
    keyed. Storage stays per-country (markets/se/02_structured); only the PANEL
    key is namespaced."""
    rows = rows_from_esef(_frame(market="se"))
    assert rows[0]["market"] == "esef_se"
    rows = rows_from_esef(_frame(market="uk"))
    assert rows[0]["market"] == "esef_uk"


def test_a_period_end_with_surrounding_whitespace_is_parsed(tmp_path):
    """iXBRL contexts sometimes carry pretty-printed whitespace inside the date
    element. The guard stripped before the emptiness check but sliced the
    UNSTRIPPED value, so int(period_end[:4]) hit '\\n   ' and killed the whole
    27-market load with one bad row."""
    rows = rows_from_esef(_frame(period_end="\n   2025-09-30\n  "))
    assert rows[0]["period_end"] == "2025-09-30"
    assert rows[0]["fiscal_year"] == 2025


def test_a_malformed_period_end_drops_the_row_not_the_market():
    """One unparseable date must not abort a 15,652-row load."""
    from collections import Counter
    drops = Counter()
    assert rows_from_esef(_frame(period_end="not-a-date"), drops=drops) == []
    assert drops["unparseable_period_end"] == 1
