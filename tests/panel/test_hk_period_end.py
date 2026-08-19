"""Derive HK period ends from filing month instead of assuming December.

The HK facts carry filing_date and fiscal_year but no period end, so the adapter
fabricated {fiscal_year}-12-31 for all 83,144 rows. HKEX Main Board rules give
issuers 4 months after year end to publish an annual report, so the filing month
identifies the year end. Measured over 2,912 sampled filings:

    Apr 1,728 + Mar 320  -> 31 December  (~70%)
    Jul   424 + Jun 151  -> 31 March     (~20%)

The March cohort was being misdated by a full quarter, which misaligns those
issuers against every other market in any cross-sectional query.

This is INFERENCE, not a reading, so every derived date stays labelled
"derived:filing-month" -- distinct from "reported" (read from the filing) and
from "assumed:calendar-year-end" (no evidence at all).
"""

import pytest

from panel.adapters.hk import period_end_from_filing_month


# --- the two dominant cohorts -------------------------------------------


def test_april_filing_implies_a_december_year_end():
    assert period_end_from_filing_month(2023, "2024-04-02") == ("2023-12-31", "derived:filing-month")


def test_july_filing_implies_a_march_year_end():
    """FY2023 ending 31 March 2024, published July 2024 -- inside the 4-month
    window. The year end lands in the CALENDAR YEAR AFTER the fiscal label."""
    assert period_end_from_filing_month(2023, "2024-07-15") == ("2024-03-31", "derived:filing-month")


def test_march_filing_is_an_early_december_filer():
    assert period_end_from_filing_month(2023, "2024-03-20")[0] == "2023-12-31"


def test_june_filing_is_a_march_year_end():
    assert period_end_from_filing_month(2023, "2024-06-28")[0] == "2024-03-31"


# --- honesty about what this is -----------------------------------------


def test_a_derived_date_is_never_labelled_reported():
    _, source = period_end_from_filing_month(2023, "2024-04-02")
    assert source.startswith("derived:")


def test_no_filing_date_falls_back_and_says_so():
    """Without a filing date there is no evidence at all -- that must not
    masquerade as a derivation."""
    assert period_end_from_filing_month(2023, None) == ("2023-12-31", "assumed:calendar-year-end")
    assert period_end_from_filing_month(2023, "") == ("2023-12-31", "assumed:calendar-year-end")


def test_an_unparseable_filing_date_falls_back():
    assert period_end_from_filing_month(2023, "not-a-date")[1] == "assumed:calendar-year-end"


# --- months outside both cohorts ----------------------------------------


def test_an_ambiguous_month_falls_back_rather_than_guessing():
    """Aug-Feb filings fit neither cohort cleanly. 2,912 sampled filings put
    only ~10% there, and inventing a year end for them would trade a known
    error for an invented one."""
    for month in ("2024-09-10", "2024-11-04", "2024-01-15"):
        assert period_end_from_filing_month(2023, month)[1] == "assumed:calendar-year-end"


def test_the_derived_date_never_precedes_the_fiscal_year():
    end, _ = period_end_from_filing_month(2023, "2024-07-15")
    assert end >= "2023-12-31"
