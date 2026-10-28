"""Operational QA tests for the India BSE Stage-02 job."""

import json
import subprocess

import pytest
import requests

from in_bse_stage02_job import (
    _contiguous_page_ranges,
    _ocr_fallback,
    _ocr_pages,
    _ocr_row_set_quality,
    broad_statement_kinds,
    canonical_rows,
    download_manifest_pdf,
    download_pdf,
    download_gcs_pdf,
    expand_ocr_candidate_pages,
    extract_pdf,
    merge_ocr_rows_by_scope,
    merge_repair_overlay,
    pending_shard_indices,
    read_jsonl_files,
    repair_candidate_filing_ids,
    reduced_run_is_valid,
    select_low_text_pages,
    select_ocr_candidate_pages,
    select_ocr_output_pages,
    select_ocr_primary_pages,
    select_statement_toc_pages,
    supplement_rows_with_balance_text,
    summarize_run,
)
from scripts.build_in_bse_stage02_manifest import (
    deterministic_manifest_order,
    gcs_uri_for_local_path,
)


def test_broad_statement_kinds_routes_ocr_pages():
    assert broad_statement_kinds("Standalone Balance Sheet as at 31 March 2025") == {
        "balance_sheet"
    }
    assert broad_statement_kinds(
        "Statement of Financial Position as at 31 March 2025"
    ) == {"balance_sheet"}
    assert broad_statement_kinds("PROFIT AND LOSS STATEMENT FOR THE YEAR") == {
        "profit_and_loss"
    }
    assert broad_statement_kinds("STATEMENT OF PROFIT OR LOSS FOR THE YEAR") == {
        "profit_and_loss"
    }
    assert broad_statement_kinds(
        "Statoment of Profit and Loss forthe year onded 31 March 2019"
    ) == {"profit_and_loss"}
    assert broad_statement_kinds(
        "AUDITED STATEM ENT OF PRO FIT AND LOSS FOR THE YEAR"
    ) == {"profit_and_loss"}
    assert broad_statement_kinds("%DODQFH 6KHHW DV DW 31 March") == {
        "balance_sheet"
    }
    assert broad_statement_kinds(
        "6WDWHPHQW RI 3URÀW DQG /RVV IRU WKH <HDU"
    ) == {"profit_and_loss"}
    assert broad_statement_kinds('45"/%-0/& #"-"/$& 4)&&5 "4 "5') == {
        "balance_sheet"
    }
    assert broad_statement_kinds(
        '45"/%"-0/& 45"5&.&/5 0\' 130\'*5 "/% -044'
    ) == {"profit_and_loss"}
    assert broad_statement_kinds("ÞßÔßÒÝÛ ÍØÛÛÌ ßÍ ßÌ") == {
        "balance_sheet"
    }
    assert broad_statement_kinds(
        "ÍÌßÌÛÓÛÒÌ ÑÚ ÐÎÑÚ×Ì ßÒÜ ÔÑÍÍ ÚÑÎ ÌØÛ ÇÛßÎ ÛÒÜÛÜ"
    ) == {"profit_and_loss"}
    assert broad_statement_kinds("ordinary narrative about the business") == set()


def test_pending_shard_indices_only_resubmits_incomplete_pairs(tmp_path):
    (tmp_path / "rows-000000.jsonl").write_text("\n")
    (tmp_path / "documents-000000.jsonl").write_text("\n")
    (tmp_path / "rows-000001.jsonl").write_text("\n")
    (tmp_path / "documents-000002.jsonl").write_text("\n")

    assert pending_shard_indices(tmp_path, 4) == [1, 2, 3]


def test_read_jsonl_files_parallelizes_without_changing_file_order(tmp_path):
    paths = []
    for index in range(4):
        path = tmp_path / f"part-{index}.jsonl"
        path.write_text(json.dumps({"index": index}) + "\n")
        paths.append(path)

    assert read_jsonl_files(paths, max_workers=4) == [
        {"index": 0},
        {"index": 1},
        {"index": 2},
        {"index": 3},
    ]


def test_statement_toc_pages_route_reported_statement_pages_and_small_pdf_offset():
    pages = ["cover", "contents", "narrative"] + [""] * 138
    pages[1] = """
    CONTENTS
    Auditors' Report 079
    Balance Sheet 082
    Profit & Loss Account 083
    Cash Flow Statement 084
    Notes to the Financial Statements 089
    """

    assert select_statement_toc_pages("\f".join(pages), page_count=141) == [
        82,
        83,
        84,
        85,
        86,
        87,
    ]


def test_low_text_page_selection_includes_short_and_broken_font_maps():
    short_statement = "S" * 399
    broken_font_map = ("#$% " * 200) + "annual report"
    healthy_text = "annual report financial statement revenue income expenses"
    pages = "\f".join((short_statement, broken_font_map, healthy_text * 20))
    assert select_low_text_pages(pages) == [1, 2]


def test_summary_contract_uses_testable_denominators_and_reports_exclusions():
    docs = [
        {"filing_id": "a", "status": "extracted", "extraction_method": "pdftotext"},
        {"filing_id": "b", "status": "extracted", "extraction_method": "ocr_300dpi_psm3"},
    ]
    rows = [
        {
            "filing_id": "a",
            "industry_template": "industrial",
            "operating_profit_closure_status": "pass",
            "tax_identity_status": "pass",
            "period_end": "2025-03-31",
            "currency": "INR",
            "unit_scale": 100_000,
            "total_borrowings": 10.0,
            "screen_eligible": True,
        },
        {
            "filing_id": "b",
            "industry_template": "financial_services",
            "operating_profit_closure_status": "not_applicable",
            "tax_identity_status": "pass",
            "period_end": "2025-03-31",
            "currency": "INR",
            "unit_scale": None,
            "total_borrowings": None,
            "screen_eligible": False,
        },
    ]
    got = summarize_run(docs, rows)
    assert got["document_extraction_rate"] == 1.0
    assert got["ocr_document_count"] == 1
    assert got["operating_profit_testable_rows"] == 1
    assert got["operating_profit_closure_rate"] == 1.0
    assert got["tax_identity_rate"] == 1.0
    assert got["industrial_debt_resolution_rate"] == 1.0
    assert got["period_end_plausibility_rate"] == 1.0
    assert got["unresolved_unit_rows"] == 1
    assert got["normalization_contract_pass"] is True


