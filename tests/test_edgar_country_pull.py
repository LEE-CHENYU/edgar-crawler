"""Tests for the EDGAR country pull (Ireland first).

Ireland has zero rows in the corpus and no reachable domestic source:
oam.centralbank.ie does not connect, CRO/CORE return 403, the CBI OAM page
404s. But 400+ Ireland-based filers file 20-F/10-K with the SEC, which this
repo already has tooling and rate-limit etiquette for.
"""

import json

import pytest

from scripts.edgar_country_pull import (
    ANNUAL_FORMS,
    accession_no_dashes,
    build_row,
    document_url,
    is_annual_form,
    local_path_for,
    market_for_country,
    parse_ciks,
    select_annual_filings,
)


ATOM = """<feed>
  <entry title="x"><content type="text/xml"><company-info name="y">
    <cik>0001472685</cik><state>L2</state></company-info></content></entry>
  <entry title="x"><content type="text/xml"><company-info name="y">
    <cik>0000320193</cik><state>L2</state></company-info></content></entry>
</feed>"""


def _submissions():
    return {
        "cik": "1472685",
        "name": "RYANAIR HOLDINGS PLC",
        "filings": {
            "recent": {
                "form": ["20-F", "6-K", "20-F/A", "10-K", "8-K"],
                "accessionNumber": [
                    "0001472685-24-000010", "0001472685-24-000011",
                    "0001472685-23-000012", "0001472685-22-000013",
                    "0001472685-21-000014",
                ],
                "filingDate": ["2024-07-22", "2024-08-01", "2023-07-20",
                               "2022-07-19", "2021-07-15"],
                "primaryDocument": ["ryanair-20240331.htm", "six-k.htm",
                                    "amend.htm", "tenk.htm", "eightk.htm"],
                "reportDate": ["2024-03-31", "", "2023-03-31", "2022-03-31", ""],
            }
        },
    }


# --- CIK enumeration ----------------------------------------------------


def test_parse_ciks_reads_the_atom_feed():
    """The tag carries attributes, so a naive <company-info> match finds none."""
    assert parse_ciks(ATOM) == ["0001472685", "0000320193"]


def test_parse_ciks_is_empty_on_junk():
    assert parse_ciks("<feed></feed>") == []


# --- form selection -----------------------------------------------------


def test_annual_forms_cover_foreign_and_domestic_issuers():
    assert "20-F" in ANNUAL_FORMS and "10-K" in ANNUAL_FORMS


def test_is_annual_form_accepts_amendments():
    assert is_annual_form("20-F") is True
    assert is_annual_form("20-F/A") is True
    assert is_annual_form("10-K") is True


def test_is_annual_form_rejects_periodic_and_event_filings():
    for form in ("6-K", "8-K", "S-1", "424B4", ""):
        assert is_annual_form(form) is False, form


def test_select_annual_filings_picks_only_annual_reports():
    got = select_annual_filings(_submissions())
    assert [f["form"] for f in got] == ["20-F", "20-F/A", "10-K"]
    assert got[0]["accession"] == "0001472685-24-000010"
    assert got[0]["primary_document"] == "ryanair-20240331.htm"


def test_select_annual_filings_tolerates_missing_blocks():
    assert select_annual_filings({"filings": {}}) == []
    assert select_annual_filings({}) == []


# --- urls and paths -----------------------------------------------------


def test_accession_no_dashes_strips_for_the_archive_path():
    assert accession_no_dashes("0001472685-24-000010") == "000147268524000010"


def test_document_url_uses_the_undashed_accession():
    url = document_url("1472685", "0001472685-24-000010", "ryanair-20240331.htm")
    assert url == (
        "https://www.sec.gov/Archives/edgar/data/1472685/000147268524000010"
        "/ryanair-20240331.htm"
    )


def test_local_path_groups_by_country_and_cik(tmp_path):
    path = local_path_for(str(tmp_path), "IE", "1472685", "0001472685-24-000010",
                          "ryanair-20240331.htm")
    assert "/raw/EDGAR_IE/1472685/0001472685-24-000010/ryanair-20240331.htm" in path


def test_market_for_country_is_namespaced():
    assert market_for_country("IE") == "EDGAR_IE"
    assert market_for_country("de") == "EDGAR_DE"


# --- rows ---------------------------------------------------------------


def test_build_row_carries_provenance_and_report_date():
    filing = select_annual_filings(_submissions())[0]
    row = build_row(filing, cik="1472685", company="RYANAIR HOLDINGS PLC",
                    country="IE", local_path="/p/x.htm")
    assert row["market"] == "EDGAR_IE"
    assert row["filing_id"] == "0001472685-24-000010"
    assert row["filing_date"] == "2024-07-22"
    assert row["company_name"] == "RYANAIR HOLDINGS PLC"
    assert row["stock_code"] == "1472685"
    assert row["category"] == "20-F"
    assert row["local_path"] == "/p/x.htm"
    raw = json.loads(row["raw_metadata"])
    assert raw["report_date"] == "2024-03-31"
    assert raw["source"] == "sec.gov/EDGAR"
