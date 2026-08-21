"""Nordic adapter: 8 countries, currency read per filing, and a year that isn't a lie.

The sweep's `fiscal_year` is the PUBLICATION year -- the directory an
announcement was filed under -- not the year the statements cover. Measured on
the live corpus, the filename year is directory-year MINUS ONE for 85.1% of
Norwegian rows, 57% of Swedish, 47% of Danish, 61% of Finnish. Taking the
directory year at face value misdates most of the corpus by a FULL YEAR, which
is worse than any period-end defect found so far.

Each country becomes its own market rather than one "nordic" blob, because they
have different currencies and belong to different exchanges -- and because one
shared identity is what let five markets sit behind a single US universe hash.
"""

import pandas as pd
import pytest

from panel.adapters.nordic import fiscal_year_from_doc_id, rows_from_nordic


def _frame(**over):
    row = {"ticker": "INFK", "filing_date": "2022", "fiscal_year": 2022,
           "doc_id": "INFK_2022_562830_239964_2021_Årsmelding", "n_metrics": 3,
           "country": "no", "currency": "NOK", "currency_source": "declared",
           "total_assets": 100.0, "revenue": 50.0}
    row.update(over)
    return pd.DataFrame([row])


# --- the year defect -----------------------------------------------------


def test_the_filename_year_wins_over_the_directory_year():
    """The live case: a 2021 annual report filed in the 2022 directory."""
    assert fiscal_year_from_doc_id(
        "INFK_2022_562830_239964_2021_Årsmelding", "INFK", 2022) == (2021, "derived:filename")


def test_a_filename_without_a_year_falls_back_to_publication_minus_one():
    """Annual reports are published the year AFTER the period they cover; 47-85%
    of rows show exactly that offset."""
    assert fiscal_year_from_doc_id(
        "INFK_2022_562830_239966_Revisors_beretning", "INFK", 2022) == (2021, "derived:publication-year-minus-1")


def test_a_filename_year_equal_to_the_directory_year_is_kept():
    assert fiscal_year_from_doc_id("ABC_2022_1_2_Rapport_2022", "ABC", 2022)[0] == 2022


def test_an_implausible_filename_year_is_ignored():
    """Message ids and org numbers contain 4-digit runs that are not years."""
    got, _ = fiscal_year_from_doc_id("ABC_2022_562830_239964_Delrapport_1899", "ABC", 2022)
    assert got == 2021


# --- market identity and currency ---------------------------------------


def test_each_country_becomes_its_own_market():
    rows = rows_from_nordic(_frame(country="no"))
    assert rows[0]["market"] == "no"
    rows = rows_from_nordic(_frame(country="se", currency="SEK"))
    assert rows[0]["market"] == "se"


def test_currency_comes_from_the_row_not_the_country():
    """A Swedish filer reporting EUR must stay EUR -- 1,926 EUR and 1,546 USD
    filings sit in a corpus a country rule would call SEK/NOK/DKK."""
    rows = rows_from_nordic(_frame(country="se", currency="EUR"))
    assert rows[0]["currency"] == "EUR"


def test_rows_without_a_country_are_dropped_and_counted():
    from collections import Counter
    drops = Counter()
    rows = rows_from_nordic(_frame(country=None), drops=drops)
    assert rows == [] and drops["missing_country"] == 1


def test_period_end_is_a_labelled_convention():
    """Nordic statutory years end 31 December, but the source carries no period
    end, so it must not claim to be reported."""
    rows = rows_from_nordic(_frame())
    assert rows[0]["period_end"] == "2021-12-31"
    assert rows[0]["period_end_source"] == "convention:no"


def test_unit_scale_is_unknown_not_one():
    """No scale detection has been run over the Nordic corpus."""
    rows = rows_from_nordic(_frame())
    assert rows[0]["unit_scale"] is None


def test_rows_with_no_metrics_are_dropped():
    rows = rows_from_nordic(_frame(n_metrics=0, total_assets=None, revenue=None))
    assert rows == []