def test_summary_contract_counts_invalid_non_month_end_and_future_periods():
    docs = [
        {"filing_id": "a", "status": "extracted"},
        {"filing_id": "b", "status": "extracted"},
    ]
    base = {
        "industry_template": "industrial",
        "operating_profit_closure_status": "pass",
        "tax_identity_status": "pass",
        "currency": "INR",
        "unit_scale": 1,
        "total_borrowings": 1.0,
        "screen_eligible": True,
    }
    got = summarize_run(docs, [
        {**base, "filing_id": "a", "period_end": "2025-03-30"},
        {**base, "filing_id": "b", "period_end": "2075-03-31"},
    ])

    assert got["period_end_resolution_rate"] == 1.0
    assert got["period_end_plausibility_rate"] == 0.0
    assert got["normalization_contract_pass"] is False


def test_repair_candidates_cover_document_and_row_level_contract_failures():
    documents = [
        {"filing_id": "good", "status": "extracted"},
        {"filing_id": "unresolved", "status": "unresolved"},
        {"filing_id": "error", "status": "error"},
    ]
    rows = [
        {
            "filing_id": "good",
            "industry_template": "industrial",
            "operating_profit_closure_status": "fail",
            "tax_identity_status": "pass",
            "total_borrowings": 10.0,
        },
        {
            "filing_id": "tax",
            "industry_template": "industrial",
            "operating_profit_closure_status": "pass",
            "tax_identity_status": "fail",
            "total_borrowings": 10.0,
        },
        {
            "filing_id": "debt",
            "industry_template": "industrial",
            "operating_profit_closure_status": "pass",
            "tax_identity_status": "pass",
            "total_borrowings": None,
        },
        {
            "filing_id": "clean",
            "industry_template": "industrial",
            "operating_profit_closure_status": "pass",
            "tax_identity_status": "pass",
            "total_borrowings": 10.0,
        },
    ]

    assert repair_candidate_filing_ids(documents, rows) == {
        "good",
        "unresolved",
        "error",
        "tax",
        "debt",
    }


def test_repair_overlay_is_scope_local_and_never_regresses_contract_dimensions():
    baseline_documents = [
        {"filing_id": "a", "status": "extracted", "row_count": 2},
        {"filing_id": "b", "status": "unresolved", "row_count": 0},
    ]
    baseline_rows = [
        {
            "filing_id": "a",
            "statement_scope": "standalone",
            "industry_template": "industrial",
            "operating_profit_closure_status": "pass",
            "tax_identity_status": "pass",
            "total_borrowings": 10.0,
            "period_end": "2020-03-31",
            "currency": "INR",
            "unit_scale": 1,
            "screen_eligible": True,
            "profit_before_tax": 100.0,
            "parser_version": "baseline",
        },
        {
            "filing_id": "a",
            "statement_scope": "consolidated",
            "industry_template": "industrial",
            "operating_profit_closure_status": "pass",
            "tax_identity_status": "fail",
            "total_borrowings": None,
            "period_end": "2020-03-31",
            "currency": "INR",
            "unit_scale": 1,
            "screen_eligible": False,
            "profit_before_tax": 200.0,
            "parser_version": "baseline",
        },
    ]
    repair_documents = [
        {"filing_id": "a", "status": "extracted", "row_count": 2},
        {"filing_id": "b", "status": "extracted", "row_count": 1},
    ]
    repair_rows = [
        {
            "filing_id": "a",
            "statement_scope": "standalone",
            "industry_template": "industrial",
            "operating_profit_closure_status": "fail",
            "tax_identity_status": "pass",
            "total_borrowings": 10.0,
            "period_end": "2020-03-31",
            "currency": "INR",
            "unit_scale": 1,
            "screen_eligible": False,
            "profit_before_tax": 101.0,
            "parser_version": "repair",
        },
        {
            "filing_id": "a",
            "statement_scope": "consolidated",
            "industry_template": "industrial",
            "operating_profit_closure_status": "pass",
            "tax_identity_status": "pass",
            "total_borrowings": 20.0,
            "period_end": "2020-03-31",
            "currency": "INR",
            "unit_scale": 1,
            "screen_eligible": True,
            "profit_before_tax": 200.0,
            "parser_version": "repair",
        },
        {
            "filing_id": "b",
            "statement_scope": "standalone",
            "industry_template": "industrial",
            "operating_profit_closure_status": "insufficient_components",
            "tax_identity_status": "pass",
            "total_borrowings": 0.0,
            "period_end": "2011-03-31",
            "currency": "INR",
            "unit_scale": 1,
            "screen_eligible": False,
            "parser_version": "repair",
        },
    ]

    documents, rows, audit = merge_repair_overlay(
        baseline_documents, baseline_rows, repair_documents, repair_rows
    )
    by_key = {(row["filing_id"], row["statement_scope"]): row for row in rows}

    assert by_key[("a", "standalone")]["parser_version"] == "baseline"
    assert by_key[("a", "consolidated")]["parser_version"] == "repair"
    assert by_key[("b", "standalone")]["parser_version"] == "repair"
    assert {document["filing_id"]: document["row_count"] for document in documents} == {
        "a": 2,
        "b": 1,
    }
    assert audit["added_scope_rows"] == 1
    assert audit["replaced_scope_rows"] == 1
    assert audit["rejected_repair_scope_rows"] == 1


