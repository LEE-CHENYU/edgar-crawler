"""Traditional/Simplified Chinese statement vocabulary.

TWSE swept at 0.8% yield (2 of 250) with an extractor that knew English and
Nordic terms only. The documents are fine -- median 3.1 MB, ~500K characters
extracted, and 16 of 25 sampled filings contain recoverable CJK statement terms
(the other 9 hit font-encoding failures that no vocabulary can fix). This is the
Nordic defect again: a market's statements are unreadable because nobody taught
the extractor its language.
"""

from asx_financials_extract import extract_metrics


def _m(line, name):
    return extract_metrics(line).get(name)


# --- Traditional Chinese (TWSE) -----------------------------------------


def test_traditional_balance_sheet_totals():
    assert _m("資產總計 1,234,567", "total_assets") == 1234567.0
    assert _m("負債總計 500,000", "total_liabilities") == 500000.0
    assert _m("權益總計 734,567", "total_equity") == 734567.0


def test_traditional_alternate_total_forms():
    assert _m("資產總額 999", "total_assets") == 999.0
    assert _m("負債總額 111", "total_liabilities") == 111.0


def test_traditional_current_and_noncurrent():
    assert _m("流動資產合計 400", "current_assets") == 400.0
    assert _m("非流動資產合計 600", "non_current_assets") == 600.0
    assert _m("流動負債合計 300", "current_liabilities") == 300.0
    assert _m("非流動負債合計 200", "non_current_liabilities") == 200.0


def test_traditional_income_statement():
    assert _m("營業收入 9,100", "revenue") == 9100.0
    assert _m("營業成本 6,000", "cost_of_revenue") == 6000.0
    assert _m("營業毛利 3,100", "gross_profit") == 3100.0
    assert _m("所得稅費用 200", "income_tax_expense") == 200.0
    assert _m("基本每股盈餘 3.5", "basic_eps") == 3.5


def test_traditional_profit_lines():
    assert _m("稅前淨利 800", "profit_before_tax") == 800.0
    assert _m("本期淨利 600", "net_income") == 600.0


def test_traditional_cash_and_working_capital():
    assert _m("現金及約當現金 50", "cash_and_equivalents") == 50.0
    assert _m("應收帳款 30", "accounts_receivable") == 30.0
    assert _m("存貨 20", "inventories") == 20.0


def test_traditional_cash_flow_lines():
    assert _m("營業活動之淨現金流入 700", "operating_cash_flow") == 700.0
    assert _m("投資活動之淨現金流出 (400)", "investing_cash_flow") == -400.0
    assert _m("籌資活動之淨現金流入 100", "financing_cash_flow") == 100.0


# --- Simplified Chinese --------------------------------------------------


def test_simplified_variants():
    assert _m("资产总计 1,000", "total_assets") == 1000.0
    assert _m("营业收入 2,000", "revenue") == 2000.0
    assert _m("负债合计 500", "total_liabilities") == 500.0


# --- ordering: a longer label must not be shadowed -----------------------


def test_noncurrent_is_not_captured_by_the_current_label():
    """流動資產合計 is a prefix of nothing, but 非流動資產合計 CONTAINS it -- a
    naive contains-match would misfile every non-current total as current."""
    assert _m("非流動資產合計 600", "current_assets") is None
    assert _m("非流動負債合計 200", "current_liabilities") is None


def test_english_and_nordic_still_work():
    assert _m("Total assets 111", "total_assets") == 111.0
    assert _m("Summa tillgangar 222", "total_assets") == 222.0
