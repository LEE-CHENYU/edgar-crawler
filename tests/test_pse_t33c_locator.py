"""T33c PSE producer: standalone statement locator, C$M scale."""

from pse_stage02_v2 import extract_pdf_statement_text, normalize_currency_unit, extract_html_summary

TARGET = {"filing_id": "edge-t33c", "filing_date": "2026-04-15", "company_id": "9",
          "company_name": "Example Corp", "title": "Annual Report"}

BODY = """
{b}STATEMENTS OF FINANCIAL POSITION
(Amounts in Thousands)

                                              December 31
                                         2025            2024
ASSETS
  Cash and cash equivalents            1,000           900
  TOTAL ASSETS                        10,000         9,000
  TOTAL LIABILITIES                    4,000         3,500
  TOTAL EQUITY                         6,000         5,500
{i}STATEMENTS OF COMPREHENSIVE INCOME
(Amounts in Thousands)
                                              Years Ended December 31
                                         2025            2024
  Operating profit                       500             450
  Profit before income tax               400             350
  Income tax expense                     100              90
  Net income                             300             260
{c}STATEMENTS OF CASH FLOWS
"""


def doc(prefix):
    return BODY.format(b=prefix, i=prefix, c=prefix)


def test_standalone_statements_are_located_when_no_consolidated_set_exists():
    row = extract_pdf_statement_text(doc(""), TARGET)
    assert row["document_classification"] == "primary_statement_text"
    assert row["statement_scope"] == "standalone"
    assert row["total_assets"] == 10_000_000.0
    assert row["cash_and_equivalents"] == 1_000_000.0
    assert row["profit_before_tax"] == 400_000.0


def test_consolidated_set_keeps_consolidated_scope_and_is_not_overridden_by_standalone():
    text = doc("CONSOLIDATED ").replace("10,000", "99,999") + doc("")
    row = extract_pdf_statement_text(text, TARGET)
    assert row["statement_scope"] == "consolidated"
    assert row["total_assets"] == 99_999_000.0


def test_parent_company_prefixed_headings_are_not_standalone_primary():
    row = extract_pdf_statement_text(doc("PARENT COMPANY "), TARGET)
    assert row["document_classification"] == "no_primary_statements_found"


def test_standalone_headings_with_trailing_period_are_accepted():
    text = doc("").replace("STATEMENTS OF FINANCIAL POSITION", "STATEMENTS OF FINANCIAL POSITION.")
    row = extract_pdf_statement_text(text, TARGET)
    assert row["document_classification"] == "primary_statement_text"
    assert row["statement_scope"] == "standalone"


def test_no_headings_stays_unlocated():
    row = extract_pdf_statement_text("Narrative only. Total assets rose to 10,000 thousand.\n", TARGET)
    assert row["document_classification"] == "no_primary_statements_found"
    assert row["statement_scope"] != "standalone"


def test_c_dollar_m_is_millions_cad():
    assert normalize_currency_unit("C$M except EPS & BV")[:2] == ("CAD", 1_000_000.0)
    assert normalize_currency_unit("C$millions except EPS")[:2] == ("CAD", 1_000_000.0)


def test_ocr_csm_is_millions_but_currency_stays_unresolved():
    cur, scale, _ = normalize_currency_unit("CSM except EPS & BV")
    assert scale == 1_000_000.0 and cur is None


def test_plain_m_without_currency_prefix_is_not_millions():
    assert normalize_currency_unit("PHP")[1] == 1.0
    assert normalize_currency_unit("M")[1] == 1.0


def test_prose_mention_of_consolidated_statements_blocks_the_standalone_fallback():
    text = ("Notes: see the consolidated statements of financial position of the Group for details.\n" + doc(""))
    row = extract_pdf_statement_text(text, TARGET)
    assert row["document_classification"] == "no_primary_statements_found"
