"""TDD tests for the ASX PDF financial-statement extractor.

Core is extract_metrics(text): given pdftotext -layout output of an Australian
annual report (English, AASB≈IFRS), pull canonical metrics from the primary
statements. First-pass regex extractor (HK-style); values are captured as shown
(unit normalisation of $'000 / $m is a downstream adapter concern).
"""
import asx_financials_extract as ax

SAMPLE = """
Consolidated Statement of Profit or Loss
                                              2023        2022
                                             $'000       $'000
Revenue                                      45,231      41,002
Cost of sales                              (28,100)    (25,300)
Gross profit                                 17,131      15,702
Profit before income tax                      8,450       7,200
Income tax expense                          (2,535)     (2,160)
Profit for the year                           5,915       5,040
Basic earnings per share (cents)               12.4        10.6

Consolidated Statement of Financial Position
Cash and cash equivalents                     6,300       5,100
Trade and other receivables                   8,200       7,900
Inventories                                   4,150       3,980
Total current assets                         22,100      20,050
Total assets                                 98,700      91,200
Total current liabilities                    15,300      14,100
Total liabilities                            41,200      39,800
Total equity                                 57,500      51,400

Consolidated Statement of Cash Flows
Net cash from operating activities            9,200       8,100
Net cash used in investing activities       (6,400)     (5,900)
Net cash from financing activities          (1,500)       (900)
"""


# ---- parse_amount ----
def test_parse_amount_parens_and_commas():
    assert ax.parse_amount("45,231") == 45231.0
    assert ax.parse_amount("(28,100)") == -28100.0
    assert ax.parse_amount("12.4") == 12.4
    assert ax.parse_amount("-") is None


# ---- extract_metrics: current-year (first) column ----
def test_extract_income_statement():
    m = ax.extract_metrics(SAMPLE)
    assert m["revenue"] == 45231
    assert m["cost_of_revenue"] == -28100
    assert m["gross_profit"] == 17131
    assert m["profit_before_tax"] == 8450
    assert m["income_tax_expense"] == -2535
    assert m["net_income"] == 5915
    assert m["basic_eps"] == 12.4

def test_extract_balance_sheet():
    m = ax.extract_metrics(SAMPLE)
    assert m["cash_and_equivalents"] == 6300
    assert m["accounts_receivable"] == 8200
    assert m["inventories"] == 4150
    assert m["current_assets"] == 22100
    assert m["total_assets"] == 98700
    assert m["current_liabilities"] == 15300
    assert m["total_liabilities"] == 41200
    assert m["total_equity"] == 57500

def test_extract_cash_flow():
    m = ax.extract_metrics(SAMPLE)
    assert m["operating_cash_flow"] == 9200
    assert m["investing_cash_flow"] == -6400
    assert m["financing_cash_flow"] == -1500

def test_total_assets_not_confused_with_current():
    """'Total assets' must not match on the 'Total current assets' line."""
    m = ax.extract_metrics(SAMPLE)
    assert m["total_assets"] == 98700 and m["current_assets"] == 22100

def test_empty_text_yields_nothing():
    assert ax.extract_metrics("") == {}


# ---- note-column handling: [label] [Note] [Current] [Prior] ----
NOTE_COL = """
Consolidated Statement of Profit or Loss
                                   Note      2023        2022
                                            $'000       $'000
Revenue                             3      45,231      41,002
Profit for the year               8,9       5,915       5,040
Total assets                       12      98,700      91,200
"""

def test_note_column_not_grabbed_as_value():
    m = ax.extract_metrics(NOTE_COL)
    assert m["revenue"] == 45231        # not 3 (the note number)
    assert m["net_income"] == 5915      # not 8 or 9
    assert m["total_assets"] == 98700   # not 12

def test_current_year_is_second_to_last():
    """Current-year value is the left of the two year columns."""
    m = ax.extract_metrics("Revenue    45,231    41,002")
    assert m["revenue"] == 45231        # current, not prior (41,002)

def test_single_value_line():
    m = ax.extract_metrics("Total equity    57,500")
    assert m["total_equity"] == 57500