def test_ocr_primary_page_selection_rejects_auditor_prose_mentions():
    audit_page = """
    We audited the financial statements, which comprise the Balance Sheet and
    Statement of Profit and Loss. In our opinion they give a true and fair view.
    """
    balance_page = """
    Balance Sheet as at 31 March 2025
    Total Assets 42.39 46.56
    Total Equity (165.74) (147.95)
    Financial liabilities
    Borrowing 106.58 100.00
    """
    profit_page = """
    Statement of Profit and Loss for the period ended 31 March 2025
    Revenue from operations - -
    Total Income 0.07 0.51
    Finance costs 8.20 7.84
    Total expenses 18.82 19.31
    Profit/(Loss) before tax (18.75) (18.80)
    """
    assert select_ocr_primary_pages(
        {3: audit_page, 67: balance_page, 68: profit_page}
    ) == [67, 68]
    assert select_ocr_candidate_pages(
        {3: audit_page, 67: balance_page, 68: profit_page}
    ) == [67, 68]


def test_ocr_primary_page_selection_rejects_balance_sheet_abstracts():
    abstract_page = """
    The audited statements include the Profit and Loss Account and Balance Sheet.
    Balance Sheet Abstract and Company's General Business Profile
    Total Assets 20,118.52
    Total Equity 13,995.73
    Secured Borrowings 4,780.71
    Current Assets 5,213.61
    Total Income 16,198.09
    Total Expenses 14,887.16
    Profit Before Tax 1,311.70
    """
    assert select_ocr_primary_pages({33: abstract_page}) == []
    assert select_ocr_candidate_pages({33: abstract_page}) == []


def test_ocr_output_selection_keeps_adjacent_pages_until_both_statements_are_covered():
    balance_page = """
    Balance Sheet as at 31 March 2025
    Total Assets 42.39 46.56
    Total Equity (165.74) (147.95)
    Borrowings 106.58 100.00
    Current Assets 12.00 11.00
    """
    profit_page = """
    Statement of Profit and Loss for the year ended 31 March 2025
    Revenue from operations 100.00 90.00
    Total Income 102.00 92.00
    Finance costs 8.20 7.84
    Total expenses 80.00 70.00
    Profit before tax 22.00 22.00
    """
    pages = {51: balance_page, 52: profit_page}
    assert select_ocr_output_pages(pages, [51], [50, 51, 52]) == [50, 51, 52]
    assert select_ocr_output_pages(pages, [51, 52], [50, 51, 52]) == [51, 52]


def test_ocr_output_selection_keeps_pages_after_primary_statement_titles():
    pages = {
        51: """
        Consolidated Balance Sheet as at 31 March 2022
        Total Assets 6,648.11 6,212.56
        Total Equity 3,277.93 3,308.45
        Current Assets 4,920.59 4,342.38
        """,
        52: """
        Equity and Liabilities continued
        Non-current borrowings 50.73 128.45
        Current borrowings 878.93 962.51
        """,
        53: """
        Consolidated Statement of Profit and Loss for the year ended 31 March 2022
        Revenue from operations 10,000.00 9,000.00
        Total income 10,100.00 9,090.00
        Total expenses 9,000.00 8,000.00
        Profit before tax 1,100.00 1,090.00
        """,
        54: "Other comprehensive income and signatures",
    }

    assert select_ocr_output_pages(pages, [51, 53], [50, 51, 52, 53, 54]) == [
        51,
        52,
        53,
        54,
    ]


def test_ocr_candidate_expansion_includes_two_adjacent_pages():
    assert expand_ocr_candidate_pages({1, 121}, page_count=123) == [1, 2, 3, 119, 120, 121, 122, 123]


def test_manifest_download_falls_back_to_source_url_when_gcs_pdf_is_corrupt(
    monkeypatch, tmp_path
):
    calls = []

    def fail_gcs(client, gcs_uri, destination, *, expected_bytes=None):
        raise RuntimeError("corrupt staged object")

    def succeed_http(urls, destination):
        calls.append(list(urls))
        return {
            "download_url": urls[0],
            "download_attempts": 1,
            "pdf_bytes": 2048,
            "pdf_sha256": "abc",
        }

    monkeypatch.setattr("in_bse_stage02_job.download_gcs_pdf", fail_gcs)
    monkeypatch.setattr("in_bse_stage02_job.download_pdf", succeed_http)

    result = download_manifest_pdf(
        {
            "gcs_uri": "gs://bucket/corrupt.pdf",
            "expected_pdf_bytes": 2048,
            "candidate_urls": ["https://www.bseindia.com/report.pdf"],
        },
        tmp_path / "document.pdf",
        gcs_client=object(),
    )

    assert calls == [["https://www.bseindia.com/report.pdf"]]
    assert result["source_transport"] == "bse_http_fallback"
    assert result["gcs_fallback_error"] == "RuntimeError: corrupt staged object"


def test_manifest_download_tries_quarter_end_filename_variants_for_dead_legacy_url(
    monkeypatch, tmp_path
):
    calls = []

    monkeypatch.setattr(
        "in_bse_stage02_job.download_gcs_pdf",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError("corrupt staged object")
        ),
    )

    def succeed_http(urls, destination):
        calls.append(list(urls))
        return {
            "download_url": urls[1],
            "download_attempts": 1,
            "pdf_bytes": 442737,
            "pdf_sha256": "abc",
        }

    monkeypatch.setattr("in_bse_stage02_job.download_pdf", succeed_http)
    result = download_manifest_pdf(
        {
            "gcs_uri": "gs://bucket/corrupt.pdf",
            "expected_pdf_bytes": 8351,
            "candidate_urls": [
                "https://www.bseindia.com/bseplus/AnnualReport/509563/5095630611.pdf"
            ],
        },
        tmp_path / "document.pdf",
        gcs_client=object(),
    )

    assert calls == [[
        "https://www.bseindia.com/bseplus/AnnualReport/509563/5095630611.pdf",
        "https://www.bseindia.com/bseplus/AnnualReport/509563/5095630311.pdf",
        "https://www.bseindia.com/bseplus/AnnualReport/509563/5095630911.pdf",
        "https://www.bseindia.com/bseplus/AnnualReport/509563/5095631211.pdf",
    ]]
    assert result["download_url"].endswith("5095630311.pdf")


