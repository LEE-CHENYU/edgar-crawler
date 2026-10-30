"""PSE EDGE source identity, format routing, and HTML summary contracts."""

import csv
import json

from pse_stage02_v2 import (
    build_targets,
    classify_payload,
    extract_html_summary,
    extract_pdf_statement_text,
    finalize_pse_rows,
    normalize_currency_unit,
)


TARGET = {
    "filing_id": "edge-1",
    "filing_date": "2025-04-15",
    "company_id": "104",
    "company_name": "Example Philippines Corp",
    "title": "[Amend-1]Annual Report",
}


def _html():
    return b"""<!doctype html><html><body>
    <table><tr><th>For the fiscal year ended</th><td>Dec 31, 2024</td></tr>
    <tr><th>Currency</th><td>PHP</td></tr></table>
    <table id="BS"><tr><th>Current Assets</th><td>60</td><td>55</td></tr>
    <tr><th>Total Assets</th><td>100</td><td>90</td></tr>
    <tr><th>Current Liabilities</th><td>25</td><td>20</td></tr>
    <tr><th>Total Liabilities</th><td>40</td><td>35</td></tr>
    <tr><th>Stockholders' Equity</th><td>60</td><td>55</td></tr></table>
    <table id="IS"><tr><th>Gross Revenue</th><td>200</td><td>180</td></tr>
    <tr><th>Gross Expense</th><td>150</td><td>140</td></tr>
    <tr><th>Income/(Loss) Before Tax</th><td>40</td><td>35</td></tr>
    <tr><th>Income Tax Expense</th><td>10</td><td>8</td></tr>
    <tr><th>Net Income/(Loss) After Tax</th><td>30</td><td>27</td></tr></table>
    </body></html>"""


def test_payload_magic_routes_pdf_html_and_unknown():
    assert classify_payload(b"%PDF-1.7\n") == "pdf"
    assert classify_payload(b"\xef\xbb\xbf <!-- x --><HTML>") == "html"
    assert classify_payload(b"not a filing") == "unknown"


def test_manifest_uses_stable_company_id_and_preserves_amendment_metadata(tmp_path):
    source = tmp_path / "annual_report.html"
    source.write_bytes(_html())
    metadata = tmp_path / "metadata.csv"
    fields = [
        "market", "filing_id", "filing_date", "company_name", "stock_code",
        "title", "local_path", "document_url", "raw_metadata",
    ]
    with metadata.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerow({
            "market": "PSE_EDGE", "filing_id": "edge-1",
            "filing_date": "2025-04-15", "company_name": "Example",
            "stock_code": "WRONG-GUESSED-TICKER", "title": "[Amend-1]Annual Report",
            "local_path": str(source), "document_url": "https://example.test/1",
            "raw_metadata": json.dumps({
                "company_id": "104", "report_number": "CR00001-2025",
                "download_file_id": "123", "title": "[Amend-1]Annual Report",
            }),
        })

    rows = build_targets(metadata)

    assert len(rows) == 1
    assert rows[0]["company_id"] == "104"
    assert rows[0]["source_kind"] == "html"
    assert rows[0]["amendment_sequence"] == 1
    assert rows[0]["report_number"] == "CR00001-2025"


def test_html_summary_keeps_reported_identities_and_fails_debt_closed():
    row = extract_html_summary(_html(), TARGET)

    assert row["company_id"] == "104"
    assert row["period_end"] == "2024-12-31"
    assert row["currency"] == "PHP"
    assert row["total_assets"] == 100.0
    assert row["total_liabilities"] == 40.0
    assert row["total_equity"] == 60.0
    assert row["balance_identity_status"] == "pass"
    assert row["revenue"] == 200.0
    assert row["operating_profit"] == 50.0
    assert row["operating_profit_derivation"] == "gross_revenue_minus_gross_expense"
    assert row["tax_identity_status"] == "pass"
    assert row["debt_reconciliation_status"] == "unresolved_not_reported"
    assert row["screen_eligible"] is False


def test_edge_currency_spellings_preserve_currency_and_presentation_scale():
    assert normalize_currency_unit("Philippine Pesos")[:2] == ("PHP", 1.0)
    assert normalize_currency_unit("IN PESOS")[:2] == ("PHP", 1.0)
    assert normalize_currency_unit("PHP IN 000'S")[:2] == ("PHP", 1_000.0)
    assert normalize_currency_unit("USD (in thousands)")[:2] == ("USD", 1_000.0)
    assert normalize_currency_unit("PHP millions")[:2] == ("PHP", 1_000_000.0)


