"""TDD for the TWSE (Taiwan) extractor — Traditional-Chinese IFRS statements.
Reuses the ASX number machinery (second-to-last column); Chinese labels only."""
import twse_financials_extract as tw

SAMPLE = """
合併綜合損益表
                                    2023年        2022年
營業收入                          45,231        41,002
營業成本                        (28,100)      (25,300)
營業毛利                          17,131        15,702
營業利益                           8,900         7,500
稅前淨利                           8,450         7,200
所得稅費用                        (2,535)       (2,160)
本期淨利                           5,915         5,040
基本每股盈餘                        12.40         10.60

合併資產負債表
現金及約當現金                     6,300         5,100
存貨                               4,150         3,980
流動資產合計                      22,100        20,050
資產總計                          98,700        91,200
流動負債合計                      15,300        14,100
負債總計                          41,200        39,800
權益總計                          57,500        51,400

合併現金流量表
營業活動之淨現金流入（流出）        9,200         8,100
投資活動之淨現金流入（流出）      (6,400)       (5,900)
籌資活動之淨現金流入（流出）      (1,500)         (900)
"""


def test_income_statement_zh():
    m = tw.extract_metrics(SAMPLE)
    assert m["revenue"] == 45231
    assert m["cost_of_revenue"] == -28100
    assert m["gross_profit"] == 17131
    assert m["operating_profit"] == 8900
    assert m["profit_before_tax"] == 8450
    assert m["income_tax_expense"] == -2535
    assert m["net_income"] == 5915
    assert m["basic_eps"] == 12.40

def test_balance_sheet_zh():
    m = tw.extract_metrics(SAMPLE)
    assert m["cash_and_equivalents"] == 6300
    assert m["inventories"] == 4150
    assert m["current_assets"] == 22100
    assert m["total_assets"] == 98700          # 資產總計, not 流動資產合計
    assert m["current_liabilities"] == 15300
    assert m["total_liabilities"] == 41200
    assert m["total_equity"] == 57500

def test_cash_flow_zh():
    m = tw.extract_metrics(SAMPLE)
    assert m["operating_cash_flow"] == 9200
    assert m["investing_cash_flow"] == -6400
    assert m["financing_cash_flow"] == -1500

def test_operating_profit_not_revenue():
    """營業收入/營業成本/營業利益 all start with 營業 — must not cross-match."""
    m = tw.extract_metrics(SAMPLE)
    assert m["revenue"] == 45231 and m["operating_profit"] == 8900
