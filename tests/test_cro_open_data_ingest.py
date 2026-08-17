"""Tests for the CRO (Ireland) open-data ingest.

opendata.cro.ie publishes a CKAN portal under CC BY 4.0 with an index of every
set of accounts filed in Ireland -- 230,410 rows for 2024 alone. It is an
INDEX, not the documents: rows carry a `file_name` like `130097420.pdf` whose
image is the paid part.
"""

import json

import pytest

from scripts.cro_open_data_ingest import (
    CKAN_BASE,
    is_index_only,
    local_path_for,
    package_url,
    resource_url,
    select_resources,
    summarise,
    year_of_resource,
)


def _pkg(names=("Financial Statements 2022", "Financial Statements 2023")):
    return {
        "result": {
            "title": "Financial Statements",
            "license_title": "Creative Commons Attribution 4.0",
            "resources": [
                {"name": n, "format": "CSV", "url": f"https://x/{n.replace(' ','_')}.csv"}
                for n in names
            ],
        }
    }


# --- resource selection -------------------------------------------------


def test_select_resources_keeps_only_csv():
    pkg = _pkg()
    pkg["result"]["resources"].append({"name": "notes", "format": "PDF", "url": "https://x/n.pdf"})
    got = select_resources(pkg)
    assert [r["format"] for r in got] == ["CSV", "CSV"]


def test_select_resources_handles_lowercase_format():
    pkg = {"result": {"resources": [{"name": "a", "format": "csv", "url": "https://x/a.csv"}]}}
    assert len(select_resources(pkg)) == 1


def test_select_resources_skips_resources_without_a_url():
    pkg = {"result": {"resources": [{"name": "a", "format": "CSV", "url": ""}]}}
    assert select_resources(pkg) == []


def test_select_resources_on_empty_package():
    assert select_resources({"result": {}}) == []


# --- year parsing -------------------------------------------------------


def test_year_of_resource_reads_the_name():
    assert year_of_resource({"name": "Financial Statements 2024"}) == "2024"
    assert year_of_resource({"name": "Financial Statements 2022"}) == "2022"


def test_year_of_resource_falls_back_to_the_url():
    assert year_of_resource({"name": "x", "url": "https://x/financial_statements_2023.csv"}) == "2023"


def test_year_of_resource_returns_undated_when_absent():
    """The base Company Records resource carries no year."""
    assert year_of_resource({"name": "Company Records", "url": "https://x/c.csv"}) == "undated"


def test_year_of_resource_ignores_implausible_numbers():
    assert year_of_resource({"name": "Statements 12345", "url": ""}) == "undated"


# --- urls and paths -----------------------------------------------------


def test_package_url_targets_the_ckan_action_api():
    assert package_url("financial-statements") == (
        f"{CKAN_BASE}/api/3/action/package_show?id=financial-statements"
    )


def test_resource_url_passes_through():
    assert resource_url({"url": "https://x/a.csv"}) == "https://x/a.csv"


def test_local_path_lands_under_the_irish_canonical_tree():
    p = local_path_for("/root", "financial-statements", "2024")
    assert p == "/root/markets/ie/01_raw/cro_open_data/financial-statements/2024.csv"


# --- the index-vs-documents distinction ---------------------------------


def test_index_only_is_true_for_cro_rows():
    """CRO rows name a PDF we do not hold; treating them as acquired
    documents would report Ireland as 0% acquired across 690k phantom rows and
    swamp every real gap in the coverage surface."""
    assert is_index_only("financial-statements") is True
    assert is_index_only("companies") is True


# --- summary ------------------------------------------------------------


def test_summarise_counts_rows_and_companies(tmp_path):
    csv_path = tmp_path / "2024.csv"
    csv_path.write_text(
        "file_name,company_num,company_name,submission_num\n"
        "1.pdf,100,ALPHA LTD,SR1\n"
        "2.pdf,100,ALPHA LTD,SR2\n"
        "3.pdf,200,BETA LTD,SR3\n"
    )
    s = summarise(csv_path)
    assert s["rows"] == 3
    assert s["companies"] == 2
    assert s["documents_named"] == 3


def test_summarise_tolerates_a_bom_header(tmp_path):
    """The live file's first column arrives as '\\ufefffile_name'."""
    csv_path = tmp_path / "x.csv"
    csv_path.write_text("﻿file_name,company_num\n1.pdf,100\n")
    s = summarise(csv_path)
    assert s["rows"] == 1
    assert s["documents_named"] == 1


def test_looks_like_zip_detects_mislabelled_resources(tmp_path):
    """CKAN declares Company Records as format CSV, but it ships a ZIP.

    Trusting the declared format made csv.DictReader fail with
    "_csv.Error: line contains NUL" on the PK header.
    """
    from scripts.cro_open_data_ingest import looks_like_zip

    z = tmp_path / "a.csv"
    z.write_bytes(b"PK\x03\x04" + b"\x00" * 40)
    assert looks_like_zip(z) is True

    c = tmp_path / "b.csv"
    c.write_text("file_name,company_num\n1.pdf,100\n")
    assert looks_like_zip(c) is False


def test_looks_like_zip_on_missing_file(tmp_path):
    from scripts.cro_open_data_ingest import looks_like_zip
    assert looks_like_zip(tmp_path / "nope.csv") is False
