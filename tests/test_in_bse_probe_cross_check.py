"""Numerical acceptance checks for blind India BSE repair probes."""

from scripts.in_bse_probe_cross_check import cross_check_probe


def _row(**changes):
    row = {
        "filing_id": "filing-1",
        "statement_scope": "standalone",
        "industry_template": "industrial",
        "screen_eligible": False,
        "operating_profit_closure_status": "pass",
        "tax_identity_status": "pass",
        "total_borrowings": None,
        "period_end": "2025-03-31",
        "currency": "INR",
        "unit_scale": 1,
    }
    row.update(changes)
    return row


def test_cross_check_accepts_non_regressing_component_reconciled_debt():
    baseline = [_row()]
    payloads = [{
        "record": {"filing_id": "filing-1"},
        "rows": [_row(
            total_assets=1_000.0,
            current_borrowings=40.0,
            non_current_borrowings=60.0,
            total_borrowings=100.0,
            debt_reconciliation_status="components_only",
        )],
        "metadata": {"extraction_method": "pdftotext"},
    }]

    report = cross_check_probe(
        baseline,
        [{"filing_id": "filing-1"}],
        payloads,
        expected_documents=1,
    )

    assert report["accepted"] is True
    assert report["improvements"] == 1
    assert report["regressions"] == []
    assert report["numerical_failures"] == []


def test_cross_check_rejects_gate_regression_even_when_debt_improves():
    baseline = [_row()]
    payloads = [{
        "record": {"filing_id": "filing-1"},
        "rows": [_row(total_borrowings=100.0, tax_identity_status="fail")],
        "metadata": {"extraction_method": "pdftotext"},
    }]

    report = cross_check_probe(
        baseline,
        [{"filing_id": "filing-1"}],
        payloads,
        expected_documents=1,
    )

    assert report["accepted"] is False
    assert len(report["regressions"]) == 1


def test_cross_check_rejects_borrowing_component_identity_failure():
    baseline = [_row()]
    payloads = [{
        "record": {"filing_id": "filing-1"},
        "rows": [_row(
            current_borrowings=40.0,
            non_current_borrowings=60.0,
            total_borrowings=1_000.0,
            debt_reconciliation_status="components_only",
        )],
        "metadata": {"extraction_method": "pdftotext"},
    }]

    report = cross_check_probe(
        baseline,
        [{"filing_id": "filing-1"}],
        payloads,
        expected_documents=1,
    )

    assert report["accepted"] is False
    assert report["numerical_failures"][0]["check"] == "borrowing_components"
