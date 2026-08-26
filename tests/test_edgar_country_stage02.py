"""Feed country-pull EDGAR filings into extract_items.

716 documents (EDGAR_IE 407, EDGAR_DE 309) were acquired and never processed.
The blocker was never the extractor -- ExtractItems.resolve_source_path already
honours an explicit `source_path`, with a docstring naming this exact case
("Country-pull documents live under market/CIK/accession"). What was missing was
the adapter that turns MARKET_FILINGS metadata rows into the dicts it expects.

The traps are in the mapping, not the extraction:

  1. Our raw layout is {CIK}/{accession}/{file}.htm, which the legacy
     Type/filename resolution cannot express -- source_path must be set or the
     extractor looks in the wrong place and finds nothing.
  2. Filing TYPE decides which item set is extracted: a 20-F has different
     items from a 10-K, and Irish/German issuers file both.
  3. A row without a local_path was never acquired; feeding it produces a
     confident empty extraction.
"""

import pytest

from scripts.edgar_country_stage02 import filing_type_for, metadata_row


def _row(**over):
    row = {"market": "EDGAR_IE", "filing_id": "0001571049-15-002062",
           "stock_code": "1603469", "company_name": "RYANAIR HOLDINGS PLC",
           "filing_date": "2015-07-28", "category": "20-F",
           "local_path": "/x/raw/EDGAR_IE/1603469/0001571049-15-002062/t1500616_20f.htm"}
    row.update(over)
    return row


def test_source_path_is_explicit_so_the_legacy_layout_is_bypassed():
    md = metadata_row(_row())
    assert md["source_path"].endswith("t1500616_20f.htm")


def test_the_filing_type_drives_the_item_set():
    """A 20-F has different items from a 10-K; misreporting the type extracts
    the wrong sections silently."""
    assert filing_type_for("20-F") == "20-F"
    assert filing_type_for("10-K") == "10-K"
    assert filing_type_for("Annual report 20-F") == "20-F"


def test_an_unknown_category_falls_back_to_10k_only_when_the_file_says_so():
    assert filing_type_for("", filename="acahu-20211231x10k.htm") == "10-K"
    assert filing_type_for("", filename="t1500616_20f.htm") == "20-F"


def test_an_unclassifiable_filing_returns_none_rather_than_guessing():
    assert filing_type_for("", filename="something.htm") is None


def test_required_columns_are_present():
    md = metadata_row(_row())
    for key in ("cik", "company", "Type", "Date", "filename", "source_path"):
        assert key in md, key


def test_a_row_without_a_local_path_is_rejected():
    """It was never acquired; extracting it yields a confident empty result."""
    assert metadata_row(_row(local_path="")) is None


def test_cik_and_filename_come_from_the_path_not_a_guess():
    md = metadata_row(_row())
    assert md["cik"] == "1603469"
    assert md["filename"] == "t1500616_20f.htm"


def test_both_key_casings_are_supplied():
    """extract_items reads cik and CIK, company and Company, depending on the
    code path. Supplying one raised KeyError deep in extraction, AFTER the file
    had been read -- 8 of 8 smoke filings failed that way."""
    md = metadata_row(_row())
    for lower, upper in (("cik", "CIK"), ("company", "Company"), ("sic", "SIC")):
        assert lower in md and upper in md, (lower, upper)
