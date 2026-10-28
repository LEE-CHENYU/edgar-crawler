"""Contract and regression tests for India BSE cash and financial asset extraction.

Covers:
- Recognition of Ind-AS and Indian GAAP cash headings:
  'Cash and cash equivalents', 'Cash & cash equivalents', 'Cash and bank balances',
  'Balances with banks'.
- Recognition of secondary liquid asset lines:
  'Bank balances other than cash and cash equivalents' (other_bank_balances),
  'Current investments', 'Investments in mutual funds' (current_investments).
- Multi-column Ind-AS layout handling (side-by-side statements).
- Double-counting prevention (separate fields, priority ordering, exclusion of 'other than').
- INR scaling enrichment in in_bse_stage02_job.py.
"""

from pathlib import Path
import pytest

import in_bse_stage02 as bse
import in_bse_stage02_job as job

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "in_bse_financial_assets"


def test_fixture_oriental_hotels_side_by_side():
    """Multi-column Ind-AS layout with Cash & cash equivalents and other bank balances."""
    text = (FIXTURES_DIR / "oriental_hotels_500314.txt").read_text()
    rows = bse.extract_document(text, company_name="Oriental Hotels Ltd.")
    assert len(rows) == 1
    row = rows[0]
    assert row["cash_and_equivalents"] == pytest.approx(709.42)
    assert row["other_bank_balances"] == pytest.approx(436.23)
    assert row["total_assets"] == pytest.approx(69977.83)
    assert row["period_end"] == "2026-03-31"


def test_fixture_pix_transmissions_ind_as():
    """Ind-AS Schedule III with Cash & cash equivalents, other bank balances, and current investments."""
    text = (FIXTURES_DIR / "pix_transmissions_500333.txt").read_text()
    rows = bse.extract_document(text, company_name="Pix Transmissions Ltd.")
    assert len(rows) == 1
    row = rows[0]
    assert row["cash_and_equivalents"] == pytest.approx(6776.30)
    assert row["other_bank_balances"] == pytest.approx(713.18)
    assert row["current_investments"] == pytest.approx(19957.39)
    assert row["total_assets"] == pytest.approx(76475.36)


def test_fixture_tanfac_mutual_funds():
    """Ind-AS layout with investments in mutual funds under Current Assets."""
    text = (FIXTURES_DIR / "tanfac_506854.txt").read_text()
    rows = bse.extract_document(text, company_name="TANFAC Industries Ltd.")
    assert len(rows) == 1
    row = rows[0]
    assert row["cash_and_equivalents"] == pytest.approx(3011.80)
    assert row["other_bank_balances"] == pytest.approx(775.43)
    assert row["current_investments"] == pytest.approx(698.93)
    assert row["total_assets"] == pytest.approx(42641.14)


def test_fixture_asian_tea_bank_balances():
    """Indian GAAP Schedule VI with 'Cash and bank balances'."""
    text = (FIXTURES_DIR / "asian_tea_519532.txt").read_text()
    rows = bse.extract_document(text, company_name="Asian Tea & Exports Ltd.")
    assert len(rows) == 1
    row = rows[0]
    assert row["cash_and_equivalents"] == pytest.approx(1219.0)
    assert row["other_bank_balances"] == pytest.approx(486.0)
    assert row["total_assets"] == pytest.approx(717311.0)


def test_fixture_astal_balances_with_banks():
    """Ind-AS layout reporting 'Balances with banks' under cash."""
    text = (FIXTURES_DIR / "astal_512600.txt").read_text()
    rows = bse.extract_document(text, company_name="ASTAL LABORATORIES LIMITED")
    assert len(rows) == 1
    row = rows[0]
    assert row["cash_and_equivalents"] == pytest.approx(173.00)
    assert row["total_assets"] == pytest.approx(6334.06)


def test_cash_priority_precedence_tier1_over_tier2_and_tier3():
    """Tier 1 (Cash & cash equivalents) must take precedence over Tier 2 and Tier 3."""
    lines = [
        "BALANCE SHEET AS AT 31ST MARCH, 2025",
        "ASSETS",
        "Current Assets",
        "Balances with banks                    100.00    90.00",
        "Cash and bank balances                 200.00   180.00",
        "Cash and cash equivalents              300.00   250.00",
        "Total Assets                          5000.00  4500.00",
        "EQUITY AND LIABILITIES",
        "Total Equity                          3000.00  2800.00",
        "Borrowings                            1000.00   900.00",
    ]
    res = bse._extract_balance_sheet(lines)
    assert res["cash_and_equivalents"] == pytest.approx(300.00)