def test_pdf_statement_parser_closes_direct_oi_tax_balance_and_complete_debt():
    text = """
    Contents
    Consolidated Statements of Financial Position 69
    Consolidated Statements of Total Comprehensive Income 70

    DigiPlus Interactive Corp. and Subsidiaries
    Consolidated Statements of Financial Position
    As at December 31, 2025 and 2024
    (All amounts in thousands of Philippine Peso)
    Notes 2025 2024
    Assets
    Current assets
      Cash and cash equivalents 2 23,401,849 13,977,434
      Total current assets 28,270,089 20,280,925
    Non-current assets
      Total non-current assets 24,815,543 23,863,962
      Total assets 53,085,632 44,144,887
    Liabilities and Equity
    Current liabilities
      Current portion of:
        Long-term loans payable 13 745,768 733,781
        Lease liabilities 18 522,409 476,217
      Total current liabilities 9,163,941 9,817,231
    Non-current liabilities
      Lease liabilities, net of current portion 18 1,080,266 1,294,632
      Total non-current liabilities 2,827,045 3,047,910
      Total liabilities 11,990,986 12,865,141
    Equity
      Non-controlling interests 188,648 190,081
      Total equity 41,094,646 31,279,746
      Total liabilities and equity 53,085,632 44,144,887

    Consolidated Statements of Total Comprehensive Income
    For each of the three years in the period ended December 31, 2025
    (All amounts in thousands of Philippine Peso)
    Notes 2025 2024 2023
    Operating profit 12,629,725 12,868,846 4,284,145
    Profit before income tax 12,592,774 12,630,615 4,179,238
    Income tax expense 22 (28,900) (46,509) (84,385)
    Net income for the year 12,563,874 12,584,106 4,094,853

    13 Loans payable
    Short-term loans
    Beginning balance - 1,000 116,739
    Additions 10,000 15,000 576,000
    Payments (10,000) (16,000) (691,739)
    Ending balance - - 1,000
    """
    target = {
        **TARGET,
        "filing_id": "plus-2025",
        "filing_date": "2026-04-01",
        "local_path": "/corpus/plus-2025.pdf",
        "document_url": "https://edge.pse.com.ph/downloadFile.do?file_id=1",
        "amendment_sequence": 0,
    }

    row = extract_pdf_statement_text(text, target, source_sha256="a" * 64)

    assert row["period_end"] == "2025-12-31"
    assert row["currency"] == "PHP"
    assert row["source_unit_scale"] == 1_000.0
    assert row["unit_scale"] == 1.0
    assert row["cash_and_equivalents"] == 23_401_849_000.0
    assert row["operating_profit"] == 12_629_725_000.0
    assert row["profit_before_tax"] == 12_592_774_000.0
    assert row["income_tax_expense"] == -28_900_000.0
    assert row["tax_identity_status"] == "pass"
    assert row["balance_identity_status"] == "pass"
    assert row["total_interest_bearing_debt"] == 2_348_443_000.0
    assert row["debt_reconciliation_status"] == (
        "resolved_statement_components_plus_note_zero"
    )
    assert row["non_controlling_interest"] == 188_648_000.0
    assert row["screen_eligible"] is True
    assert row["source_sha256"] == "a" * 64


def test_pdf_statement_parser_does_not_infer_missing_borrowings_as_zero():
    text = """
    Consolidated Statements of Financial Position
    As at December 31, 2025 and 2024
    (All amounts in thousands of Philippine Peso)
    Notes 2025 2024
    Cash and cash equivalents 2 1,000 900
    Total assets 10,000 9,000
    Lease liabilities 18 100 90
    Lease liabilities, net of current portion 18 200 180
    Total liabilities 4,000 3,500
    Total equity 6,000 5,500
    Total liabilities and equity 10,000 9,000
    Consolidated Statements of Income
    Notes 2025 2024
    Operating profit 500 450
    Profit before income tax 400 350
    Income tax expense 100 90
    Net income 300 260
    """
    row = extract_pdf_statement_text(text, TARGET)

    assert row["total_interest_bearing_debt"] is None
    assert row["debt_reconciliation_status"] == "unresolved_borrowings"
    assert row["screen_eligible"] is False


