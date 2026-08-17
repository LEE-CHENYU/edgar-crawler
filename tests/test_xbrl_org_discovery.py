"""Tests for the filings.xbrl.org completion worker (Workstream D1).

The source holds 25,675 filings; 15,875 were pulled by an ad-hoc pass that
left no state file and no script in either repo. This worker enumerates the
catalogue, diffs against existing metadata, and fetches only what is missing.
"""

import json

from scripts.xbrl_org_discovery import (
    absolute_url,
    build_row,
    local_path_for,
    market_for,
    select_missing,
    sha256_status,
    taxonomy_of,
)


def _filing(fxo="0W5QHUNYV4W7GJO62R27-2020-12-31-ESEF-AT-0", country="AT",
            package="/0W5QHUNYV4W7GJO62R27/2020-12-31/ESEF/AT/0/x.zip"):
    return {
        "id": "1106",
        "attributes": {
            "fxo_id": fxo,
            "country": country,
            "period_end": "2020-12-31",
            "package_url": package,
            "report_url": "/r.html",
            "json_url": "/r.json",
            "sha256": "abc123",
            "date_added": "2021-05-18 00:00:00",
            "error_count": "1",
            "warning_count": "0",
            "inconsistency_count": "0",
        },
    }


# --- naming, matching the existing 15,875 rows ---------------------------


def test_market_name_matches_existing_esef_rows():
    """Existing rows use ESEF_AT, so the worker must not invent a new name."""
    assert market_for(_filing()["attributes"]) == "ESEF_AT"


def test_market_name_carries_non_esef_taxonomy():
    """The catalogue is not only ESEF -- Ukraine files under UAIFRS."""
    attrs = _filing(fxo="EDRPOU-32033791-2020-12-31-UAIFRS-UA-0", country="UA")["attributes"]
    assert market_for(attrs) == "UAIFRS_UA"


def test_taxonomy_of_reads_the_fxo_id():
    assert taxonomy_of("0W5QHUNYV4W7GJO62R27-2020-12-31-ESEF-AT-0") == "ESEF"
    assert taxonomy_of("EDRPOU-32033791-2020-12-31-UAIFRS-UA-0") == "UAIFRS"


def test_taxonomy_of_tolerates_garbage():
    assert taxonomy_of("") == "UNKNOWN"
    assert taxonomy_of("nodashes") == "UNKNOWN"


def test_local_path_mirrors_the_existing_layout():
    attrs = _filing()["attributes"]
    path = local_path_for("/root", attrs)
    assert path == (
        "/root/markets/at/01_raw/europe_annual_reports/xbrl_org/AT"
        "/0W5QHUNYV4W7GJO62R27/2020-12-31"
        "/0W5QHUNYV4W7GJO62R27-2020-12-31-ESEF-AT-0"
        "/0W5QHUNYV4W7GJO62R27-2020-12-31.zip"
    )


def test_absolute_url_prefixes_relative_package_paths():
    assert absolute_url("/a/b.zip") == "https://filings.xbrl.org/a/b.zip"
    assert absolute_url("https://filings.xbrl.org/a/b.zip") == "https://filings.xbrl.org/a/b.zip"


# --- diffing against what we already have -------------------------------


def test_select_missing_skips_filings_already_in_metadata():
    filings = [_filing(fxo="A-2020-12-31-ESEF-AT-0"), _filing(fxo="B-2020-12-31-ESEF-AT-0")]
    missing = select_missing(filings, existing_ids={"A-2020-12-31-ESEF-AT-0"})
    assert [f["attributes"]["fxo_id"] for f in missing] == ["B-2020-12-31-ESEF-AT-0"]


def test_select_missing_skips_filings_without_a_package():
    """package_url 'None' means there is nothing to download."""
    filings = [_filing(fxo="C-2020-12-31-ESEF-AT-0", package="None")]
    assert select_missing(filings, existing_ids=set()) == []


def test_select_missing_keeps_everything_new_with_a_package():
    filings = [_filing(fxo="D-2020-12-31-ESEF-AT-0")]
    assert len(select_missing(filings, existing_ids=set())) == 1


# --- integrity ----------------------------------------------------------


def test_sha256_status_flags_mismatch():
    assert sha256_status(expected="abc", actual="abc") == "match"
    assert sha256_status(expected="abc", actual="def") == "MISMATCH"


def test_sha256_status_handles_absent_expectation():
    """The catalogue does not always publish a digest."""
    assert sha256_status(expected="", actual="def") == "unverified"
    assert sha256_status(expected=None, actual="def") == "unverified"


# --- row shape ----------------------------------------------------------


def test_build_row_matches_metadata_schema_and_keys_on_fxo_id():
    row = build_row(_filing(), local_path="/p/x.zip", entity_name="HYPO TIROL BANK AG",
                    sha256_actual="abc123")
    assert row["market"] == "ESEF_AT"
    assert row["filing_id"] == "0W5QHUNYV4W7GJO62R27-2020-12-31-ESEF-AT-0"
    assert row["stock_code"] == "0W5QHUNYV4W7GJO62R27"
    assert row["company_name"] == "HYPO TIROL BANK AG"
    assert row["local_path"] == "/p/x.zip"
    assert row["document_url"].startswith("https://filings.xbrl.org/")
    raw = json.loads(row["raw_metadata"])
    assert raw["fxo_id"] == row["filing_id"]
    assert raw["sha256_status"] == "match"
    assert raw["download_kind"] == "package"


def test_build_row_records_failed_download_without_local_path():
    row = build_row(_filing(), local_path="", entity_name="X", sha256_actual="")
    assert row["local_path"] == ""
    assert json.loads(row["raw_metadata"])["sha256_status"] == "unverified"


def test_sha256_status_absent_actual_is_unverified_not_mismatch():
    """A failed download has no digest; that is absence, not corruption."""
    assert sha256_status(expected="abc", actual="") == "unverified"
    assert sha256_status(expected="abc", actual=None) == "unverified"
