"""Period end must follow the market's fiscal calendar, not `-12-31`.

Both adapters that derive a period end from a fiscal-year label hardcoded
December 31. That is wrong for two markets already in the panel: Australian
companies close 30 June and Indian ones 31 March, so every AU row was misdated
by 6 months and every IN row by 9. Misdated rows misalign against every other
market in any cross-sectional query -- an AU "2024" row compared against a CN
2024-12-31 row is comparing figures two quarters apart.

The provenance column is the other half: a consumer must be able to tell a
period end that was READ from the filing from one this code inferred from a
directory name.
"""

import pandas as pd
import pytest

from panel.adapters.in_bse import rows_from_canonical_metrics
from panel.adapters.screening_input import rows_from_screening_csv


def _wide(fiscal_year=2024):
    """A canonical_metrics_wide frame -- the adapter takes the DataFrame
    itself, not a path."""
    return pd.DataFrame([{
        "ticker": "ABC", "fiscal_year": fiscal_year, "doc_id": "d1",
        "company_name": "Abc Ltd", "total_assets": 100.0,
    }])


# --- the two broken markets ---------------------------------------------


def test_australia_closes_30_june():
    rows = rows_from_canonical_metrics(_wide(), market="au", currency="AUD")
    assert rows[0]["period_end"] == "2024-06-30"


def test_india_closes_31_march():
    rows = rows_from_canonical_metrics(_wide(), market="in_bse", currency="INR")
    assert rows[0]["period_end"] == "2024-03-31"


def test_calendar_year_markets_are_unchanged():
    """tw/ph/kr genuinely close 31 December -- the fix must not move them."""
    for market in ("tw", "ph", "kr"):
        rows = rows_from_canonical_metrics(_wide(), market=market, currency="TWD")
        assert rows[0]["period_end"] == "2024-12-31", market


# --- provenance ---------------------------------------------------------


def test_convention_derived_period_end_is_labelled_as_such():
    rows = rows_from_canonical_metrics(_wide(), market="au", currency="AUD")
    assert rows[0]["period_end_source"] == "convention:au"


def test_unknown_market_is_labelled_assumed_not_convention():
    """An unlisted market still needs a date, but must not claim a convention
    nobody verified -- that is how `-12-31` passed for AU in the first place."""
    rows = rows_from_canonical_metrics(_wide(), market="atlantis", currency="USD")
    assert rows[0]["period_end"] == "2024-12-31"
    assert rows[0]["period_end_source"] == "assumed:calendar-year-end"


# --- the same defect in the screening adapter ---------------------------


def _screening_csv(tmp_path, year=2024):
    path = tmp_path / "s.csv"
    path.write_text(
        "Ticker,bsns_year,Company Name,Currency,Total Assets\n"
        f"ABC,{year},Abc Ltd,AUD,100\n", encoding="utf-8")
    return path


def test_screening_adapter_follows_the_same_convention(tmp_path):
    rows = rows_from_screening_csv(_screening_csv(tmp_path), market="au")
    assert rows[0]["period_end"] == "2024-06-30"
    assert rows[0]["period_end_source"] == "convention:au"


# --- period_type must be derived from the corrected date ----------------


def test_period_type_follows_the_corrected_date():
    """period_type is computed FROM period_end; moving the date must not leave
    a period_type derived from the old one."""
    rows = rows_from_canonical_metrics(_wide(), market="in_bse", currency="INR")
    assert rows[0]["period_end"] == "2024-03-31"
    assert rows[0]["period_type"] == "A"