def test_pdf_parser_accepts_complete_pse_primary_statement_debt_layout():
    text = """
    Consolidated Statements of Financial Position
    DECEMBER 31, 2024 AND 2023
    (Amounts in Philippine Pesos)
    Notes 2024 2023
    CURRENT ASSETS
      Cash and cash equivalents 5 1,000,000 900,000
      Total Current Assets 6,000,000 5,500,000
    NON-CURRENT ASSETS
      Total Non-current Assets 9,500,000 8,800,000
    TOTAL ASSETS P 15,500,000 P 14,300,000
    LIABILITIES AND EQUITY
    CURRENT LIABILITIES
      Lease liabilities 10 3,400 5,100
      Total Current Liabilities 680,000 610,000
    NON-CURRENT LIABILITIES
      Interest-bearing loan 14 875,000 105,000
      Lease liabilities 10 500 -
      Total Non-current Liabilities 2,054,000 1,349,000
      Total Liabilities 2,734,000 1,959,000
    EQUITY
      Total Equity 12,766,000 12,341,000
    TOTAL LIABILITIES AND EQUITY P 15,500,000 P 14,300,000

    Consolidated Statements of Income
    FOR THE YEARS ENDED DECEMBER 31, 2024, 2023 AND 2022
    (Amounts in Philippine Pesos)
    Notes 2024 2023 2022
    OPERATING PROFIT 637,915 576,346 471,613
    PROFIT BEFORE TAX 849,489 731,345 592,144
    TAX EXPENSE 246,688 199,580 163,218
    NET PROFIT 602,801 531,765 428,926
    """

    row = extract_pdf_statement_text(text, TARGET)

    assert row["period_end"] == "2024-12-31"
    assert row["currency"] == "PHP"
    assert row["total_interest_bearing_debt"] == 878_900.0
    assert row["debt_reconciliation_status"] == (
        "resolved_primary_statement_recognized_components"
    )
    assert row["tax_identity_status"] == "pass"
    assert row["balance_identity_status"] == "pass"
    assert row["screen_eligible"] is True


def test_pse_finality_prefers_latest_amendment_for_company_period():
    base = {
        "company_id": "104",
        "period_end": "2025-12-31",
        "filing_date": "2026-04-01",
        "screen_eligible": True,
    }
    original = {**base, "filing_id": "a", "amendment_sequence": 0}
    amendment = {**base, "filing_id": "b", "amendment_sequence": 1}

    finalized = finalize_pse_rows([original, amendment])

    selected = [row for row in finalized if row["screen_eligible"]]
    assert len(selected) == 1
    assert selected[0]["filing_id"] == "b"
    assert selected[0]["finality_status"] == (
        "latest_amendment_for_company_period"
    )
    assert finalized[0]["finality_status"] == "superseded_company_period"


def test_pdf_recovers_float_components_by_liability_section_and_scale():
    text = '''
    Consolidated Statements of Financial Position
    December 31, 2025 and 2024
    (All amounts in thousands of Philippine Peso)
    Notes 2025 2024
    Current liabilities
      Trade and other payables 12,000 8,000
      Contract liabilities 7,000 6,000
    Non-current liabilities
      Trade and other payables 3,000 2,000
      Contract liabilities 2,000 1,000
    Equity
      Total equity 60,000 55,000
    Consolidated Statements of Income
    Notes 2025 2024
    Operating profit 20,000 18,000
    Profit before tax 20,000 18,000
    Income tax expense 4,000 3,600
    Net income 16,000 14,400
    Consolidated Statements of Cash Flows
    '''
    row = extract_pdf_statement_text(text, TARGET)
    assert row['trade_and_other_current_payables'] == 12000000
    assert row['trade_and_other_non_current_payables'] == 3000000
    assert row['current_contract_liabilities'] == 7000000
    assert row['non_current_contract_liabilities'] == 2000000


def test_delay_notice_is_not_financial_statement_or_statement_period():
    text = '''SEC FORM 17-L
    NOTIFICATION OF INABILITY TO FILE ALL OR ANY PORTION OF SEC FORM 17-A OR 17-Q
    April 14, 2025
    For the period ended December 31, 2024
    '''
    row = extract_pdf_statement_text(text, TARGET, source_sha256='source-hash')
    assert row['period_end'] is None
    assert row['document_classification'] == 'filing_delay_notice_without_statements'
    assert row['screen_eligible'] is False
    assert row['source_sha256'] == 'source-hash'
    assert row['filing_id'] == TARGET['filing_id']
