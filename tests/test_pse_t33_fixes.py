"""T33 PSE producer fixes: split period header, document-policy currency."""

from pse_stage02_v2 import extract_pdf_statement_text

TARGET = {
    "filing_id": "edge-t33",
    "filing_date": "2026-04-15",
    "company_id": "104",
    "company_name": "Example Philippines Corp",
    "title": "Annual Report",
}

SPLIT_HEADER = """
CONSOLIDATED STATEMENTS OF FINANCIAL POSITION
(Amounts in Thousands)

                                              December 31
                                         2025            2024
ASSETS
  Cash and cash equivalents            1,000           900
  TOTAL ASSETS                        10,000         9,000
  TOTAL LIABILITIES                    4,000         3,500
  TOTAL EQUITY                         6,000         5,500
CONSOLIDATED STATEMENTS OF COMPREHENSIVE INCOME
(Amounts in Thousands)
                                              Years Ended December 31
                                         2025            2024
  Operating profit                       500             450
  Profit before income tax               400             350
  Income tax expense                     100              90
  Net income                             300             260
CONSOLIDATED STATEMENTS OF CASH FLOWS
"""

POLICY_PESO = (
    "Note 2. The consolidated financial statements are presented in Philippine "
    "peso, which is the Company's functional and presentation currency.\n"
)
POLICY_USD = (
    "Note 2. The consolidated financial statements are presented in United "
    "States dollar, the Company's functional currency.\n"
)
POLICY_MIXED = (
    "Note 2. The consolidated financial statements are presented in Philippine "
    "peso. The functional currency of a subsidiary is the United States dollar.\n"
)


def test_split_month_day_and_year_header_gives_current_year_period_end():
    row = extract_pdf_statement_text(SPLIT_HEADER, TARGET)
    assert row["period_end"] == "2025-12-31"


def test_bare_unit_header_with_peso_presentation_policy_gives_php_with_provenance():
    row = extract_pdf_statement_text(SPLIT_HEADER + POLICY_PESO, TARGET)
    assert row["currency"] == "PHP"
    assert row["currency_source"] == "document_accounting_policy"
    assert row["source_unit_scale"] == 1_000.0
    assert row["cash_and_equivalents"] == 1_000_000.0


def test_bare_unit_header_without_policy_leaves_currency_unresolved():
    row = extract_pdf_statement_text(SPLIT_HEADER, TARGET)
    assert row["currency"] is None
    assert row["currency_source"] is None
    assert row["screen_eligible"] is False


def test_usd_presentation_policy_gives_usd():
    row = extract_pdf_statement_text(SPLIT_HEADER + POLICY_USD, TARGET)
    assert row["currency"] == "USD"
    assert row["currency_source"] == "document_accounting_policy"


def test_functional_currency_of_a_subsidiary_is_not_presentation_evidence():
    row = extract_pdf_statement_text(SPLIT_HEADER + POLICY_MIXED, TARGET)
    assert row["currency"] == "PHP"  # presentation phrase wins; subsidiary functional currency ignored


def test_explicit_statement_header_currency_keeps_header_provenance():
    text = SPLIT_HEADER.replace("(Amounts in Thousands)", "(Amounts in Thousands of Philippine Peso)")
    row = extract_pdf_statement_text(text, TARGET)
    assert row["currency"] == "PHP"
    assert row["currency_source"] == "statement_header"


def test_conflicting_presentation_phrases_leave_currency_unresolved():
    row = extract_pdf_statement_text(SPLIT_HEADER + POLICY_PESO + POLICY_USD, TARGET)
    assert row["currency"] is None
    assert row["currency_source"] is None


def test_policy_currency_not_used_when_statement_has_no_unit_header():
    text = SPLIT_HEADER.replace("(Amounts in Thousands)", "") + POLICY_PESO
    row = extract_pdf_statement_text(text, TARGET)
    assert row["currency"] is None
    assert row["screen_eligible"] is False