def test_ocr_fallback_retries_selected_pages_with_psm6_after_psm3_parse_failure(
    monkeypatch, tmp_path
):
    calls = []
    balance = """
    Balance Sheet as at 31 March 2025
    Total Assets 42.39 46.56
    Total Equity (165.74) (147.95)
    Borrowings 106.58 100.00
    Current Assets 12.00 11.00
    """
    profit = """
    Statement of Profit and Loss for the year ended 31 March 2025
    Revenue from operations 100.00 90.00
    Total Income 102.00 92.00
    Finance costs 8.20 7.84
    Total expenses 80.00 70.00
    Profit before tax 22.00 22.00
    """

    def fake_ocr_pages(pdf_path, page_numbers, *, workdir, dpi, psm, image_format):
        requested = list(page_numbers)
        calls.append((dpi, psm, requested))
        if dpi == 140:
            return {1: balance, 2: profit}
        prefix = "PSM6" if psm == 6 else "PSM3"
        return {1: f"{prefix}\n{balance}", 2: f"{prefix}\n{profit}"}

    def fake_extract_document(text, *, company_name=None):
        return [{"statement_scope": "standalone"}] if "PSM6" in text else []

    monkeypatch.setattr("in_bse_stage02_job._ocr_pages", fake_ocr_pages)
    monkeypatch.setattr("in_bse_stage02_job.extract_document", fake_extract_document)
    rows, metadata = _ocr_fallback(
        tmp_path / "document.pdf",
        "\f",
        company_name=None,
        workdir=tmp_path,
        include_debug_text=True,
    )
    assert rows == [{"statement_scope": "standalone"}]
    assert (300, 6, [1, 2]) in calls
    assert metadata["ocr_selected_psm"] == 6
    assert metadata["ocr_selected_text"].startswith("PSM6")
    assert metadata["ocr_psm3_text"].startswith("PSM3")
    assert metadata["ocr_psm6_text"].startswith("PSM6")


def test_ocr_fallback_skips_psm6_when_primary_ocr_exhausts_time_budget(
    monkeypatch, tmp_path
):
    calls = []
    balance = """
    Balance Sheet as at 31 March 2025
    Total Assets 42.39 46.56
    Total Equity (165.74) (147.95)
    Borrowings 106.58 100.00
    Current Assets 12.00 11.00
    """
    profit = """
    Statement of Profit and Loss for the year ended 31 March 2025
    Revenue from operations 100.00 90.00
    Total Income 102.00 92.00
    Finance costs 8.20 7.84
    Total expenses 80.00 70.00
    Profit before tax 22.00 22.00
    """

    def fake_ocr_pages(pdf_path, page_numbers, *, workdir, dpi, psm, image_format):
        requested = list(page_numbers)
        calls.append((dpi, psm, requested))
        if dpi == 140:
            return {1: balance, 2: profit}
        return {1: f"PSM{psm}\n{balance}", 2: f"PSM{psm}\n{profit}"}

    monkeypatch.setattr("in_bse_stage02_job._ocr_pages", fake_ocr_pages)
    monkeypatch.setattr("in_bse_stage02_job.extract_document", lambda *args, **kwargs: [])
    monotonic = iter([100.0, 2801.0])
    monkeypatch.setattr(
        "in_bse_stage02_job.time.monotonic", lambda: next(monotonic)
    )

    rows, metadata = _ocr_fallback(
        tmp_path / "document.pdf",
        "\f",
        company_name=None,
        workdir=tmp_path,
        alternate_ocr_budget_seconds=2700.0,
    )

    assert rows == []
    assert (300, 3, [1, 2]) in calls
    assert not any(dpi == 300 and psm == 6 for dpi, psm, _ in calls)
    assert metadata["ocr_psm6_attempted"] is False
    assert metadata["ocr_psm6_skipped_budget"] is True


def test_ocr_fallback_prefers_psm6_when_psm3_row_is_missing_industrial_debt(
    monkeypatch, tmp_path
):
    calls = []
    balance = """
    Balance Sheet as at 31 March 2025
    Total Assets 42.39 46.56
    Total Equity (165.74) (147.95)
    Borrowings 106.58 100.00
    Current Assets 12.00 11.00
    """
    profit = """
    Statement of Profit and Loss for the year ended 31 March 2025
    Revenue from operations 100.00 90.00
    Total Income 102.00 92.00
    Finance costs 8.20 7.84
    Total expenses 80.00 70.00
    Profit before tax 22.00 22.00
    """

    def fake_ocr_pages(pdf_path, page_numbers, *, workdir, dpi, psm, image_format):
        requested = list(page_numbers)
        calls.append((dpi, psm, requested))
        if dpi == 140:
            return {1: balance, 2: profit}
        prefix = "PSM6" if psm == 6 else "PSM3"
        return {1: f"{prefix}\n{balance}", 2: f"{prefix}\n{profit}"}

    def fake_extract_document(text, *, company_name=None):
        complete = "PSM6" in text
        return [
            {
                "statement_scope": "standalone",
                "industry_template": "industrial",
                "operating_profit_closure_status": "pass",
                "tax_identity_status": "pass",
                "total_borrowings": 106.58 if complete else None,
                "screen_eligible": complete,
            }
        ]

    monkeypatch.setattr("in_bse_stage02_job._ocr_pages", fake_ocr_pages)
    monkeypatch.setattr("in_bse_stage02_job.extract_document", fake_extract_document)
    rows, metadata = _ocr_fallback(
        tmp_path / "document.pdf",
        "\f",
        company_name=None,
        workdir=tmp_path,
        include_debug_text=True,
    )
    assert rows[0]["total_borrowings"] == pytest.approx(106.58)
    assert rows[0]["screen_eligible"] is True
    assert (300, 6, [1, 2]) in calls
    assert metadata["ocr_selected_psm"] == 6
    assert metadata["ocr_selected_text"].startswith("PSM6")


