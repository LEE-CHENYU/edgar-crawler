"""Operational contract tests for the DART Stage-02 V2 job."""

import csv
import json

from dart_stage02_v2_job import (
    build_targets,
    committed,
    process_target,
    summarize_run,
    write_target_output,
)


def _metadata_row(filing_id, filing_date, report_name):
    raw = {
        "corp_code": "00126380",
        "corp_name": "삼성전자",
        "stock_code": "005930",
        "report_nm": report_name,
        "rcept_no": filing_id,
        "rcept_dt": filing_date.replace("-", ""),
    }
    return {
        "market": "DART",
        "filing_id": filing_id,
        "filing_date": filing_date,
        "company_name": "삼성전자",
        "stock_code": "005930",
        "title": report_name,
        "local_path": "/raw/report.zip",
        "raw_metadata": json.dumps(raw, ensure_ascii=False),
    }


def test_build_targets_selects_latest_filing_for_company_period(tmp_path):
    path = tmp_path / "metadata.csv"
    rows = [
        _metadata_row("20260301000001", "2026-03-01", "사업보고서 (2025.12)"),
        _metadata_row("20260315000002", "2026-03-15", "[첨부추가]사업보고서 (2025.12)"),
        _metadata_row("20260320000003", "2026-03-20", "사업보고서제출기한연장신고서"),
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=rows[0])
        writer.writeheader()
        writer.writerows(rows)

    targets = build_targets(path, require_local=False)

    assert len(targets) == 1
    assert targets[0]["filing_id"] == "20260315000002"
    assert targets[0]["period_end"] == "2025-12-31"
    assert targets[0]["corp_code"] == "00126380"


def test_summarize_run_uses_testable_gate_denominators():
    documents = [
        {"filing_id": "a", "status": "extracted"},
        {"filing_id": "b", "status": "no_structured_data"},
    ]
    rows = [
        {
            "operating_profit_closure_status": "pass",
            "tax_identity_status": "pass",
            "balance_identity_status": "pass",
            "industry_template": "industrial",
            "debt_reconciliation_status": "resolved_components",
            "period_end": "2025-12-31",
            "currency": "KRW",
            "screen_eligible": True,
        },
        {
            "operating_profit_closure_status": "insufficient_components",
            "tax_identity_status": "fail",
            "balance_identity_status": "pass",
            "industry_template": "financial_services",
            "debt_reconciliation_status": "unresolved_not_reported",
            "period_end": "2025-12-31",
            "currency": "KRW",
            "screen_eligible": False,
        },
    ]

    got = summarize_run(documents, rows, threshold=0.5)

    assert got["document_extraction_rate"] == 0.5
    assert got["operating_profit_closure_rate"] == 1.0
    assert got["tax_identity_rate"] == 0.5
    assert got["balance_identity_rate"] == 1.0
    assert got["industrial_debt_resolution_rate"] == 1.0
    assert got["normalization_contract_pass"] is True


def test_target_commit_requires_document_rows_and_raw_account_facts(tmp_path):
    document = {"filing_id": "20260315000002", "status": "extracted"}
    rows = [{"filing_id": "20260315000002", "statement_scope": "standalone"}]
    facts = {
        "OFS": [{"account_id": "ifrs-full_Assets", "thstrm_amount": "100"}],
    }

    write_target_output(tmp_path, document, rows, facts)

    assert committed(tmp_path, document["filing_id"]) is True
    assert (tmp_path / "accounts" / "20260315000002.json").exists()
    (tmp_path / "accounts" / "20260315000002.json").unlink()
    assert committed(tmp_path, document["filing_id"]) is False


def test_rate_limited_output_is_never_considered_committed(tmp_path):
    document = {"filing_id": "limited", "status": "rate_limited"}
    write_target_output(tmp_path, document, [], {})

    assert committed(tmp_path, "limited") is False


def test_process_target_surfaces_daily_api_limit_without_trying_other_scope(monkeypatch):
    calls = []

    def fake_fetch(*args, **kwargs):
        calls.append(kwargs["fs_div"])
        return [], "020"

    monkeypatch.setattr("dart_stage02_v2_job.fetch_accounts", fake_fetch)
    target = {
        "filing_id": "limited", "filing_date": "2026-03-15",
        "corp_code": "00126380", "stock_code": "005930",
        "company_name": "삼성전자", "report_nm": "사업보고서 (2025.12)",
        "period_end": "2025-12-31",
    }

    document, rows, accounts = process_target(target, "secret")

    assert document["status"] == "rate_limited"
    assert calls == ["CFS"]
    assert rows == []
    assert accounts == {"CFS": []}