def test_cash_priority_tier2_over_tier3():
    """Tier 2 (Cash and bank balances) must take precedence over Tier 3 (Balances with banks)."""
    lines = [
        "BALANCE SHEET AS AT 31ST MARCH, 2025",
        "ASSETS",
        "Current Assets",
        "Balances with banks                    100.00    90.00",
        "Cash and bank balances                 200.00   180.00",
        "Total Assets                          5000.00  4500.00",
        "EQUITY AND LIABILITIES",
        "Total Equity                          3000.00  2800.00",
        "Borrowings                            1000.00   900.00",
    ]
    res = bse._extract_balance_sheet(lines)
    assert res["cash_and_equivalents"] == pytest.approx(200.00)


def test_other_bank_balances_never_counted_as_cash():
    """'Bank balances other than cash' must populate other_bank_balances, not cash."""
    lines = [
        "BALANCE SHEET AS AT 31ST MARCH, 2025",
        "ASSETS",
        "Current Assets",
        "Cash and cash equivalents                          150.00   120.00",
        "Bank balances other than cash and cash equivalents  75.00    50.00",
        "Total Assets                                      5000.00  4500.00",
        "EQUITY AND LIABILITIES",
        "Total Equity                                      3000.00  2800.00",
        "Borrowings                                        1000.00   900.00",
    ]
    res = bse._extract_balance_sheet(lines)
    assert res["cash_and_equivalents"] == pytest.approx(150.00)
    assert res["other_bank_balances"] == pytest.approx(75.00)


def test_other_than_pattern_not_misidentified_as_cash():
    """A line starting with 'Bank balances other than...' should not match cash patterns."""
    lines = [
        "BALANCE SHEET AS AT 31ST MARCH, 2025",
        "ASSETS",
        "Current Assets",
        "Bank balances other than cash and cash equivalents  85.00    60.00",
        "Total Assets                                      5000.00  4500.00",
        "EQUITY AND LIABILITIES",
        "Total Equity                                      3000.00  2800.00",
        "Borrowings                                        1000.00   900.00",
    ]
    res = bse._extract_balance_sheet(lines)
    assert res.get("cash_and_equivalents") is None
    assert res["other_bank_balances"] == pytest.approx(85.00)


def test_non_current_investments_not_counted_as_current():
    """Non-current investments must NEVER populate current_investments."""
    lines = [
        "BALANCE SHEET AS AT 31ST MARCH, 2025",
        "ASSETS",
        "Non-current assets",
        "Non-current investments                           1200.00  1000.00",
        "Current Assets",
        "Current investments                                400.00   300.00",
        "Cash and cash equivalents                          100.00    80.00",
        "Total Assets                                      5000.00  4500.00",
        "EQUITY AND LIABILITIES",
        "Total Equity                                      3000.00  2800.00",
        "Borrowings                                        1000.00   900.00",
    ]
    res = bse._extract_balance_sheet(lines)
    assert res["current_investments"] == pytest.approx(400.00)
    assert res["cash_and_equivalents"] == pytest.approx(100.00)


def test_investments_in_mutual_funds_recognized():
    """'Investments in mutual funds' must be extracted as current_investments."""
    lines = [
        "BALANCE SHEET AS AT 31ST MARCH, 2025",
        "ASSETS",
        "Current Assets",
        "Investments in mutual funds                        350.00   200.00",
        "Cash and cash equivalents                          100.00    80.00",
        "Total Assets                                      5000.00  4500.00",
        "EQUITY AND LIABILITIES",
        "Total Equity                                      3000.00  2800.00",
        "Borrowings                                        1000.00   900.00",
    ]
    res = bse._extract_balance_sheet(lines)
    assert res["current_investments"] == pytest.approx(350.00)


def test_enrich_financial_rows_scales_new_fields():
    """enrich_rows must scale cash, other_bank_balances, and current_investments by unit_scale."""
    rows = [
        {
            "cash_and_equivalents": 12.5,
            "other_bank_balances": 3.0,
            "current_investments": 25.0,
            "unit_scale": 100000.0,
            "period_end": "2025-03-31",
            "statement_scope": "standalone",
        },
        {
            "cash_and_equivalents": None,
            "other_bank_balances": None,
            "current_investments": None,
            "unit_scale": 100000.0,
            "period_end": "2024-03-31",
            "statement_scope": "standalone",
        },
    ]
    manifest = {"filing_id": "test_1", "symbol": "in_bse:500001", "company_name": "Test Co"}
    meta = {"download_url": "http://example.com/test.pdf"}
    enriched = job.enrich_rows(rows, manifest, meta)

    assert len(enriched) == 2
    r0 = enriched[0]
    assert r0["cash_and_equivalents_inr"] == pytest.approx(1250000.0)
    assert r0["other_bank_balances_inr"] == pytest.approx(300000.0)
    assert r0["current_investments_inr"] == pytest.approx(2500000.0)

    r1 = enriched[1]
    assert r1["cash_and_equivalents_inr"] is None
    assert r1["other_bank_balances_inr"] is None
    assert r1["current_investments_inr"] is None