def test_ocr_fallback_supplements_psm3_profit_with_psm6_balance(
    monkeypatch, tmp_path
):
    low_balance = """
    Balance Sheet as at 31 March 2025
    Total Assets 42.39 46.56
    """
    primary_balance = """
    Standalone Balance Sheet as at 31 March 2025
    Rs. in lakhs
    Total Assets 42.39 46.56
    Total Equity 20.00 18.00
    """
    alternate_balance = """
    Standalone Balance Sheet as at 31 March 2025
    Rs. in lakhs
    Total Assets 42.39 46.56
    Total Equity 20.00 18.00
    Non-current liabilities
    Long-term Borrowings 40.00 35.00
    Current liabilities
    Short-term Borrowings 66.58 65.00
    """
    profit = """
    Statement of Standalone Profit and Loss for the year ended 31 March 2025
    Rs. in lakhs
    Revenue from operations 100.00 90.00
    Other income 2.00 2.00
    Total Income 102.00 92.00
    Finance costs 8.20 7.84
    Other expenses 71.80 62.16
    Total expenses 80.00 70.00
    Profit before tax 22.00 22.00
    Current tax 5.00 5.00
    Profit for the year 17.00 17.00
    """

    def fake_ocr_pages(pdf_path, page_numbers, *, workdir, dpi, psm, image_format):
        if dpi == 140:
            return {1: low_balance, 2: profit}
        if psm == 3:
            return {1: primary_balance, 2: profit}
        return {1: alternate_balance, 2: "unreadable alternate profit page"}

    monkeypatch.setattr("in_bse_stage02_job._ocr_pages", fake_ocr_pages)

    rows, metadata = _ocr_fallback(
        tmp_path / "document.pdf",
        "\f",
        company_name=None,
        workdir=tmp_path,
        include_debug_text=True,
    )

    assert rows[0]["total_borrowings"] == pytest.approx(106.58)
    assert rows[0]["balance_supplement_source"] == "ocr_psm6"
    assert metadata["ocr_selected_psm"] == 3


def test_ocr_fallback_supplements_psm3_with_psm6_statement_scale(
    monkeypatch, tmp_path
):
    primary = """
    PSM3
    Statement of Profit and Loss for the year ended 31 March 2017
    Revenue from operations 100 90
    Profit before tax 20 18
    Profit for the year 15 14
    """
    alternate = """
    PSM6
    Statement of Profit and Loss for the year ended 31 March 2017
    (Rs. in lakhs)
    Revenue from operations 100 90
    Profit before tax 20 18
    Profit for the year 15 14
    """

    def fake_ocr_pages(pdf_path, page_numbers, *, workdir, dpi, psm, image_format):
        selected_text = primary if dpi == 140 or psm == 3 else alternate
        return {page: selected_text for page in page_numbers}

    def fake_extract_document(text, *, company_name=None):
        alternate_layout = "PSM6" in text
        return [{
            "statement_scope": "standalone",
            "period_end": "2017-03-31",
            "industry_template": "industrial",
            "currency": "INR",
            "currency_source": (
                "statement_header"
                if alternate_layout
                else "market_jurisdiction_default"
            ),
            "unit_scale": 100_000 if alternate_layout else None,
            "unit_scale_source": (
                "statement_header" if alternate_layout else "unknown"
            ),
            "operating_profit_closure_status": (
                "insufficient_components" if alternate_layout else "pass"
            ),
            "tax_identity_status": "pass",
            "total_borrowings": None,
            "screen_eligible": False,
        }]

    monkeypatch.setattr("in_bse_stage02_job._ocr_pages", fake_ocr_pages)
    monkeypatch.setattr("in_bse_stage02_job.extract_document", fake_extract_document)

    rows, metadata = _ocr_fallback(
        tmp_path / "document.pdf",
        "\f",
        company_name=None,
        workdir=tmp_path,
    )

    assert rows[0]["operating_profit_closure_status"] == "pass"
    assert rows[0]["unit_scale"] == 100_000
    assert rows[0]["unit_scale_source"] == "statement_header"
    assert rows[0]["currency_source"] == "statement_header"
    assert rows[0]["statement_metadata_supplement_source"] == "ocr_psm6"
    assert metadata["ocr_selected_psm"] == 3


def test_ocr_fallback_supplements_pdftotext_profit_with_ocr_balance(
    monkeypatch, tmp_path
):
    balance = """
    Balance Sheet as at 31 March 2024
    Total Assets 65,676.91 15,030.63
    Non-current liabilities
    Long-term Borrowings - -
    Current liabilities
    Short-term Borrowings 50,000.00 -
    """
    broken_profit = """
    Statement of Profit and Loss for the year ended 31 March 2024
    Revenue from operations 815.10 3,016.78
    Other income 33.80 -
    Total income 848.90 3,016.78
    Profit before tax 2,452.62 416.32
    Current tax 0.50 -
    Profit for the period 2,452.12 416.32
    """
    base_rows = [{
        "statement_scope": "standalone",
        "period_end": "2024-03-31",
        "industry_template": "industrial",
        "currency": "INR",
        "currency_source": "statement_header",
        "unit_scale": 100_000,
        "operating_profit_closure_status": "pass",
        "tax_identity_status": "pass",
        "total_borrowings": None,
        "total_borrowings_null_reason": "not_reported",
        "screen_eligible": False,
    }]

    def fake_ocr_pages(pdf_path, page_numbers, *, workdir, dpi, psm, image_format):
        return {1: balance, 2: broken_profit}

    def fake_extract_document(text, *, company_name=None):
        return [{
            "statement_scope": "standalone",
            "period_end": "2024-03-31",
            "industry_template": "industrial",
            "currency": "INR",
            "currency_source": "statement_header",
            "unit_scale": 100_000,
            "operating_profit_closure_status": "insufficient_components",
            "tax_identity_status": "pass",
            "total_borrowings": 50_000.0,
            "debt_reconciliation_status": "components_only",
            "screen_eligible": False,
        }]

    monkeypatch.setattr("in_bse_stage02_job._ocr_pages", fake_ocr_pages)
    monkeypatch.setattr("in_bse_stage02_job.extract_document", fake_extract_document)

    rows, metadata = _ocr_fallback(
        tmp_path / "document.pdf",
        "\f",
        company_name=None,
        workdir=tmp_path,
        base_rows=base_rows,
    )

    assert rows[0]["operating_profit_closure_status"] == "pass"
    assert rows[0]["total_borrowings"] == pytest.approx(50_000)
    assert rows[0]["screen_eligible"] is True
    assert metadata["ocr_supplemented_pdftotext"] is True


def test_ocr_fallback_uses_alternate_balance_for_pdftotext_profit(
    monkeypatch, tmp_path
):
    primary_balance = """
    PSM3
    Balance Sheet as at 31 March 2024
    Rs. in lakhs
    Total Assets 65,676.91 15,030.63
    """
    alternate_balance = """
    PSM6
    Balance Sheet as at 31 March 2024
    Rs. in lakhs
    Total Assets 65,676.91 15,030.63
    Non-current liabilities
    Long-term Borrowings - -
    Current liabilities
    Short-term Borrowings 50,000.00 -
    """
    profit = """
    Statement of Profit and Loss for the year ended 31 March 2024
    Rs. in lakhs
    Revenue from operations 815.10 3,016.78
    Other income 33.80 -
    Total income 848.90 3,016.78
    Total expenses 432.59 2,600.46
    Profit before tax 416.32 416.32
    Current tax - -
    Profit for the period 416.32 416.32
    """
    base_rows = [{
        "statement_scope": "standalone",
        "period_end": "2024-03-31",
        "industry_template": "industrial",
        "currency": "INR",
        "currency_source": "statement_header",
        "unit_scale": 100_000,
        "operating_profit_closure_status": "pass",
        "tax_identity_status": "pass",
        "total_borrowings": None,
        "total_borrowings_null_reason": "not_reported",
        "screen_eligible": False,
    }]

    def fake_ocr_pages(pdf_path, page_numbers, *, workdir, dpi, psm, image_format):
        if dpi == 140:
            return {1: primary_balance, 2: profit}
        if psm == 3:
            return {1: primary_balance, 2: profit}
        return {1: alternate_balance, 2: "unreadable alternate profit page"}

    def fake_extract_document(text, *, company_name=None):
        has_alternate_debt = "PSM6" in text
        return [{
            "statement_scope": "standalone",
            "period_end": "2024-03-31",
            "industry_template": "industrial",
            "currency": "INR",
            "currency_source": "statement_header",
            "unit_scale": 100_000,
            "operating_profit_closure_status": "insufficient_components",
            "tax_identity_status": "pass",
            "total_borrowings": 50_000.0 if has_alternate_debt else None,
            "debt_reconciliation_status": (
                "components_only" if has_alternate_debt else None
            ),
            "screen_eligible": False,
        }]

    monkeypatch.setattr("in_bse_stage02_job._ocr_pages", fake_ocr_pages)
    monkeypatch.setattr("in_bse_stage02_job.extract_document", fake_extract_document)

    rows, metadata = _ocr_fallback(
        tmp_path / "document.pdf",
        "\f",
        company_name=None,
        workdir=tmp_path,
        base_rows=base_rows,
    )

    assert rows[0]["operating_profit_closure_status"] == "pass"
    assert rows[0]["total_borrowings"] == pytest.approx(50_000)
    assert rows[0]["balance_supplement_source"] == "ocr_psm6"
    assert rows[0]["screen_eligible"] is True
    assert metadata["ocr_supplemented_pdftotext"] is True


def test_ocr_quality_prefers_more_individually_closed_screening_gates():
    common = {
        "statement_scope": "standalone",
        "industry_template": "industrial",
        "tax_identity_status": "fail",
        "total_borrowings": 1_806.8,
        "screen_eligible": False,
    }
    psm3 = [{**common, "operating_profit_closure_status": "insufficient_components"}]
    psm6 = [{**common, "operating_profit_closure_status": "pass"}]

    assert _ocr_row_set_quality(psm6) > _ocr_row_set_quality(psm3)


def test_ocr_rows_merge_per_scope_without_trading_one_scope_for_another():
    common = {
        "period_end": "2025-03-31",
        "industry_template": "industrial",
        "currency": "INR",
        "unit_scale": 10_000_000,
        "screen_eligible": False,
    }
    psm3 = [
        {
            **common,
            "statement_scope": "standalone",
            "operating_profit_closure_status": "pass",
            "tax_identity_status": "fail",
            "total_borrowings": None,
        },
        {
            **common,
            "statement_scope": "consolidated",
            "operating_profit_closure_status": "pass",
            "tax_identity_status": "pass",
            "total_borrowings": None,
        },
    ]
    psm6 = [
        {
            **common,
            "statement_scope": "standalone",
            "operating_profit_closure_status": "insufficient_components",
            "tax_identity_status": "insufficient_components",
            "total_borrowings": 0.0,
        },
        {
            **common,
            "statement_scope": "consolidated",
            "operating_profit_closure_status": "insufficient_components",
            "tax_identity_status": "insufficient_components",
            "total_borrowings": None,
        },
    ]

    merged = merge_ocr_rows_by_scope(psm3, psm6)
    by_scope = {row["statement_scope"]: row for row in merged}

    assert by_scope["standalone"]["total_borrowings"] == 0.0
    assert by_scope["standalone"]["tax_identity_status"] == (
        "insufficient_components"
    )
    assert by_scope["consolidated"]["operating_profit_closure_status"] == "pass"
    assert by_scope["consolidated"]["tax_identity_status"] == "pass"


def test_alternate_ocr_balance_supplements_missing_debt_for_one_matching_scope():
    primary = [{
        "statement_scope": "standalone",
        "period_end": "2013-03-31",
        "industry_template": "industrial",
        "currency": "INR",
        "currency_source": "statement_header",
        "unit_scale": 1,
        "operating_profit_closure_status": "pass",
        "tax_identity_status": "pass",
        "total_borrowings": None,
        "total_borrowings_null_reason": "not_reported",
        "screen_eligible": False,
    }]
    alternate = """
    Balance Sheet as at 31st March 2013
    Total Assets 80,448,779.00 48,059,174.18
    Non-Current Liabilities
    Long Term Borrowings 3 1,460,271.53 2,223,346.53
    Current Liabilities
    Short-Term Borrowing 5 31,922,223.05 33,066,594.00
    """

    rows = supplement_rows_with_balance_text(primary, alternate, source="ocr_psm6")

    assert rows[0]["total_borrowings"] == pytest.approx(33_382_494.58)
    assert rows[0]["total_borrowings_null_reason"] is None
    assert rows[0]["balance_supplement_source"] == "ocr_psm6"
    assert rows[0]["screen_eligible"] is True


def test_alternate_ocr_does_not_promote_partial_dash_to_true_zero_debt():
    primary = [{
        "statement_scope": "standalone",
        "period_end": "2015-03-31",
        "industry_template": "industrial",
        "total_borrowings": None,
        "total_borrowings_null_reason": "not_reported",
        "screen_eligible": False,
    }]
    alternate = """
    Balance Sheet as at 31 March 2015
    Non-current liabilities
    Long-term borrowings : .
    Current liabilities
    Short-term borrowings - -
    """

    rows = supplement_rows_with_balance_text(primary, alternate, source="ocr_psm6")

    assert rows[0]["total_borrowings"] is None
    assert rows[0]["total_borrowings_null_reason"] == "not_reported"
    assert "balance_supplement_source" not in rows[0]


def test_preferred_rows_fall_back_to_eligible_standalone():
    rows = [
        {
            "ticker": "500325",
            "period_end": "2025-03-31",
            "statement_scope": "standalone",
            "publication_date": "2025-08-07",
            "filing_id": "standalone",
            "screen_eligible": True,
        },
        {
            "ticker": "500325",
            "period_end": "2025-03-31",
            "statement_scope": "consolidated",
            "publication_date": "2025-08-07",
            "filing_id": "consolidated",
            "screen_eligible": False,
        },
    ]
    scoped, preferred = canonical_rows(rows)
    assert len(scoped) == 2
    assert [row["filing_id"] for row in preferred] == ["standalone"]
    assert preferred[0]["scope_preference"] == (
        "standalone_fallback_due_to_ineligible_consolidated"
    )


def test_reduced_run_requires_at_least_one_eligible_preferred_row():
    qa = {"normalization_contract_pass": True}
    assert reduced_run_is_valid(qa, []) is False
    assert reduced_run_is_valid(qa, [{"screen_eligible": True}]) is True


def test_manifest_hash_order_breaks_company_history_clusters_reproducibly():
    records = [
        {"filing_id": "ticker-a-2010"},
        {"filing_id": "ticker-a-2011"},
        {"filing_id": "ticker-b-2010"},
        {"filing_id": "ticker-c-2010"},
    ]
    got = deterministic_manifest_order(records)
    assert [row["filing_id"] for row in got] == [
        "ticker-a-2011",
        "ticker-b-2010",
        "ticker-c-2010",
        "ticker-a-2010",
    ]
    assert deterministic_manifest_order(records) == got
    assert [row["filing_id"] for row in records] == [
        "ticker-a-2010",
        "ticker-a-2011",
        "ticker-b-2010",
        "ticker-c-2010",
    ]


def test_manifest_maps_local_pdf_to_deterministic_gcs_object():
    source_root = "/raw/IN_BSE_ANNUAL_REPORTS"
    assert gcs_uri_for_local_path(
        "/raw/IN_BSE_ANNUAL_REPORTS/500493/2017/5004930317.pdf",
        source_root=source_root,
        gcs_prefix="gs://markets-transfer/in-bse-stage02-v16/raw/",
    ) == "gs://markets-transfer/in-bse-stage02-v16/raw/500493/2017/5004930317.pdf"


def test_batch_ocr_renders_contiguous_ranges_once(monkeypatch, tmp_path):
    calls = []

    def fake_run(command, *, cwd, timeout=900):
        calls.append(command)
        if command[0] == "pdftoppm":
            first = int(command[command.index("-f") + 1])
            last = int(command[command.index("-l") + 1])
            stem = command[-1]
            for page in range(first, last + 1):
                (cwd / f"{stem}-{page}.jpg").write_bytes(b"jpeg")
            return subprocess.CompletedProcess(command, 0, "", "")
        return subprocess.CompletedProcess(command, 0, f"text:{command[1]}", "")

    monkeypatch.setattr("in_bse_stage02_job._run", fake_run)
    got = _ocr_pages(
        tmp_path / "document.pdf",
        [2, 3, 7],
        workdir=tmp_path,
        dpi=140,
        psm=6,
        image_format="jpeg",
    )
    assert _contiguous_page_ranges([7, 3, 2, 3]) == [(2, 3), (7, 7)]
    assert len([call for call in calls if call[0] == "pdftoppm"]) == 2
    assert len([call for call in calls if call[0] == "tesseract"]) == 3
    assert set(got) == {2, 3, 7}


def test_gcs_download_validates_pdf_size_and_records_provenance(tmp_path):
    payload = b"%PDF" + b"x" * 2048

    class FakeBlob:
        def download_to_filename(self, destination):
            destination.write_bytes(payload)

    class FakeBucket:
        def blob(self, object_name):
            assert object_name == "raw/500493/2017/5004930317.pdf"
            return FakeBlob()

    class FakeClient:
        def bucket(self, bucket_name):
            assert bucket_name == "markets-transfer"
            return FakeBucket()

    destination = tmp_path / "document.pdf"
    got = download_gcs_pdf(
        FakeClient(),
        "gs://markets-transfer/raw/500493/2017/5004930317.pdf",
        destination,
        expected_bytes=len(payload),
    )
    assert got["download_url"].startswith("gs://markets-transfer/")
    assert got["download_attempts"] == 1
    assert got["source_transport"] == "gcs"
    assert got["pdf_bytes"] == len(payload)

    with pytest.raises(RuntimeError, match="size mismatch"):
        download_gcs_pdf(
            FakeClient(),
            "gs://markets-transfer/raw/500493/2017/5004930317.pdf",
            destination,
            expected_bytes=len(payload) + 1,
        )


def test_high_resolution_png_ocr_renders_grayscale(monkeypatch, tmp_path):
    calls = []

    def fake_run(command, *, cwd, timeout=900):
        calls.append(command)
        if command[0] == "pdftoppm":
            stem = command[-1]
            (cwd / f"{stem}-1.png").write_bytes(b"png")
        return subprocess.CompletedProcess(command, 0, "statement text", "")

    monkeypatch.setattr("in_bse_stage02_job._run", fake_run)
    _ocr_pages(
        tmp_path / "document.pdf",
        [1],
        workdir=tmp_path,
        dpi=300,
        psm=3,
        image_format="png",
    )
    render = next(call for call in calls if call[0] == "pdftoppm")
    assert "-gray" in render


def test_extract_pdf_debug_mode_preserves_text_for_remote_diagnostics(
    monkeypatch, tmp_path
):
    pdf_path = tmp_path / "document.pdf"
    pdf_path.write_bytes(b"%PDF-test")

    def fake_run(command, *, cwd, timeout=900):
        (cwd / "document.txt").write_text("text layer without a primary statement")
        return subprocess.CompletedProcess(command, 0, "", "")

    def fake_ocr(pdf_path, pdftotext_text, **kwargs):
        assert kwargs["include_debug_text"] is True
        return [], {"ocr_selected_text": "selected OCR statement text"}

    monkeypatch.setattr("in_bse_stage02_job._run", fake_run)
    monkeypatch.setattr("in_bse_stage02_job._ocr_fallback", fake_ocr)
    rows, metadata = extract_pdf(
        pdf_path, workdir=tmp_path, include_debug_text=True
    )
    assert rows == []
    assert metadata["pdftotext_text"] == "text layer without a primary statement"
    assert metadata["ocr_selected_text"] == "selected OCR statement text"


def test_extract_pdf_uses_better_ocr_when_text_layer_only_yields_partial_rows(
    monkeypatch, tmp_path
):
    pdf_path = tmp_path / "document.pdf"
    pdf_path.write_bytes(b"%PDF-test")
    partial = {
        "industry_template": "industrial",
        "operating_profit_closure_status": "insufficient_components",
        "tax_identity_status": "fail",
        "total_borrowings": 10.0,
        "period_end": "2020-03-31",
        "currency": "INR",
        "unit_scale": 100_000,
        "statement_scope": "standalone",
        "screen_eligible": False,
    }
    recovered = {
        **partial,
        "operating_profit_closure_status": "pass",
        "tax_identity_status": "pass",
        "screen_eligible": True,
    }

    def fake_run(command, *, cwd, timeout=900):
        (cwd / "document.txt").write_text("partially decoded statement")
        return subprocess.CompletedProcess(command, 0, "", "")

    def fake_extract(text, *, company_name=None):
        assert text == "partially decoded statement"
        return [partial]

    def fake_ocr(pdf_path, pdftotext_text, **kwargs):
        return [recovered], {"ocr_selected_psm": 3}

    monkeypatch.setattr("in_bse_stage02_job._run", fake_run)
    monkeypatch.setattr("in_bse_stage02_job.extract_document", fake_extract)
    monkeypatch.setattr("in_bse_stage02_job._ocr_fallback", fake_ocr)

    rows, metadata = extract_pdf(pdf_path, workdir=tmp_path)

    assert rows == [recovered]
    assert metadata["extraction_method"] == "ocr_300dpi_psm3"


def test_extract_pdf_keeps_partial_text_when_ocr_is_worse(monkeypatch, tmp_path):
    pdf_path = tmp_path / "document.pdf"
    pdf_path.write_bytes(b"%PDF-test")
    partial = {
        "industry_template": "industrial",
        "operating_profit_closure_status": "pass",
        "tax_identity_status": "fail",
        "total_borrowings": 10.0,
        "period_end": "2020-03-31",
        "currency": "INR",
        "unit_scale": 100_000,
        "statement_scope": "standalone",
        "screen_eligible": False,
    }
    worse_ocr = {
        **partial,
        "operating_profit_closure_status": "insufficient_components",
        "total_borrowings": None,
        "unit_scale": None,
    }

    def fake_run(command, *, cwd, timeout=900):
        (cwd / "document.txt").write_text("partially decoded statement")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("in_bse_stage02_job._run", fake_run)
    monkeypatch.setattr(
        "in_bse_stage02_job.extract_document",
        lambda text, *, company_name=None: [partial],
    )
    monkeypatch.setattr(
        "in_bse_stage02_job._ocr_fallback",
        lambda *args, **kwargs: ([worse_ocr], {"ocr_selected_psm": 6}),
    )

    rows, metadata = extract_pdf(pdf_path, workdir=tmp_path)

    assert rows == [partial]
    assert metadata["extraction_method"] == "pdftotext"
    assert metadata["ocr_replaced_pdftotext"] is False


def test_download_pdf_retries_transient_http_errors(monkeypatch, tmp_path):
    calls = []
    sleeps = []
    content = b"%PDF" + b"x" * 2048

    class FakeResponse:
        def __init__(self, fail):
            self.fail = fail

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def raise_for_status(self):
            if self.fail:
                raise requests.HTTPError("403 transient")

        def iter_content(self, chunk_size):
            yield content

    def fake_get(url, **kwargs):
        calls.append(url)
        return FakeResponse(fail=len(calls) == 1)

    monkeypatch.setattr(requests, "get", fake_get)
    monkeypatch.setattr("in_bse_stage02_job.time.sleep", sleeps.append)
    result = download_pdf(
        ["https://example.test/report.pdf"],
        tmp_path / "document.pdf",
        max_attempts=3,
        initial_backoff_seconds=2.0,
    )
    assert calls == [
        "https://example.test/report.pdf",
        "https://example.test/report.pdf",
    ]
    assert sleeps == [2.0]
    assert result["pdf_bytes"] == len(content)
