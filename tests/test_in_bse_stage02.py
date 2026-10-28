"""Contract tests for the statement-aware India BSE Stage-02 rebuild."""

import pytest
import in_bse_stage02 as in_bse

from in_bse_stage02 import (
    extract_document,
    normalize_manifest_record,
    parse_amount,
)


UMA_STATEMENTS = """
Standalone Balance Sheet as at 31st March, 2025
Rs. in lakhs
Particulars Note No. As at 31st March, 2025 As at 31st March, 2024
ASSETS
Non-current assets
Total Non-Current Assets 5,337.10 5,531.05
Current assets
Cash and cash equivalents 9 246.13 35.41
Total Current Assets 37,531.37 28,537.34
TOTAL ASSETS 42,868.46 34,068.39
EQUITY AND LIABILITIES
EQUITY
Total Equity 17,371.45 17,288.44
LIABILITIES
Non current liabilities
Financial Liabilities
(a) Borrowings 17 50.61 47.01
Total Non-Current Liabilities 68.87 66.75
Current liabilities
Financial Liabilities
(a) Borrowings 19 20,770.49 12,069.83
Total Current Liabilities 25,428.15 16,713.21
TOTAL EQUITY & LIABILITIES 42,868.46 34,068.39

Statement of Standalone Profit and loss for the year ended 31st March 2025
Rs. in lakhs
Particulars Note No. For the year ended 31st March, 2025 For the year ended 31st March, 2024
INCOME :
Revenue from operations 25 1,66,838.31 1,38,609.85
Other income 26 723.39 366.28
Total Income 1,67,561.70 1,38,976.13
EXPENSES :
Cost of Material Consumed 27 678.60 -
Purchases of stock in trade 28 1,65,014.58 1,35,367.24
Changes in inventories of finished goods and Stock-in-Trade 29 (5,260.93) (4,135.68)
Employee benefits expense 30 290.75 293.83
Finance costs 31 1,181.12 836.23
Depreciation and amortization expense 32 63.53 58.21
Other expenses 33 5,498.07 5,666.86
Total Expenses 1,67,465.73 1,38,086.70
Profit before exceptional items and tax 95.97 889.44
Exceptional items 34 - 37.22
Profit before tax 95.97 852.22
Tax expenses:
(1) Current tax 36.71 245.07
(2) Income Tax Adjustment - 14.85
(3) Deferred tax Adjustment (12.55) (14.36)
Profit for the year 71.80 606.67
"""


FINANCIAL_STATEMENTS = """
BALANCE SHEET AS AT MARCH 31, 2025
(All Amounts in Rs. Lakhs, unless otherwise stated)
Particulars Note As at March 31, 2025 As at March 31, 2024
Cash and Cash Equivalents 3 218.18 94.47
TOTAL ASSETS 4,793.47 4,550.14
TOTAL LIABILITIES AND EQUITY 4,793.47 4,550.14

STATEMENT OF PROFIT AND LOSS FOR THE YEAR ENDED MARCH 31, 2025
(All Amounts in Rs. Lakhs, unless otherwise stated)
Interest Income 15 331.82 314.37
Dividend Income 16 0.14 0.09
Fair Value Changes 17 20.97 9.93
Total Revenue From Operations 352.93 324.39
Other Income 18 17.53 0.96
Total Income 370.46 325.35
Finance Cost 19 - 0.01
Employee Benefit Expenses 20 10.05 19.69
Other Expenses 21 78.88 69.33
Total Expenses 88.93 89.03
Profit before tax 281.53 236.32
Current tax 71.08 65.14
Earlier Year Adjustment 0.01 11.89
MAT Credit Availed (24.26) (19.93)
Profit for the year 234.70 179.22
"""


FALSE_MENTION_THEN_PRIMARY = """
Statement of Profit and Loss when the asset is derecognised or impaired.
This accounting policy applies prospectively and contains no statement table.

Statement of Profit & Loss for the year ended March 31,2025
(` in Lakhs)
Revenue from operations 1,000.00 900.00
Other income 10.00 9.00
Employee benefits expense 100.00 90.00
Finance costs 20.00 18.00
Depreciation and amortization expense 30.00 25.00
Other expenses 750.00 680.00
Total expenses 900.00 813.00
Profit before tax 110.00 96.00
Current tax 30.00 25.00
Profit for the year 80.00 71.00
"""


SIDE_BY_SIDE_PRIMARY = """
Standalone Balance Sheet                                      Standalone Statement of Profit and Loss
for the year ended March 31, 2025                             for the year ended March 31, 2025
(All amounts are in INR Millions, unless stated otherwise)
Total Assets 5,000.00 4,000.00                                Revenue from operations 2,000.00 1,800.00
Total Equity 3,000.00 2,500.00                                Other income 100.00 90.00
Non-current liabilities                                      Employee benefits expense 300.00 280.00
Borrowings 100.00 120.00                                     Finance costs 50.00 45.00
Deferred tax liabilities 999.00 888.00                        Deferred tax 10.00 9.00
Current liabilities                                          Depreciation and amortization expense 100.00 90.00
Borrowings 200.00 180.00                                     Other expenses 1,150.00 1,025.00
                                                               Total expenses 1,600.00 1,440.00
                                                               Profit before tax 450.00 405.00
                                                               Current tax 100.00 90.00
                                                               Profit for the year 340.00 306.00
"""


DECIMAL_NOTES_AND_LOSS_LABELS = """
Statement of Profit and Loss
For the year ended March 31, 2025
(H in Lakhs)
Revenue from operations 18.1 59,052.47 1,85,434.17
Other income 18.2 5,010.25 5,038.89
Employee benefits expense 20 10,000.00 9,000.00
Finance costs 21 1,525.32 1,400.00
Depreciation and amortization expense 22 2,000.00 1,900.00
Other expenses 23 71,044.07 1,75,103.06
Total expenses 84,569.39 1,87,403.06
Loss before tax (20,506.67) (6,929.99)
Tax expense 25
Current tax - -
Deferred tax (48.40) 1,960.91
Total tax expenses (48.40) 1,960.91
Loss for the year (20,458.27) (8,890.90)
"""


EXCEPTIONAL_LOSS_STATEMENT = """
Statement of Profit and Loss for the year ended March 31, 2025
Rs. in Lakhs
Revenue from operations 4,685.64 4,261.94
Other income 3,219.80 113.50
Employee benefits expense 3,646.59 3,037.02
Finance costs 23.05 27.74
Depreciation and amortization expense 141.19 143.49
Other expenses 938.73 1,072.22
Total expenses 4,749.55 4,280.47
V Profit before exceptional items and tax 3,155.89 94.97
VI Exceptional Items & Extraordinary items 239.24 -
VII Profit before tax (V-VI) 2,916.65 94.97
Current tax 501.28 80.76
Taxes of earlier years 0.07 2.50
Deferred tax (28.11) (7.36)
Profit for the year from continuing operations (VII-VIII) 2,443.40 19.07

Cash Flow Statement
Exceptional Items - Loss of disposal of building 147.68 -
"""


DISCONTINUED_OPERATIONS_STATEMENT = """
Standalone Statement of Profit and Loss for the year ended March 31, 2025
Rs. in Lakhs
Revenue from operations 2,900.93 3,796.57
Other income 943.44 764.17
Finance costs 429.71 396.90
Employee benefits expense 1,118.72 1,274.96
Depreciation and amortization expense 542.24 677.27
Other expenses 2,798.84 2,781.28
Total expenses 4,459.51 5,130.41
Loss before tax (615.14) (569.67)
Current tax (3.70) (7.52)
Deferred tax 84.34 (242.81)
Total tax expense 80.64 (250.33)
Loss after tax from continuing (695.78) (319.34)
Profit from discontinuing operations 19.59 (354.66)
Loss for the year (676.19) (674.00)
"""


OCR_VARIANT_STATEMENT = """
Balance Sheet as at 31 March 2025
(All Amounts in INR Lakhs unless otherwise stated)
Total Asset 42.39 46.56
Total Equity (165.74) (147.95)
Non-current liabilities
Financial liabilities
Borrowing 7 106.58 100.00

Statement of Profit and Loss for the period ended 31 March 2025
(All Amounts in INR Lakhs unless otherwise stated)
Revenue from operations - -
Other Income 11 0.07 0.51
Employee benefits expense 12 1.98 3.24
Finance costs 13 8.20 7.84
Depreciation and amortisation expense - -
Other expenses 14 8.63 8.23
Total expenses 18.82 19.31
Profit/(Loss) before exceptional items and tax (18.75) (18.80)
Exceptional items - -
Profit/(Loss) after exceptional items and before tax (18.75) (18.80)
Current tax - -
Deferred tax (0.96) 3.71
Profit/(Loss) for the period from continuing operations (17.79) (22.51)
"""


RELIANCE_ASSOCIATE_STATEMENT = """
Consolidated Balance Sheet as at 31st March, 2025
(C in crore)
Total Assets 19,50,121 18,77,207
Total Equity 10,09,626 9,25,788
Non-current liabilities
Financial Liabilities
Borrowings 2,36,899 2,03,352
Current liabilities
Financial Liabilities
Borrowings 1,10,631 1,21,086

Consolidated Statement of Profit and Loss
For the year ended 31st March, 2025
(C in crore)
Revenue from Operations 9,80,136 9,14,472
Other Income 17,978 16,057
Total Income 9,98,114 9,30,529
Cost of Materials Consumed 4,22,127 4,00,345
Purchase of Stock-in-Trade 2,22,686 1,89,881
Changes in Inventories of Finished Goods and Stock-in-Trade (15,124) (4,883)
Employee Benefits Expense 28,559 25,679
Finance Costs 24,269 23,118
Depreciation and Amortisation Expense 53,136 50,832
Other Expenses 1,41,001 1,27,809
Total Expenses 8,92,097 8,26,189
Profit Before Share of Profit / (Loss) of Associates / Joint Ventures and Tax 1,06,017 1,04,340
Current Tax 12,758 13,590
Deferred Tax 12,472 12,117
Profit After Tax 80,787 78,633
Share of Profit / (Loss) of Associates and Joint Ventures 522 387
Profit for the year 81,309 79,020
"""


HISTORICAL_FORMULA_AND_COMBINED_NOTE_STATEMENT = """
Statement of Profit and Loss for the year ended March 31, 2022
Amount in Lakhs
Particulars Note For the year ended March 31, 2022 For the year ended March 31, 2021
Revenue from operations 20 893.58 792.03
Other income 21 4.43 4.91
Total income 898.01 796.94
Employee benefits expense 24 16.48 6.70
Finance costs 25 & 29 0.35 0.01
Depreciation and amortization expense 4 2.07 0.00
Other expenses 26 819.07 641.35
Total expenses (2) 837.97 648.06
Loss/profit before tax expense (1-2) 60.03 148.88
Current Taxes (24.10) (24.85)
Deferred Taxes (0.22) (10.57)
Adjustment of tax relating to earlier periods (1.81) 20.27
Tax expenses 27 (26.13) (15.14)
Profit/Loss for the year (3-4) 33.90 133.73
"""


HISTORICAL_EXCEPTIONAL_GAIN_STATEMENT = """
Statement of Profit and Loss for the year ended March 31, 2023
` Crores (unless otherwise stated)
Revenue from operations 27 186.41 193.51
Other income 28 5.60 4.85
Total income 192.01 198.36
Employee benefits expenses 31 65.15 66.02
Finance costs 32 25.66 22.84
Depreciation and amortisation expenses 33 4.30 4.44
Other expenses 34 140.12 122.84
Total expenses 235.23 216.14
Profit / (Loss) before exceptional items and tax expense from continuing operations (43.22) (17.78)
Exceptional items 35 100.43 449.65
Profit / (Loss) before tax expense from continuing operations 57.21 431.87
Current tax Nil Nil
Adjustment of tax relating to earlier periods Nil Nil
Profit /(Loss) for the year from continuing operations 57.21 431.87
Profit / (Loss) for the year 57.21 431.87
"""


HISTORICAL_AFTER_TAX_ZERO_TAX_STATEMENT = """
Statement of Profit and Loss for the year ended 31st March, 2025
` Lakhs
Revenue from operations 29 20,331.09 19,909.45
Other income 30 1,509.19 980.20
Total income 21,840.28 20,889.65
Employee benefit expenses and payment to contractors 32 5,939.47 5,464.59
Finance costs 33 222.62 240.15
Depreciation and amortisation expenses 34 1,564.42 1,436.23
Other expenses 35 11,582.84 11,368.63
Total Expenses 19,309.35 18,509.36
Profit/(loss) before exceptional items and Tax 2,530.93 2,380.29
Exceptional items - Profit/(loss) (net) 36 81.56 -
Profit before Tax 2,612.49 2,380.29
Tax expense 37.11 - -
Profit after Tax (A + B) 2,612.49 2,380.29
"""


HISTORICAL_FINANCE_INCOME_STATEMENT = """
Consolidated statement of profit and loss for the year ended March 31, 2020
Rs in Lakhs
Revenue from operations 25 1,88,446 1,69,102
Other income 26 1,741 856
Finance income 27 5,498 4,849
Total income 1,95,685 1,74,807
Employee benefits expense 28 44,924 44,055
Finance costs 29 15,266 10,127
Depreciation and amortisation expense 30 11,974 10,264
Other expenses 31 1,29,879 1,09,529
Total expenses 1,86,777 1,73,975
Profit before tax 8,908 832
Current tax 24 451 632
Adjustment of tax relating to earlier years 24 (17) (45)
Deferred tax (credit) 24 (1,060) (274)
Total tax expenses (626) 851
Profit/(loss) after tax 9,534 (19)
Other comprehensive loss for the year, net (394) (34)
"""


HISTORICAL_MINORITY_INTEREST_STATEMENT = """
Consolidated Statement of Profit and Loss                                                                 Consolidated Cash Flow Statement
For the Year Ended 31st March, 2012                                                                       For the Year Ended 31st March, 2012
Rs. in lacs
Revenue from operations 22 10,119.11 9,046.32
Other Income 23 96.84 171.03
Total Revenue 10,215.95 9,217.35
Employee benefits expense 25 1,713.14 1,579.79
Finance Cost 26 4,265.75 2,689.34
Depreciation 12 911.70 879.40
Other expenses 27 5,220.62 4,656.10
Total expenses 12,111.21 9,804.63
                                                                                                         Deferred tax liability on extraordinary items 111.58 -
Profit/(Loss) before tax (1,662.94) (587.28)
Current tax - -
Deferred tax 32 (608.92) (191.49)
Income tax pertaining to earlier years written back (0.71) (74.95)
Profit/(Loss) after tax but before Minority Interest (1,053.31) (320.84)
Share of Minority Interest in Profit/(Loss) (5.36) (5.16)
Profit/(Loss) for the year available for majority shareholders (1,047.95) (315.68)
"""


HISTORICAL_MULTIPLE_PBT_AND_EARLIER_TAX_STATEMENT = """
Standalone Statement of Profit & Loss for the Year ended March 31, 2020
Amounts in INR
Revenue from Operations 25 25,50,10,212 34,75,48,486
Other Income 26 32,47,061 42,14,194
Total Revenue 25,82,57,273 35,17,62,680
Employee Benefits Expense 30 3,33,51,810 3,28,28,897
Finance Costs 31 1,04,33,185 1,00,02,938
Depreciation and Amortization Expense 3A & 3B 36,31,492 47,86,392
Other Expenses 32 22,49,14,267 30,34,05,675
Total Expenses 25,93,78,939 35,10,22,902
PROFIT BEFORE TAXATION 11,21,665 7,39,779
Extra-Ordinary Items - -
Profit before Tax -11,21,665 7,39,779
Current Tax - -
Deferred Tax Assets 1,92,620 -8,47,532
Prior Period tax 17,22,076 -
Net Profit/(Loss) for the Year -30,36,361 15,87,311
"""


HISTORICAL_ASSOCIATE_AFTER_TAX_STATEMENT = """
CONSOLIDATED STATEMENT OF PROFIT AND LOSS FOR THE YEAR ENDED 31st MARCH 2017
` In Crores
Revenue from operations 36 4,582.02 4,139.41
Other Income 37 39.23 75.47
Total Revenue 4,621.25 4,214.88
Employee Benefits Expense 42 278.52 259.40
Finance Costs 43 104.88 183.76
Depreciation and Amortization Expense 44 266.87 305.26
Other Expenses 45 3,218.95 2,803.54
Total Expenses 3,764.34 3,551.96
Profit Before Tax 856.91 662.92
Tax Expenses 28
Current Tax 188.21 149.84
Net Current tax expenses 188.21 126.00
Deferred Tax 17.73 25.01
Total Tax Expenses 202.70 131.66
Profit for the year before share of profit/(loss) of Associates 654.21 531.26
Add: Share of Profit/(Loss) of Associates 9.77 14.06
PROFIT FOR THE YEAR 663.98 545.32
"""


HISTORICAL_GROSS_NET_REVENUE_STATEMENT = """
PROFIT & LOSS ACCOUNT FOR THE YEAR ENDED 31ST MARCH, 2012
Amounts in INR
Revenue from operations (gross) 16 466,530,797 468,247,821
Less: Excise duty 26,502,335 18,637,128
Revenue from operations (net) 440,028,462 449,610,693
Other income 17 7,848,707 1,666,651
Total revenue (1+2) 447,877,169 451,277,344
Finance costs 20 215,218 4,539,528
Depreciation and amortisation expense 8 12,308,737 13,504,997
Other expenses 21 410,516,000 426,373,985
Total expenses 423,039,955 444,417,510
Profit / (Loss) before exceptional and 24,837,215 6,859,834
extraordinary items and tax (3 - 4)
Exceptional items 22.a -2,000,000 -395,222,088
Profit / (Loss) before tax (7 + 8) 25,525,499 (388,362,254)
"""


HISTORICAL_INCOMPLETE_PRIMARY_STATEMENT = """
STATEMENT OF PROFIT & LOSS ACCOUNT FOR THE YEAR ENDED 31ST MARCH , 2017
Amount in Rs
Revenue from Operation 13 1,483,505 (202,800)
Other Income - -
Total Revenue 1,483,505 (202,800)
Employee Benefits Expenses 14 - -
Finance Cost 15 - -
Depreciation And Amortised Expenses 8 - -
Other Expenses 16 707,085 733,289
Profit / (Loss) before Tax 776,420 (936,089)
Current Tax - -
Deferred Tax - -
Profit / (Loss) for the period from Continuing Operations 776,420 (936,089)
"""


HISTORICAL_TAX_HEADING_ZERO_STATEMENT = """
Statement of Profit and Loss for the year ended March 31, 2015
Amount in Lakhs
Revenue from operations 50,000.00 45,000.00
Other income 1,000.00 900.00
Total income 51,000.00 45,900.00
Finance costs 500.00 450.00
Other expenses 44,925.93 40,000.00
Total expenses 45,425.93 40,450.00
Profit before tax 5,574.07 5,450.00
Tax expense - -
Current tax
Income tax 1,235.00 1,200.00
Minimum alternative tax credit entitlement (100.00) (90.00)
Deferred tax 803.25 740.00
Profit for the year 3,635.82 3,600.00
"""


HISTORICAL_DROPPED_GROUPING_SEPARATOR_STATEMENT = """
Standalone Statement of Profit and Loss for the year ended 31st March, 2024
` in Lakh
Revenue from Operations 18                    58 08.63                      68 04.80
Other Income 19                               24 61.18                      12 88.77
Total Income                                  82 69.81                      80 93.57
Employee Benefits Expense 20                  16 75.32                      17 68.91
Finance Costs                                          -                            -
Other Expenses 21                             66 84.86                      71 09.89
Total Expenses                                66 84.86                      71 09.89
Profit before tax                             15 84.95                      17 75.67
Current Tax 23                                2 25.62                       3 39.78
Deferred Tax 23                               2 71.83                      (1 22.15)
Provision for Income Tax of earlier years        66.23                             -
Profit for the Year                           10 21.27                      15 58.04
"""


HISTORICAL_OCR_SPLIT_OPERATIONS_STATEMENT = """
STATEMENT OF PROFIT AND LOSS FOR THE YEAR ENDED 31ST MARCH 2017
Rs. in Lakhs
Revenue from opera ons
Total revenue from opera ons 2,700.30 2,397.34
Other income 88.27 13.50
Total Revenue 2,788.57 2,410.84
Employee benefits expense 156.81 138.16
Finance costs 243.01 264.66
Depreciation and amortisation expense 65.17 61.30
Other expenses 2,286.89 1,908.51
Total expenses 2,751.88 2,373.33
Profit before tax 36.69 37.51
Tax expense - -
Profit for the period 36.69 37.51
"""


def test_parse_amount_supports_indian_grouping_parentheses_and_nil():
    assert parse_amount("1,66,838.31") == pytest.approx(166838.31)
    assert parse_amount("(5,260.93)") == pytest.approx(-5260.93)
    assert parse_amount("Nil") == 0.0
    assert parse_amount("-") is None


def test_industrial_document_carries_scope_period_currency_and_scale():
    rows = extract_document(UMA_STATEMENTS)
    assert len(rows) == 1
    row = rows[0]
    assert row["statement_scope"] == "standalone"
    assert row["period_end"] == "2025-03-31"
    assert row["currency"] == "INR"
    assert row["unit_scale"] == 100_000
    assert row["unit_scale_source"] == "statement_header"
    assert row["industry_template"] == "industrial"


def test_industrial_profit_bridges_close_and_tax_sign_is_resolved():
    row = extract_document(UMA_STATEMENTS)[0]
    assert row["revenue_from_operations"] == pytest.approx(166838.31)
    assert row["total_expenses"] == pytest.approx(167465.73)
    assert row["finance_costs"] == pytest.approx(1181.12)
    assert row["operating_profit_after_da"] == pytest.approx(553.70)
    assert row["operating_profit_pbt_bridge"] == pytest.approx(553.70)
    assert row["operating_profit_closure_status"] == "pass"
    assert row["operating_profit_closure_ratio"] <= 0.01
    assert row["income_tax_expense"] == pytest.approx(24.16)
    assert row["tax_sign_convention"] == "subtract"
    assert row["tax_identity_status"] == "pass"


def test_balance_sheet_context_aggregates_current_and_noncurrent_borrowings():
    row = extract_document(UMA_STATEMENTS)[0]
    assert row["non_current_borrowings"] == pytest.approx(50.61)
    assert row["current_borrowings"] == pytest.approx(20770.49)
    assert row["total_borrowings"] == pytest.approx(20821.10)
    assert row["total_borrowings_null_reason"] is None
    assert row["debt_reconciliation_status"] == "components_only"


def test_financial_presentation_is_not_forced_through_industrial_formula():
    row = extract_document(FINANCIAL_STATEMENTS)[0]
    assert row["industry_template"] == "financial_services"
    assert row["statement_scope"] == "standalone"
    assert row["statement_scope_source"] == "inferred_single_primary"
    assert row["operating_profit_after_da"] is None
    assert row["operating_profit_null_reason"] == "wrong_template"
    assert row["screen_eligible"] is False


def test_financial_company_name_prevents_industrial_formula_even_with_generic_rows():
    row = extract_document(
        UMA_STATEMENTS, company_name="Abans Financial Services Limited"
    )[0]
    assert row["industry_template"] == "financial_services"
    assert row["industry_template_source"] == "company_name"
    assert row["operating_profit_closure_status"] == "not_applicable"


def test_prose_mentions_are_rejected_and_ampersand_heading_is_accepted():
    rows = extract_document(FALSE_MENTION_THEN_PRIMARY)
    assert len(rows) == 1
    assert rows[0]["period_end"] == "2025-03-31"
    assert rows[0]["statement_scope"] == "standalone"
    assert rows[0]["statement_scope_source"] == "inferred_single_primary"
    assert rows[0]["unit_scale"] == 100_000
    assert rows[0]["currency"] == "INR"


def test_adjacent_duplicate_profit_statement_subtitle_does_not_split_the_table():
    text = HISTORICAL_AFTER_TAX_ZERO_TAX_STATEMENT.replace(
        "Statement of Profit and Loss for the year ended 31st March, 2025",
        "Statement of Profit and Loss for the year ended 31st March, 2025\n"
        "Part II; Statement of Profit and Loss",
    )
    rows = extract_document(text)
    assert len(rows) == 1
    assert rows[0]["profit_before_tax"] == pytest.approx(2612.49)


def test_source_spaced_indian_grouping_and_multi_term_formula_keep_current_values():
    text = """
    Profit and Loss Statement for the year ended March 31, 2021
    (Amounts in Indian Rupees)
    Revenue from operations 11,36,56,606 10,83,78,094
    Other income 4,79,330 6,94,814
    Total revenue 11,41,35,936 10,90,72,908
    Employee benefits expense 2,54,51,291 3,39,28,563
    Finance costs 86,844 1,80,653
    Depreciation and amortization expense 41,35,569 15,10,199
    Other expenses 8,30,10,574 7,13,96,345
    Total expenses 11,26,84,277 10,70,15,760
    Profit before exceptional items and tax 14,51,659 20,57,147
    Exceptional items - -
    Profit before tax (3+4-5) 14,51 ,659 20,57, 147
    Current tax 6,54,565 5,19,567
    Deferred tax liabilities/(assets) (2,77, 138) 84,944
    Profit for the period 10,74,232 14,52,636
    """

    rows = extract_document(text)
    assert len(rows) == 1
    assert rows[0]["profit_before_tax"] == pytest.approx(1_451_659)
    assert rows[0]["deferred_tax"] == pytest.approx(-277_138)
    assert rows[0]["income_tax_expense"] == pytest.approx(377_427)
    assert rows[0]["operating_profit_closure_status"] == "pass"
    assert rows[0]["tax_identity_status"] == "pass"


def test_source_bare_current_tax_row_is_scoped_by_provision_heading():
    text = """
    PROFIT AND LOSS ACCOUNT FOR THE YEAR ENDED 31ST MARCH 2011
    (Rs. in 000's)
    Net Sales 817,317 560,897
    Other Income 29,895 22,165
    Total Income 847,212 583,062
    Interest and Finance Charges 6 22
    Total Expenses 611,636 437,597
    Profit before tax 235,576 145,465
    Less: Provision for Taxation
      Current 75,500 46,827
      Deferred 567 405
    Profit after tax 159,509 98,233
    """

    rows = extract_document(text)
    assert len(rows) == 1
    assert rows[0]["current_tax"] == pytest.approx(75_500)
    assert rows[0]["deferred_tax"] == pytest.approx(567)
    assert rows[0]["income_tax_expense"] == pytest.approx(76_067)
    assert rows[0]["tax_identity_status"] == "pass"


def test_source_dash_tax_value_does_not_promote_note_number():
    text = """
    STATEMENT OF PROFIT & LOSS FOR THE PERIOD ENDED ON 31st March, 2022
    (Amount in Rs. Lakhs)
    Particulars Note No. 2021-22 2020-21
    Revenue from operations 26 926.79 823.47
    Other Income 27 42.76 57.19
    Total Revenue 969.55 880.66
    Finance costs 31 50.09 40.04
    Total Expenses 1,066.04 1,070.15
    Profit before exceptional items & tax (III-IV) (96.48) (189.49)
    Exceptional Items 33 (29.27) (7.05)
    Profit before tax (V-VI) (67.21) (182.44)
    Tax expense:
    (1) Current tax 34 -
    (2) Deferred tax - -
    Profit/(Loss) for the period from continuing operations (VII-VIII) (67.21) (183.88)
    """

    rows = extract_document(text)
    assert len(rows) == 1
    assert rows[0]["current_tax"] == pytest.approx(0)
    assert rows[0]["income_tax_expense"] == pytest.approx(0)
    assert rows[0]["operating_profit_closure_status"] == "pass"
    assert rows[0]["tax_identity_status"] == "pass"


def test_source_enumerated_tax_adjustments_keep_value_and_presentation_sign():
    text = """
    CONSOLIDATED STATEMENT OF PROFIT & LOSS FOR THE YEAR ENDED 31ST MARCH 2022
    (Amount in Rs. Lakhs)
    Revenue from operations 926.79 823.47
    Other income 47.15 59.92
    Total revenue 973.94 883.39
    Finance costs 50.09 40.04
    Total expenses 1,067.44 1,040.80
    Profit before exceptional items and tax (93.50) (157.41)
    Exceptional items (44.92) (45.73)
    Profit before tax (48.58) (111.67)
    Tax expense:
    (1) Current tax 1.26 7.00
    (2) Deferred tax (0.14) 0.33
    (3) Short/(excess) provision reversal (0.44) -
    (4) Earlier Year Tax - 1.44
    (5) MAT Credit 1.73 -
    Profit/(Loss) for the period from Continuing Operations (50.99) (120.44)
    """

    rows = extract_document(text)
    assert len(rows) == 1
    assert rows[0]["tax_provision_adjustment"] == pytest.approx(-0.44)
    assert rows[0]["mat_credit"] == pytest.approx(1.73)
    assert rows[0]["income_tax_expense"] == pytest.approx(2.41)
    assert rows[0]["tax_identity_status"] == "pass"


def test_source_mat_credit_continuation_uses_reported_net_tax_subtotal():
    text = """
    Statement of Profit and Loss for the year ended 31 March 2012
    (` in Lacs)
    Note No. 31 March 2012 31 March 2011
    Revenue from Operations 19 164365.82 121741.19
    Other Income 20 590.73 467.42
    Total Revenue 164956.55 122208.61
    Finance Costs 25 6078.21 4426.74
    Total Expenditure 158464.61 119122.28
    Profit before Tax 6491.94 3086.33
    Tax Expense
    Current Tax 1298.89 620.00
    Less : Mat Credit Entitlement 1033.79 620.00

    265.10 -
    Deferred Tax 885.87 1150.97 577.42 577.42
    Profit for the Year 5340.97 2508.91
    """

    rows = extract_document(text)
    assert len(rows) == 1
    assert rows[0]["current_tax_net"] == pytest.approx(265.10)
    assert rows[0]["deferred_tax"] == pytest.approx(885.87)
    assert rows[0]["income_tax_expense"] == pytest.approx(1150.97)
    assert rows[0]["tax_identity_status"] == "pass"


def test_source_ocr_profitkloss_rows_recover_primary_statement():
    text = """
    PROFIT AND LOSS STATEMENT FOR THE YEAR ENDED 31ST MARCH 2014
    Revenue from operations (net) 12 49195346 50142174
    Other income 13 95243 249549
    TOTAL REVENUE 49290589 50391723
    Finance costs 17 4081426 3781996
    TOTAL EXPENSES 56290026 50349197
    PROFIT/(LOSS) BEFORE EXCEPTIONAL IT EMS —|
    AND TAX (4V) -6999437 42526
    EXCEPTIONAL ITEMS 0 0
    PROFITKLOSS) BEFORE TAX (V-V) -6990437 42526
    TAX EXPENSE
    (1)Current Tax 0 0
    (2)Deferred Tax 0 0
    PROFITHLOSS) FOR THE YEAR (VIII-IX) -6999437 42526
    """

    rows = extract_document(text)
    assert len(rows) == 1
    assert rows[0]["profit_before_tax"] == pytest.approx(-6990437)
    assert rows[0]["profit_for_period"] == pytest.approx(-6999437)
    assert rows[0]["operating_profit_closure_status"] == "pass"
    assert rows[0]["tax_identity_status"] == "pass"


def test_source_trading_profit_heading_is_primary_statement():
    text = """
    Trading, Profit & Loss Account for the year ended 31st March, 2010
    Income
    Sales 8 0 42819394
    Other Income 9 343697 3540249
    TOTAL 343697 46359643
    Expenditure
    Financial Expenses 12 0 1400
    TOTAL 3276537 43837716
    Net Profit Before Tax -2932840 2521927
    Provision for Tax 0 310000
    Provision for Deferred Tax -367300 520300
    NET PROFITHLOSS FOR THE YEAR -3300140 2713227
    """

    rows = extract_document(text)
    assert len(rows) == 1
    assert rows[0]["period_end"] == "2010-03-31"
    assert rows[0]["profit_before_tax"] == pytest.approx(-2932840)
    assert rows[0]["operating_profit_closure_status"] == "pass"
    assert rows[0]["tax_identity_status"] == "pass"


def test_source_statement_without_of_and_wrapped_continuing_profit():
    text = """
    STATEMENT PROFIT AND LOSS FOR THE YEAR ENDED 31st MARCH, 2016
    Amount in Rupees
    Revenue from Operations 500,000 600,000
    Total Revenue 500,000 600,000
    Finance costs - -
    Total Expenses 2,050,762 2,150,668
    Profit / (Loss) before Tax (1,550,762) (1,550,668)
    Tax Expense:
    Current Tax - -
    Deferred Tax - -
    Profit / (Loss) for the Period fram Continuing
    Operations (1,550,762) (1,550,668)
    """

    rows = extract_document(text)
    assert len(rows) == 1
    assert rows[0]["profit_from_continuing_operations"] == pytest.approx(-1550762)
    assert rows[0]["tax_identity_status"] == "pass"


def test_source_spaced_tax_and_roman_value_continuation():
    text = """
    PROFIT AND LOSS STATEMENT FOR THE YEAR ENDED ON 31st MARCH 2014
    Revenue from operations - -
    Other Income 165,250 212,500
    Total Revenue 165,250 212,500
    Depreciation and amortisation expenses 459,430 494,109
    Other Expenditure 953,207 976,903
    Total Expenses 1,412,637 1,471,012
    Profit (+) / Loss Before T ax (VI-VII)

    VIII (1,247,387) (1,258,512)
    IX Tax Expenses - -
    X Profit (+) / Loss (-) for the period (VIII-IX) (1,247,387) (1,258,512)
    """

    rows = extract_document(text)
    assert len(rows) == 1
    assert rows[0]["profit_before_tax"] == pytest.approx(-1247387)
    assert rows[0]["operating_profit_closure_status"] == "pass"
    assert rows[0]["tax_identity_status"] == "pass"


def test_source_deferred_tax_followed_by_net_profit_subtotal_closes_tax():
    text = """
    Trading, Profit & Loss Account for the year ended 31st March, 2010
    Income
    Sales 0 42,819,394
    Other Income 343,697 3,540,249
    Expenditure
    Administrative & Other Expenses 2,576,916 1,911,237
    Financial Expenses 0 1,400
    Depreciation 699,620 699,620
    Net Profit Before Tax -2,932,840 2,521,927
    Provision for Tax 0 -310,000
    Provision for Deferred Tax -367,300 520,300
    -3,300,140 2,713,227
    Add/Less : Profit / Loss of Previous Year -4,819,614 -7,236,314
    """

    rows = extract_document(text)
    assert len(rows) == 1
    assert rows[0]["profit_after_tax"] == pytest.approx(-3300140)
    assert rows[0]["operating_profit_closure_status"] == "pass"
    assert rows[0]["tax_identity_status"] == "pass"


def test_source_pre_extraordinary_subtotal_closes_operating_bridge():
    text = """
    STATEMENT PROFIT AND LOSS FOR THE YEAR ENDED 31st MARCH, 2016
    Amount in Rupees
    Revenue from Operations 500,000 600,000
    Total Revenue 500,000 600,000
    Empicyee Senefit Expense 60,000 115,000
    Depreciation 962,428 1,295,944
    Other Expenses 1,028,334 639,724
    Total Expenses 2,050,762 2,050,668
    Profit / (Loss) before Extraordinary Items and Tax (1,550,762) (1,550,668)
    Extraordinary items (1,722,996) -
    Profit / (Loss) before Tax (3,273,758) (1,550,668)
    Tax Expense - -
    Profit / (Loss) for the Period (3,273,758) (1,550,668)
    """

    rows = extract_document(text)
    assert len(rows) == 1
    assert rows[0]["profit_before_exceptional_items_and_tax"] == pytest.approx(-1550762)
    assert rows[0]["operating_profit_closure_status"] == "pass"
    assert rows[0]["tax_identity_status"] == "pass"


def test_source_otter_expenses_preserves_an_ocr_primary_statement():
    text = """
    STATEMENT OF PROFIT & LOSS ACCOUNT FOR THE PERIOD ENDED ON 31ST MARCH 2014
    Revenue from operations (Net) 45,828,018 1,605,803
    Other Income 0 1,148,554
    TOTAL REVENUE 45,828,018 2,754,357
    Otter expenses 545,850 360,067
    (Loss)/Profit before tax (480,746) (106,920)
    Tax Expenses - -
    (Loss)/Profit for the year from continuing operations (480,746) (106,920)
    """

    rows = extract_document(text)
    assert len(rows) == 1
    assert rows[0]["other_expenses"] == pytest.approx(545850)
    assert rows[0]["profit_before_tax"] == pytest.approx(-480746)
    assert rows[0]["tax_identity_status"] == "pass"


def test_source_compensation_income_and_ocr_loss_braces_recover_tax_identity():
    text = """
    PROFIT & LOSS ACCOUNT FOR THE YEAR ENDED 31ST MARCH, 2010
    INCOME
    Compensation/Demages Received 9,471,150.00 38,727,804.00
    TOTAL 9,471,150.00 38,727,804.00
    EXPENDITURE
    Depreciation 1,875,057.31 1,399,466.00
    SUB TOTAL 5,817,881.07 6,648,440.01
    Profit/Loss¢-) Before Tax 3,653,268.93 33,079,363.99
    Less Provision for Taxation & FBT 550,000.00 3,800,000.00
    Profit/Loss{-) atter Tax 3,103,268.93 29,179,363.99
    Profit & Loss Account balance 46,786,587.73 43,830,653.80
    TOTAL 140,002,000 140,002,000
    """

    rows = extract_document(text)
    assert len(rows) == 1
    assert rows[0]["other_income"] == pytest.approx(9471150)
    assert rows[0]["total_expenses"] == pytest.approx(5817881.07)
    assert rows[0]["profit_before_tax"] == pytest.approx(3653268.93)
    assert rows[0]["profit_after_tax"] == pytest.approx(3103268.93)
    assert rows[0]["tax_identity_status"] == "pass"


def test_source_ocr_mislabeled_balance_heading_requires_full_profit_anchors():
    text = """
    Balance Sheet As at 31 March 2012
    Rs. In Lacs
    Notes 31-Mar-12
    Revenue from operations (net) 17 148.24 122.60
    Other income 18 13.46 8.40
    Total revenue 161.70 131.00
    Cost of raw materials consumed 19 50.58 50.89
    Changes in inventories 20 28.50 4.83
    Employee benefits expense 21 8.80 13.34
    Other expenses 22 51.84 60.94
    Depreciation and amortization expense 23 12.92 11.66
    Finance costs 24 0.18 0.14
    Profit/(loss) before tax 8.88 0.20
    Current tax 0.00 2.03
    Deferred tax (2.74) (1.95)
    Profit/(loss) for the year 11.62 0.12
    """

    rows = extract_document(text)
    assert len(rows) == 1
    assert rows[0]["period_end"] == "2012-03-31"
    assert rows[0]["profit_before_tax"] == pytest.approx(8.88)
    assert rows[0]["tax_identity_status"] == "pass"


def test_source_ocr_deffered_tax_spelling_closes_identity():
    text = """
    STATEMENT OF PROFIT AND LOSS FOR THE YEAR ENDED 31st March, 2021
    Amount in Rupees
    Revenue From Operations - 340
    Other Income 42,264 121,512
    Total Income 42,264 121,852
    Finance costs 267 1,315
    Total expenses 36,052 118,599
    Profit/(loss) before tax 6,089 3,253
    Current tax 1,206 878
    Deffered tax 1,342 171
    Profit/(loss) for the period 3,541 2,204
    """

    rows = extract_document(text)
    assert len(rows) == 1
    assert rows[0]["deferred_tax"] == pytest.approx(1342)
    assert rows[0]["income_tax_expense"] == pytest.approx(2548)
    assert rows[0]["tax_identity_status"] == "pass"


def test_source_wrapped_preexceptional_subtotal_is_direct_pbt_before_tax_rows():
    text = """
    PROFIT & LOSS STATEMENT FOR THE PERIOD
    ENDED ON 31ST MARCH, 2015
    Revenue from operations 58,954,368 7,571,800
    Other income 5,009,464 5,926,565
    Total revenue 63,963,832 13,498,366
    Finance costs 105,751 143,063
    Total expenses 60,556,272 10,731,209
    Profit/(Loss) before
    exceptional and 3,407,560 2,767,157
    extraordinary items and tax
    Less: Tax Expense
    (a) Current tax expense for current year 610,916 490,474
    (b) Previous Years Tax 67,239 -
    (c) (Less) MAT Credit 412,270 (212,882)
    (d) Deferred tax (45,131) (23,286)
    Profit After Tax 2,362,267 2,512,851
    """

    rows = extract_document(text)
    assert len(rows) == 1
    assert rows[0]["profit_before_tax"] == pytest.approx(3407560)
    assert rows[0]["operating_profit_closure_status"] == "pass"
    assert rows[0]["tax_identity_status"] == "pass"


def test_source_other_gain_closes_bridge_and_cash_flow_does_not_contaminate_pl():
    text = """
    CONSOLIDATED STATEMENT OF PROFIT AND LOSS
    for the year ended March 31, 2018
    (All figures in INR million)
    Revenue from operations 24 58,333.73 43,871.97
    Other income 25 147.53 106.89
    Other gain/(loss) 26 217.91 (20.15)
    Total Income 58,699.17 43,958.71
    Finance costs 30 924.83 766.90
    Total expenses 56,698.67 42,793.44
    Profit before tax and exceptional items (1-2) 2,000.50 1,165.27
    Share of profit/(loss) of associates / joint ventures 40 (117.86) (60.46)
    Exceptional items 29 8.33 -
    Profit before tax (3+4-5) 1,874.31 1,104.81
    Current tax 597.97 349.56
    Deferred tax (353.94) (341.85)
    Total tax expense 244.03 7.71
    Profit for the year 1,630.28 1,097.10

    CONSOLIDATED STATEMENT OF CASH FLOWS
    Net profit before tax 1,874.31 1,104.81
    Finance income (42.51) (30.00)
    """

    rows = extract_document(text)
    assert len(rows) == 1
    assert rows[0]["profit_before_tax"] == pytest.approx(1_874.31)
    assert rows[0]["other_gain_loss"] == pytest.approx(217.91)
    assert rows[0].get("finance_income") is None
    assert rows[0]["operating_profit_after_da"] == pytest.approx(2_559.89)
    assert rows[0]["operating_profit_pbt_bridge"] == pytest.approx(2_559.89)
    assert rows[0]["operating_profit_closure_status"] == "pass"
    assert rows[0]["tax_identity_status"] == "pass"


def test_strongest_primary_statement_wins_across_spurious_periods_in_one_filing():
    text = """
    8. Statement of Profit & Loss
    For the period ended 29 September 2018
    Revenue from operations 16,973.86 11,279.09
    Other income 23.86 130.70
    Total revenue 16,997.72 11,409.79
    Finance cost 175.59 113.99
    Total expenses 15,727.54 11,192.91
    Profit before tax 1,270.17 216.88
    Tax expenses 466.37 79.27
    Profit after tax 803.80 137.61

    STATEMENT OF PROFIT AND LOSS FOR THE YEAR ENDED MARCH 31, 2018
    (Amounts in Indian Rupees)
    Revenue from operations 1,69,73,85,606 1,12,79,08,850
    Other income 23,86,170 1,30,70,135
    Total income 1,69,97,71,776 1,14,09,78,985
    Employee benefits expense 1,48,37,380 1,20,00,000
    Finance costs 1,75,58,808 1,13,99,586
    Depreciation and amortization expense 49,73,721 40,00,000
    Other expenses 14,52,24,466 10,18,91,356
    Total expenses 1,57,27,54,375 1,11,92,90,942
    Profit before tax 12,70,17,401 2,16,88,042
    Total tax expense 4,66,37,460 79,27,014
    Profit/(loss) for the years 8,03,79,941 1,37,61,028
    """

    rows = extract_document(text)
    assert len(rows) == 1
    assert rows[0]["period_end"] == "2018-03-31"
    assert rows[0]["profit_before_tax"] == pytest.approx(127_017_401)
    assert rows[0]["profit_for_period"] == pytest.approx(80_379_941)
    assert rows[0]["tax_identity_status"] == "pass"


def test_legacy_sales_interest_and_depreciation_labels_form_primary_candidate():
    text = """
    BALANCE SHEET AS AT 31ST MARCH, 2011
    (Rs. in Lacs)
    Total Assets 17,283.69 18,942.52
    Total Equity 11,809.04 12,721.80
    Loan Funds
    Secured Loans 4,844.74 5,491.66
    Unsecured Loans 253.70 -
    Sub-Total 5,098.44 5,491.66
    Deferred Tax Liability 376.21 729.06

    PROFIT & LOSS ACCOUNT FOR THE YEAR ENDED 31ST MARCH, 2011
    (Rs. in Lacs)
    Sales 52,062.39 52,451.97
    Net Sales 51,204.72 51,960.53
    Other Income 175.54 156.33
    Materials Consumed & Purchase of Goods 43,600.89 47,746.34
    Interest 672.56 608.86
    Depreciation 387.49 427.19
    PROFIT / (LOSS) BEFORE TAX (1,265.33) (983.01)
    Deferred Tax (352.85) (378.68)
    Earlier Year: Income Tax 0.28 13.94
    PROFIT / (LOSS) AFTER TAX (912.76) (618.27)
    """
    rows = extract_document(text)
    assert len(rows) == 1
    assert rows[0]["revenue_from_operations"] == pytest.approx(51204.72)
    assert rows[0]["finance_costs"] == pytest.approx(672.56)
    assert rows[0]["depreciation_amortization"] == pytest.approx(387.49)
    assert rows[0]["tax_identity_status"] == "pass"
    assert rows[0]["total_borrowings"] == pytest.approx(5098.44)


def test_numeric_legacy_loan_funds_section_prefix_retains_components():
    text = """
    BALANCE SHEET AS AT 31ST MARCH, 2010
    (Amounts in Indian Rupees)
    2. LOAN FUNDS
    (a) Secured Loan 3 6,132,598.79 5,646,580.66
    (b) Unsecured Loan 4 - 450,000.00
    TOTAL ASSETS 39,837,520.01 38,922,601.11

    PROFIT & LOSS ACCOUNT FOR THE YEAR ENDED 31ST MARCH, 2010
    (Amounts in Indian Rupees)
    Sales 10,000.00 9,000.00
    Other Income 100.00 90.00
    Total Income 10,100.00 9,090.00
    Total Expenses 9,000.00 8,100.00
    PROFIT BEFORE TAX 1,100.00 990.00
    Current Tax 300.00 270.00
    PROFIT AFTER TAX 800.00 720.00
    """

    row = extract_document(text)[0]

    assert row["total_borrowings"] == pytest.approx(6_132_598.79)
    assert row["debt_reconciliation_status"] == "reported_total_only"


def test_legacy_component_and_subtotal_cells_do_not_concatenate_amounts():
    text = """
    BALANCE SHEET AS AT 31ST MARCH, 2011
    (Amounts in Indian Rupees)
    2. Loan Funds
    a) Secured Loans 3    108,750,901                  81,349,137
    b) Unsecured Loans 4     45,400,000 154,150,901      23,400,000 104,749,137
    TOTAL ASSETS 552,618,862 423,871,260

    PROFIT & LOSS ACCOUNT FOR THE YEAR ENDED 31ST MARCH, 2011
    (Amounts in Indian Rupees)
    Sales 10,000.00 9,000.00
    Other Income 100.00 90.00
    Total Income 10,100.00 9,090.00
    Total Expenses 9,000.00 8,100.00
    PROFIT BEFORE TAX 1,100.00 990.00
    Current Tax 300.00 270.00
    PROFIT AFTER TAX 800.00 720.00
    """

    row = extract_document(text)[0]

    assert row["total_borrowings"] == pytest.approx(154_150_901)


def test_legacy_prort_heading_and_two_digit_statement_dates_are_recovered():
    text = """
    ANNUAL REPORT 2009-2010
    BALANCE SHEET
    SCHEDULE AS AT AS AT
    31-Mar-10 31-Mar-09
    RUPEES RUPEES
    LOAN FUNDS
    Secured Loans 749,844,212 973,915,456
    Unsecured Loans 571,788,342 620,355,265
    TOTAL ASSETS 3,520,217,238 3,632,011,260

    AUDITED PRORT & LOSS ACCOUNT
    YEAR ENDED YEAR ENDED
    SCHEDULE 31-Mar-10 31-Mar-09
    RUPEES RUPEES
    Sales 1,021,749,192 1,446,469,842
    Other Income 5,232,759 61,591,486
    Total Income 1,026,981,951 1,508,061,328
    Interest & finance charges 62,362,487 29,101,681
    Depreciation 135,996,765 136,026,262
    Other Expenses 728,622,699 1,100,000,000
    Total Expenses 926,981,951 1,265,127,943
    PROFIT BEFORE TAX 100,000,000 242,933,385
    Current Tax 30,000,000 72,880,015
    PROFIT AFTER TAX 70,000,000 170,053,370
    """

    row = extract_document(text)[0]

    assert row["period_end"] == "2010-03-31"
    assert row["total_borrowings"] == pytest.approx(1_321_632_554)
    assert row["profit_before_tax"] == pytest.approx(100_000_000)


def test_trading_profit_and_loss_heading_is_accepted():
    text = """
    BALANCE SHEET AS AT 31ST MARCH 2016
    (Rupees)
    Total Assets 31,700,080 57,069,318
    Current liabilities
    Short-Term Borrowing 1,390,000 2,500,000

    STATEMENT OF TRADING & PROFIT AND LOSS FOR THE PERIOD ENDED ON 31ST MARCH 2016
    (Rupees)
    Revenue from operations 182,429,889 105,665,148
    Other Income - 3,663
    Employee benefit expenses 738,640 911,240
    Depreciation & amortise expenses 12,622 21,037
    Other administrative expenses 1,936,003 1,194,343
    Total expenses 181,921,322 105,175,535
    Profit before tax (| - II) 508,567 493,276
    Current Tax 158,433 152,612
    Deferred tax 154 (86)
    Net Profit after tax 349,980 340,750
    """
    rows = extract_document(text)
    assert len(rows) == 1
    assert rows[0]["profit_before_tax"] == pytest.approx(508567.0)
    assert rows[0]["tax_identity_status"] == "pass"


def test_side_by_side_profit_heading_keeps_period_before_column_crop():
    text = """
    Profit and Loss Account for the year ended March 31, 2011Cash Flow Statement for the year ended March 31, 2011
    Income from Operations 1,830,089,069 1,585,712,975
    Other Income 84,936,164 83,988,842
    Employee Costs 86,371,008 64,408,357
    Depreciation / Amortisation 142,189,561 149,821,694
    Interest 14,365,124 13,427,245
    Profit before Taxation 857,122,410 718,429,842
    Current Tax 175,000,000 130,000,000
    Profit after Taxation 682,122,410 588,429,842
    """
    rows = extract_document(text)
    assert len(rows) == 1
    assert rows[0]["period_end"] == "2011-03-31"
    assert rows[0]["revenue_from_operations"] == pytest.approx(1830089069.0)


def test_annual_report_page_header_recovers_current_fiscal_year_from_bad_ocr_date():
    text = """
    Balance Sheet as at March 31, 2011
    (Amount in Rupees)
    Total Assets 1,080,538,432 730,068,620
    Total Equity 214,477,215 410,046,304

    Annual Report 2010 -11 Chapter: Profit and Loss account| 46
    Profit and Loss account (Amount in Rupees)
    Particulars Schedule March 3° 2022 March 31 2010
    Sales & Other Income 1,019,242,030 751,813,481
    Financial Expenses 42,963,309 23,186,225
    Earlier Year Income Tax 2,858,898 357,954
    Profit before Taxation 47,377,482 39,651,698
    Provision for Taxation 7,800,000 5,040,000
    Provision for Deferred Tax 6,096,573 9,798,234
    Profit for the Year 33,480,909 24,813,464
    """
    rows = extract_document(text)
    assert len(rows) == 1
    assert rows[0]["period_end"] == "2011-03-31"
    assert rows[0]["profit_before_tax"] == pytest.approx(47377482.0)
    assert rows[0]["tax_identity_status"] == "pass"


def test_side_by_side_primary_statements_are_detected_and_paired():
    rows = extract_document(SIDE_BY_SIDE_PRIMARY)
    assert len(rows) == 1
    row = rows[0]
    assert row["statement_scope"] == "standalone"
    assert row["revenue_from_operations"] == pytest.approx(2000.0)
    assert row["current_borrowings"] == pytest.approx(200.0)
    assert row["non_current_borrowings"] == pytest.approx(100.0)
    assert row["deferred_tax"] == pytest.approx(10.0)
    assert row["tax_identity_status"] == "pass"


def test_decimal_note_numbers_and_loss_labels_are_not_values():
    row = extract_document(DECIMAL_NOTES_AND_LOSS_LABELS)[0]
    assert row["revenue_from_operations"] == pytest.approx(59052.47)
    assert row["profit_before_tax"] == pytest.approx(-20506.67)
    assert row["income_tax_expense"] == pytest.approx(-48.40)
    assert row["profit_for_period"] == pytest.approx(-20458.27)
    assert row["tax_identity_status"] == "pass"


def test_exceptional_loss_closes_bridge_and_cash_flow_does_not_override_it():
    row = extract_document(EXCEPTIONAL_LOSS_STATEMENT)[0]
    assert row["exceptional_items"] == pytest.approx(239.24)
    assert row["operating_profit_after_da"] == pytest.approx(-40.86)
    assert row["operating_profit_pbt_bridge"] == pytest.approx(-40.86)
    assert row["operating_profit_closure_status"] == "pass"


def test_tax_identity_uses_continuing_profit_when_discontinued_result_is_separate():
    row = extract_document(DISCONTINUED_OPERATIONS_STATEMENT)[0]
    assert row["profit_from_continuing_operations"] == pytest.approx(-695.78)
    assert row["profit_for_period"] == pytest.approx(-676.19)
    assert row["tax_identity_profit_basis"] == "continuing_operations"
    assert row["tax_identity_status"] == "pass"


def test_ocr_variant_after_exceptional_items_still_identifies_pbt():
    row = extract_document(OCR_VARIANT_STATEMENT)[0]
    assert row["profit_before_tax"] == pytest.approx(-18.75)
    assert row["operating_profit_closure_status"] == "pass"
    assert row["tax_identity_status"] == "pass"


def test_ocr_missing_zero_dashes_derives_revenue_from_total_and_other_income():
    text = OCR_VARIANT_STATEMENT.replace(
        "Revenue from operations - -\nOther Income 11 0.07 0.51",
        "Revenue from operations\nOther Income 11 0.07 0.51\nTotal Income 0.07 0.51",
    )
    row = extract_document(text)[0]
    assert row["revenue_from_operations"] == 0.0
    assert row["revenue_from_operations_source"] == (
        "derived_total_income_less_other_income"
    )
    assert row["operating_profit_closure_status"] == "pass"
    assert row["screen_eligible"] is True


def test_pbt_before_associate_share_uses_profit_after_tax_for_both_closures():
    row = extract_document(RELIANCE_ASSOCIATE_STATEMENT)[0]
    assert row["profit_before_tax"] == pytest.approx(106017.0)
    assert row["profit_before_tax_basis"] == "before_associate_share"
    assert row["profit_after_tax"] == pytest.approx(80787.0)
    assert row["operating_profit_after_da"] == pytest.approx(112308.0)
    assert row["operating_profit_pbt_bridge"] == pytest.approx(112308.0)
    assert row["operating_profit_closure_status"] == "pass"
    assert row["tax_identity_profit_basis"] == "profit_after_tax_before_associate"
    assert row["tax_identity_status"] == "pass"
    assert row["screen_eligible"] is True


def test_historical_formula_tokens_and_combined_notes_are_not_statement_values():
    row = extract_document(HISTORICAL_FORMULA_AND_COMBINED_NOTE_STATEMENT)[0]
    assert row["currency"] == "INR"
    assert row["finance_costs"] == pytest.approx(0.35)
    assert row["profit_before_tax"] == pytest.approx(60.03)
    assert row["profit_for_period"] == pytest.approx(33.90)
    assert row["income_tax_expense"] == pytest.approx(-26.13)
    assert row["operating_profit_closure_status"] == "pass"
    assert row["tax_identity_status"] == "pass"


def test_historical_pre_exceptional_subtotal_resolves_exceptional_gain_direction():
    row = extract_document(HISTORICAL_EXCEPTIONAL_GAIN_STATEMENT)[0]
    assert row["profit_before_exceptional_items_and_tax"] == pytest.approx(-43.22)
    assert row["exceptional_items"] == pytest.approx(100.43)
    assert row["operating_profit_after_da"] == pytest.approx(-23.16)
    assert row["operating_profit_pbt_bridge"] == pytest.approx(-23.16)
    assert row["operating_profit_closure_status"] == "pass"


def test_historical_zero_tax_and_profit_after_tax_close_standard_tax_identity():
    row = extract_document(HISTORICAL_AFTER_TAX_ZERO_TAX_STATEMENT)[0]
    assert row["income_tax_expense"] == 0.0
    assert row["profit_after_tax"] == pytest.approx(2612.49)
    assert row["operating_profit_closure_status"] == "pass"
    assert row["tax_identity_profit_basis"] == "profit_after_tax"
    assert row["tax_identity_status"] == "pass"


def test_historical_finance_income_is_removed_by_the_operating_profit_bridge():
    row = extract_document(HISTORICAL_FINANCE_INCOME_STATEMENT)[0]
    assert row["finance_income"] == pytest.approx(5498.0)
    assert row["operating_profit_after_da"] == pytest.approx(16935.0)
    assert row["operating_profit_pbt_bridge"] == pytest.approx(16935.0)
    assert row["operating_profit_closure_status"] == "pass"
    assert row["tax_identity_status"] == "pass"


def test_tax_identity_prefers_after_tax_subtotal_before_minority_interest():
    row = extract_document(HISTORICAL_MINORITY_INTEREST_STATEMENT)[0]
    assert row["earlier_year_tax"] == pytest.approx(-0.71)
    assert row["profit_after_tax"] == pytest.approx(-1053.31)
    assert row["profit_for_period"] == pytest.approx(-1047.95)
    assert row["tax_identity_profit_basis"] == "profit_after_tax"
    assert row["tax_identity_status"] == "pass"


def test_historical_final_pbt_and_earlier_year_tax_override_intermediate_rows():
    row = extract_document(HISTORICAL_MULTIPLE_PBT_AND_EARLIER_TAX_STATEMENT)[0]
    assert row["profit_before_tax"] == pytest.approx(-1121665.0)
    assert row["earlier_year_tax"] == pytest.approx(1722076.0)
    assert row["income_tax_expense"] == pytest.approx(1914696.0)
    assert row["tax_identity_status"] == "pass"


def test_associate_share_after_tax_does_not_change_the_operating_profit_bridge():
    row = extract_document(HISTORICAL_ASSOCIATE_AFTER_TAX_STATEMENT)[0]
    assert row["income_tax_expense"] == pytest.approx(202.70)
    assert row["operating_profit_after_da"] == pytest.approx(922.56)
    assert row["operating_profit_pbt_bridge"] == pytest.approx(922.56)
    assert row["operating_profit_closure_status"] == "pass"
    assert row["tax_identity_status"] == "pass"


def test_historical_net_revenue_and_wrapped_pre_exceptional_subtotal_close():
    row = extract_document(HISTORICAL_GROSS_NET_REVENUE_STATEMENT)[0]
    assert row["revenue_from_operations"] == pytest.approx(440028462.0)
    assert row["profit_before_exceptional_items_and_tax"] == pytest.approx(24837215.0)
    assert row["operating_profit_after_da"] == pytest.approx(17203725.0)
    assert row["operating_profit_pbt_bridge"] == pytest.approx(17203726.0)
    assert row["operating_profit_closure_status"] == "pass"


def test_historical_primary_without_total_is_reconciled_but_not_screen_eligible():
    rows = extract_document(HISTORICAL_INCOMPLETE_PRIMARY_STATEMENT)
    assert len(rows) == 1
    row = rows[0]
    assert row["period_end"] == "2017-03-31"
    assert row["revenue_from_operations"] == pytest.approx(1483505.0)
    assert row["total_income"] == pytest.approx(1483505.0)
    assert row["total_expenses"] == pytest.approx(707085.0)
    assert row["total_expenses_source"] == "summed_expense_components"
    assert row["operating_profit_closure_status"] == "pass"
    assert row["screen_eligible"] is False


def test_nonzero_tax_components_override_a_zero_tax_section_heading():
    row = extract_document(HISTORICAL_TAX_HEADING_ZERO_STATEMENT)[0]
    assert row["income_tax_expense"] == pytest.approx(1938.25)
    assert row["income_tax_expense_source"] == "summed_tax_components"
    assert row["tax_identity_status"] == "pass"


def test_current_year_subrow_under_tax_heading_is_the_current_tax_value():
    text = HISTORICAL_TAX_HEADING_ZERO_STATEMENT.replace(
        "Income tax 1,235.00 1,200.00", "of Current year 1,235.00 1,200.00"
    )
    row = extract_document(text)[0]
    assert row["current_tax"] == pytest.approx(1235.0)
    assert row["tax_identity_status"] == "pass"


def test_income_tax_paid_for_earlier_years_is_an_earlier_period_component():
    text = HISTORICAL_MINORITY_INTEREST_STATEMENT.replace(
        "Income tax pertaining to earlier years written back",
        "Income Tax paid for earlier years",
    )
    row = extract_document(text)[0]
    assert row["earlier_year_tax"] == pytest.approx(-0.71)
    assert row["tax_identity_status"] == "pass"


def test_bse_jurisdiction_currency_default_is_tagged_and_not_screen_eligible():
    text = HISTORICAL_INCOMPLETE_PRIMARY_STATEMENT.replace("Amount in Rs\n", "")
    row = extract_document(text)[0]
    assert row["currency"] == "INR"
    assert row["currency_source"] == "market_jurisdiction_default"
    assert row["screen_eligible"] is False


def test_dropped_indian_grouping_separators_are_rejoined_within_amounts():
    row = extract_document(HISTORICAL_DROPPED_GROUPING_SEPARATOR_STATEMENT)[0]
    assert row["profit_before_tax"] == pytest.approx(1584.95)
    assert row["current_tax"] == pytest.approx(225.62)
    assert row["deferred_tax"] == pytest.approx(271.83)
    assert row["income_tax_expense"] == pytest.approx(563.68)
    assert row["profit_for_period"] == pytest.approx(1021.27)
    assert row["tax_identity_status"] == "pass"


def test_operating_revenue_subtotal_does_not_replace_total_statement_income():
    row = extract_document(HISTORICAL_OCR_SPLIT_OPERATIONS_STATEMENT)[0]
    assert row["total_income"] == pytest.approx(2788.57)
    assert row["revenue_from_operations"] == pytest.approx(2700.30)
    assert row["operating_profit_closure_status"] == "pass"


def test_legacy_sales_subrow_does_not_replace_modern_operating_revenue():
    text = HISTORICAL_OCR_SPLIT_OPERATIONS_STATEMENT.replace(
        "Other income 88.27 13.50",
        "Other income 88.27 13.50\nSales 110.50 95.00",
    )
    row = extract_document(text)[0]
    assert row["revenue_from_operations"] == pytest.approx(2700.30)
    assert row["operating_profit_closure_status"] == "pass"


def test_explicit_current_tax_overrides_earlier_income_tax_prose_zero():
    text = HISTORICAL_TAX_HEADING_ZERO_STATEMENT.replace(
        "Income tax 1,235.00 1,200.00",
        "Exceptional items (Previous year - Excess provision\n"
        "of Income Tax written back) - -\n"
        "Current tax 1,235.00 1,200.00",
    )
    row = extract_document(text)[0]
    assert row["current_tax"] == pytest.approx(1235.0)
    assert row["income_tax_expense"] == pytest.approx(1938.25)
    assert row["tax_identity_status"] == "pass"


def test_unknown_debt_is_not_zero_without_positive_evidence():
    row = extract_document(FINANCIAL_STATEMENTS)[0]
    assert row["total_borrowings"] is None
    assert row["total_borrowings_null_reason"] == "not_reported"


def test_direct_short_and_long_term_borrowing_labels_do_not_need_context_headers():
    text = UMA_STATEMENTS.replace(
        "Non current liabilities\nFinancial Liabilities\n(a) Borrowings 17 50.61 47.01",
        "Long-term borrowings 17 50.61 47.01",
    ).replace(
        "Current liabilities\nFinancial Liabilities\n(a) Borrowings 19 20,770.49 12,069.83",
        "Short-term borrowings 19 20,770.49 12,069.83",
    )
    row = extract_document(text)[0]
    assert row["non_current_borrowings"] == pytest.approx(50.61)
    assert row["current_borrowings"] == pytest.approx(20770.49)


def test_standalone_financial_statement_as_on_is_a_balance_heading():
    text = UMA_STATEMENTS.replace(
        "Standalone Balance Sheet as at 31st March, 2025",
        "STANDALONE FINANCIAL STATEMENT As On 31st March, 2025",
    )
    row = extract_document(text)[0]
    assert row["total_borrowings"] == pytest.approx(20821.10)


def test_letter_prefixed_assets_section_supports_evidenced_zero_debt():
    text = """
    Balance Sheet as at 31 March, 2016
    Amount in Rupees
    A | EQUITY AND LIABILITIES
    Non-current liabilities
    Long-term provisions 425,000 159,500
    Current liabilities
    Other current liabilities 70,711 88,589
    B | ASSETS
    Non-current assets 33,256,867 31,248,494
    Current assets 210,923 280,877
    TOTAL 1-21 33,467,790 31,529,371

    Statement of Profit and Loss for the year ended 31 March, 2016
    Total Income 2,893,122 2,296,886
    Employee Costs 504,292 462,042
    Depreciation 93,328 90,811
    Other expenses 1,113,206 710,801
    Total expenses 1,710,826 1,263,654
    Profit before tax 1,182,296 1,033,232
    Current tax - -
    Profit for the year 1,182,296 1,033,232
    """
    row = extract_document(text)[0]
    assert row["total_assets"] == pytest.approx(33467790.0)
    assert row["total_borrowings"] == 0.0
    assert row["debt_reconciliation_status"] == "true_zero"


def test_wrapped_consolidated_profit_heading_starts_a_new_statement_block():
    text = """
    STATEMENT OF PROFIT AND LOSS FOR THE YEAR ENDED 31 MARCH 2018
    Amount in Rs
    Revenue from operations 359,148.91 350,000.00
    Other income 1,602.03 1,500.00
    Total income 360,750.94 351,500.00
    Employee benefit expenses 30,134.77 29,000.00
    Finance costs 3,616.12 3,500.00
    Other expenses 319,043.08 310,000.00
    Total expenses 352,793.97 342,500.00
    Profit before tax 7,956.97 9,000.00
    Current tax 1,755.63 2,000.00
    Profit for the year 6,201.34 7,000.00

    CONSOLIDATED STATEMENT OF
    PROFIT AND LOSS FOR THE YEAR ENDED 31 MARCH 2018
    Amount in Rs
    Revenue from operations 369,672.43 375,690.34
    Other income 1,653.10 3,126.86
    Total income 371,325.53 378,817.20
    Employee benefit expenses 31,000.00 30,000.00
    Finance costs 4,000.00 3,900.00
    Other expenses 307,183.78 310,000.00
    Total expenses 342,183.78 343,900.00
    Profit before tax 29,141.75 34,917.20
    Current tax 1,755.63 2,025.40
    Profit for the year from continuing operations 27,386.12 32,891.80
    """
    rows = extract_document(text)
    assert [row["statement_scope"] for row in rows] == [
        "standalone",
        "consolidated",
    ]
    assert all(row["tax_identity_status"] == "pass" for row in rows)


def test_ocr_corrupted_ordinal_date_is_normalized_in_primary_heading():
    text = """
    Audited Statement of Profit and Loss for the year ended 31*t March 2025
    Rs. in Lakhs
    Revenue from Operations 214.13 207.37
    Other income 0.08 0.12
    Total revenue 214.21 207.49
    Employee benefit expense 23.76 29.69
    Finance costs 56.29 71.92
    Other expenses 64.97 37.18
    Total expenses 145.02 138.79
    Profit before exceptional and extraordinary items and tax 69.19 68.71
    Current Tax 18.00 17.86
    PROFIT AFTER TAX 51.19 50.85
    """
    rows = extract_document(text)
    assert len(rows) == 1
    assert rows[0]["period_end"] == "2025-03-31"
    assert rows[0]["profit_before_exceptional_items_and_tax"] == pytest.approx(69.19)
    assert rows[0]["profit_after_tax"] == pytest.approx(51.19)


def test_date_range_uses_year_end_and_explicit_income_expenditure_sections():
    text = """
    PROFIT AND LOSS ACCOUNT FOR THE PERIOD FROM APRIL 1, 2010 TO MARCH 31, 2011
    Rupees
    INCOME:
    Other Income 969,354 28,474
    EXPENDITURE:
    Administration and other expenses
    PROFIT BEFORE TAXATION 477,568 377,942
    Current Tax NIL 111,000
    Deferred Tax NIL 111,000
    PROFIT AFTER TAXATION 477,568 266,942
    """
    rows = extract_document(text)
    assert len(rows) == 1
    assert rows[0]["period_end"] == "2011-03-31"
    assert rows[0]["other_income"] == pytest.approx(969354.0)
    assert rows[0]["profit_after_tax"] == pytest.approx(477568.0)
    assert rows[0]["tax_identity_status"] == "pass"


def test_bare_profit_and_loss_for_period_heading_is_primary():
    text = """
    Profit & Loss for the Period 31st March, 2017
    Amount in Rs.
    Revenue from operations 166,556,373 36,780,635
    Other income 278,802 47,297,491
    Total revenue 166,835,175 84,078,126
    Employee benefits expense 11,152,365 8,589,373
    Finance costs 1,647,979 1,535,538
    Other expenses 151,819,004 79,749,843
    Total expenses 164,619,348 82,874,754
    Profit before tax 2,101,662 1,157,772
    Current tax expense 444,710 -
    Deferred tax 14,489 -
    Profit for the year 1,642,463 1,157,772
    """
    rows = extract_document(text)
    assert len(rows) == 1
    assert rows[0]["period_end"] == "2017-03-31"
    assert rows[0]["tax_identity_status"] == "pass"


def test_colored_ocr_statement_ignores_formula_and_minority_interest_false_match():
    text = """
    Consolidated Statement of Profit and Loss
    for the year ended 31 March 2012
    (Rs in millions)
    I. Revenue from operations 18 3,825.65 3,991.49
    II. Other Income 19 83.37 53.17
    III. Total Revenue (1 +I!) 3,909.01 4,044.66
    IV. Expenses:
    Cost of Real Estate Purchase, Materials and Consumption Expenses 20 1,292.08 3,579.81
    Changes in inventories of finished goods and Work in progress 21 502.78 (2,391.64)
    Employee benefits expense 22 145.17 156.91
    Fianance costs 23 1,192.83 1,039.81
    Depreciation and amortization expense 11 60.08 53.69
    Other expenses 24 387.10 486.24
    Total expenses 3,580.06 2,924.82
    IX. Profit before tax (VII - VIII) 328.96 1,119.84
    X. Tax expense:
    (1) Current tax 199.61 307.11
    Less : Mat credit entitlement 95.02 -
    (2) Deferred tax (107.49) 23.95
    (3) Short provision of tax in respect of earlier years 4.61 -
    XI. Profit(Loss) from the period from continuing operations
    137.22 788.78
    (IX-X)
    XV. Minority Interest 20.19 6.12
    XV. Profit/(Loss) for the year (XI + XIV) 117.03 782.66
    """
    rows = extract_document(text)
    assert len(rows) == 1
    row = rows[0]
    assert row["total_income"] == pytest.approx(3909.01)
    assert row["finance_costs"] == pytest.approx(1192.83)
    assert row["profit_from_continuing_operations"] == pytest.approx(137.22)
    assert row["operating_profit_closure_status"] == "pass"
    assert row["tax_identity_status"] == "pass"


def test_ocr_grouping_dots_and_small_income_residual_are_reconciled():
    text = """
    Statement of Profit and Loss for the year ended March 31, 2024
    (Amount in INR Lakhs)
    Revenue From Operations 2 1.156.79 914.02
    Other Income 421 55.76
    Total Income (1) 1,161.00 969.78
    Finance Costs 28 206.06 226.20
    Total expenses (II) 1.319.66 1,210.87
    Profit before tax (I-II) (158.66) (241.09)
    Tax expense:
    (2) Deferred 18 11.82 8.64
    Profit for the period (III-IV) (170.48) (249.73)
    """
    row = extract_document(text)[0]
    assert row["revenue_from_operations"] == pytest.approx(1156.79)
    assert row["other_income"] == pytest.approx(4.21)
    assert row["total_expenses"] == pytest.approx(1319.66)
    assert row["deferred_tax"] == pytest.approx(11.82)
    assert row["operating_profit_closure_status"] == "pass"
    assert row["tax_identity_status"] == "pass"


def test_square_bracket_row_formulas_are_not_statement_values():
    text = """
    Consolidated Statement of Profit and Loss for the year ended March 31, 2021
    (All amounts in Indian Rupees in lakhs)
    Revenue from operations 13 1,274.35 744.70
    Other income 14 13.61 7.40
    Total income 1,287.96 752.10
    Finance costs 18 46.26 59.37
    Total expenses 1,091.74 779.67
    Profit Before Tax (PBT) [1 - 2] 196.22 (27.57)
    Tax expense
    Current tax 8.51 -
    Deferred tax, net (3.19) (4.80)
    Total tax expense 5.75 (4.80)
    Profit for the year [3-4] 190.47 (22.77)
    """
    row = extract_document(text)[0]
    assert row["profit_before_tax"] == pytest.approx(196.22)
    assert row["profit_for_period"] == pytest.approx(190.47)
    assert row["tax_identity_status"] == "pass"


def test_single_period_note_column_is_not_read_as_current_value():
    text = """
    CONSOLIDATED STATEMENT OF PROFIT AND LOSS FOR THE YEAR ENDED 31ST MARCH, 2012
    (Rs in Lacs)
    PARTICULARS NOTE NO. 31st MARCH, 2012
    Revenue from Operations (Net) 19 5,702.97
    Other Income 20 25.96
    TOTAL REVENUE (1+2) 5,728.93
    Cost of materials consumed 21 1,890.37
    Employee benefits expense 23 1,049.65
    Finance costs 24 200.83
    Other expenses 25 1,223.26
    TOTAL EXPENSES 4,985.14
    Profit before Tax (3-4) 743.79
    Tax Expenses
    Current tax expense for current year 183.76
    Current tax expense relating to prior years (14.44)
    Deferred tax 46.49
    Profit after tax before minority interest (5-6) 527.98
    """
    row = extract_document(text)[0]
    assert row["revenue_from_operations"] == pytest.approx(5702.97)
    assert row["other_income"] == pytest.approx(25.96)
    assert row["finance_costs"] == pytest.approx(200.83)
    assert row["profit_after_tax"] == pytest.approx(527.98)
    assert row["earlier_year_tax"] == pytest.approx(-14.44)
    assert row["operating_profit_closure_status"] == "pass"
    assert row["tax_identity_status"] == "pass"


def test_discontinuing_tax_line_does_not_overwrite_reported_tax_expense():
    text = """
    Standalone Statement of Profit and Loss for the Year ended 31st March, 2019
    Rs.
    Revenue from operations 23 72,79,04,408 62,88,61,233
    Other income 24 1,56,18,237 1,44,36,887
    TOTAL REVENUE (I + II) 74,35,22,645 64,32,98,120
    Finance costs 29 2,65,84,980 2,54,11,885
    TOTAL EXPENSES 72,49,57,524 62,79,43,391
    Profit before tax (VII - VIII) 1,85,65,120 1,53,54,729
    Tax expense: 22,62,245 38,90,826
    Current Tax 38,50,760 38,02,970
    Previous Year 66,840 -
    Deferred Tax (16,55,355) 87,856
    Profit for the period from continuing operations
    1,63,02,876 1,14,63,903
    Tax expense of discontinuing operations - -
    """
    row = extract_document(text)[0]
    assert row["income_tax_expense_reported"] == pytest.approx(2262245.0)
    assert row["earlier_year_tax"] == pytest.approx(66840.0)
    assert row["profit_from_continuing_operations"] == pytest.approx(16302876.0)
    assert row["tax_identity_status"] == "pass"


def test_complete_balance_with_only_lease_liabilities_proves_zero_borrowings():
    text = UMA_STATEMENTS.replace(
        "(a) Borrowings 17 50.61 47.01", "(a) Lease Liabilities 17 50.61 47.01"
    ).replace(
        "(a) Borrowings 19 20,770.49 12,069.83",
        "(a) Lease Liabilities 19 20.49 12.83",
    )
    row = extract_document(text)[0]
    assert row["total_borrowings"] == 0.0
    assert row["total_borrowings_null_reason"] == "true_zero"
    assert row["total_lease_liabilities"] == pytest.approx(71.10)
    assert row["total_interest_bearing_debt"] == pytest.approx(71.10)


def test_complete_financial_liability_sections_without_borrowings_prove_zero():
    text = UMA_STATEMENTS.replace(
        "(a) Borrowings 17 50.61 47.01", "(a) Trade payables 17 50.61 47.01"
    ).replace(
        "(a) Borrowings 19 20,770.49 12,069.83",
        "(a) Trade payables 19 20,770.49 12,069.83",
    ).replace("Financial Liabilities\n", "").replace(
        "Non current liabilities", "Non‐Current Liabilities"
    ).replace("TOTAL ASSETS 42,868.46 34,068.39", "TOTAL 42,868.46 34,068.39")
    row = extract_document(text)[0]
    assert row["total_borrowings"] == 0.0
    assert row["debt_reconciliation_status"] == "true_zero"
    assert row["total_interest_bearing_debt"] == 0.0


def test_unsecured_loans_in_liability_section_are_debt_not_inferred_zero():
    text = UMA_STATEMENTS.replace(
        "Financial Liabilities\n(a) Borrowings 17 50.61 47.01",
        "(a) Unsecured Loans 17 50.61 47.01",
    ).replace(
        "Financial Liabilities\n(a) Borrowings 19 20,770.49 12,069.83",
        "(a) Other Current Liabilities 19 20,770.49 12,069.83",
    )

    row = extract_document(text)[0]

    assert row["total_borrowings"] == pytest.approx(50.61)
    assert row["debt_reconciliation_status"] == "components_only"


def test_legacy_secured_and_unsecured_rows_work_without_loan_funds_heading():
    text = UMA_STATEMENTS.replace(
        "Non current liabilities\nFinancial Liabilities\n(a) Borrowings 17 50.61 47.01",
        "SECURED LOANS 3 23,645.02 - 13,095.73\n"
        "UNSECURED LOANS 4 - 1,500.00",
    ).replace(
        "Current liabilities\nFinancial Liabilities\n(a) Borrowings 19 20,770.49 12,069.83",
        "Current liabilities\n(a) Other Current Liabilities 19 20,770.49 12,069.83",
    )

    row = extract_document(text)[0]

    assert row["total_borrowings"] == pytest.approx(23_645.02)
    assert row["debt_reconciliation_status"] == "reported_total_only"


def test_explicit_no_borrowing_language_proves_zero_without_a_balance_component():
    row = extract_document(
        FINANCIAL_STATEMENTS + "\nThe Company has not availed any borrowings.\n"
    )[0]
    assert row["total_borrowings"] == 0.0
    assert row["total_borrowings_null_reason"] == "true_zero"


def test_multipage_balance_sheet_continuation_is_merged_before_pairing():
    text = UMA_STATEMENTS.replace(
        "EQUITY AND LIABILITIES",
        "Standalone Balance Sheet as at 31st March, 2025\nEQUITY AND LIABILITIES",
        1,
    )
    row = extract_document(text)[0]
    assert row["total_assets"] == pytest.approx(42868.46)
    assert row["total_borrowings"] == pytest.approx(20821.10)


def test_balance_sheet_split_year_columns_are_paired_with_profit_period():
    text = UMA_STATEMENTS.replace(
        "Standalone Balance Sheet as at 31st March, 2025",
        "Standalone Balance Sheet as at March 31\nParticulars Note 2025 2024",
    ).replace(
        "Particulars Note No. As at 31st March, 2025 As at 31st March, 2024",
        "Particulars Note 2025 2024",
    )
    row = extract_document(text)[0]
    assert row["period_end"] == "2025-03-31"
    assert row["total_assets"] == pytest.approx(42868.46)
    assert row["total_borrowings"] == pytest.approx(20821.10)


def test_balance_sheet_split_year_columns_ignore_layout_whitespace():
    heading = "Standalone Balance Sheet as at March 31" + (" " * 220)
    heading += "Notes 2025 2024"
    text = UMA_STATEMENTS.replace(
        "Standalone Balance Sheet as at 31st March, 2025",
        heading,
    ).replace(
        "Particulars Note No. As at 31st March, 2025 As at 31st March, 2024",
        "Particulars Note",
    )

    row = extract_document(text)[0]

    assert row["period_end"] == "2025-03-31"
    assert row["total_borrowings"] == pytest.approx(20821.10)


@pytest.mark.parametrize(
    "heading",
    (
        "Standalone Balance Sheet 4s at March 31",
        "Standalone Balance Sheet asat March 31",
    ),
)
def test_balance_heading_as_at_ocr_variants_use_split_year_columns(heading):
    text = UMA_STATEMENTS.replace(
        "Standalone Balance Sheet as at 31st March, 2025",
        heading,
    ).replace(
        "Particulars Note No. As at 31st March, 2025 As at 31st March, 2024",
        "Particulars Note 2025 2024",
    )
    row = extract_document(text)[0]
    assert row["total_assets"] == pytest.approx(42868.46)
    assert row["total_borrowings"] == pytest.approx(20821.10)


def test_legacy_balance_heading_ocr_typos_retain_debt_and_period():
    text = UMA_STATEMENTS.replace(
        "Standalone Balance Sheet as at 31st March, 2025",
        "Standalone BALANGE SHEET AS AT MARCH 32, 2025",
    )
    row = extract_document(text)[0]
    assert row["period_end"] == "2025-03-31"
    assert row["total_assets"] == pytest.approx(42868.46)
    assert row["total_borrowings"] == pytest.approx(20821.10)


def test_explicit_no_borrowings_is_a_true_zero():
    text = UMA_STATEMENTS.replace(
        "(a) Borrowings 17 50.61 47.01",
        "The Company has no borrowings as at 31 March 2025",
    ).replace("(a) Borrowings 19 20,770.49 12,069.83", "")
    row = extract_document(text)[0]
    assert row["total_borrowings"] == 0.0
    assert row["total_borrowings_null_reason"] == "true_zero"


def test_december_year_end_is_read_instead_of_assuming_march():
    text = UMA_STATEMENTS.replace("31st March, 2025", "31 December 2025").replace(
        "31st March 2025", "31 December 2025"
    )
    row = extract_document(text)[0]
    assert row["period_end"] == "2025-12-31"


def test_note_and_page_reference_columns_are_not_statement_values():
    text = """
    Profit and Loss Statement for the year ended 31st March 2014
    (Rs in Crores)
    Particulars Note Page Figures for the year ended 31st March 2014 Figures for the year ended 31st March 2013
    Revenue from Operations(Net) 2.1 64 1,254.40 935.80
    Other Income 2.2 64 64.81 60.39
    Total Revenue (I+II) 1,319.21 996.19
    Finance costs 2.6 65 7.05 7.88
    Total Expenses 1,260.29 913.14
    Profit before Exceptional Items and Tax 58.92 83.05
    Exceptional items (9.33) 2.28
    Profit before tax 68.25 80.77
    Tax Expense:
    Current Tax 14.74 15.17
    Deferred Tax 6.47 6.64
    Total Tax Expenses 14.01 18.51
    Profit for the year from continuing operations 54.24 62.26
    """
    row = extract_document(text)[0]
    assert row["revenue_from_operations"] == pytest.approx(1254.40)
    assert row["other_income"] == pytest.approx(64.81)
    assert row["finance_costs"] == pytest.approx(7.05)
    assert row["operating_profit_closure_status"] == "pass"
    assert row["tax_identity_status"] == "pass"


def test_single_period_notes_header_and_excise_duty_produce_net_revenue():
    text = """
    CONSOLIDATED STATEMENT OF PROFIT AND LOSS
    FOR THE YEAR ENDED 31ST MARCH, 2013
    Rs. In lacs
    Notes Year Ended 31.03.2013
    Revenue from operations 20 87,695.08
    Less: Excise Duty 8,119.12
    Other Income 21 3,156.35
    Total Revenue 82,732.31
    Finance costs 25 790.98
    Total Expenses 70,149.00
    Profit before tax 12,583.31
    Total tax expense 4,047.32
    Profit after tax for the year 8,535.99
    """
    row = extract_document(text)[0]
    assert row["revenue_from_operations"] == pytest.approx(79575.96)
    assert row["other_income"] == pytest.approx(3156.35)
    assert row["finance_costs"] == pytest.approx(790.98)
    assert row["operating_profit_closure_status"] == "pass"
    assert row["tax_identity_status"] == "pass"


def test_oci_tax_rows_do_not_overwrite_primary_tax_components():
    text = """
    Standalone Statement of Profit and Loss for the year ended 31 March 2020
    INR in Lakhs
    Revenue From Operations 1,49,925.77 1,04,401.25
    Other Income 2,477.53 2,297.84
    Total Income 1,52,403.30 1,06,699.09
    Finance Costs 1,463.80 916.05
    Total Expenses 1,34,961.11 92,845.75
    Profit Before Tax 17,442.19 13,853.34
    Tax Expense:
    Current Tax 4,608.22 5,143.16
    Deferred Tax (92.06) (313.44)
    Profit for the year 12,926.03 9,023.62
    Items that will not be reclassified to profit or loss
    Income tax expenses relating to items that will be reclassified to profit or loss 4.42 -
    Total other comprehensive income, net of tax expense (37.83) (72.23)
    """
    row = extract_document(text)[0]
    assert row["income_tax_expense"] == pytest.approx(4516.16)
    assert row["income_tax_expense_source"] == "summed_tax_components"
    assert row["tax_identity_status"] == "pass"


def test_continuing_pbt_is_kept_and_discontinued_pbt_is_ignored():
    text = """
    Consolidated Statement of Profit and Loss for the year ended 31 March 2023
    INR in Lakhs
    Continuing Operations
    Revenue from Operations 63,239.98 52,598.83
    Other Income 503.08 507.81
    Total Income 63,743.06 53,106.64
    Finance Costs 822.72 944.71
    Total Expenses 56,330.84 44,743.94
    Profit from Continuing Operations before Tax Expense 7,416.25 8,364.40
    Current Tax Charge 2,070.77 1,211.22
    Deferred Tax Charge 272.08 (21.16)
    Total Tax Expense 2,342.85 1,190.06
    Profit for the Year from continuing operations 5,073.40 7,174.34
    Discontinued Operations
    Profit before tax from discontinued operations - 196.54
    Tax expense of discontinued operations - 129.12
    Profit after tax from discontinued operations - 159.92
    Profit for the year 5,073.40 7,334.26
    """
    row = extract_document(text)[0]
    assert row["profit_before_tax"] == pytest.approx(7416.25)
    assert row["profit_from_continuing_operations"] == pytest.approx(5073.40)
    assert row["tax_identity_status"] == "pass"


def test_eps_basis_text_is_not_a_continuing_operations_profit_subtotal():
    text = """
    Standalone Statement of Profit and Loss for the year ended 31 March 2012
    INR in Lakhs
    Revenue from Operations 500.00 450.00
    Other Income 20.00 15.00
    Total Income 520.00 465.00
    Finance Costs 10.00 9.00
    Total Expenses 420.00 385.00
    Profit before Tax 100.00 80.00
    Current Tax 20.00 16.00
    Profit for the Year 80.00 64.00
    Computed on the basis of profit from continuing operations 25.00 N.A. 0.00
    """
    row = extract_document(text)[0]
    assert row.get("profit_from_continuing_operations") is None
    assert row["profit_for_period"] == pytest.approx(80.0)
    assert row["tax_identity_profit_basis"] == "profit_for_period"
    assert row["tax_identity_status"] == "pass"


def test_ligature_profit_heading_starts_after_balance_sheet_tax_assets():
    text = """
    Standalone Balance Sheet as at 31 March 2023
    INR in Crores
    Current Tax Assets (Net) 4.28 -
    Deferred Tax Liabilities (Net) 67.63 3.65
    Statement of Proﬁt and Loss for the year ended 31.03.2023
    INR in Crores
    Revenue from Operations 29,616.52 15,857.09
    Other Income 192.62 25.61
    Total Income 29,809.14 15,882.70
    Finance Cost 301.64 132.96
    Total Expenses 29,199.37 15,737.88
    Proﬁt / (Loss) before exceptional item and tax 609.77 144.82
    Exceptional Item - -
    Proﬁt/(Loss) before Tax 609.77 144.82
    Current tax 92.75 13.87
    Deferred tax 63.98 22.82
    Short / (Excess) tax for earlier years (3.06) (0.07)
    Total Tax Expense 153.67 36.62
    Proﬁt/(Loss) for the year 456.10 108.20
    """
    row = extract_document(text)[0]
    assert row["source_profit_heading"].startswith("Statement of Proﬁt")
    assert row["current_tax"] == pytest.approx(92.75)
    assert row["deferred_tax"] == pytest.approx(63.98)
    assert row["tax_identity_status"] == "pass"


def test_reversed_split_profit_heading_starts_after_balance_sheet_tax_assets():
    text = """
    Standalone Balance Sheet as at March 31, 2025
    Rs. In Lakhs
    Deferred Tax Assets (Net) 92.42 219.53
    Current Tax Assets (Net) - -
    Statement of Standalone
    for the year ended March 31, 2025
    Profit or Loss
    Rs. In Lakhs
    Revenue from operations 2,43,001.62 1,37,055.78
    Other income 5,249.89 1,814.68
    Total Income 2,48,251.51 1,38,870.46
    Finance costs 1,756.86 2,639.86
    Total expenses 2,24,182.82 1,25,700.00
    Profit/(loss) before exceptional items and tax 24,068.69 13,170.46
    Exceptional items - -
    Profit/(loss) before tax 24,068.69 13,170.46
    Current Tax 4,982.13 3,208.22
    Deferred Tax 145.46 (27.26)
    Taxes relating to earlier years - (105.22)
    Profit/(loss) for the period 18,941.10 10,094.72
    """
    row = extract_document(text)[0]
    assert row["source_profit_heading"].startswith("Standalone Statement of Profit")
    assert row["current_tax"] == pytest.approx(4982.13)
    assert row["deferred_tax"] == pytest.approx(145.46)
    assert row["tax_identity_status"] == "pass"


def test_tax_expense_note_reference_is_not_a_reported_tax_total():
    text = """
    Consolidated Statement of Profit and Loss for the year ended 31 March 2021
    INR in Crores
    Particulars Note No. 2021 2020
    Revenue from Operations 29 5,393.13 4,605.75
    Other Income 30 10.02 4.94
    Total Income 5,403.14 4,610.69
    Finance Costs 34(15) 16.02 27.16
    Total Expense 4,035.06 3,567.22
    Profit Before Tax 1,368.08 999.82
    VIII Tax Expense 34(10)
    (i) Current Tax 255.47 204.57
    (ii) Deferred Tax (9.56) (4.30)
    (iii) Short/Excess Tax Provision 7.41 (1.09)
    Profit after Tax Before Share of Profit of Associate and Joint Ventures 1,114.76 800.64
    """
    row = extract_document(text)[0]
    assert row["income_tax_expense"] == pytest.approx(253.32)
    assert row["income_tax_expense_source"] == "summed_tax_components"
    assert row["tax_identity_status"] == "pass"


def test_current_year_tax_and_single_r_defered_tax_are_statement_values():
    text = """
    Statement of Profit and Loss for the year ended 31st March, 2016
    INR in Rupees
    Particulars Note No. 2016 2015
    Revenue from operations 13 2,404,650.00 2,569,715.00
    Other Income - -
    Total Revenue 2,404,650.00 2,569,715.00
    Other Expenses 14 2,351,160.31 2,512,640.59
    Total Expenses 2,351,160.31 2,512,640.59
    Profit before tax 53,489.69 57,074.41
    Tax expense:
    (a) Income Tax Current Year 250.00 -
    (b) Defered tax Current Year 52,746.00 17,582.00
    Profit / (Loss) from continuing operations 493.69 39,492.41
    """
    row = extract_document(text)[0]
    assert row["current_tax"] == pytest.approx(250.0)
    assert row["deferred_tax"] == pytest.approx(52746.0)
    assert row["income_tax_expense"] == pytest.approx(52996.0)
    assert row["tax_identity_status"] == "pass"


def test_ebitda_row_is_not_finance_cost_or_exceptional_item():
    text = """
    Consolidated Statement of Profit and Loss for the year ended 31 March 2013
    INR in Crores
    Revenue from operations (Net) 2,790.62 2,342.18
    Other income 76.94 75.55
    Total Revenue 2,867.56 2,417.73
    Total Expenses 2,156.44 1,774.78
    Earnings before interest, tax, depreciation and amortization (EBITDA) 711.12 642.95
    Finance Costs 231.43 151.28
    Depreciation and amortization expenses 205.94 181.73
    Profit before exceptional items and tax 273.75 309.94
    Exceptional Item - -
    Profit before tax 273.75 270.40
    Total Tax Expense 133.58 122.85
    Profit after Tax before Share of Profit in Associates 140.17 147.55
    """
    row = extract_document(text)[0]
    assert row["finance_costs"] == pytest.approx(231.43)
    assert row["exceptional_items"] == pytest.approx(0.0)
    assert row["operating_profit_after_da"] == pytest.approx(428.24)
    assert row["operating_profit_pbt_bridge"] == pytest.approx(428.24)
    assert row["operating_profit_closure_status"] == "pass"


def test_profit_before_da_financial_charges_and_nested_note_reference_close():
    text = """
    PROFIT AND LOSS ACCOUNT FOR THE PERIOD ENDED ON 30th JUNE, 2011
    INR in Crores
    Net sales 899.48 665.58
    Other Income 8.97 8.93
    Profit before Depreciation, Financial Charges and Tax 158.01 182.96
    Financial Charges 97.64 83.40
    Less: Depreciation and Amortisation 107.42 81.28
    Profit/(Loss) before Tax (47.05) 18.28
    Current Income Tax - 3.11
    Deferred Tax (Net) (14.40) (39.66)
    MAT Credit Entitlement (Refer Note (7)(ii) of II of Schedule -15) (3.10) (34.20)
    Profit/(Loss) for the period after Tax (29.55) 89.03
    """
    row = extract_document(text)[0]
    assert row["finance_costs"] == pytest.approx(97.64)
    assert row["depreciation_amortization"] == pytest.approx(107.42)
    assert row["mat_credit"] == pytest.approx(-3.10)
    assert row["operating_profit_after_da"] == pytest.approx(41.62)
    assert row["operating_profit_pbt_bridge"] == pytest.approx(41.62)
    assert row["operating_profit_closure_status"] == "pass"
    assert row["tax_identity_status"] == "pass"


def test_schedule_column_is_a_note_column_in_single_comparative_statement():
    text = """
    Consolidated Profit & Loss Account for the year ended 31 March, 2010
    Rs. in Lacs
    Schedules Year ended 31.03.2010
    Net Sales/Income from Operations 13 33,086.81
    Other Income 14 17.38
    Total Income 33,104.19
    Finance Cost 19 582.96
    Total Expenditure 31,511.45
    Net Profit before Tax 1,592.74
    Current Tax 390.00
    Prior Years (Net) 20.06
    Deferred Tax 23.94
    Profit after Tax 1,158.74
    """
    row = extract_document(text)[0]
    assert row["revenue_from_operations"] == pytest.approx(33086.81)
    assert row["other_income"] == pytest.approx(17.38)
    assert row["finance_costs"] == pytest.approx(582.96)
    assert row["earlier_year_tax"] == pytest.approx(20.06)
    assert row["operating_profit_closure_status"] == "pass"
    assert row["tax_identity_status"] == "pass"


def test_wrapped_current_tax_wealth_disclosure_uses_trailing_statement_value():
    text = """
    Profit & Loss Account for the year ended June 30, 2010
    INR in Crores
    Business Income 12,061.78 12,336.81
    Other Income 58.90 33.60
    Depreciation and Amortisation 21.73 17.31
    Profit before Tax 368.65 373.86
    Tax expense/(credit) 21 (5)
    - Current [Wealth tax ` 0.01 Crores
    (2009 - ` 0.02 Crores)] 111.96 122.77
    - Deferred (4.86) (10.80)
    Profit after Tax 261.55 260.44
    """
    row = extract_document(text)[0]
    assert row.get("income_tax_expense_reported") is None
    assert row["current_tax"] == pytest.approx(111.96)
    assert row["deferred_tax"] == pytest.approx(-4.86)
    assert row["income_tax_expense"] == pytest.approx(107.10)
    assert row["tax_identity_status"] == "pass"


def test_current_tax_expense_for_year_is_not_mistaken_for_note_then_dash():
    text = """
    Statement of Profit and Loss for the year ended 31 March 2017
    INR in Crores
    Particulars Note No. 2017 2016
    Revenue from operations 865.34 625.53
    Other income 7.15 3.48
    Total income 872.49 629.01
    Finance cost 30.80 48.57
    Total expenses 817.53 601.99
    Profit before tax 54.49 27.02
    Current tax expense for the year 1.28 -
    Deferred tax - -
    Profit for the year 53.21 27.02
    """
    row = extract_document(text)[0]
    assert row["current_tax"] == pytest.approx(1.28)
    assert row["tax_identity_status"] == "pass"


def test_finance_income_below_ebitda_does_not_rewrite_reported_other_income():
    text = """
    Consolidated Statement of Profit and Loss for the year ended March 31, 2025
    INR in Crores
    Revenue from operations 1,000.00 900.00
    Other income 100.00 90.00
    Total income 1,100.00 990.00
    Total expenses 600.00 550.00
    Profit before depreciation and amortisation, finance costs and tax 500.00 440.00
    Depreciation and amortisation expenses (refer note 54) 100.00 90.00
    Finance costs 30.00 25.00
    Finance income (100.00) (80.00)
    Profit before tax 470.00 405.00
    Current tax 100.00 90.00
    Profit for the year 370.00 315.00
    """
    row = extract_document(text)[0]
    assert row["other_income"] == pytest.approx(100.0)
    assert row["depreciation_amortization"] == pytest.approx(100.0)
    assert row["finance_income"] == pytest.approx(100.0)
    assert row["operating_profit_after_da"] == pytest.approx(300.0)
    assert row["operating_profit_pbt_bridge"] == pytest.approx(300.0)
    assert row["operating_profit_closure_status"] == "pass"


def test_split_consolidated_heading_with_trailing_period_text_is_a_boundary():
    text = """
    Consolidated Balance Sheet as at March 31, 2025
    Rs. In Lakhs
    Current Tax Assets 157.21 -
    Statement of Consolidated for the year ended
    March 31, 2025
    Profit or Loss
    Rs. In Lakhs
    Revenue from operations 246,766.31 138,287.03
    Other income 5,035.81 1,812.62
    Total income 251,802.12 140,099.65
    Finance costs 2,769.26 2,935.88
    Total expenses 231,810.76 127,966.46
    Profit before tax 19,991.36 12,133.19
    Current tax 4,982.13 3,208.22
    Deferred tax (829.12) (267.68)
    Profit for the period 15,838.35 9,297.87
    """
    row = extract_document(text)[0]
    assert row["source_profit_heading"].startswith("Consolidated Statement of Profit")
    assert row["current_tax"] == pytest.approx(4982.13)
    assert row["deferred_tax"] == pytest.approx(-829.12)
    assert row["tax_identity_status"] == "pass"


def test_profit_before_associate_share_is_the_matching_after_tax_basis():
    text = """
    Consolidated Statement of Profit and Loss for the year ended 31 March 2025
    INR in Lakhs
    Revenue from Operations 97,894.75 96,420.98
    Other Income 2,470.33 1,860.53
    Total Income 1,00,365.08 98,281.51
    Finance Costs 5,978.02 4,554.70
    Total Expenses 93,832.07 92,641.23
    Profit before tax from Continuing Operations 10,478.74 4,924.91
    Current Tax 2,776.16 1,606.49
    Deferred Tax 192.36 25.02
    Total Tax Expense 2,968.52 1,631.51
    Profit for the year before Share of Profit from Jointly Controlled Entities 7,510.22 3,293.40
    hare of Profit from Jointly Controlled Entities 507.73 40.64
    Profit for the Year from Continuing Operations 8,017.95 3,334.04
    Profit for the Year 8,004.99 3,335.27
    """
    row = extract_document(text)[0]
    assert row["profit_before_tax_basis"] == "before_associate_share"
    assert row["profit_after_tax"] == pytest.approx(7510.22)
    assert row["tax_identity_profit_basis"] == "profit_after_tax_before_associate"
    assert row["tax_identity_status"] == "pass"


def test_pre_exceptional_subtotal_without_and_tax_closes_operating_profit():
    text = """
    Standalone Statement of Profit and Loss for the year ended 31 March 2022
    INR in Lakhs
    Revenue from Operations 63,635.67 46,551.31
    Other Income 3,262.79 1,768.25
    Total Income 66,898.46 48,319.56
    Finance Costs 432.44 419.39
    Total Expenses 54,532.94 39,200.89
    Profit before Exceptional Items 12,365.52 9,118.67
    Exceptional Items 274.51 -
    Profit before Tax 12,640.03 9,118.67
    Total Tax expenses 3,373.63 2,368.59
    Profit for the year 9,266.40 6,750.08
    """
    row = extract_document(text)[0]
    assert row["profit_before_exceptional_items_and_tax"] == pytest.approx(12365.52)
    assert row["operating_profit_closure_status"] == "pass"
    assert row["tax_identity_status"] == "pass"


def test_minimum_alternate_tax_is_a_current_tax_component():
    text = """
    Profit and Loss statement for the period ended 31st March 2018
    Amount in Rupees
    Revenue from operations 6,493,100.00 23,808,477.00
    Other Income 4,584,787.00 2,883,408.00
    Total Revenue 11,077,887.00 26,691,885.00
    Finance costs 100,000.00 200,000.00
    Total Expenses 10,804,502.88 26,302,680.28
    Profit before tax 273,384.12 389,204.72
    Tax expense:
    Minimum Alternate Tax 52,093.00 74,163.00
    Deferred tax (766,637.00) (560,922.00)
    Profit for the period from continuing operations 987,928.12 875,963.72
    """
    row = extract_document(text)[0]
    assert row["current_tax"] == pytest.approx(52093.0)
    assert row["tax_identity_status"] == "pass"


def test_profit_before_earlier_year_tax_is_not_a_tax_component():
    text = """
    CONSOLIDATED PROFIT AND LOSS ACCOUNT FOR THE YEAR ENDED 31st March 2010
    (Rs. in lakhs)
    Revenue from Operations 2,000.00 1,500.00
    Other Income 347.90 246.51
    Total Income 2,347.90 1,746.51
    Finance Costs 100.00 90.00
    Total Expenses 700.41 913.25
    Profit Before Tax 1,647.49 833.26
    Current Tax 21.71 58.43
    Deferred Tax - -
    Profit after Tax before earlier years tax 1,625.78 774.52
    Income Tax for Earlier Years - -
    Net Profit for the year 1,625.78 774.52
    """
    row = extract_document(text)[0]
    assert row.get("earlier_year_tax") in (None, 0.0)
    assert row["tax_identity_status"] == "pass"


def test_source_derived_tax_section_note_is_not_reported_tax_expense():
    text = """
    Statement of Profit and Loss for the year ended 31 March 2023
    (` in Lakhs)
    Note 31 March 2023 31 March 2022
    Revenue from operations 22 2,088.67 1,768.47
    Other income 23 1,136.36 1,086.39
    Total income 3,225.03 2,854.86
    Finance costs 25 271.08 284.86
    Total expenses 2,457.38 2,096.52
    Profit before tax 767.65 758.34
    Tax expense 28
    Current tax - -
    Deferred tax 753.08 615.89
    Profit/(Loss) for the year 14.57 152.37
    """
    row = extract_document(text)[0]
    assert row.get("income_tax_expense_reported") is None
    assert row["income_tax_expense"] == pytest.approx(753.08)
    assert row["tax_identity_status"] == "pass"


def test_source_derived_appropriation_balance_is_not_earlier_year_tax():
    text = """
    Consolidated Profit and Loss Account for the year ended 31 March 2010
    Rs. in lakhs
    Revenue from Operations 2,000.00 1,500.00
    Other Income 100.00 75.00
    Total Income 2,100.00 1,575.00
    Finance Costs 50.00 40.00
    Total Expenses 1,500.00 1,125.00
    Profit Before Tax 600.00 450.00
    Current Tax 120.00 90.00
    Profit after Tax 480.00 360.00
    Balance B/f from Previous year 3,013.09 2,198.84
    """
    row = extract_document(text)[0]
    assert row.get("earlier_year_tax") is None
    assert row["tax_identity_status"] == "pass"


@pytest.mark.parametrize(
    "label",
    [
        "Earlier years tax adjustments (net)",
        "For earlier years",
        "Earlier Years",
        "Tax Expense Relating to Earlier Year's",
        "Tax Expense relating to earlier years",
        "of Earlier years",
    ],
)
def test_source_derived_earlier_year_tax_labels_close_identity(label):
    text = f"""
    Statement of Profit and Loss for the year ended 31 March 2024
    INR in Lakhs
    Revenue from Operations 2,000.00 1,500.00
    Other Income 100.00 75.00
    Total Income 2,100.00 1,575.00
    Finance Costs 50.00 40.00
    Total Expenses 1,500.00 1,125.00
    Profit Before Tax 600.00 450.00
    Tax Expense
    Current Tax 100.00 80.00
    Deferred Tax 20.00 10.00
    {label} (10.00) -
    Profit for the year 490.00 360.00
    """
    row = extract_document(text)[0]
    assert row["earlier_year_tax"] == pytest.approx(-10.0)
    assert row["tax_identity_status"] == "pass"


def test_source_derived_provision_for_tax_is_current_tax():
    text = """
    Consolidated Profit and Loss Account for the year ended 31 March 2011
    Amount in Rupees
    Revenue from Operations 2,000.00 1,500.00
    Other Income 100.00 75.00
    Total Income 2,100.00 1,575.00
    Finance Costs 50.00 40.00
    Total Expenses 1,500.00 1,125.00
    Profit Before Tax 600.00 450.00
    Provision for Tax (including Wealth Tax) 100.00 80.00
    Deferred Tax 20.00 10.00
    Profit after Tax 480.00 360.00
    """
    row = extract_document(text)[0]
    assert row["current_tax"] == pytest.approx(100.0)
    assert row["tax_identity_status"] == "pass"


def test_source_derived_wrapped_jointly_controlled_share_closes_tax_identity():
    text = """
    Consolidated Statement of Profit and Loss for the year ended 31 March 2026
    INR in Crore
    Revenue from Operations 2,000.00 1,500.00
    Other Income 100.00 75.00
    Total Income 2,100.00 1,575.00
    Finance Costs 50.00 40.00
    Total Expenses 1,500.00 1,125.00
    Profit Before Tax from Continuing Operations 600.00 450.00
    Total Tax Expense 120.00 90.00
    Profit for the year before Share of Profit / (Loss) from 480.00 360.00
    hare of Profit / (Loss) from Jointly Controlled 20.00 10.00
    Entities & Associates
    Profit for the Year from Continuing Operations 500.00 370.00
    """
    row = extract_document(text)[0]
    assert row["associate_jv_share"] == pytest.approx(20.0)
    assert row["tax_identity_profit_basis"] == "profit_after_tax_before_associate"
    assert row["tax_identity_status"] == "pass"


def test_net_profit_of_joint_venture_is_included_in_operating_profit_bridge():
    text = """
    Consolidated Statement of Profit and Loss for the year ended 31 March 2017
    INR in Million
    Revenue from operations 7,655.70 7,395.20
    Other income 289.89 212.36
    Total income 7,945.59 7,607.56
    Finance costs 3.32 3.43
    Depreciation and amortisation expense 147.98 152.67
    Total expenses 6,143.10 5,973.37
    Profit before share of net profit of investments accounted for using equity method and tax 1,802.49 1,634.19
    Share of net profit of joint venture accounted for using the equity method 43.50 36.05
    Profit before tax 1,845.99 1,670.24
    Total tax expense 610.45 540.41
    Profit for the year 1,235.54 1,129.83
    """
    row = extract_document(text)[0]
    assert row["associate_jv_share"] == pytest.approx(43.50)
    assert row["operating_profit_after_da"] == pytest.approx(1_515.92)
    assert row["operating_profit_pbt_bridge"] == pytest.approx(1_515.92)
    assert row["operating_profit_closure_status"] == "pass"


def test_associate_and_joint_venture_share_rows_are_summed_for_bridge():
    text = """
    Consolidated Statement of Profit and Loss for the year ended 31 March 2023
    INR in Lakh
    Revenue from operations 269,993.58 225,531.32
    Other income 4,173.65 2,953.61
    Total income 274,167.23 228,484.93
    Finance costs 191.22 147.50
    Depreciation and amortization expense 4,627.19 4,065.09
    Total expenses 246,635.98 206,965.14
    Profit before share of profit/(loss) of associate and joint ventures and exceptional items and tax 27,531.25 21,519.79
    Share of profit/(loss) of associate (net of tax) 11.46 (17.85)
    Share of profit/(loss) of joint venture (net of tax) 279.41 (1,210.13)
    Profit before exceptional items and tax 27,822.12 20,291.81
    Profit before tax 27,822.12 20,291.81
    Total tax expense 6,988.72 5,370.30
    Profit for the year 20,833.40 14,921.51
    """
    row = extract_document(text)[0]
    assert row["associate_jv_share"] == pytest.approx(290.87)
    assert row["operating_profit_after_da"] == pytest.approx(23_548.82)
    assert row["operating_profit_pbt_bridge"] == pytest.approx(23_548.82)
    assert row["operating_profit_closure_status"] == "pass"


def test_source_derived_less_tax_expense_parenthesized_current_tax_is_deduction():
    text = """
    Standalone Statement of Profit and Loss for the year ended 31 March 2016
    INR in Lakhs
    Revenue from Operations 2,000.00 1,500.00
    Other Income 100.00 75.00
    Total Income 2,100.00 1,575.00
    Finance Costs 50.00 40.00
    Total Expenses 1,500.00 1,125.00
    Profit Before Tax 600.00 450.00
    Less: Tax Expense
    Current Tax (100.00) (80.00)
    Deferred Tax 20.00 10.00
    Profit after Tax 480.00 360.00
    """
    row = extract_document(text)[0]
    assert row["current_tax"] == pytest.approx(100.0)
    assert row["deferred_tax"] == pytest.approx(20.0)
    assert row["tax_identity_status"] == "pass"


def test_source_derived_associate_loss_precedes_preexceptional_subtotal():
    text = """
    Consolidated Statement of Profit and Loss for the year ended 31 March 2020
    INR in Crores
    Revenue from Operations 8,901.07 12,895.64
    Other Income 112.98 124.33
    Total Income 9,014.05 13,019.97
    Finance Costs 553.85 521.95
    Total Expenses 8,566.89 12,010.95
    Profit Before Share of (Loss) from Associate Companies, Exceptional Items and Tax 447.16 1,009.02
    Share of (Loss) of Associate Companies (10.62) (6.86)
    Profit Before Exceptional Items and Tax 436.54 1,002.16
    Exceptional Items (Net) (49.63) (107.82)
    Profit Before Tax 386.91 894.34
    Total Tax Expense 72.80 326.77
    Profit for the year 314.11 567.57
    """
    row = extract_document(text)[0]
    assert row["associate_jv_share"] == pytest.approx(-10.62)
    assert row["operating_profit_after_da"] == pytest.approx(888.03)
    assert row["operating_profit_pbt_bridge"] == pytest.approx(888.03)
    assert row["operating_profit_closure_status"] == "pass"


def test_source_derived_parenthesized_loss_labels_are_results():
    text = """
    Profit and Loss Account for the year ended 31 March 2011
    Amount in Rupees
    Sales 230,500 251,500
    Other Income 8,000 7,000
    Manufacturing and Other Expenses 302,421 327,724
    Finance Costs 1,167 1,084
    Total Expenses 301,588 356,640
    (Loss) before taxation (63,088) (98,140)
    Current tax - -
    Deferred tax - -
    (Loss) after taxation (63,088) (98,140)
    """
    row = extract_document(text)[0]
    assert row["profit_before_tax"] == pytest.approx(-63088.0)
    assert row["profit_after_tax"] == pytest.approx(-63088.0)
    assert row["tax_identity_status"] == "pass"


@pytest.mark.parametrize(
    "income_lines, expected_revenue, expected_other",
    [
        (
            "Gross Turnover 83,051,255 2,638,540,540\n"
            "Net Turnover 83,051,255 2,400,098,061\n"
            "Miscellaneous Income 174,717,477 40,668,160",
            83051255.0,
            174717477.0,
        ),
        (
            "Commission & Brokerage 147,897 42,380\n"
            "Interest on Income Tax Refund 1,162,431 -",
            147897.0,
            None,
        ),
    ],
)
def test_source_derived_legacy_operating_income_labels_are_anchors(
    income_lines, expected_revenue, expected_other
):
    text = f"""
    Profit and Loss Account for the year ended 31 March 2010
    Amount in Rupees
    INCOME
    {income_lines}
    EXPENDITURE
    Finance Costs 50,000 40,000
    Other Expenses 500,000 400,000
    Total Expenses 550,000 440,000
    Profit before Tax 100,000 80,000
    Current Tax 20,000 16,000
    Profit after Tax 80,000 64,000
    """
    row = extract_document(text)[0]
    assert row["revenue_from_operations"] == pytest.approx(expected_revenue)
    if expected_other is not None:
        assert row["other_income"] == pytest.approx(expected_other)


def test_source_derived_positive_mat_credit_entitlement_reduces_tax_expense():
    text = """
    Consolidated Statement of Profit and Loss for the year ended 31 March 2010
    Amount in Rupees
    Revenue from Operations 2,000.00 1,500.00
    Other Income 100.00 75.00
    Total Income 2,100.00 1,575.00
    Finance Costs 50.00 40.00
    Total Expenses 1,500.00 1,125.00
    Profit Before Tax (201.41) (180.00)
    Current Tax 1.34 -
    Deferred Tax (70.10) (60.00)
    MAT Credit Entitlement 1.29 -
    Profit after Tax (131.36) (120.00)
    """
    row = extract_document(text)[0]
    assert row["mat_credit"] == pytest.approx(-1.29)
    assert row["tax_identity_status"] == "pass"


@pytest.mark.parametrize(
    "fund_heading, secured, unsecured, expected",
    [
        ("A. Source of Funds", "568,794", "189,300", 758094.0),
        ("B Loan Funds", "2,312,351,124", "154,029,390", 2466380514.0),
    ],
)
def test_source_derived_legacy_fund_sections_resolve_reported_loans(
    fund_heading, secured, unsecured, expected
):
    text = f"""
    Balance Sheet as at 31 March 2011
    Amount in Rupees
    {fund_heading}
    Secured Loans {secured} 1,000
    Un-secured Loans {unsecured} 2,000
    Application of Funds
    Total Assets 3,000,000 2,000,000

    Profit and Loss Account for the year ended 31 March 2011
    Amount in Rupees
    Sales 2,000,000 1,500,000
    Other Income 100,000 75,000
    Total Income 2,100,000 1,575,000
    Finance Costs 50,000 40,000
    Total Expenses 1,500,000 1,125,000
    Profit Before Tax 600,000 450,000
    Current Tax 120,000 90,000
    Profit after Tax 480,000 360,000
    """
    row = extract_document(text)[0]
    assert row["total_borrowings"] == pytest.approx(expected)
    assert row["debt_reconciliation_status"] == "reported_total_only"


def test_source_derived_legacy_secured_and_unsecured_nil_proves_zero_debt():
    text = """
    Balance Sheet as at 31 March 2011
    Amount in Rupees Thousands
    Sources of Funds
    Secured Loans Nil Unsecured Loans Nil
    Application of Funds
    Total Assets 7,606 7,000

    Profit and Loss Account for the year ended 31 March 2011
    Amount in Rupees Thousands
    Sales 230,500 251,500
    Other Income 8,000 7,000
    Total Income 238,500 258,500
    Finance Costs 1,167 1,084
    Total Expenses 301,588 356,640
    Loss before taxation (63,088) (98,140)
    Current tax - -
    Loss after taxation (63,088) (98,140)
    """
    row = extract_document(text)[0]
    assert row["total_borrowings"] == 0.0
    assert row["debt_reconciliation_status"] == "true_zero"


def test_source_derived_legacy_loan_funds_row_is_reported_total_debt():
    text = """
    Balance Sheet as on 31st March, 2010
    Amount in Rupees
    Sources of Funds
    Share Capital 430,599,990 330,000,000
    Reserves & Surplus 107,792,439 13,188,432
    Loan Funds 3 11,438,319 10,998,319
    Application of Funds
    Total Assets 549,630,748 364,126,751

    Profit and Loss Account for the year ended 31st March, 2010
    Amount in Rupees
    Income from Operations 650,573,609 593,990,664
    Expenses 600,202,716 591,615,005
    Finance Costs 100,000 90,000
    Profit Before Tax 50,370,893 2,375,659
    Current Tax 5,575,500 751,696
    Profit After Tax 44,795,393 1,623,963
    """
    row = extract_document(text)[0]
    assert row["total_borrowings"] == pytest.approx(11_438_319)
    assert row["debt_reconciliation_status"] == "reported_total_only"


def test_side_by_side_loan_funds_heading_does_not_take_profit_statement_value():
    values = in_bse._extract_balance_sheet(
        [
            "Balance Sheet as at 31 March 2011     Profit and Loss Account",
            "Sources of Funds                     Expenditure",
            "Loan Funds 3                         Increase/(Decrease) in Stocks 12 4,314.36 360.31",
            "Secured Loans 113,846.95 103,060.54",
            "Unsecured Loans 254.91 179.32",
            "Application of Funds",
        ]
    )

    assert values["total_borrowings"] == pytest.approx(114_101.86)
    assert values["debt_reconciliation_status"] == "reported_total_only"


def test_source_legacy_loan_funds_accepts_bare_secured_and_unsecured_rows():
    text = """
    Balance Sheet as at 31 March 2010
    Amount in Rupees
    Source of Funds
    Share Capital 46,13,25,280 46,13,25,280
    Reserves and Surplus 40,71,79,614 34,60,95,355
    Loan Funds
    Secured 3 22,57,88,442 33,96,91,577
    Unsecured 4 6,82,26,573 10,32,89,216
    Deferred Tax Liability 10,77,29,177 8,68,64,536
    Total Assets 127,02,49,086 133,72,65,964

    Profit and Loss Account for the year ended 31 March 2010
    Amount in Rupees
    Revenue from operations 100,000,000 90,000,000
    Other income 1,000,000 900,000
    Total income 101,000,000 90,900,000
    Total expenses 80,000,000 70,000,000
    Profit before tax 21,000,000 20,900,000
    Current tax 5,000,000 4,000,000
    Profit after tax 16,000,000 16,900,000
    """

    row = extract_document(text)[0]

    assert row["total_borrowings"] == pytest.approx(294_015_015)
    assert row["debt_reconciliation_status"] == "reported_total_only"


def test_source_derived_inline_schedule_reference_is_not_deferred_tax():
    text = """
    Profit and Loss Account for the year ended 31 March 2011
    Amount in Rupees
    Sales 230,500 251,500
    Other Income 8,000 7,000
    Total Income 238,500 258,500
    Finance Costs 1,167 1,084
    Total Expenses 301,588 356,640
    Loss before taxation (63,088) (98,140)
    Current tax - -
    Deferred tax (See note 4 of Schedule 14) - -
    Loss after taxation (63,088) (98,140)
    """
    row = extract_document(text)[0]
    assert row["deferred_tax"] == 0.0
    assert row["tax_identity_status"] == "pass"


def test_source_derived_note_only_revenue_header_defers_to_income_identity():
    text = """
    Statement of Profit and Loss for the period ended 31 March 2016
    INR in Lakhs
    Particulars Note 31 March 2016 30 September 2014
    Revenue from operations 21
    Sale of product (net) 83,857.54 99,310.26
    Sale of services 374.55 369.04
    Other operating revenue 285.46 232.04
    Total 84,517.55 99,911.34
    Other income 22 791.84 694.72
    Total Revenue 85,309.39 100,606.06
    Finance costs 26 13,715.32 10,612.54
    Total Expenses 111,083.76 125,720.35
    Loss before exceptional items and tax (25,774.37) (25,114.29)
    Exceptional items (13,650.81) (1,129.53)
    Loss before tax (39,425.18) (26,243.82)
    Current tax - -
    Loss for the period (39,425.18) (26,243.82)
    """
    row = extract_document(text)[0]
    assert row["revenue_from_operations"] == pytest.approx(84517.55)
    assert row["revenue_from_operations_source"] == (
        "derived_total_income_less_other_income"
    )
    assert row["operating_profit_closure_status"] == "pass"


def test_source_derived_ampersand_tax_suffix_does_not_expose_row_formula():
    text = """
    Standalone Statement of Profit and Loss for the year ended 31 March 2025
    INR in Lakhs
    Revenue from operations 19 13,721.18 10,807.34
    Other income 20 44.46 33.74
    Total income 13,765.65 10,841.08
    Finance cost 22 28.43 -
    Total expenses 9,141.09 6,251.75
    Profit/(Loss) before exceptional items & tax (3-4) 4,624.55 4,589.33
    Exceptional items - -
    Profit/(Loss) before tax (5-6) 4,624.55 4,589.33
    Total tax expenses 1,148.08 1,166.38
    Profit/(loss) for the year 3,476.47 3,422.95
    """
    row = extract_document(text)[0]
    assert row["profit_before_exceptional_items_and_tax"] == pytest.approx(4624.55)
    assert row["operating_profit_closure_status"] == "pass"


@pytest.mark.parametrize(
    "heading, expected_period",
    [
        (
            "Ind AS Statement of Profit and Loss for the year ended 31 March 2026",
            "2026-03-31",
        ),
        (
            "PART II - Form of Profit and Loss Account for the year ended 31 March 2013",
            "2013-03-31",
        ),
        (
            "PROFIT & LOSS ACCCOUNT FOR THE YEAR ENDING 31 March 2018",
            "2018-03-31",
        ),
        (
            "PROFIT & LOSS A/C FOR THE YEAR ENDED ON 31st MARCH, 2010",
            "2010-03-31",
        ),
        (
            "Statement of Profit and Loss for the year ended 31st, MARCH 2019",
            "2019-03-31",
        ),
        (
            "Statement of Profit and Loss for the year ended 31st March'2015",
            "2015-03-31",
        ),
    ],
)
def test_source_derived_historical_profit_headings_are_primary(
    heading, expected_period
):
    text = f"""
    {heading}
    Revenue from Operations 2,000.00 1,500.00
    Other Income 100.00 75.00
    Total Income 2,100.00 1,575.00
    Finance Costs 50.00 40.00
    Total Expenses 1,500.00 1,125.00
    Profit Before Tax 600.00 450.00
    Current Tax 120.00 90.00
    Profit for the year 480.00 360.00
    """
    row = extract_document(text)[0]
    assert row["period_end"] == expected_period
    assert row["profit_before_tax"] == pytest.approx(600.0)
    assert row["tax_identity_status"] == "pass"


@pytest.mark.parametrize(
    "label",
    [
        "PROFIT /( LOSS) BEFORE TAX",
        "Profit I(Loss)before tax",
    ],
)
def test_source_derived_ocr_profit_loss_separators_are_accepted(label):
    text = f"""
    Profit and Loss Account for the year ended 31 March 2015
    Revenue from Operations 2,000.00 1,500.00
    Other Income 100.00 75.00
    Total Income 2,100.00 1,575.00
    Finance Costs 50.00 40.00
    Total Expenses 1,500.00 1,125.00
    {label} 600.00 450.00
    Current Tax 120.00 90.00
    Profit for the year 480.00 360.00
    """
    row = extract_document(text)[0]
    assert row["profit_before_tax"] == pytest.approx(600.0)
    assert row["tax_identity_status"] == "pass"


def test_source_derived_scanned_alps_statements_survive_ocr_heading_noise():
    text = """
    ALPS INDUSTRIES LTD.
    | ss BALANCESHEET SHEET
    BALANCE SHEET AS AT 31° MARCH, 2018 AS AT AS AT AS AT
    31.03.18 31.03.17 01.04.16
    Notes Rs.(in Lakh) Rs.(in Lakh) Rs.(in Lakh)
    ASSETS
    TOTAL ASSETS 42,223.74 51,771.06 65,649.22
    EQUITY AND LIABILITIES
    Equity 42,223.74 51,771.06 65,649.22
    Non-current liabilities
    Financial Liability
    Borrowings 15 40,065.23 40,391.43 40,181.23
    Current liabilities
    Financial liabilities
    Borrowings 17 32,287.14 26,721.27 25,713.41

    ALPS INDUSTRIES LTD.
    | STATEMENT OF PROFIT & LOSS OF PROFIT & LOSS
    STATEMENT OF PROFIT & LOSS FOR THE YEAR ENDED 31" MARCH, 2018
    Year Ended Year Ended
    31.03.18 31.03.17
    Notes Rs.(in Lakh) Rs.(in Lakh)
    Income
    Revenue from operations 21 36351.63 50,156.84
    Other Income 22 710.27 444.19
    Total Income 37,061.90 50,601.03
    Expenses
    Finance costs 26 5,449.59 5,318.15
    Other expenses 28 8,837.27 11,084.75
    Total Expense 48,650.97 63,953.58
    Profit/(Loss) before exceptional items and tax (11,589.07) (13,352.55)
    Add/(Less): Exceptional items - 2,558.37
    Profit/(Loss) before Tax (11589.07) (10794.18)
    Less/(-Add): Tax expense - -
    Profit/(Loss) for the period from continuing operations (11,589.07) (10,794.18)
    """
    row = extract_document(text, "Alps Industries Ltd.")[0]
    assert row["period_end"] == "2018-03-31"
    assert row["unit_scale"] == 100_000
    assert row["revenue_from_operations"] == pytest.approx(36351.63)
    assert row["total_borrowings"] == pytest.approx(72352.37)
    assert row["operating_profit_closure_status"] == "pass"
    assert row["tax_identity_status"] == "pass"
    assert row["screen_eligible"] is True


def test_source_derived_scanned_maximus_profit_or_loss_and_rupee_glyph():
    text = """
    BALANCE SHEET AS AT 31ST MARCH, 2018
    (Amount in %)
    Particulars Note As at 31st March, 2018 As at 31st March, 2017
    EQUITY AND LIABILITIES
    Non-Current Liabilities
    Long Term borrowings 5 41,34,791 8,91,118
    Current Liabilities
    Short-term borrowings 7 1,24,09,150 56,777

    STATEMENT OF PROFIT OR LOSS FOR THE YEAR ENDED 31ST MARCH, 2018
    (Amount in %)
    Particulars Note For the year ended 31st March, 2018 For the year ended 31st March, 2017
    INCOME
    Revenue from operations 19 21,80,05,693 10,28,52,084
    Other Income 20 54,01,272 4,05,457
    Total Income 22,34,06,965 10,32,57,541
    EXPENSES
    Purchases of Stock-In-Trade 21 20,69,39,311 8,86,56,308
    Employee Benefit Expenses 22 14,24,444 9,88,341
    Finance Cost 23 14,66,631 15,19,997
    Depreciation 11 10,30,269 2,84,185
    Other Expenses 24 72,13,379 56,40,731
    Total Expenses 21,80,74,034 9,70,89,562
    Profit Before Tax 53,32,931 61,67,979
    Tax Expenses
    Current Tax 12,82,400 21,33,200
    Deferred Tax 2,18,319 25,755
    Income tax earlier year written back - (3,850)
    Profit After Tax 38,32,212 40,12,874
    """
    row = extract_document(text, "Maximus International Limited")[0]
    assert row["period_end"] == "2018-03-31"
    assert row["currency"] == "INR"
    assert row["currency_source"] == "statement_header"
    assert row["unit_scale"] == 1
    assert row["revenue_from_operations"] == pytest.approx(218005693)
    assert row["total_borrowings"] == pytest.approx(16543941)
    assert row["operating_profit_closure_status"] == "pass"
    assert row["tax_identity_status"] == "pass"
    assert row["screen_eligible"] is True


def test_manifest_normalization_retains_identity_and_finality_inputs():
    record = {
        "filing_id": "IN_BSE_500325_2025_abc",
        "filing_date": "2025",
        "company_name": "Reliance Industries Ltd",
        "stock_code": "500325",
        "document_url": "https://example.test/abc.pdf",
        "raw_metadata": {
            "report": {"dt_tm": "2025-08-07T12:30:00", "year": "2025"},
            "scrip": {
                "SCRIP_CD": "500325",
                "scrip_id": "RELIANCE",
                "ISIN_NUMBER": "INE002A01018",
                "Status": "Active",
                "Segment": "Equity",
            },
        },
    }
    got = normalize_manifest_record(record)
    assert got["ticker"] == "500325"
    assert got["symbol"] == "RELIANCE"
    assert got["isin"] == "INE002A01018"
    assert got["publication_date"] == "2025-08-07"
    assert got["listing_status"] == "Active"
    assert got["document_url"].endswith("abc.pdf")


def test_source_mat_credit_entitlement_is_not_consumed_as_current_tax():
    text = """
    Profit and Loss Account for the year ended March 31, 2010
    Rupees in lacs
    Particulars 2010 2009
    Gross Sales and Operating Revenues 13,766.03 11,383.96
    Other Income 52.90 64.48
    Total Income 13,818.93 11,448.44
    Total Expenditure 11,995.68 10,784.01
    Profit before taxation 1,823.25 664.43
    Provision for Income Tax
    -Current Tax (Minimum Alternative Tax) 309.86 75.28
    -Tax adjustment for earlier years (net) (15.70) (22.04)
    -Deferred Tax 64.10 85.92
    -Minimum Aternative Tax Credit Entitlement (177.95) (75.28)
    Net Profit after tax 1,642.94 592.55
    """
    row = extract_document(text)[0]
    assert row["current_tax"] == pytest.approx(309.86)
    assert row["mat_credit"] == pytest.approx(-177.95)
    assert row["income_tax_expense"] == pytest.approx(180.31)
    assert row["tax_identity_status"] == "pass"


def test_source_mat_credit_for_current_year_is_not_consumed_by_bare_current():
    text = """
    Statement of Profit and Loss for the year ended 31 March 2024
    INR in Lakhs
    Particulars 2024 2023
    Revenue from operations 10,000.00 9,000.00
    Other income 100.00 90.00
    Total income 10,100.00 9,090.00
    Total expenses 8,186.41 7,500.00
    Profit from Continuing before Tax 1,913.59 1,590.00
    Current Tax - MAT 328.71 250.00
    MAT Credit for current year (328.71) (250.00)
    MAT Credit relating to earlier years 10.83 -
    Deferred Tax 698.45 600.00
    Profit from Continuing after Tax 1,204.31 990.00
    """
    row = extract_document(text)[0]
    assert row["mat_credit"] == pytest.approx(-328.71)
    assert row["earlier_year_tax"] == pytest.approx(10.83)
    assert row["income_tax_expense"] == pytest.approx(709.28)
    assert row["tax_identity_status"] == "pass"


def test_source_tax_identity_uses_post_tax_profit_before_minority_interest():
    text = """
    Consolidated Statement of Profit and Loss for the year ended 31 March 2025
    INR in Lakhs
    Particulars 2025 2024
    Revenue from operations 1,000.00 900.00
    Other income 10.00 9.00
    Total income 1,010.00 909.00
    Total expenses 526.93 500.00
    Profit/(Loss) before Tax 483.07 409.00
    Current Tax 81.80 70.00
    Deferred Tax 2.17 2.00
    Profit/(Loss) before Minority Interest 399.10 337.00
    Minority Interest 23.50 20.00
    Profit from continuing operations 375.60 317.00
    """
    row = extract_document(text)[0]
    assert row["profit_after_tax_before_minority_interest"] == pytest.approx(399.10)
    assert row["tax_identity_profit_basis"] == "profit_after_tax_before_minority_interest"
    assert row["tax_identity_status"] == "pass"


def test_source_countervailing_mat_credit_cancels_negative_current_tax():
    text = """
    Statement of Profit and Loss for the year ended 31 March 2019
    INR in Lakhs
    Particulars 2019 2018
    Revenue from operations 10,000.00 9,000.00
    Other income 100.00 90.00
    Total income 10,100.00 9,090.00
    Total expenses 5,210.81 5,000.00
    Profit before tax 4,889.19 4,090.00
    Current tax (943.65) (800.00)
    MAT credit entitlement 943.65 800.00
    Deferred tax 171.90 150.00
    Profit after tax 5,061.09 4,240.00
    """
    row = extract_document(text)[0]
    assert row["current_tax"] == pytest.approx(-943.65)
    assert row["mat_credit"] == pytest.approx(943.65)
    assert row["income_tax_expense"] == pytest.approx(171.90)
    assert row["tax_sign_convention"] == "add"
    assert row["tax_identity_status"] == "pass"


def test_source_side_by_side_pbt_uses_numeric_continuation_when_inline_value_is_expense_total():
    text = """
    Consolidated Statement of Profit and Loss for the year ended 31 March 2022
    INR in Lakhs
    Particulars
    Revenue from operations 37,739.35 20,000.00
    Other income 269.72 100.00
    Total income 38,009.07 20,100.00
    Total expenses 37,536.01 19,500.00
    Profit before tax 37,536.01
    473.06 600.00
    Tax expense 60.18 80.00
    Profit after tax 412.88 520.00
    """
    row = extract_document(text)[0]
    assert row["profit_before_tax"] == pytest.approx(473.06)
    assert row["profit_before_tax_source"] == "numeric_continuation_after_side_by_side_label"
    assert row["tax_identity_status"] == "pass"


def test_source_valid_inline_pbt_is_not_replaced_by_cropped_numeric_fragment():
    text = """
    Standalone Statement of Profit and Loss for the year ended 31 March 2024
    INR in Lakhs
    Particulars Notes 2024 2023
    Revenue from operations 26 239,490.36 246,723.73
    Other income 27 14,166.98 10,914.01
    Total income (I + II) 253,657.34 257,637.74
    Finance costs 31 3,573.86 2,600.84
    Depreciation and amortisation expense 32 17,465.14 10,230.33
    Total expenses (IV) 222,133.32 197,590.24
    Profit before tax (III - IV) 31,524.02 60,047.50
    60.46
    Tax expense:
    Current tax 34 7,564.12 15,541.51
    Current tax adjustment related to earlier years 34 (106.76) (116.33)
    Deferred tax 34 912.35 (928.71)
    Total tax expense 8,369.71 14,496.47
    Profit for the year 23,154.31 45,551.03
    """
    row = extract_document(text)[0]
    assert row["profit_before_tax"] == pytest.approx(31524.02)
    assert row["operating_profit_closure_status"] == "pass"
    assert row["tax_identity_status"] == "pass"


def test_source_preexceptional_subtotal_remains_valid_when_final_pbt_scale_is_corrupt():
    text = """
    Statement of Profit and Loss for the year ended 31 March 2024
    INR in Lakhs
    Particulars 2024 2023
    Revenue from operations 950.00 900.00
    Other income 50.00 40.00
    Total income 1,000.00 940.00
    Finance costs 20.00 18.00
    Total expenses 1,100.00 900.00
    Profit before exceptional items and tax (100.00) 40.00
    Exceptional items (2,000.00) -
    Profit before tax (2,100.00) 40.00
    Current tax - 10.00
    Profit for the year (2,100.00) 30.00
    """
    row = extract_document(text)[0]
    assert row["operating_profit_after_da"] == pytest.approx(-130.0)
    assert row["operating_profit_pbt_bridge"] == pytest.approx(-130.0)
    assert row["operating_profit_bridge_basis"] == "pre_exceptional_subtotal"
    assert row["operating_profit_closure_status"] == "pass"


def test_exceptional_item_already_in_total_expenses_allows_neutral_pbt_bridge():
    text = """
    Statement of Profit and Loss for the year ended 31 March 2013
    INR in Lakhs
    Particulars 2013 2012
    Revenue from operations 3,000.00 2,800.00
    Other income 314.92 300.00
    Total income 3,314.92 3,100.00
    Finance costs 12.99 11.00
    Exceptional items 100.77 90.00
    Other expenses 372.55 340.00
    Total expenses 486.31 441.00
    Profit before tax 2,828.61 2,659.00
    Current tax 100.00 90.00
    Profit for the year 2,728.61 2,569.00
    """
    row = extract_document(text)[0]
    assert row["exceptional_items"] == pytest.approx(100.77)
    assert row["operating_profit_after_da"] == pytest.approx(2526.68)
    assert row["operating_profit_pbt_bridge"] == pytest.approx(2526.68)
    assert row["operating_profit_bridge_basis"] == "pbt_without_adjustments"
    assert row["operating_profit_closure_status"] == "pass"


def test_ocr_repeated_c_in_current_tax_is_normalized_before_tax_identity():
    text = """
    Standalone Statement of Profit and Loss for the year ended 31 March 2026
    INR in Lakhs
    Particulars 2026 2025
    Other income 921.23 366.29
    Total income 921.23 366.29
    Finance costs 0.06 0.06
    Total expenses 70.06 39.93
    Profit before tax 851.17 326.35
    CCURRENT TAX 106.76 54.28
    Deferred tax expense 1.42 -
    MAT credit utilized (12.21) 0.78
    Profit after tax 755.20 271.27
    """
    row = extract_document(text)[0]
    assert row["current_tax"] == pytest.approx(106.76)
    assert row["income_tax_expense"] == pytest.approx(95.97)
    assert row["tax_identity_status"] == "pass"


def test_duplicate_for_in_profit_for_year_label_keeps_current_period_value():
    text = """
    Statement of Profit and Loss for the year ended 31 March 2024
    INR in Lakhs
    Particulars Note No. 2023-24 2022-23
    Revenue from operations 22 14,492.07 14,043.74
    Other income 23 212.85 394.66
    Total income 14,704.91 14,438.41
    Finance costs 28 82.35 74.71
    Total expenses 13,571.42 13,428.51
    Profit before tax 1,133.49 1,009.89
    Current tax 10 313.00 249.50
    Deferred tax 10.1 12.24 36.23
    Short/(Excess) Provision of Previous years 3.64 -
    Total tax expense 304.40 285.74
    Profit / (Loss) for for the year 829.09 724.15
    """
    row = extract_document(text)[0]
    assert row["profit_for_period"] == pytest.approx(829.09)
    assert row["tax_identity_status"] == "pass"


def test_single_current_period_earlier_year_tax_is_not_replaced_by_next_row():
    text = """
    Consolidated Statement of Profit and Loss for the period ended March 31, 2023
    Amount in Lakhs
    Particulars Notes March 31, 2023 March 31, 2022
    Revenue from operations 21 4,137.75 4,013.92
    Other income 22 46.90 28.05
    Total income 4,184.66 4,041.97
    Finance costs 27 115.72 122.45
    Total expenses 4,058.97 3,953.08
    Profit before tax 120.15 88.89
    Current tax 23.50 15.45
    Income tax for earlier year 3.57
    Deferred Tax Liability/(Assets) 36.66 -
    Profit for the year 56.42 73.44
    """
    row = extract_document(text)[0]
    assert row["earlier_year_tax"] == pytest.approx(3.57)
    assert row["income_tax_expense"] == pytest.approx(63.73)
    assert row["tax_identity_status"] == "pass"


def test_formal_year_ended_statement_beats_richer_prior_period_summary():
    text = """
    Consolidated Profit and Loss Statement
    for the year ended March 31, 2024
    INR in Lakhs
    Revenue from operations 64,111.64 48,994.62
    Other income 6,987.97 6,528.07
    Total income 71,099.61 55,522.69
    Employee benefit expense 28,436.02 24,606.16
    Finance costs 1,152.67 924.66
    Depreciation and amortisation expense 4,086.02 3,739.31
    Other expenses 20,622.81 16,448.92
    Total expenses 54,297.52 45,719.05
    Profit before tax from continuing operations 16,802.09 9,803.64
    Tax expenses 982.85 1,526.75
    Deferred tax adjustment 1,326.76 63.75
    Profit after tax from continuing operations 14,492.48 8,213.14
    Profit after tax from discontinued operation 34.09 (6,215.35)
    Profit for the year 14,526.57 1,997.79

    Consolidated Statement of Profit and Loss for the year ended March 31, 2025
    INR in Lakhs
    Particulars Note March 31, 2025 March 31, 2024
    Revenue from operations 18 64,111.64 48,994.62
    Other income 19 6,987.97 6,528.07
    Total income 71,099.61 55,522.69
    Finance costs 21 1,152.67 924.66
    Depreciation and amortisation expense 4,086.02 3,739.31
    Other expenses 20,622.81 16,448.92
    Total expenses 54,297.52 45,719.05
    Profit before tax from continuing operations 16,802.09 9,803.64
    Current tax 23 982.85 1,526.75
    Deferred tax expense 24 1,326.76 63.75
    Total tax expense 2,309.61 1,590.50
    Profit for continuing operations, net of tax 14,492.48 8,213.14
    Profit for the year, net of tax 14,526.57 1,997.79
    """
    rows = extract_document(text)
    assert len(rows) == 1
    row = rows[0]
    assert row["period_end"] == "2025-03-31"
    assert row["income_tax_expense"] == pytest.approx(2309.61)
    assert row["tax_identity_status"] == "pass"


def test_primary_profit_candidate_rank_prefers_formal_year_ended_heading():
    narrative = {
        "score": 21,
        "period": "2024-03-31",
        "block": in_bse.StatementBlock(
            "profit_and_loss",
            "consolidated",
            100,
            180,
            "Consolidated Profit and Loss Statement",
        ),
    }
    formal = {
        "score": 19,
        "period": "2025-03-31",
        "block": in_bse.StatementBlock(
            "profit_and_loss",
            "consolidated",
            400,
            480,
            "Consolidated Statement of Profit and Loss for the year ended March 31, 2025",
        ),
    }
    assert in_bse._profit_candidate_rank(formal) > in_bse._profit_candidate_rank(
        narrative
    )


def test_primary_profit_candidate_rank_prefers_later_period_when_headings_tie():
    older_summary = {
        "score": 21,
        "period": "2024-03-31",
        "block": in_bse.StatementBlock(
            "profit_and_loss",
            "consolidated",
            100,
            180,
            "Consolidated Profit and Loss Statement",
        ),
    }
    current_primary = {
        "score": 19,
        "period": "2025-03-31",
        "block": in_bse.StatementBlock(
            "balance_sheet",
            "consolidated",
            400,
            480,
            "Consolidated Balance Sheet     Consolidated Statement of Profit and Loss",
        ),
    }
    assert in_bse._profit_candidate_rank(
        current_primary
    ) > in_bse._profit_candidate_rank(older_summary)


def test_scope_statement_then_of_profit_and_loss_split_heading_is_primary():
    text = """
    STANDALONE STATEMENT
    OF PROFIT AND LOSS FOR THE YEAR ENDED 31 MARCH 2023
    INR in Millions
    Particulars Notes 2023 2022
    Revenue from operations 22 2,540.00 2,193.51
    Other income 23 53.23 46.57
    Total income 2,593.23 2,240.08
    Finance cost 28 64.97 24.68
    Total expenses 2,153.99 1,707.52
    Profit before tax 439.24 532.56
    Current tax 107.27 142.53
    Deferred tax 8.55 (5.14)
    Total tax expense 115.82 137.39
    Profit after tax 323.42 395.17
    """
    rows = extract_document(text)
    assert len(rows) == 1
    row = rows[0]
    assert row["statement_scope"] == "standalone"
    assert row["period_end"] == "2023-03-31"
    assert row["profit_before_tax"] == pytest.approx(439.24)
    assert row["tax_identity_status"] == "pass"


def test_total_profit_with_separate_discontinued_tax_derives_continuing_profit():
    text = """
    Consolidated Statement of Profit and Loss for the period ended March 31, 2023
    Amount in Lakhs
    Particulars Notes March 31, 2023 March 31, 2022
    Revenue from operations 2.17 7,001.71 10,863.27
    Other income 2.18 328.62 611.17
    Total income 7,330.33 11,474.43
    Finance cost 2.22 13.50 8.24
    Total expenses 7,282.45 11,276.17
    Profit before tax 46.89 198.26
    Current tax 12.69 52.32
    Deferred tax charge 2.44 (7.60)
    Tax expenses of earlier years 0.63 7.98
    Total tax expense 15.76 52.70
    Income from discontinued operations 32.71 -
    Tax expenses of discontinued operations 8.23 -
    Profit after tax 55.61 145.56
    """
    row = extract_document(text)[0]
    assert row["discontinued_operations_pre_tax"] == pytest.approx(32.71)
    assert row["discontinued_operations_tax"] == pytest.approx(8.23)
    assert row["profit_from_continuing_operations"] == pytest.approx(31.13)
    assert row["profit_from_continuing_operations_source"] == (
        "derived_total_net_less_pre_tax_discontinued_plus_discontinued_tax"
    )
    assert row["income_tax_expense"] == pytest.approx(15.76)
    assert row["tax_identity_profit_basis"] == (
        "derived_continuing_profit_excluding_discontinued_operations"
    )
    assert row["tax_identity_status"] == "pass"


def test_wrapped_current_tax_wealth_note_uses_statement_amount_not_note_amount():
    text = """
    Consolidated Profit and Loss Account for the year ended 31st March, 2011
    Rupees in lacs
    Particulars Current Year Previous Year
    Total income 10,000.00 9,000.00
    Total expenses 3,868.23 3,565.38
    Profit before Tax 6,131.77 5,434.62
    Provision for Taxation:
    Current Tax (including Wealth tax Rs. 0.62 lacs,
    (Previous Year Rs. 0.71 lacs)) 1,928.36 1,227.60
    MAT Credit (279.11) -
    Deferred Tax (154.68) (90.00)
    (Excess) / Short Provision for Tax of Earlier years (72.03) (5.56)
    Profit after Tax 4,709.23 4,302.58
    """
    row = extract_document(text)[0]
    assert row["current_tax"] == pytest.approx(1928.36)
    assert row["income_tax_expense"] == pytest.approx(1422.54)
    assert row["tax_identity_status"] == "pass"


def test_bracketed_current_tax_note_is_removed_before_statement_values():
    text = """
    Consolidated Profit and Loss Account for the year ended 31st March, 2011
    INR in Lakhs
    Particulars 2011 2010
    Total income 100,000.00 90,000.00
    Total expenses 27,937.89 25,000.00
    Profit before tax for Continuing Operations 72,062.11 65,000.00
    Current Tax [Including Rs 26.20 Lacs (Previous Year Rs 23.51 Lacs) for Wealth Tax] 16,392.62 8,924.15
    Deferred Tax 279.75 1,981.42
    Tax effect (premium on redemption of FCCBs) 1,009.31 -
    Tax adjustment for earlier years 208.55 0.01
    MAT Credit (260.25) -
    Profit after tax from Continuing Operations 54,432.13 54,094.42
    """
    row = extract_document(text)[0]
    assert row["current_tax"] == pytest.approx(16392.62)
    assert row["tax_identity_status"] == "pass"


def test_earlier_year_tax_subtable_and_generic_net_profit_close_identity():
    text = """
    Profit and Loss Account for the year ended 31st December, 2011
    Rs. in Crores
    Particulars 2011 2010
    Total income 8,833.34 7,637.81
    Total expenses 7,130.47 5,975.94
    Profit before tax 1,702.87 1,661.87
    Provision for Taxation:
    For the current year:
      - Current tax 482.00 446.25
      - Deferred tax 59.29 (10.88)
    Relating to earlier years:
      - Current tax (120.71) (92.76)
      - Deferred tax 53.43 55.92
      - Fringe benefits tax - (0.27)
    Net Profit 1,228.86 1,263.61
    """
    row = extract_document(text)[0]
    assert row["earlier_year_tax"] == pytest.approx(-67.28)
    assert row["profit_after_tax"] == pytest.approx(1228.86)
    assert row["income_tax_expense"] == pytest.approx(474.01)
    assert row["tax_identity_status"] == "pass"


def test_wrapped_deferred_tax_description_keeps_current_period_amount():
    text = """
    Statement of Profit and Loss for the year ended 31 March 2015
    INR in Lakhs
    Particulars 2015 2014
    Total income 31,562.58 24,973.26
    Total expenses 29,242.58 27,269.68
    Profit before tax 2,320.00 (2,296.42)
    Current tax 1,281.01 -
    Tax charge in respect of earlier years 163.58 31.81
    Deferred tax charge / (credit) (includes prior
    period credit of Nil) 69.51 (877.97)
    Profit for the year 805.90 (1,450.26)
    """
    row = extract_document(text)[0]
    assert row["deferred_tax"] == pytest.approx(69.51)
    assert row["income_tax_expense"] == pytest.approx(1514.10)
    assert row["tax_identity_status"] == "pass"


def test_earlier_tax_provision_label_is_a_current_period_tax_component():
    text = """
    Statement of Profit and Loss for the year ended 31 March 2015
    INR in Lakhs
    Particulars 2015 2014
    Total income 1,000.00 900.00
    Total expenses 1,233.12 326.33
    Profit before tax (233.12) 573.67
    Current tax - 119.81
    Earlier tax provision 121.43 (724.57)
    Deferred tax (79.24) 69.79
    Profit for the year (275.31) 1,108.64
    """
    row = extract_document(text)[0]
    assert row["earlier_year_tax"] == pytest.approx(121.43)
    assert row["income_tax_expense"] == pytest.approx(42.19)
    assert row["tax_identity_status"] == "pass"


def test_parenthesized_note_reference_after_plain_note_is_not_pbt_value():
    text = """
    Consolidated Statement of Profit and Loss for the year ended 31 March 2015
    INR in Lakhs
    Particulars Note 2015 2014
    Total income 10,000.00 9,000.00
    Total expenses 17,253.82 10,891.56
    Profit before tax (VII - VIII) 3 (8) (7,253.82) (1,891.56)
    Current tax - 490.82
    Earlier tax provision 121.43 (724.57)
    Deferred tax 2 (4) (2,763.78) (538.60)
    Profit for the year (4,611.48) (1,119.21)
    """
    row = extract_document(text)[0]
    assert row["profit_before_tax"] == pytest.approx(-7253.82)
    assert row["tax_identity_status"] == "pass"


def test_financial_template_tax_identity_accounts_for_post_tax_associate_share():
    text = """
    Consolidated Statement of Profit and Loss for the year ended 31 March 2017
    INR in Rupees
    Particulars 2017 2016
    Total income 10,000,000 9,000,000
    Total expenses 12,198,045 8,000,000
    Profit before tax (2,198,045) 1,000,000
    Current tax 27,585 20,000
    Deferred tax credit (575,724) (10,000)
    Share in profit of associate companies 4,189,914 3,598,934
    Profit for the period 2,540,008 4,588,934
    """
    row = extract_document(text, company_name="Example Finance Limited")[0]
    assert row["industry_template"] == "financial_services"
    assert row["profit_before_tax_basis"] == "before_associate_share"
    assert row["tax_identity_profit_basis"] == (
        "profit_for_period_after_associate_share"
    )
    assert row["tax_identity_status"] == "pass"


def test_source_pbt_sign_is_recovered_from_reported_income_expense_subtotals():
    text = """
    Statement of Profit and Loss for the year ended 31 March 2013
    INR in Lakhs
    Particulars 2013 2012
    Revenue from operations 26,810.43 24,500.00
    Other income 62.79 70.46
    Total income 26,873.22 24,570.46
    Finance costs 166.33 148.64
    Total expenses 28,268.02 25,750.21
    Profit before exceptional items and tax 1,394.80 1,179.75
    Exceptional items - -
    Profit before tax 1,394.80 1,179.75
    Current tax 0.35 0.33
    Profit for the year 1,395.15 1,180.08
    """
    row = extract_document(text)[0]
    assert row["profit_before_tax"] == pytest.approx(-1394.80)
    assert row["profit_before_tax_source"] == (
        "sign_recovered_from_total_income_less_total_expenses"
    )
    assert row["profit_for_period"] == pytest.approx(-1395.15)
    assert row["operating_profit_closure_status"] == "pass"
    assert row["tax_identity_status"] == "pass"


def test_source_compound_note_references_are_not_monetary_values():
    text = """
    Statement of Profit and Loss for the year ended March 31, 2013
    Amount in INR
    Particulars Note No. March 31, 2013 March 31, 2012
    Revenue from operations 3(1) 1,00,92,53,637 49,05,97,422
    Other income 3(2) 50,15,859 4,74,137
    Total revenue 1,01,42,69,496 49,10,71,559
    Finance costs 3(5) 2,29,74,283 51,83,595
    Depreciation expense 2(11) 4,18,75,388 1,11,91,892
    Total expenses 89,86,72,267 45,55,86,128
    Profit before tax 11,55,97,229 3,54,85,431
    Current tax 2,74,85,386 71,00,000
    Net current tax 2,74,85,386 71,00,000
    Deferred tax 2(5) 1,05,45,470 74,44,754
    Profit for the period 7,75,66,373 2,09,40,677
    """
    row = extract_document(text)[0]
    assert row["revenue_from_operations"] == pytest.approx(1009253637)
    assert row["other_income"] == pytest.approx(5015859)
    assert row["finance_costs"] == pytest.approx(22974283)
    assert row["depreciation_amortization"] == pytest.approx(41875388)
    assert row["operating_profit_closure_status"] == "pass"
    assert row["tax_identity_status"] == "pass"


def test_source_left_column_bleed_does_not_create_second_profit_value_column():
    text = """
    Consolidated Statement of Profit and Loss for the year ended 31st March, 2024
    INR in lakhs
    Particulars Note For the Year Ended
    No. 31st March, 2024
    Revenue from Operations 29 39,457.84
    Other Income 30 542.40
    Total Income 40,000.24
    20,262.55 Cost of Materials Consumed 31 12,847.10
    18,904.20 Employee Benefits Expense 32 7,523.70
    Finance Costs 33 270.17
    Depreciation and Amortization Expense 34 1,644.87
    Other Expenses 35 12,164.27
    Total Expenses 36,845.13
    Profit Before Tax 3,155.11
    Current Tax 1,310.54
    Deferred Tax (503.58)
    Total Tax Expenses 806.96
    Profit for the Year 2,348.15
    """
    row = extract_document(text)[0]
    assert row["revenue_from_operations"] == pytest.approx(39457.84)
    assert row["other_income"] == pytest.approx(542.40)
    assert row["finance_costs"] == pytest.approx(270.17)
    assert row["depreciation_amortization"] == pytest.approx(1644.87)
    assert row["operating_profit_closure_status"] == "pass"
    assert row["tax_identity_status"] == "pass"


def test_source_total_exceptional_items_does_not_overwrite_total_expenses():
    text = """
    Consolidated Statement of Profit and Loss for the year ended 31 March 2025
    INR in crores
    Particulars 2025 2024
    Revenue from operations 950.00 900.00
    Other income 50.00 40.00
    Total income 1,000.00 940.00
    IV Expenses:
    Finance costs 20.00 18.00
    Total expenses 900.00 850.00
    Profit before exceptional items and tax 100.00 90.00
    Exceptional items:
    Loss on sale of investments (20.00) -
    Total exceptional items (20.00) -
    Profit before tax 80.00 90.00
    Current tax 20.00 22.00
    Profit for the year 60.00 68.00
    """
    row = extract_document(text)[0]
    assert row["total_expenses"] == pytest.approx(900.0)
    assert row["exceptional_items"] == pytest.approx(-20.0)
    assert row["operating_profit_closure_status"] == "pass"
    assert row["tax_identity_status"] == "pass"


def test_source_financing_operations_residual_is_retained_as_operating_income():
    text = """
    Consolidated Statement of Profit and Loss for the year ended 31 March 2025
    INR in crores
    Particulars Notes 2025 2024
    Revenue from operations 2.1 42,333.28 40,956.35
    Income from financing operations 2.1 6,201.86 4,746.99
    Other income 2.2 358.46 227.88
    Total income 48,893.60 45,931.22
    Finance costs 2.4 3,930.21 2,982.25
    Total expenses 44,344.25 41,757.31
    Profit before share of profit of associates, exceptional items and tax 4,549.35 4,173.91
    Share of profit of associates 31.58 16.38
    Profit before exceptional items and tax 4,580.93 4,190.29
    Exceptional items 15.40 (84.22)
    Profit before tax 4,596.33 4,106.07
    Current tax 1,725.86 1,478.91
    Deferred tax (512.32) (69.18)
    Profit for the year 3,382.79 2,696.34
    """
    row = extract_document(text)[0]
    assert row["total_income"] == pytest.approx(48893.60)
    assert row["other_operating_income"] == pytest.approx(6201.86)
    assert row["other_operating_income_source"] == (
        "derived_reported_total_residual"
    )
    assert row["operating_profit_closure_status"] == "pass"
    assert row["tax_identity_status"] == "pass"


def test_source_bare_operating_income_is_included_in_derived_total_income():
    text = """
    Profit and Loss Account for the year ended 31 March 2010
    INR in lakhs
    Particulars 2010 2009
    Net sales 20,193.80 18,000.00
    Operating income 370.43 300.00
    Other income 236.25 200.00
    Finance costs 144.25 130.00
    Total expenses 18,858.41 17,000.00
    Profit before tax 1,942.07 1,500.00
    Current tax 600.00 400.00
    Deferred tax 11.80 10.00
    Profit after tax 1,330.27 1,090.00
    """
    row = extract_document(text)[0]
    assert row["other_operating_income"] == pytest.approx(370.43)
    assert row["total_income"] == pytest.approx(20800.48)
    assert row["operating_profit_closure_status"] == "pass"
    assert row["tax_identity_status"] == "pass"


def test_source_post_tax_sign_is_recovered_when_parentheses_are_lost():
    text = """
    Statement of Profit and Loss for the year ended 31 March 2013
    INR in lakhs
    Particulars 2013 2012
    Revenue from operations 1,000.00 900.00
    Other income 50.00 40.00
    Total income 1,050.00 940.00
    Finance costs 100.00 90.00
    Total expenses 3,000.16 2,500.00
    Profit before tax (1,950.16) (1,560.00)
    Current tax - -
    Deferred tax 552.54 400.00
    Profit for the period 1,397.62 1,160.00
    """
    row = extract_document(text)[0]
    assert row["profit_for_period"] == pytest.approx(-1397.62)
    assert row["profit_for_period_source"] == (
        "sign_recovered_with_pre_tax_total_and_tax_identity"
    )
    assert row["operating_profit_closure_status"] == "pass"
    assert row["tax_identity_status"] == "pass"


def test_source_tax_identity_selects_valid_post_tax_subtotal_over_row_number():
    text = """
    Consolidated Statement of Profit and Loss for the year ended 31 March 2017
    INR in rupees
    Particulars 2017 2016
    Revenue from operations 1,200.00 1,100.00
    Other income 50.00 40.00
    Total income 1,250.00 1,140.00
    Finance costs 100.00 90.00
    Total expenses 250.00 240.00
    Profit before tax 1,000.00 900.00
    Total tax expense 200.00 180.00
    Profit after tax 5 4
    Profit for the period 800.00 720.00
    """
    row = extract_document(text)[0]
    assert row["profit_after_tax"] == pytest.approx(5.0)
    assert row["tax_identity_profit_basis"] == "profit_for_period"
    assert row["tax_identity_status"] == "pass"


def test_source_tax_identity_recovers_post_exceptional_pbt_from_subtotals():
    text = """
    Standalone Statement of Profit and Loss for the year ended 31 March 2023
    INR in lakhs
    Particulars 2023 2022
    Revenue from operations 32,892.05 38,796.06
    Other income 756.00 752.62
    Total income 33,648.05 39,548.68
    Finance costs 2,094.23 1,722.60
    Total expenses 33,557.92 35,292.44
    Profit before exceptional items and tax 90.13 4,256.24
    Exceptional items loss 5,958.92 -
    Profit after exceptional items (5,868.79) 4,256.24
    Tax expense credit (115.52) 1,232.51
    Profit for the year (5,753.27) 3,023.73
    """
    row = extract_document(text)[0]
    assert row["profit_before_tax"] == pytest.approx(-5868.79)
    assert row["profit_before_tax_source"] == (
        "tax_identity_validated_preexceptional_minus_exceptional"
    )
    assert row["tax_identity_status"] == "pass"


def test_source_net_current_tax_does_not_double_count_earlier_year_adjustment():
    text = """
    Statement of Profit and Loss for the year ended 31 March 2012
    INR in lakhs
    Particulars 2012 2011
    Revenue from operations 900.00 800.00
    Other income 100.00 80.00
    Total income 1,000.00 880.00
    Finance costs 50.00 40.00
    Total expenses 261.60 240.00
    Profit before tax 738.40 640.00
    Current tax 180.00 150.00
    Current tax relating to earlier years (14.44) (10.00)
    Net current tax 165.56 140.00
    Deferred tax 45.90 35.00
    Profit for the period 526.94 465.00
    """
    row = extract_document(text)[0]
    assert row["income_tax_expense"] == pytest.approx(211.46)
    assert row["tax_identity_status"] == "pass"


def test_source_defferd_tax_and_unlabelled_tax_subtotal_close_identities():
    text = """
    Statement of Profit and Loss for the year ended 31 March 2022
    INR in Rupees
    Particulars 2022 2021
    Revenue from operations 49,887,147 40,000,000
    Other income 26,851,292 20,000,000
    Total income 76,738,439 60,000,000
    Total expenses 48,286,062 40,000,000
    Earning before interest, tax, depreciation and amortisation 28,452,377 20,000,000
    Depreciation and amortisation 4,266,052 3,000,000
    Profit before tax 24,186,325 17,000,000
    Current tax 4,125,785 3,000,000
    Defferd tax (180,455) (100,000)
    (c) MAT Credit
    3,945,330 2,900,000
    Profit after tax 20,240,995 14,100,000
    """
    row = extract_document(text)[0]
    assert row["deferred_tax"] == pytest.approx(-180455)
    assert row["income_tax_expense"] == pytest.approx(3945330)
    assert row["finance_costs"] == 0.0
    assert row["operating_profit_closure_status"] == "pass"
    assert row["tax_identity_status"] == "pass"


def test_source_other_operating_income_is_included_in_operating_profit_component():
    text = """
    Statement of Profit and Loss for the year ended 31 March 2012
    Currency: INR in lakhs
    Particulars 2012 2011
    Revenue from operations (net) 58,406.53 63,648.41
    Other operating income 1,000.07 477.30
    Other income 2,841.25 1,990.75
    Total revenues 62,247.85 66,116.46
    Finance costs 95.89 125.28
    Total expenses 41,756.16 42,548.71
    Profit before tax 20,491.69 23,567.75
    Tax expense 6,022.29 7,038.45
    Profit for the year 14,469.40 16,529.30
    """
    row = extract_document(text)[0]
    assert row["other_operating_income"] == pytest.approx(1000.07)
    assert row["operating_profit_after_da"] == pytest.approx(17746.33)
    assert row["operating_profit_pbt_bridge"] == pytest.approx(17746.33)
    assert row["operating_profit_closure_status"] == "pass"
    assert row["tax_identity_status"] == "pass"


def test_source_tax_section_includes_regulatory_reserve_transfers():
    text = """
    Consolidated Profit and Loss Account for the year ending 31st March 2016
    INR in Rupees
    Particulars 31.03.2016 31.03.2015
    Revenue from Operations 24,340,068.00 22,881,236.68
    Other Income 3,636,795.00 2,572,702.39
    Total Revenue 27,976,863.00 25,453,939.07
    Total Expenses 27,578,744.15 23,843,836.09
    Profit Before Tax 398,118.85 1,610,102.98
    Tax Expenses
    (i) Current Tax 75,862.00 331,341.00
    (ii) Deferred Tax (19,796.00) 103,624.00
    (iii) Transferred to Spl. Reserve 46,804.00 -
    (iv) Trf. to bad & Doubtful Reserve 171,700.00 -
    Profit for the period from continuing operations 123,548.85 1,175,137.98
    """
    row = extract_document(text)[0]
    assert row["tax_reserve_transfer"] == pytest.approx(218504.0)
    assert row["income_tax_expense"] == pytest.approx(274570.0)
    assert row["tax_identity_status"] == "pass"


def test_source_tax_annotations_and_excess_short_provision_are_not_notes():
    dic = """
    Statement of Profit and Loss for the year ended 31 December 2017
    INR in Lakhs
    Particulars 2017 2016
    Revenue from operations 1,000.00 900.00
    Other income 10.00 9.00
    Total income 1,010.00 909.00
    Total expenses 1,176.79 600.85
    Profit / (Loss) before Tax (166.79) 308.15
    Tax Expense
    Current Tax - For the Year (Note 29) - 93.50
    Current Tax- Write Back Relating to Earlier Year (30.00) -
    Deferred Tax - (Credit) (13.10) (13.77)
    Profit / (Loss) for the Year (123.69) 228.42
    """
    row = extract_document(dic)[0]
    assert row["current_tax"] == 0.0
    assert row["income_tax_expense"] == pytest.approx(-43.10)
    assert row["tax_identity_status"] == "pass"

    parle = """
    Statement of Profit and Loss for the period ended 31 March 2018
    INR in Rupees
    Particulars 2018 2017
    Revenue from operations 1,000,000 900,000
    Other income 10,000 9,000
    Total income 1,010,000 909,000
    Total expenses 898,553 800,000
    Profit before tax 111,447 109,000
    Tax expense
    Current tax 13,450 15,000
    Deferred tax 44,620 (1,000)
    Excess/Short provision of tax 11,570 -
    Profit for the period from continuing operations 41,807 95,000
    """
    row = extract_document(parle)[0]
    assert row["tax_provision_adjustment"] == pytest.approx(11570)
    assert row["tax_identity_status"] == "pass"


def test_source_final_pbt_after_exceptional_wording_overrides_intermediate_subtotal():
    nirlon = """
    Statement of Profit and Loss for the year ended 31 March 2013
    INR in Lakhs
    Particulars 2013 2012
    Revenue from operations 10,000.00 9,000.00
    Other income 100.00 90.00
    Total income 10,100.00 9,090.00
    Total expenses 8,975.58 8,000.00
    Profit before exceptional and extra ordinary items and tax 1,124.42 1,090.00
    Add: Exceptional item 1,150.67 -
    Profit after exceptional and extra ordinary items and tax 2,275.09 1,090.00
    Less: Tax Expense
    Current Tax 490.00 76.05
    Deferred Tax 582.01 (100.00)
    Profit for the year after Tax 1,203.08 1,113.95
    """
    row = extract_document(nirlon)[0]
    assert row["profit_before_tax"] == pytest.approx(2275.09)
    assert row["tax_identity_status"] == "pass"

    technofab = """
    Consolidated Statement of Profit and Loss for the year ended 31 March 2020
    INR in Rupees
    Particulars 2020 2019
    Revenue from operations 1,000,000,000 900,000,000
    Other income 1,000,000 900,000
    Total income 1,001,000,000 900,900,000
    Total expenses 1,940,220,675 1,000,000,000
    Profit / (Loss) before Exceptional items and Tax (939,220,675) (99,100,000)
    Exceptional Items 1,296,813,694 -
    Profit / (Loss) after Exceptional items but Before Tax (2,236,034,369) (99,100,000)
    Tax Expense
    Current Tax 4,739,500 1,000,000
    Deferred Tax 124,555,295 (10,000,000)
    Tax Adjustment for Earlier Years 21,441,439 -
    Total Tax Expenses 150,736,234 (9,000,000)
    Profit / (Loss) for the period from continuing operations (2,386,770,603) (90,100,000)
    """
    row = extract_document(technofab)[0]
    assert row["profit_before_tax"] == pytest.approx(-2236034369)
    assert row["tax_identity_status"] == "pass"


def test_source_mat_subtotal_and_utilisation_signs_follow_printed_tax_arithmetic():
    nocil = """
    Statement of Profit and Loss for the year ended 31 March 2013
    INR in Lakhs
    Particulars 2013 2012
    Revenue from operations 10,000.00 9,000.00
    Other income 100.00 90.00
    Total income 10,100.00 9,090.00
    Total expenses 5,686.53 4,000.00
    Profit before tax 4,413.47 5,090.00
    Tax expense
    Current tax 461.84 1,197.83
    Deferred tax 498.61 94.34
    Less: MAT credit entitlement (796.00) -
    164.45 1,292.17
    Profit for the year 4,249.02 3,797.83
    """
    row = extract_document(nocil)[0]
    assert row["income_tax_expense"] == pytest.approx(164.45)
    assert row["tax_identity_status"] == "pass"

    ces = """
    Statement of Profit and Loss for the year ended 30 June 2013
    INR in Rupees
    Particulars 2013 2012
    Revenue from operations 100,000,000 90,000,000
    Other income 1,000,000 900,000
    Total income 101,000,000 90,900,000
    Total expenses 48,005,045 58,173,414
    Profit Before Tax 52,994,955 32,726,586
    Tax expense
    Current tax 10,936,474 10,850,925
    Less: MAT Credit 1,883,409 5,152,487
    Net Current Tax 9,053,065 5,698,438
    Deferred tax (380,158) 462,747
    Profit for the period 44,322,047 26,565,401
    """
    row = extract_document(ces)[0]
    assert row["current_tax_net"] == pytest.approx(9053065)
    assert row["tax_identity_status"] == "pass"

    utilisation = """
    Statement of Profit and Loss for the year ended 31 March 2024
    INR in Lakhs
    Particulars 2024 2023
    Revenue from operations 10,000.00 9,000.00
    Other income 100.00 90.00
    Total income 10,100.00 9,090.00
    Total expenses 7,195.09 8,000.00
    Profit before tax 2,904.91 1,090.00
    Tax expense
    Current tax 513.60 200.00
    MAT Credit Utilisation 137.47 100.00
    Deferred tax charge 223.60 50.00
    Profit for the year 2,030.24 740.00
    """
    row = extract_document(utilisation)[0]
    assert row["mat_credit"] == pytest.approx(137.47)
    assert row["tax_identity_status"] == "pass"


def test_source_wrapped_tax_column_subtotals_are_not_double_counted():
    steelcast = """
    Statement of Profit and Loss for the year ended 31 March 2016
    INR in Rupees
    Particulars 2016 2015
    Revenue from operations 10,000,000 9,000,000
    Other income 100,000 90,000
    Total income 10,100,000 9,090,000
    Total expenses 5,952,830 8,000,000
    Profit Before Tax 4,147,170 1,090,000
    Tax Expenses
    Current tax -
    Deferred Tax (41,957) (39,725,907) (40,625,455)
    2,860,000 2,818,043
    Net Profit for the year 1,329,127 1,000,000
    """
    row = extract_document(steelcast)[0]
    assert row["current_tax"] == pytest.approx(2860000)
    assert row["income_tax_expense"] == pytest.approx(2818043)
    assert row["tax_identity_status"] == "pass"

    magellanic = """
    Statement of Profit and Loss for the year ended 31 March 2015
    INR in Rupees
    Particulars 2015 2014
    Revenue from operations 20,000,000 10,000,000
    Other income 1,000,000 900,000
    Total income 21,000,000 10,900,000
    Total expenses 4,578,758 5,110,318
    Profit before Tax 16,421,242 5,789,682
    Tax expense
    Current Tax 3,465,000 1,525,000
    Deferred Tax - 3,465,000 52,873 1,577,873
    Profit for the Year 12,956,242 4,211,809
    """
    row = extract_document(magellanic)[0]
    assert row["deferred_tax"] == 0.0
    assert row["income_tax_expense"] == pytest.approx(3465000)
    assert row["tax_identity_status"] == "pass"


def test_source_pre_pbt_balance_tax_is_ignored_and_defferred_is_normalized():
    contaminated = """
    Balance Sheet as at 31 March 2017
    INR in Rupees
    Deferred tax asset 327,261 300,000
    Revenue from operations 1,000,000 900,000
    Other income - -
    Total income 1,000,000 900,000
    Total expenses 1,557,540 800,000
    Profit before tax (557,540) 100,000
    Tax Expenses
    Current Tax - -
    Profit for the period (557,540) 100,000
    """
    row = extract_document(contaminated)[0]
    assert row.get("deferred_tax") is None
    assert row["tax_identity_status"] == "pass"

    misspelled = """
    Statement of Profit and Loss for the year ended 31 March 2014
    INR in Lakhs
    Particulars 2014 2013
    Revenue from operations 10,000.00 9,000.00
    Other income 100.00 90.00
    Total income 10,100.00 9,090.00
    Total expenses 22,533.71 20,000.00
    Profit/Loss Before Tax (12,433.71) (10,910.00)
    Tax Expenses
    Provision for Tax - -
    Provision for Defferred Tax (Net) (877.38) (965.43)
    Profit (Loss) for the period (11,556.33) (9,944.57)
    """
    row = extract_document(misspelled)[0]
    assert row["deferred_tax"] == pytest.approx(-877.38)
    assert row["tax_identity_status"] == "pass"


def test_source_wrapped_continuing_result_skips_roman_formula_fragment():
    text = """
    Statement of Profit and Loss for the year ended 31 March 2021
    INR in Rupees
    Particulars 2021 2020
    Revenue from operations 10,000,000 9,000,000
    Other income 100,000 90,000
    Total income 10,100,000 9,090,000
    Total expenses 13,765,866 11,000,000
    Profit/(loss) before tax (3,665,866) (1,910,000)
    Tax expense
    Current tax - -
    Deferred tax (107,408) (105,324)
    Profit (Loss) for the period from continuing operations (V -
    VII (3,558,459) (1,804,676)
    VI)
    """
    row = extract_document(text)[0]
    assert row["profit_from_continuing_operations"] == pytest.approx(-3558459)
    assert row["tax_identity_status"] == "pass"


def test_source_regulatory_deferral_uses_profit_before_regulatory_movement():
    text = """
    Standalone Statement of Profit and Loss for the year ended 31 March 2024
    INR in Crores
    Particulars 2024 2023
    Revenue from operations 20,000.00 19,000.00
    Other income 100.00 90.00
    Total income 20,100.00 19,090.00
    Total expenses 1,526.03 2,000.00
    Profit before Tax and Regulatory Deferral Account Balances 18,573.97 17,090.00
    Tax expense 2,631.98 2,000.00
    Profit for the period before Regulatory Deferral Account 15,941.99 15,090.00
    Balances
    Net movement in Regulatory Deferral Account Balances (564.51) 256.43
    Profit from Continuing Operations 15,377.48 15,346.43
    """
    row = extract_document(text)[0]
    assert row["profit_before_regulatory_deferral"] == pytest.approx(15941.99)
    assert row["tax_identity_profit_basis"] == "profit_before_regulatory_deferral"
    assert row["tax_identity_status"] == "pass"


def test_source_current_tax_including_earlier_year_disclosure_is_not_double_counted():
    text = """
    Consolidated Statement of Profit and Loss for the year ended 31 March 2015
    INR in Lakhs
    Particulars 2015 2014
    Revenue from operations 10,000.00 9,000.00
    Other income 100.00 90.00
    Total income 10,100.00 9,090.00
    Total expenses 20,846.61 20,000.00
    Loss before tax (10,746.61) (10,910.00)
    Tax expenses
    Current tax (including for earlier years 214.47 lakhs (Previous year 19.21)) 2,729.20 311.80
    MAT credit entitlement (82.49) (29.73)
    Deferred tax (credit) (1,860.01) (2,641.05)
    Loss after tax, before minority interest (11,533.31) (8,551.02)
    """
    row = extract_document(text)[0]
    assert row["current_tax"] == pytest.approx(2729.20)
    assert row.get("earlier_year_tax") is None
    assert row["tax_identity_status"] == "pass"


def test_source_bare_mat_tax_and_before_associate_basis_close_tax():
    standalone = """
    Statement of Profit and Loss for the year ended 31 March 2021
    INR in Rupees
    Particulars 2021 2020
    Revenue from operations 100,000,000 90,000,000
    Other income 1,000,000 900,000
    Total income 101,000,000 90,900,000
    Total expenses 45,032,122 40,000,000
    Profit Before Tax 55,967,878 50,900,000
    Tax Expenses
    Current Tax - -
    MAT 9,794,379 21,800,000
    Short / ( Excess ) Provision of Earlier Year 13,115,405 -
    Profit For The Year 33,058,094 29,100,000
    """
    row = extract_document(standalone)[0]
    assert row["minimum_alternative_tax"] == pytest.approx(9794379)
    assert row["tax_identity_status"] == "pass"

    associate = """
    Consolidated Statement of Profit and Loss for the year ended 31 March 2020
    INR in Rupees
    Particulars 2020 2019
    Revenue from operations 100,000,000 90,000,000
    Other income 1,000,000 900,000
    Total income 101,000,000 90,900,000
    Total expenses 80,462,724 80,000,000
    Profit before Tax 20,537,276 10,900,000
    Share in Profit of Associate Companies (Net of Taxation) 443,796 100,000
    Tax Expense 9,687,579 2,000,000
    Profit for the Period from continuing Operations 11,293,493 9,000,000
    """
    row = extract_document(associate, "Bajrang Finance Ltd.")[0]
    assert row["tax_identity_profit_basis"] == "continuing_operations_after_associate_share"
    assert row["tax_identity_status"] == "pass"


def test_source_negative_tax_presentation_preserves_positive_mat_entitlement():
    text = """
    Profit and Loss Account for the year ended 31 March 2010
    INR in Rupees
    Particulars 2010 2009
    Revenue from operations 20,000,000 19,000,000
    Other income 1,000,000 900,000
    Total income 21,000,000 19,900,000
    Total expenses 3,456,348 3,000,000
    Profit before Tax 17,543,652 16,900,000
    Tax Liability
    Current Tax (2,980,000) (1,900,000)
    Deferred Tax (6,181,012) (5,545,243)
    MAT Entitlement 2,980,000 1,840,000
    Prior Years (126,629) (56,460)
    Profit After Tax 11,236,011 11,238,297
    """
    row = extract_document(text)[0]
    assert row["mat_credit"] == pytest.approx(2980000)
    assert row["tax_identity_status"] == "pass"


def test_source_letter_enumerated_single_tax_value_is_not_treated_as_note():
    text = """
    Statement of Profit and Loss for the year ended 31 March 2024
    INR in Lakhs
    Particulars 2024 2023
    Revenue from operations 1,000.00 900.00
    Other income 10.00 9.00
    Total income 1,010.00 909.00
    Total expenses 1,024.93 746.62
    Profit Before Tax (14.93) 162.38
    Tax Expense
    (a) Current Tax - 17.39
    (b) Earlier year tax 6.65
    (c) Deferred Tax - -
    Profit For The Year (21.58) 145.00
    """
    row = extract_document(text)[0]
    assert row["earlier_year_tax"] == pytest.approx(6.65)
    assert row["tax_identity_status"] == "pass"


def test_source_control_character_word_separators_are_sanitized():
    text = (
        "Statement\x01of\x01Profit\x01and\x01Loss\x01for\x01the\x01year\x01ended\x01"
        "31\x01March\x012013\n"
        "INR\x01in\x01Lakhs\n"
        "Revenue\x01from\x01operations\x011,000.00\x01900.00\n"
        "Other\x01income\x0110.00\x019.00\n"
        "Total\x01income\x011,010.00\x01909.00\n"
        "Total\x01expenses\x01900.00\x01813.00\n"
        "Profit\x01before\x01tax\x01110.00\x0196.00\n"
        "Current\x01tax\x0130.00\x0125.00\n"
        "Profit\x01for\x01the\x01year\x0180.00\x0171.00\n"
    )

    row = extract_document(text)[0]

    assert row["period_end"] == "2013-03-31"
    assert row["revenue_from_operations"] == pytest.approx(1000.0)
    assert row["tax_identity_status"] == "pass"


def test_source_observed_statement_heading_ocr_typos_are_normalized():
    text = """
    Statoment of Profit and Loss forthe year onded 31 March 2019
    INR in Lakhs
    Revenue from operations 1,000.00 900.00
    Other income 10.00 9.00
    Total income 1,010.00 909.00
    Total expenses 900.00 813.00
    Profit before tax 110.00 96.00
    Current tax 30.00 25.00
    Profit for the year 80.00 71.00
    """

    row = extract_document(text)[0]

    assert row["period_end"] == "2019-03-31"
    assert row["revenue_from_operations"] == pytest.approx(1000.0)
    assert row["tax_identity_status"] == "pass"


def test_source_abbreviated_month_statement_period_is_parsed():
    text = """
    Statement of Profit and Loss
    in Rupees million
    Note Year ended Year ended
    31 Dec. 2013 31 Dec. 2012
    Revenue from operations 1,000.00 900.00
    Other income 10.00 9.00
    Total income 1,010.00 909.00
    Total expenses 900.00 813.00
    Profit before tax 110.00 96.00
    Current tax 30.00 25.00
    Profit for the year 80.00 71.00
    """

    row = extract_document(text)[0]

    assert row["period_end"] == "2013-12-31"
    assert row["unit_scale"] == 1_000_000
    assert row["tax_identity_status"] == "pass"


def test_source_scope_suffix_on_profit_heading_is_preserved():
    standalone = """
    Statement of Profit & Loss for the year ended 31 March 2024 - STANDALONE
    INR in Lakhs
    Revenue from operations 1,000.00 900.00
    Other income 10.00 9.00
    Total income 1,010.00 909.00
    Total expenses 900.00 813.00
    Profit before tax 110.00 96.00
    Current tax 30.00 25.00
    Profit for the year 80.00 71.00
    """
    consolidated = """
    Statement of Profit & Loss for the year ended 31 March 2024 - CONSOLIDATED
    INR in Lakhs
    Revenue from operations 2,000.00 1,800.00
    Other income 20.00 18.00
    Total income 2,020.00 1,818.00
    Total expenses 1,800.00 1,626.00
    Profit before tax 220.00 192.00
    Current tax 60.00 50.00
    Profit for the year 160.00 142.00
    """

    rows = extract_document(standalone + consolidated)

    assert [row["statement_scope"] for row in rows] == [
        "standalone",
        "consolidated",
    ]


def test_source_reported_total_income_tax_overrides_incomplete_component_sum():
    credit = """
    Standalone Statement of Profit and Loss for the year ended 31 March 2022
    INR in Lakhs
    Revenue from operations 1,000.00 900.00
    Other income 10.00 9.00
    Total income 1,010.00 909.00
    Total expenses 2,150.08 1,707.59
    Loss before tax (1,140.08) (798.59)
    Income tax expense / (credit)
    Current tax - -
    Deferred tax charge / (benefit) (239.96) (221.34)
    Total income tax (credit) / expense (194.41) (221.34)
    Net Loss for the year (945.67) (577.25)
    """
    row = extract_document(credit)[0]
    assert row["income_tax_expense"] == pytest.approx(-194.41)
    assert row["income_tax_expense_source"] == "reported_tax_expense"
    assert row["tax_identity_status"] == "pass"

    expense = """
    Statement of Profit and Loss for the year ended 31 March 2020
    INR in Lakhs
    Revenue from operations 1,000 900
    Other income 10 9
    Total income 1,010 909
    Total expenses 5,112 4,000
    Profit / (Loss) before tax and after exceptional items (4,102) (3,091)
    Tax expense
    Deferred tax (credit)/charge (244) (234)
    Total tax expenses 724 142
    Profit / (Loss) for the period from continuing operations (4,826) (3,233)
    """
    row = extract_document(expense)[0]
    assert row["income_tax_expense"] == pytest.approx(724)
    assert row["tax_identity_status"] == "pass"


def test_source_tax_component_signs_are_resolved_by_reported_net_profit():
    asset = """
    Standalone Statement of Profit and Loss for the year ended 31 March 2021
    INR in Rupees
    Revenue from operations 1,000,000 900,000
    Other income 10,000 9,000
    Total income 1,010,000 909,000
    Total expenses 870,743 805,258
    Profit before Tax 139,257 103,742
    Tax Expense
    Income Tax 35,003 26,006
    Deferred Tax Asset/(Liability) 824 299
    Profit for the period 105,078 78,035
    """
    row = extract_document(asset)[0]
    assert row["income_tax_expense"] == pytest.approx(34_179)
    assert row["income_tax_expense_source"].endswith("component_sign_grid")
    assert row["tax_identity_status"] == "pass"

    parenthesized = """
    Profit and Loss Account for the year ended 31 March 2017
    INR in Rupees
    Revenue from operations 2,000,000 1,900,000
    Other income 10,000 9,000
    Total income 2,010,000 1,909,000
    Total expenses 277,620 654,874
    Profit before Tax 1,732,380 1,254,126
    Tax Expenses
    Current Tax 480,289 356,415
    Deferred Tax (11,203) -
    Profit for the period from Continuing Operations 1,240,888 897,711
    """
    row = extract_document(parenthesized)[0]
    assert row["income_tax_expense"] == pytest.approx(491_492)
    assert row["tax_identity_status"] == "pass"


def test_source_alternate_mat_credit_and_pre_minority_profit_close_tax():
    text = """
    Consolidated Statement of Profit and Loss for the year ended 31 March 2012
    INR in Crores
    Revenue from operations 2,000.00 1,900.00
    Other income 10.00 9.00
    Total income 2,010.00 1,909.00
    Total expenses 1,662.56 1,612.35
    Profit before tax 347.44 296.65
    Tax expense
    Current tax 77.66 61.70
    Deferred tax 14.60 8.15
    Minimum alternate tax credit (4.86) (6.04)
    Profit for the year before minority interest and share of loss from associate companies 260.04 232.26
    Minority interest 15.89 26.08
    Profit for the year 244.07 206.13
    """
    row = extract_document(text)[0]
    assert row["mat_credit"] == pytest.approx(-4.86)
    assert row["profit_after_tax_before_minority_interest"] == pytest.approx(260.04)
    assert row["tax_identity_status"] == "pass"


def test_side_by_side_balance_current_label_does_not_shadow_current_tax():
    text = """
    CONSOLIDATED BALANCE SHEET CONSOLIDATED STATEMENT OF PROFIT AND LOSS
    AS AT 31st MARCH, 2024 FOR THE YEAR ENDED 31st MARCH, 2024
    (Rs. in lacs) (Rs. in lacs)
    Particulars Notes As at As at Particulars Notes Year ended Year ended
    31.03.2024 31.03.2023 31.03.2024 31.03.2023
    ASSETS INCOME
    Non-current assets Revenue from operations 25 52,291.77 51,111.22
    Property, plant and equipment 3 30,030.29 30,967.06 Other income 26 166.24 141.07
    Current assets Total Income (I) 52,458.02 51,252.29
    Inventories 10 6,934.60 7,612.51 Cost of materials consumed 27 30,551.03 33,337.37
    Financial assets Employee benefits expense 29 9,519.07 8,290.59
    b. Other financial assets 7 299.45 461.66 Finance costs 30 1,467.21 1,173.12
    Other assets 9 1,267.47 1,021.46 Depreciation and amortization expense 31 3,414.53 3,112.19
    Current assets Other expenses 32 7,906.78 7,064.80
    Current assets Total Expenses (II) 53,199.17 51,001.65
    Profit / (loss) before share of profit of associates and tax (741.15) 250.64
    Share of profit of Joint venture (64.27) (761.93)
    Profit / (loss) before tax from continuing operations (805.42) (511.29)
    f. Other financial assets 7 309.73 207.18 Tax expenses 21
    Other current t 15 1,709.26 1,636.15 Current tax 17.78 119.58
    Total Assets 55,516.05 54,799.37 Adjustment of tax relating to earlier periods (19.60) 44.12
    EQUITY AND LIABILITIES Deferred tax 500.27 (80.56)
    Equity Profit / (loss) for the year (1,303.86) (594.43)
    LIABILITIES
    Non-current liabilities
    Financial liabilities
    Borrowings 18 7,359.88 7,992.02
    Current liabilities
    Financial liabilities
    Borrowings 18 8,568.42 7,086.34
    """

    row = extract_document(text)[0]

    assert row["statement_scope"] == "consolidated"
    assert row["current_tax"] == pytest.approx(17.78)
    assert row["earlier_year_tax"] == pytest.approx(-19.60)
    assert row["deferred_tax"] == pytest.approx(500.27)
    assert row["income_tax_expense"] == pytest.approx(498.45)
    assert row["tax_identity_status"] == "pass"
    assert row["total_borrowings"] == pytest.approx(15_928.30)


def test_source_joined_deferredtax_label_is_normalized():
    text = """
    Standalone Statement of Profit and Loss for the year ended 31 March 2019
    INR in Rupees
    Revenue from operations 20,000,000 19,000,000
    Other income 1,000,000 900,000
    Total income 21,000,000 19,900,000
    Total expenses 10,999,020 13,025,061
    Profit before tax 10,000,980 6,874,939
    Current Tax 2,484,746 2,007,777
    Deferredtax 164,589 129,094
    Profit from Continuing Operations 7,351,645 4,738,068
    """
    row = extract_document(text)[0]
    assert row["deferred_tax"] == pytest.approx(164_589)
    assert row["tax_identity_status"] == "pass"


def test_source_associate_share_after_continuing_profit_is_not_added_to_pbt():
    text = """
    Consolidated Statement of Profit and Loss for the year ended 31 March 2017
    INR in Crores
    Revenue from operations 1,000.00 900.00
    Other income 10.00 9.00
    Total income 1,010.00 909.00
    Total expenses 923.07 822.21
    Profit before tax 86.93 86.79
    Current tax 19.11 21.91
    Deferred tax 1.60 1.78
    Profit for the period from continuing operations 66.22 63.10
    Share of profit of associate (net of tax) 5.41 4.16
    Profit for the period 71.63 67.26
    """
    row = extract_document(text)[0]
    assert row["associate_jv_share_position"] == "after_continuing_operations"
    assert row["tax_identity_profit_basis"] == "continuing_operations"
    assert row["tax_identity_status"] == "pass"


def test_source_ind_as_reconciliation_is_not_promoted_as_primary_statement():
    text = """
    Effect of Ind AS adoption on the standalone balance sheet as at March 31, 2019
    Revenue from operations 1,000.00 900.00
    Other income 10.00 9.00
    Total income 1,010.00 909.00
    Total expenses 787.01 700.00
    Profit before tax 222.99 209.00
    Tax Expenses 27.62 20.00
    Profit for the year 199.13 189.00
    Total assets 5,000.00 4,000.00
    """
    assert extract_document(text) == []


def test_source_minority_interest_derives_pre_minority_tax_identity_net():
    text = """
    Consolidated Profit and Loss Statement for the year ended 31 March 2025
    INR in Lakhs
    Revenue from operations 10,000.00 9,000.00
    Other income 100.00 90.00
    Total income 10,100.00 9,090.00
    Total expenses 4,686.84 5,000.00
    Profit Before Tax 5,413.16 4,090.00
    Current Tax 1,712.69 1,200.00
    Deferred Tax (449.30) (100.00)
    Minority Interest (169.20) 34.55
    Profit After Tax 4,318.97 2,955.45
    """
    row = extract_document(text)[0]
    assert row["minority_interest"] == pytest.approx(-169.20)
    assert row["profit_after_tax_before_minority_interest"] == pytest.approx(4_149.77)
    assert row["tax_identity_profit_basis"] == "derived_pre_minority_profit"
    assert row["tax_identity_status"] == "pass"


def test_source_current_tax_credit_charge_row_is_not_replaced_by_header():
    text = """
    Profit and Loss Account for the year ended 31 March 2018
    INR in Rupees
    Revenue from operations 20,000,000 19,000,000
    Other income 1,000,000 900,000
    Total income 21,000,000 19,900,000
    Total expenses 3,804,374 2,049,899
    Profit before tax 17,195,626 17,850,101
    Provision for taxation
    (a) Current tax credit/(charge) - Refer note 3.7 (997,367) (1,027,798)
    (b) Deferred tax credit/(charge) - Refer note 3.7 - -
    Profit after tax 16,198,259 16,822,303
    """
    row = extract_document(text)[0]
    assert row["current_tax"] == pytest.approx(-997_367)
    assert row["tax_identity_status"] == "pass"


def test_source_named_tax_effect_component_closes_continuing_identity():
    text = """
    Profit and Loss Account for the year ended 31 March 2011
    INR in Lakhs
    Revenue from operations 100,000.00 90,000.00
    Other income 1,000.00 900.00
    Total income 101,000.00 90,900.00
    Total expenses 43,588.51 62,197.60
    Profit before tax for Continuing Operations 57,411.49 28,702.40
    Tax expense
    Current Tax 11,178.67 5,675.00
    Tax effect (premium on redemption of FCCBs) 1,009.31 -
    Tax adjustment of earlier years 66.38 -
    Deferred Tax 506.50 1,560.32
    Profit for the year after tax from Continuing Operations 44,650.63 21,376.30
    """
    row = extract_document(text)[0]
    assert row["tax_provision_adjustment"] == pytest.approx(1_009.31)
    assert row["income_tax_expense"] == pytest.approx(12_760.86)
    assert row["tax_identity_status"] == "pass"


def test_source_parenthesized_comparative_does_not_hide_reported_tax_total():
    text = """
    Statement of Profit and Loss for the year ended 31 March 2020
    INR in Lakhs
    Particulars Note 2020 2019
    Revenue from operations 1,000 900
    Other income 10 9
    Total income 1,010 909
    Total expenses 5,112 4,000
    Profit / (Loss) before tax and after exceptional items (4,102) (3,091)
    6 Tax expense:
    Income tax adjustment related to earlier years - 92
    Deferred tax (credit)/charge (244) (234)
    Total tax expenses 724 (142)
    Profit / (Loss) for the period from continuing operations (4,826) (3,233)
    """

    row = extract_document(text)[0]

    assert row["income_tax_expense"] == pytest.approx(724)
    assert row["income_tax_expense_source"] == "reported_tax_expense"
    assert row["tax_identity_status"] == "pass"


def test_source_tax_identity_may_omit_only_ambiguous_secondary_components():
    earlier_year_comparative = """
    Consolidated Statement of Profit and Loss for the year ended 31 March 2023
    INR in Lakhs
    Revenue from operations 2,000.00 1,800.00
    Other income 20.00 18.00
    Total income 2,020.00 1,818.00
    Total expenses 1,141.04 900.00
    Profit before tax 878.96 918.00
    Current tax 206.11 180.00
    Deferred tax 40.41 35.00
    Tax adjustments for earlier years (16.30) -
    Profit for the period 632.45 703.00
    """
    row = extract_document(earlier_year_comparative)[0]
    assert row["income_tax_expense"] == pytest.approx(246.52)
    assert row["income_tax_expense_source"].endswith("optional_component_grid")
    assert row["tax_identity_status"] == "pass"

    mat_in_comparative_column = """
    Statement of Profit and Loss for the period ended 31 March 2022
    INR in Rupees
    Revenue from operations 120,089,524 217,939,540
    Other income 313,707 9,940,731
    Total income 120,403,231 227,880,271
    Total expenses 117,343,649 221,924,750
    Profit before tax 3,059,582 5,955,521
    Current tax 490,412 929,061
    Deferred tax 314,452 197,641
    MAT Credit Entitlement (929,061)
    Profit for the period from continuing operations 2,883,622 6,153,162
    """
    row = extract_document(mat_in_comparative_column)[0]
    assert row["income_tax_expense"] == pytest.approx(175_960)
    assert row["income_tax_expense_source"].endswith("optional_component_grid")
    assert row["tax_identity_status"] == "pass"


def test_source_short_provision_for_tax_is_a_tax_component():
    text = """
    Profit and Loss Account for the Year ended 31 March 2020
    INR in Rupees
    Revenue from operations 18,750,171.00 18,201,110.07
    Other income - -
    Total income 18,750,171.00 18,201,110.07
    Total expenses 2,971,781.47 2,917,165.12
    Profit before tax 15,778,389.53 15,283,944.95
    Current tax 4,050,000.00 4,100,000.00
    Tax for earlier years - -
    Short Provision for Tax 358,374.00 42,728.00
    Profit for the period 11,370,015.53 11,141,216.95
    """

    row = extract_document(text)[0]

    assert row["tax_provision_adjustment"] == pytest.approx(358_374)
    assert row["income_tax_expense"] == pytest.approx(4_408_374)
    assert row["tax_identity_status"] == "pass"


def test_source_wrapped_and_repeated_earlier_period_tax_rows_are_accumulated():
    wrapped = """
    Profit & Loss Account for the period ended 31 March 2017
    Amount in Rs.
    Revenue from operations 437,353,865 383,723,485
    Other income 16,729,114 10,067,519
    Total income 454,082,979 393,791,004
    Total expenses 505,424,497 446,157,128
    Profit before tax (51,341,518) (52,366,124)
    Provision for Current Taxes 4,350,000 -
    Provision for Deferred Taxes (4,530,917) (12,793,162)
    Earlier Periods
    Income Tax 3,682,544 3,229,409
    Profit after tax (54,843,145) (42,802,371)
    """
    row = extract_document(wrapped)[0]
    assert row["earlier_year_tax"] == pytest.approx(3_682_544)
    assert row["tax_identity_status"] == "pass"

    repeated = """
    Statement of Profit & Loss for the year ended 31 March 2015
    INR in thousands
    Revenue from operations 358,259 272,018
    Other income 28,186 2,881
    Total income 386,445 274,899
    Total expenses 405,018 338,368
    Profit before tax (18,573) (63,469)
    Current tax 26,500 -
    Earlier Period 16 21,023
    Provision written back for earlier years (net) (3,167) -
    Deferred Tax 38 -
    Profit for the year (41,960) (84,492)
    """
    row = extract_document(repeated)[0]
    assert row["earlier_year_tax"] == pytest.approx(-3_151)
    assert row["income_tax_expense"] == pytest.approx(23_387)
    assert row["tax_identity_status"] == "pass"


def test_source_reconciliation_balance_sheet_cannot_outrank_real_profit_statement():
    text = """
    Statement of Profit and Loss for the year ended 31 March 2018
    INR in Lakhs
    Revenue from operations 55,546.64 39,099.86
    Other income 346.15 156.84
    Total income 55,892.79 39,256.70
    Total expenses 52,191.44 37,805.43
    Profit before tax 3,701.35 1,451.27
    Total tax expenses 40.72 (386.28)
    Profit for the period 3,660.63 1,837.55

    Notes to financial statements for the year ended 31 March 2018
    Note 36: First-time Ind AS adoption reconciliations
    Reconciliation Balance Sheet as at 31 March 2017 Balance Sheet as at 31 March 2016
    Revenue from operations 1,000.00 900.00
    Other income 10.00 9.00
    Total income 1,010.00 909.00
    Total expenses 500.00 400.00
    Profit before tax 510.00 509.00
    Current tax 100.00 90.00
    Profit for the year 410.00 419.00
    Total assets 5,000.00 4,000.00
    """

    row = extract_document(text)[0]

    assert row["source_profit_heading"].startswith("Statement of Profit")
    assert row["profit_before_tax"] == pytest.approx(3_701.35)
    assert row["tax_identity_status"] == "pass"


def test_source_tax_identity_threshold_tolerates_binary_float_boundary():
    text = """
    Standalone Statement of Profit & Loss for the Year ended March 31, 2024
    INR in Lakhs
    Revenue from operations 10.00 9.00
    Other income 0.14 0.10
    Total income 10.14 9.10
    Total expenses 10.00 8.90
    Profit before tax 0.14 0.20
    Deferred tax (0.36) -
    Profit for the period 0.51 0.20
    """

    row = extract_document(text)[0]

    assert row["tax_identity_ratio"] == pytest.approx(0.01)
    assert row["tax_identity_status"] == "pass"


def test_source_plural_profit_and_loss_accounts_starts_after_balance_sheet():
    text = """
    BALANCE SHEET AS AT 31st MARCH 2011
    Particulars 31-Mar-11 31-Mar-10
    Total Assets 190,766,121 186,250,141
    Total Equity 185,551,629 184,971,308

    PROFIT & LOSS ACCOUNTS FOR THE YEAR ENDED 31ST MARCH, 2011
    Amount in Rs.
    INCOME:
    Revenue from operations 20,276,910 8,025,147
    Other Income 53,982,712 16,159,358
    TOTAL 74,259,622 24,184,505
    EXPENDITURE:
    Total expenses 64,247,632 24,286,268
    Profit Before Taxation and Exceptional Items 10,011,990 (101,762)
    Deferred Tax Credit (114,341) (164,074)
    Current Tax 2,000,000 365,000
    Previour years 1,084,320 -
    Profit after Taxation and before Exceptional Items 7,042,011 (302,688)
    """

    row = extract_document(text)[0]

    assert row["source_profit_heading"].startswith("PROFIT & LOSS ACCOUNTS")
    assert row["earlier_year_tax"] == pytest.approx(1_084_320)
    assert row["tax_identity_status"] == "pass"


def test_source_notes_balance_candidate_cannot_replace_real_profit_statement():
    text = """
    STATEMENT OF PROFIT AND LOSS FOR THE YEAR ENDED 31ST MARCH, 2018
    INR in Lakhs
    Revenue from operations 1,000.00 900.00
    Other income 10.00 9.00
    Total income 1,010.00 909.00
    Total expenses 834.61 800.00
    Profit before tax 175.39 109.00
    Current tax 48.47 30.00
    Deferred tax 24.20 10.00
    Profit for the year 102.72 69.00

    Notes to Financial Statements as at and for the Year Ended 31st March 2018
    Balance Sheet as at 31st March 2017
    Revenue from operations 5,000.00 4,000.00
    Other income 100.00 90.00
    Total income 5,100.00 4,090.00
    Cost of materials consumed 1,000.00 900.00
    Employee benefit expense 500.00 400.00
    Finance costs 100.00 90.00
    Depreciation and amortization expense 100.00 90.00
    Other expenses 2,449.21 2,000.00
    Total expenses 4,149.21 3,480.00
    Profit before tax 850.79 610.00
    Current tax 216.26 150.00
    Deferred tax (2.59) 10.00
    Profit for the year 551.96 450.00
    Total assets 10,000.00 9,000.00
    """

    row = extract_document(text)[0]

    assert row["source_profit_heading"].startswith("STATEMENT OF PROFIT")
    assert row["profit_before_tax"] == pytest.approx(175.39)
    assert row["tax_identity_status"] == "pass"


def test_source_prior_period_taxation_closes_reported_income_tax_total():
    text = """
    Statement of Profit and Loss for the year ended 31 March 2024
    INR in Lakhs
    Revenue from operations 18,905.03 17,000.00
    Other income 47.11 40.00
    Total income 18,952.14 17,040.00
    Total expenses 18,820.77 16,900.00
    Profit before tax 131.37 140.00
    Current tax 48.00 32.86
    Deferred tax (9.25) (10.05)
    Prior period taxation 13.85 7.31
    Income tax expense 52.60 30.12
    Net Profit for the year 78.77 109.88
    """

    row = extract_document(text)[0]

    assert row["earlier_year_tax"] == pytest.approx(13.85)
    assert row["income_tax_expense"] == pytest.approx(52.60)
    assert row["tax_identity_status"] == "pass"


def test_source_short_excess_provision_of_income_tax_uses_identity_sign():
    text = """
    Profit & Loss Account for the year ended 31 March 2010
    INR in Rupees
    Revenue from operations 745,278 700,000
    Other income - -
    Total income 745,278 700,000
    Total expenses 1,144,260 1,100,000
    Loss before tax (398,982) (400,000)
    Deferred Tax Nil Nil
    Provision for Tax Nil Nil
    Less: (Short)/Excess provision of Income tax (66,952) Nil
    Loss After Tax (465,934) (400,000)
    """

    row = extract_document(text)[0]

    assert row["tax_provision_adjustment"] == pytest.approx(-66_952)
    assert row["income_tax_expense"] == pytest.approx(-66_952)
    assert row["tax_sign_convention"] == "add"
    assert row["tax_identity_status"] == "pass"


def test_source_double_dash_preserves_empty_current_tax_component_column():
    text = """
    Statement of Profit and Loss for the year ended March 31, 2016
    INR in Rupees
    Particulars March 31, 2016 March 31, 2015
    Revenue from operations 86,494,685 78,478,308
    Other income 12,103,454 63,373,472
    Total income 98,598,139 141,851,780
    Total expenses 74,113,872 64,715,587
    Profit before tax 24,484,267 77,136,193
    Tax expense
    Current year Tax 8,155,000 15,500,000
    Previous Years Tax 127,946 998,770
    Deferred Tax Liability / (Asset) -- (660,114)
    MAT Credit entitlement -- (1,901,634)
    Profit after tax 16,201,321 63,199,171
    """

    row = extract_document(text)[0]

    assert row["deferred_tax"] == pytest.approx(0)
    assert row["mat_credit"] == pytest.approx(0)
    assert row["income_tax_expense"] == pytest.approx(8_282_946)
    assert row["tax_identity_status"] == "pass"


def test_source_numeric_subtotal_after_tax_components_is_identity_candidate():
    text = """
    Statement of Profit & Loss for the year ended 31 March 2016
    INR in Rupees
    Particulars 31 March 2016 31 March 2015
    Revenue from operations 2,112,216,470 2,502,444,186
    Other income 60,821,761 81,346,241
    Total income 2,173,038,231 2,583,790,427
    Total expenses 2,032,274,797 2,503,030,971
    Profit before tax 140,763,434 80,759,456
    Tax expenses
    Current year tax 50,000,000 35,000,000
    Excess provision written back (428,985) (2,639,293)
    Income tax refund (1,340,177) 3,265,980
    Net tax expense 48,230,838 35,626,687
    Deferred tax 351,055 (1,020,721)
    43,879,783 34,605,966
    Profit for the year 96,883,651 46,153,490
    """

    row = extract_document(text)[0]

    assert row["income_tax_expense"] == pytest.approx(43_879_783)
    assert row["income_tax_expense_source"].endswith(
        "numeric_subtotal_after_tax_components"
    )
    assert row["tax_identity_status"] == "pass"


def test_hyphenated_text_month_statement_date_is_resolved():
    text = """
    Statement of Profit and Loss for the year ended 31-Mar-2018
    INR in Lakhs
    Revenue from operations 1,000.00 900.00
    Other income 10.00 9.00
    Total income 1,010.00 909.00
    Total expenses 900.00 813.00
    Profit before tax 110.00 96.00
    Current tax 30.00 25.00
    Profit for the year 80.00 71.00
    """

    row = extract_document(text)[0]

    assert row["period_end"] == "2018-03-31"
    assert row["tax_identity_status"] == "pass"


def test_right_column_primary_statement_accepts_unrelated_left_column_heading():
    text = """
    MANAGEMENT DISCUSSION AND ANALYSIS                                                     STATEMENT OF PROFIT & LOSS FOR THE YEAR ENDED 31ST MARCH, 2013
                                                                                           INR in Lakhs
                                                                                           Revenue from operations 1,000.00 900.00
                                                                                           Other income 10.00 9.00
                                                                                           Total income 1,010.00 909.00
                                                                                           Total expenses 900.00 813.00
                                                                                           Profit before tax 110.00 96.00
                                                                                           Current tax 30.00 25.00
                                                                                           Profit for the year 80.00 71.00
    """

    row = extract_document(text)[0]

    assert row["period_end"] == "2013-03-31"
    assert row["profit_before_tax"] == pytest.approx(110.0)
    assert row["tax_identity_status"] == "pass"


def test_indented_primary_heading_does_not_crop_left_aligned_statement_rows():
    text = """
                                                                                           Statement of Profit and Loss for the year ended March 31, 2022
    INR in Lakhs
    Revenue from operations 1,000.00 900.00
    Other income 10.00 9.00
    Total income 1,010.00 909.00
    Total expenses 900.00 813.00
    Profit before tax 110.00 96.00
    Current tax 30.00 25.00
    Profit for the year 80.00 71.00
    """

    row = extract_document(text)[0]

    assert row["revenue_from_operations"] == pytest.approx(1_000.0)
    assert row["tax_identity_status"] == "pass"


def test_legacy_taxtation_ocr_spelling_is_normalized():
    text = """
    PROFIT & LOSS ACCOUNT FOR THE YEAR ENDED 31ST MARCH, 2010
    INR in Rupees
    Miscellaneous Income 101,936 412,823
    TOTAL 101,936 412,823
    EXPENDITURE
    Other Expenses 515,182 301,002
    Depreciation for the Year 79,190 181,560
    TOTAL 594,372 482,562
    Profit /(Loss) Before Taxtation (492,436) (69,739)
    Less : Provision for Taxation - -
    Profit /(Loss) After Taxtation (492,436) (69,739)
    """

    row = extract_document(text)[0]

    assert row["profit_before_tax"] == pytest.approx(-492_436)
    assert row["profit_after_tax"] == pytest.approx(-492_436)
    assert row["tax_identity_status"] == "pass"


def test_split_leading_digit_in_indian_grouped_amount_is_rejoined():
    text = """
    Statement of Profit and Loss Account for the year ended 31 March 2023
    INR in Rupees
    Revenue from operations 15 1 5,42,434.50 18,25,111.98
    Other income 16 3,972.13 6,278.08
    Total Income 15,46,406.63 18,31,390.05
    Finance Costs 20 36,905.40 38,581.20
    Total expenses 15,41,794.32 18,01,063.06
    Profit before tax 4,612.31 30,327.00
    Current tax 18,500.00 22,500.00
    Deferred tax (3,629.18) (5,353.29)
    Current tax expenses relating to prior year 148.32 (971.04)
    Profit for the period (10,406.83) 14,151.33
    """

    row = extract_document(text)[0]

    assert row["revenue_from_operations"] == pytest.approx(1_542_434.50)
    assert row["operating_profit_closure_status"] == "pass"
    assert row["tax_identity_status"] == "pass"


def test_multiple_exceptional_and_extraordinary_rows_close_operating_bridge():
    text = """
    AUDITED STATEMENT OF PROFIT & LOSS FOR THE YEAR ENDED 31.03.2019
    INR in Rupees
    Net Revenue from Operations 23,933,770 21,453,371
    Other Income 965,008 15,652
    Total Revenue 24,898,778 21,469,023
    Finance Cost 17,712 42,670
    Total Expenses 26,224,927 23,553,735
    PROFIT BEFORE EXCEPTIONAL AND EXTRAORDINARY ITEMS AND TAX 1,326,149 2,084,712
    EXCEPTIONAL ITEMS AND PRIOR YEAR ITEMS 10,450 706,465
    PROFIT BEFORE EXTRAORDINARY ITEMS AND TAX 1,315,699 1,378,247
    EXTRAORDINARY ITEMS 287,408,157 0
    PROFIT BEFORE TAX 286,092,458 1,378,247
    Tax expenses 0 0
    PROFIT FOR THE YEAR 286,092,458 1,378,247
    """

    row = extract_document(text)[0]

    assert row["exceptional_items"] == pytest.approx(287_418_607)
    assert row["operating_profit_closure_status"] == "pass"


def test_wrapped_exceptional_component_closes_operating_bridge():
    text = """
    Statement of Profit and Loss for the year ended 31 March, 2016
    INR in Rupees
    Revenue from operations 13 285,395 1,429,270
    Other income 14 895,921 843,944
    Total revenue 1,181,316 2,273,214
    Finance costs 18 144,229 1,741,541
    Total expenses 1,986,293 51,580,850
    Profit before exceptional and extraordinary items and tax (804,977) (49,307,635)
    Add:- Exceptional items
    Amount written back on account of settlement with secured lenders under OTS 19 38,933,986 65,516,596
    Profit before tax 38,129,009 16,208,961
    Provision for Tax - 52,000
    Profit from continuing operations 38,129,009 16,156,961
    """

    row = extract_document(text)[0]

    assert row["exceptional_items"] == pytest.approx(38_933_986)
    assert row["operating_profit_closure_status"] == "pass"


def test_prior_period_item_is_included_in_operating_profit_bridge():
    text = """
    Profit & Loss for the Period 31st March, 2017
    INR in Rupees
    Revenue from operations 166,556,373 36,780,635
    Other income 278,802 47,297,491
    Total revenue 166,835,175 84,078,126
    Finance costs 1,647,979 1,535,538
    Total expenses 164,619,348 82,874,754
    Profit / (Loss) before prior period items 2,215,827 1,203,372
    Prior period items 114,165 45,600
    Profit / (Loss) before tax 2,101,662 1,157,772
    Current tax expense 444,710 -
    Deferred tax 14,489 -
    Profit / (Loss) for the year 1,642,463 1,157,772
    """

    row = extract_document(text)[0]

    assert row["prior_period_items"] == pytest.approx(114_165)
    assert row["operating_profit_closure_status"] == "pass"


def test_associate_share_after_tax_and_before_final_profit_closes_tax_identity():
    text = """
    CONSOLIDATED STATEMENT OF PROFIT AND LOSS FOR THE YEAR ENDED 31ST MARCH 2017
    INR in Rupees
    Revenue from Operations 5,060,669 8,622,830
    Other Income 2,248,754 16,430,492
    Total Revenue 7,309,423 25,053,322
    Total Expenses 9,507,468 3,148,503
    Profit before Tax (2,198,045) 21,904,819
    Current Tax 27,585 1,831,112
    Deferred Tax / (Credit) (575,724) (9,904)
    Share in Profit of Associate Companies 4,189,914 3,598,934
    Profit for the Period 2,540,008 23,682,545
    """

    row = extract_document(text)[0]

    assert row["associate_jv_share"] == pytest.approx(4_189_914)
    assert row["tax_identity_profit_basis"] == "profit_for_period_after_associate_share"
    assert row["tax_identity_status"] == "pass"


def test_legacy_generic_taxes_row_is_current_tax_inside_tax_section():
    text = """
    STATEMENT OF PROFIT & LOSS FOR THE PERIOD ENDED ON 31ST MARCH, 2013
    INR in Rupees
    Revenue from operations 105,119,317.56 45,653,055.00
    Other Income (591,673.13) (2,466,436.30)
    Total Revenue 104,527,644.43 43,186,618.70
    Total Expenses 103,403,374.83 42,613,555.27
    Profit before tax 1,124,269.60 573,063.43
    Tax expense:
    Taxes 581,431.00 355,235.00
    Deferred tax (1,340.00) -
    Profit/(Loss) for the period 544,178.60 217,828.43
    """

    row = extract_document(text)[0]

    assert row["current_tax"] == pytest.approx(581_431)
    assert row["income_tax_expense"] == pytest.approx(580_091)
    assert row["tax_identity_status"] == "pass"


@pytest.mark.parametrize(
    "heading, expected_period",
    [
        (
            "AUDITED STATEM ENT OF PROFIT AND LOSS FOR THE YEAR ENDED ON 31.03.2017",
            "2017-03-31",
        ),
        (
            "STATEMENTS OF PROFIT & LOSS FOR THE YEAR ENDED MARCH 31, 2020",
            "2020-03-31",
        ),
        (
            "sl, PROFIT & LOSS STATEMENT FOR THE PERIOD ENDED ON 31ST MARCH, 2017",
            "2017-03-31",
        ),
        (
            "TABLE 2. STATEMENT OF PROFIT AND LOSS FOR THE YEAR ENDED 31 MARCH 2022",
            "2022-03-31",
        ),
        (
            "Standalone Statement of Profit and Loss for the financial year ended March 31st, 2020",
            "2020-03-31",
        ),
    ],
)
def test_source_observed_profit_heading_variants_are_primary(
    heading, expected_period
):
    text = f"""
    {heading}
    INR in Lakhs
    Revenue from operations 1,000.00 900.00
    Other income 10.00 9.00
    Total income 1,010.00 909.00
    Total expenses 900.00 813.00
    Profit before tax 110.00 96.00
    Current tax 30.00 25.00
    Profit for the year 80.00 71.00
    """

    row = extract_document(text)[0]

    assert row["period_end"] == expected_period
    assert row["tax_identity_status"] == "pass"


def test_parenthesized_legacy_generic_taxes_row_is_current_tax():
    text = """
    STATEMENT OF PROFIT & LOSS FOR THE PERIOD ENDED ON 31ST MARCH, 2013
    INR in Rupees
    Revenue from operations 105,119,317.56 45,653,055.00
    Other Income (591,673.13) (2,466,436.30)
    Total Revenue 104,527,644.43 43,186,618.70
    Total Expenses 103,403,374.83 42,613,555.27
    Profit before tax 1,124,269.60 573,063.43
    Tax expense:
    (1) Taxes 581,431.00 355,235.00
    Deferred tax (1,340.00) -
    Profit/(Loss) for the period 544,178.60 217,828.43
    """

    row = extract_document(text)[0]

    assert row["current_tax"] == pytest.approx(581_431)
    assert row["income_tax_expense"] == pytest.approx(580_091)
    assert row["tax_identity_status"] == "pass"


@pytest.mark.parametrize(
    "statement_rows",
    [
        # The result subtotal is OCR-damaged, but income and expense sections
        # still establish a primary statement rather than a narrative table.
        """
        Revenue from operations 1,000 900
        Other income 10 9
        Total income 1,010 909
        Finance costs 20 18
        Depreciation 30 28
        Other expenses 850 767
        Total expenses 900 813
        """,
        # The income labels are OCR-damaged, while the expense and result
        # sections retain enough independent numeric rows for cataloguing.
        """
        Employee benefits expense 300 280
        Finance costs 20 18
        Depreciation 30 28
        Other expenses 550 487
        Total expenses 900 813
        Profit before tax 110 96
        Profit for the year 80 71
        """,
        # The expense heading is OCR-damaged, while income and result survive.
        """
        Revenue from operations 1,000 900
        Other income 10 9
        Total income 1,010 909
        Profit before tax 110 96
        Current tax 30 25
        Profit for the year 80 71
        """,
    ],
)
def test_incomplete_but_primary_profit_statement_is_catalogued(statement_rows):
    text = f"""
    PROFIT & LOSS ACCOUNT FOR THE YEAR ENDED 31ST MARCH, 2011
    INR in Rupees
    {statement_rows}
    """

    row = extract_document(text)[0]

    assert row["period_end"] == "2011-03-31"
    assert row["screen_eligible"] is False


def test_prefixed_audit_prose_does_not_become_an_incomplete_primary_statement():
    text = """
    In our opinion the Profit and Loss Account for the year ended 31 March 2011
    Revenue from operations 1,000 900
    Other income 10 9
    Total income 1,010 909
    Finance costs 20 18
    Total expenses 900 813
    Profit before tax 110 96
    Profit for the year 80 71
    """

    assert extract_document(text) == []


def test_split_profit_heading_and_month_only_year_end_are_normalized():
    text = """
    PRO FIT AND LOSS ACCOUNT FOR THE YEAR ENDED MARCH, 2015
    INR in Rupees
    Revenue from operations 1,000 900
    Other income 10 9
    Total income 1,010 909
    Total expenses 900 813
    Profit before tax 110 96
    Current tax 30 25
    Profit for the year 80 71
    """

    row = extract_document(text)[0]

    assert row["period_end"] == "2015-03-31"
    assert row["tax_identity_status"] == "pass"


def test_statement_of_financial_position_supplies_borrowing_components():
    text = """
    Statement of Financial Position as at March 31, 2021
    INR in Rupees
    Total assets 10,000 9,000
    Non-current liabilities
    (i) Borrowings 280 300
    Current liabilities
    (i) Borrowings 1,270 2,130

    Statement of Profit and Loss for the year ended March 31, 2021
    INR in Rupees
    Revenue from operations 1,000 900
    Other income 10 9
    Total income 1,010 909
    Total expenses 900 813
    Profit before tax 110 96
    Current tax 30 25
    Profit for the year 80 71
    """

    row = extract_document(text)[0]

    assert row["total_borrowings"] == pytest.approx(1_550)
    assert row["debt_reconciliation_status"] == "components_only"


def test_legacy_borrwoings_typo_is_normalized_in_balance_sheet():
    text = """
    Balance Sheet as at 31 March 2014
    INR in Rupees
    Total assets 10,000 9,000
    Non-current liabilities
    Long-Term Borrwoings 2,613 502
    Current liabilities
    Short-Term Borrwings 4 1,113

    Profit and Loss Account for the year ended 31 March 2014
    INR in Rupees
    Revenue from operations 1,000 900
    Other income 10 9
    Total income 1,010 909
    Total expenses 900 813
    Profit before tax 110 96
    Current tax 30 25
    Profit for the year 80 71
    """

    row = extract_document(text)[0]

    assert row["total_borrowings"] == pytest.approx(2_617)


def test_month_first_starred_ordinal_resolves_legacy_balance_period():
    text = """
    Balance Sheet
    INR in Rupees
    Particulars March 31* 2011 March 31* 2010
    Sources of Funds
    Loan Funds
    Secured Loan 510,474,129 297,552,151
    Unsecured Loan - 390,000

    Profit and Loss Account for the year ended March 31, 2011
    INR in Rupees
    Revenue from operations 1,000 900
    Other income 10 9
    Total income 1,010 909
    Total expenses 900 813
    Profit before tax 110 96
    Current tax 30 25
    Profit for the year 80 71
    """

    row = extract_document(text)[0]

    assert row["total_borrowings"] == pytest.approx(510_474_129)


@pytest.mark.parametrize(
    "zero_evidence",
    [
        "Secured Loans : - Unsecured Loans: -",
        "INDEBTEDNESS of the Company: NIL",
        "INDEBTEDNESS -Indebtedness of the Company including interest outstanding/accrued but not due for payment.: NIL",
        "The Company continues to be free from debts, both on Long Term and Working Capital requirements.",
        "Secured Loans                                    Unsecured Loans\nN I L                                            N I L",
        "Indebtedness at the end of the financial year\nPrincipal Amount - - - -\nTotal (i+ii+iii) - - - -",
        (
            "The Company has not borrowed any amount from any financial "
            "institutions, Banks or debenture holders."
        ),
    ],
)
def test_source_observed_explicit_no_borrowing_forms_are_true_zero(zero_evidence):
    text = f"""
    Balance Sheet as at March 31, 2011
    INR in Rupees
    Total assets 10,000 9,000
    {zero_evidence}

    Profit and Loss Account for the year ended March 31, 2011
    INR in Rupees
    Revenue from operations 1,000 900
    Other income 10 9
    Total income 1,010 909
    Total expenses 900 813
    Profit before tax 110 96
    Current tax 30 25
    Profit for the year 80 71
    """

    row = extract_document(text)[0]

    assert row["total_borrowings"] == 0
    assert row["debt_reconciliation_status"] == "true_zero"


def test_grouped_earlier_year_tax_subrows_are_summed():
    text = """
    Standalone Statement of profit and loss for the year ended March 31, 2022
    Rs. in Lacs
    Revenue from operations 21,182.11 18,307.91
    Other income 1,077.07 894.81
    Total Income 22,259.18 19,202.72
    Total Expenses 21,595.06 18,609.85
    Profit before Tax 664.12 592.87
    Tax expense:
    Current Tax 162.00 131.00
    Deferred Tax 25.00 (2.98)
    Tax adjustment relating to earlier years
    Current Tax (137.30) 63.26
    Deferred Tax 211.50 (59.57)
    Profit for the period 402.92 461.16
    """

    row = extract_document(text)[0]

    assert row["earlier_year_tax"] == pytest.approx(74.20)
    assert row["income_tax_expense"] == pytest.approx(261.20)
    assert row["tax_identity_status"] == "pass"


def test_comma_separated_note_references_are_not_deferred_tax_values():
    text = """
    Consolidated Statement of Profit and Loss for the year ended March 31, 2025
    INR in crores
    Particulars Notes March 31, 2025 March 31, 2024
    Revenue from operations 29 11,155.87 9,321.53
    Other income 30 44.72 46.18
    Total income 11,200.59 9,367.71
    Total expenses 11,086.26 9,253.49
    Profit before tax 114.50 117.73
    Current tax 15 6.86 7.44
    Adjustment of tax relating to earlier years 15 0.80 0.43
    Deferred tax credit 13, 14 (3.63) (2.80)
    Income tax expense 4.03 5.07
    Profit for the year 110.47 112.66
    """

    row = extract_document(text)[0]

    assert row["deferred_tax"] == pytest.approx(-3.63)
    assert row["income_tax_expense"] == pytest.approx(4.03)
    assert row["tax_identity_status"] == "pass"


def test_numeric_row_formulas_after_label_text_are_not_statement_values():
    text = """
    Statement of Profit and Loss Account for the year ended 31 March, 2013
    INR in Rupees
    Particulars Note 2013 2012
    Revenue from operations 267,203,124.72 267,686,904.00
    Other income 4,580,923.00 3,000,000.00
    Total income 271,784,047.72 270,686,904.00
    Total expenses 269,479,267.44 268,593,108.42
    5 Profit before exceptional and extraordinary items and tax (3-4) 2,304,780.28 2,093,795.58
    7 Profit before extraordinary items and tax (5-6) 2,304,780.28 2,093,795.58
    9 Profit before tax (7 ± 8) 2,304,780.28 2,093,795.58
    10 Tax expense:
    (a) Current tax expense for current year 1,175,149.00 1,078,033.00
    (b) Deferred tax (296,735.72) (254,539.88)
    11 Profit for the year ended from continuing operations (9-10) 1,426,367.00 1,270,302.46
    """

    row = extract_document(text)[0]

    assert row["profit_before_tax"] == pytest.approx(2_304_780.28)
    assert row["profit_for_period"] == pytest.approx(1_426_367.00)
    assert row["tax_identity_status"] == "pass"


def test_current_tax_note_only_row_joins_parenthetical_value_continuation():
    text = """
    Standalone Statement of Profit and Loss for the year ended March 31, 2018
    INR in Lakhs
    Particulars Note March 31, 2018 March 31, 2017
    Revenue from operations 1,000.00 900.00
    Other income 10.00 9.00
    Total income 1,010.00 909.00
    Total expenses 649.92 607.22
    Profit before tax 360.08 301.78
    Tax expense
    (1) Current Tax
    (Including MAT Credit Utilisation CY 30.41; PY 6.28) 32 73.96 12.77
    Deferred Tax 32 6.43 16.72
    Profit for the year 279.69 272.29
    """

    row = extract_document(text)[0]

    assert row["current_tax"] == pytest.approx(73.96)
    assert row.get("mat_credit") is None
    assert row["income_tax_expense"] == pytest.approx(80.39)
    assert row["tax_identity_status"] == "pass"


def test_current_tax_note_only_row_prefers_current_year_subrow_amount():
    text = """
    Standalone Statement of Profit and Loss for the year ended March 31, 2023
    INR in Lakhs
    Particulars Note March 31, 2023 March 31, 2022
    Revenue from operations 20,000.00 19,000.00
    Other income 500.00 400.00
    Total income 20,500.00 19,400.00
    Total expenses 8,308.42 7,704.45
    Profit before tax 12,191.58 11,695.55
    Tax Expense:
    (1) Current Tax 23
    - Current year 3,140.36 3,017.70
    Tax adjustment relating to earlier years 82.55 28.08
    Deferred Tax Charge/(Credit) 23 (39.80) (128.50)
    Profit for the year 9,008.47 8,778.27
    """

    row = extract_document(text)[0]

    assert row["current_tax"] == pytest.approx(3_140.36)
    assert row["income_tax_expense"] == pytest.approx(3_183.11)
    assert row["tax_identity_status"] == "pass"


def test_current_year_deferred_tax_outranks_prior_period_deferred_row():
    text = """
    Standalone Statement of Profit and Loss for the year ended March 31, 2022
    INR in Lakhs
    Particulars Note March 31, 2022 March 31, 2021
    Revenue from operations 10,000.00 9,000.00
    Other income 100.00 90.00
    Total income 10,100.00 9,090.00
    Total expenses 1,194.31 6,863.84
    Profit before tax 8,905.69 2,226.16
    Current Tax 2,053.12 386.74
    Tax pertaining to earlier years (5.26) (136.84)
    Deferred Tax Prior Period - (1,328.51)
    Deferred Tax current year 333.93 243.58
    Profit for the period from continuing operations 6,523.90 2,275.19
    """

    row = extract_document(text)[0]

    assert row["deferred_tax"] == pytest.approx(333.93)
    assert row["income_tax_expense"] == pytest.approx(2_381.79)
    assert row["tax_identity_status"] == "pass"


def test_indian_grouped_amounts_with_spaces_are_single_negative_values():
    text = """
    Consolidated Statement of Profit & Loss for the year ended March 31, 2016
    Amount in INR
    Particulars Note March 31, 2016 March 31, 2015
    Revenue from operations (4 00 00 000) (3 00 00 000)
    Other income 1 00 00 000 90 00 000
    Total expenses 2 93 53 502 2 46 46 491
    Profit before tax for the year (5 93 53 502) (2 46 46 491)
    Current tax 5,02,000.00 -
    Earlier Year's excess Provision w/off (1,10,70,295.00) -
    Deferred tax (54,37,834.00) -
    Profit (Loss) for the period (4 33 47 373) (2 46 46 491)
    """

    row = extract_document(text)[0]

    assert row["profit_before_tax"] == pytest.approx(-59_353_502)
    assert row["profit_for_period"] == pytest.approx(-43_347_373)
    assert row["tax_identity_status"] == "pass"


def test_previous_year_only_header_and_wealth_tax_keep_current_column():
    text = """
    Consolidated Profit and Loss Account for the year ended March 31, 2010
    Previous Year
    Schedule (Rs. in lakhs) (Rs. in lakhs)
    Sales 24,456.10 23,000.00
    Other income 247.01 200.00
    Total expenses 24,729.93 19,764.92
    Profit before tax (26.82) 3,435.08
    Less: Provision for taxation
    Current tax 621.04 1,729.49
    Deferred tax credit (644.51) (936.42)
    Add: Provision for wealth tax 2.66 2.77
    Profit after tax (6.01) 2,613.03
    """

    row = extract_document(text)[0]

    assert row["deferred_tax"] == pytest.approx(-644.51)
    assert row["tax_provision_adjustment"] == pytest.approx(2.66)
    assert row["income_tax_expense"] == pytest.approx(-20.81)
    assert row["tax_identity_status"] == "pass"


@pytest.mark.parametrize(
    "period_header",
    [
        "For the year ended 31st March\nNote 2015 2014",
        "YEAR ENDED YEAR ENDED\n31ST MARCH 31ST MARCH\nSchedule 2015 2014",
    ],
)
def test_statement_year_on_following_header_row_recovers_period(period_header):
    text = f"""
    Statement of Profit and Loss
    {period_header}
    Amount in Rs.
    Revenue from operations 1,000 900
    Other income 10 9
    Total income 1,010 909
    Total expenses 900 813
    Profit before tax 110 96
    Current tax 30 25
    Profit for the year 80 71
    """

    row = extract_document(text)[0]

    assert row["period_end"] == "2015-03-31"
    assert row["tax_identity_status"] == "pass"


def test_deferred_tax_reserve_withdrawal_offsets_reported_deferred_tax():
    text = """
    Consolidated Profit and Loss Account for the year ended March 31, 2011
    INR in Rupees
    Particulars March 31, 2011 March 31, 2010
    Revenue from operations 100,000,000 90,000,000
    Other income 1,000,000 900,000
    Total income 101,000,000 90,900,000
    Total expenses 71,958,668 88,701,229
    Profit before taxation 29,041,332 2,198,771
    Provision / (Credit) for Taxes
    Income Tax - 73,313
    Deferred Tax 5,863,083 5,751,380
    Less: Deferred Tax Reserve Withdrawn 5,863,083 5,751,380
    Profit after taxation 29,041,332 2,125,458
    """

    row = extract_document(text)[0]

    assert row["tax_reserve_transfer"] == pytest.approx(-5_863_083)
    assert row["income_tax_expense"] == pytest.approx(0)
    assert row["tax_identity_status"] == "pass"


def test_company_name_prefix_on_same_line_keeps_primary_profit_statement():
    rows = extract_document(
        """
        Vimta Labs Limited Statement of Profit & Loss for the year ended March 31, 2018
        (All amounts in INR millions)
        Revenue from operations 1,810.94 1,538.48
        Other income 11.97 9.41
        Total income 1,822.91 1,547.89
        Employee benefits expense 536.42 463.36
        Finance costs 52.00 20.87
        Depreciation expense 157.00 96.30
        Other expenses 821.85 801.01
        Total expenses 1,567.27 1,382.54
        Profit before tax 255.64 165.35
        Current tax 76.91 61.39
        Prior year tax adjustments 2.40 (1.47)
        Deferred tax 12.00 (0.49)
        Total Tax expense 91.31 59.43
        Profit for the year 164.33 105.92
        """,
        company_name="Vimta Labs Ltd.",
    )

    assert len(rows) == 1
    assert rows[0]["period_end"] == "2018-03-31"
    assert rows[0]["profit_before_tax"] == pytest.approx(255.64)
    assert rows[0]["tax_identity_status"] == "pass"


def test_tax_provision_net_of_adjustments_is_reported_tax_total():
    row = extract_document(
        """
        Statement of Profit and Loss for the year ended 31 March 2010
        (INR crore)
        Revenue from operations 900.00 800.00
        Other income 44.21 40.00
        Total income 944.21 840.00
        Total expenses 600.00 514.30
        Profit before tax 344.21 325.70
        Tax Provision (Net of Adj.) 109.34 114.12
        Profit after tax 234.87 211.58
        """
    )[0]

    assert row["income_tax_expense"] == pytest.approx(109.34)
    assert row["income_tax_expense_source"] == "reported_tax_expense"
    assert row["tax_identity_status"] == "pass"


def test_nested_current_prior_and_deferred_tax_rows_sum_to_statement_tax():
    row = extract_document(
        """
        Standalone Statement of Profit and Loss for the year ended March 31, 2023
        (INR crore)
        Revenue from operations 14,000.00 13,000.00
        Other income 490.49 480.00
        Total income 14,490.49 13,480.00
        Total expenses 12,535.50 10,523.80
        Profit before tax 1,954.99 3,954.87
        Tax Expense
        (a) Current tax 36 - -
        (i) Current year 475.47 1,061.63
        (ii) Earlier years (181.06) (9.88)
        (b) Deferred tax 36 116.09 (48.85)
        Profit for the year 1,544.49 2,951.97
        """
    )[0]

    assert row["current_tax"] == pytest.approx(475.47)
    assert row["earlier_year_tax"] == pytest.approx(-181.06)
    assert row["deferred_tax"] == pytest.approx(116.09)
    assert row["income_tax_expense"] == pytest.approx(410.50)
    assert row["tax_identity_status"] == "pass"


def test_deferred_tax_rate_remeasurement_is_a_tax_component():
    row = extract_document(
        """
        Standalone Statement of Profit and Loss for the year ended March 31, 2025
        INR in Crore
        Revenue from operations 1,000.00 900.00
        Other income 10.00 9.00
        Total income 1,010.00 909.00
        Total expenses 806.34 700.25
        Profit before tax 203.66 208.75
        Tax Expenses:
        Current tax 26.00 28.25
        Deferred tax 22.26 25.95
        Impact of deferred tax liability remeasurement on account of change in tax rate 3,558.07 -
        Profit/(loss) for the year (3,402.67) 154.55
        """
    )[0]

    assert row["tax_provision_adjustment"] == pytest.approx(3_558.07)
    assert row["income_tax_expense"] == pytest.approx(3_606.33)
    assert row["tax_identity_status"] == "pass"


def test_blank_final_pbt_can_use_reported_preexceptional_subtotal_when_identity_closes():
    row = extract_document(
        """
        Standalone Statement of Profit and Loss for the year ending 31-03-2018
        INR in Rupees
        Revenue from operations 100,000,000 90,000,000
        Total income 101,000,000 91,000,000
        Total expenses 157,524,312 110,860,679
        Profit before exceptional items and tax (56,524,312) (19,860,679)
        Exceptional item (net) (56,524,312) (19,860,679)
        Profit/(Loss) before tax
        Tax expense
        Current tax - -
        Deferred tax - -
        (Short)/excess provision for earlier years 7,860,849 -
        Total tax expense 7,860,849 -
        Loss for the year (64,385,161) (19,860,679)
        """
    )[0]

    assert row["tax_identity_status"] == "pass"


def test_explicit_income_tax_provision_overrides_zero_current_mat_row():
    row = extract_document(
        """
        Profit and Loss Account for the period ended 31st March 2011
        Amount in Rupees
        Revenue from operations 20,000,000 10,000,000
        Total income 21,000,000 11,000,000
        Total expenses 8,053,642 4,556,653
        Profit before tax 12,946,358 6,443,347
        Provision for Taxation - Current (MAT) - 985,000
        Provision For Income Tax 3,950,000 -
        Deferred Tax (140,633) 36,671
        Profit after tax 9,136,991 5,421,676
        """
    )[0]

    assert row["current_tax"] == pytest.approx(3_950_000)
    assert row["tax_identity_status"] == "pass"


def test_bracketed_refer_note_does_not_replace_deferred_tax_value():
    row = extract_document(
        """
        Statement of Profit and Loss for the year ended 31st March, 2014
        INR in Crore
        Revenue from operations 10,000.00 9,000.00
        Total income 10,100.00 9,100.00
        Total expenses 8,126.87 7,065.52
        Profit before tax 1,973.13 2,034.48
        Tax Expense:
        Current Tax 525.00 605.10
        Deferred Tax [Refer Note 26(9)(b)] 197.77 78.74
        Profit for the year 1,250.36 1,350.64
        """
    )[0]

    assert row["deferred_tax"] == pytest.approx(197.77)
    assert row["tax_identity_status"] == "pass"


def test_abbreviated_short_income_tax_provision_is_a_component():
    row = extract_document(
        """
        Profit and Loss Account for the year ended on 31 March, 2011
        INR in Rupees
        Revenue from operations 1,000,000 900,000
        Total income 1,100,000 990,000
        Total expenses 871,165 626,990
        Profit Before Taxes 228,835 363,010
        Less: Provision for Taxes 65,000 130,000
        Short Prov of Income Tax 7,250 -
        Provision for Deferred Tax 10,200 -
        Profit After Taxes 146,385 233,010
        """
    )[0]

    assert row["tax_provision_adjustment"] == pytest.approx(7_250)
    assert row["tax_identity_status"] == "pass"


def test_current_year_tax_label_precedes_deferred_current_year_component():
    row = extract_document(
        """
        Statement of Profit and Loss for the year ended 31.03.2021
        INR in Rupees
        Revenue from operations 20,000,000 19,000,000
        Total income 21,000,000 20,000,000
        Total expenses 17,419,730 16,559,235
        Profit before tax 3,580,270 3,440,765
        - Current Year Tax 3,724,024 3,005,032
        - Deferred Tax Current Year (1,462,341) 316,824
        Current tax Expenses relating to previous
        - -
        years
        Profit After tax 1,318,587 118,909
        """
    )[0]

    assert row["current_tax"] == pytest.approx(3_724_024)
    assert row["deferred_tax"] == pytest.approx(-1_462_341)
    assert row["tax_identity_status"] == "pass"


def test_deferred_income_tax_is_not_consumed_as_current_income_tax():
    row = extract_document(
        """
        Statement of Profit and Loss for the year ended 31st March, 2012
        INR in Crore
        Revenue from operations 10,000.00 9,000.00
        Total income 10,100.00 9,100.00
        Total expenses 8,563.34 8,593.42
        Profit before tax 1,536.66 506.58
        Tax Expenses:
        Current Income Tax 435.00 74.82
        Deferred Income Tax 64.74 84.49
        Profit for the year after tax 1,036.92 347.27
        """
    )[0]

    assert row["deferred_tax"] == pytest.approx(64.74)
    assert row["tax_identity_status"] == "pass"


def test_inline_tax_component_and_subtotal_columns_keep_component_value():
    row = extract_document(
        """
        Statement of Profit and Loss for the year ended March 31, 2014
        INR in Crore
        Particulars 2014 2013
        Revenue from operations 100,000.00 90,000.00
        Total income 101,000.00 91,000.00
        Total expenses 91,466.57 80,000.00
        Profit Before Tax 9,533.43 11,000.00
        Tax Expenses
        Current Tax 1,998.25 2,000.00
        MAT Credit Utilised/(Available for Set-off) (1,998.25) (2,000.00)
        Deferred Tax 3,241.07 3,000.00
        Income Tax Paid for Earlier Years 96.40 3,337.47 - 3,000.00
        Profit for the year 6,195.96 8,000.00
        """
    )[0]

    assert row["earlier_year_tax"] == pytest.approx(96.40)
    assert row["income_tax_expense"] == pytest.approx(3_337.47)
    assert row["tax_identity_status"] == "pass"


def test_source_befor_ocr_typo_normalizes_final_profit_before_tax():
    row = extract_document(
        """
        Statement of Profit and Loss for the year ended 31st March, 2014
        INR in Rupees
        Revenue from operations 10,000,000 9,000,000
        Total income 10,100,000 9,100,000
        Total expenses 8,333,330 6,924,690
        Profit before exceptional and extra-ordinary items and tax 1,766,670 2,175,310
        Less Exceptional items 13,390 52,506
        Profit befor tax 1,753,280 2,122,804
        Less Tax expenses
        Current tax (600,000) (800,000)
        Deferred tax (109,738) 40,128
        Tax paid for earlier years 198,501 -
        Profit/Loss for the period from continuing operations 1,242,043 1,362,932
        """
    )[0]

    assert row["profit_before_tax"] == pytest.approx(1_753_280)
    assert row["tax_identity_status"] == "pass"


def test_befor_ocr_typo_is_a_direct_pbt_without_preexceptional_subtotal():
    row = extract_document(
        """
        Statement of Profit and Loss for the year ended 31st March, 2014
        INR in Rupees
        Revenue from operations 10,000,000 9,000,000
        Total income 10,100,000 9,100,000
        Total expenses 8,346,720 6,977,196
        Profit befor tax 1,753,280 2,122,804
        Current tax 600,000 800,000
        Deferred tax 109,738 (40,128)
        Tax paid for earlier years (198,501) -
        Profit for the period from continuing operations 1,242,043 1,362,932
        """
    )[0]

    assert row["profit_before_tax"] == pytest.approx(1_753_280)
    assert row["tax_identity_status"] == "pass"


def test_split_as_at_march_header_uses_nearby_current_year_column():
    rows = extract_document(
        """
        Statement of Profit and Loss Account
        Amount in Rs.
        As at 31st March
        As at 31st March
        Particulars Notes 2022 2021
        Revenue from operations 19 617,563,311.00 456,300,349.00
        Total income 617,563,311.00 456,300,349.00
        Total expenses 614,030,395.71 453,018,395.48
        Profit before tax 3,532,915.29 3,281,953.52
        Current tax 1,031,000.00 917,000.00
        Deferred tax (102,800.00) (117,400.00)
        Profit for the year 2,604,715.29 2,482,353.52
        """
    )

    assert len(rows) == 1
    assert rows[0]["period_end"] == "2022-03-31"


def test_financial_year_range_heading_resolves_indian_march_period():
    rows = extract_document(
        """
        Statement of Profit and Loss for the financial year 2022-2023
        INR in Lakhs
        Revenue from operations 100.00 90.00
        Total income 111.26 102.50
        Total expenses 47.76 69.51
        Profit before tax 63.50 32.99
        Current tax 14.95 2.32
        Deferred tax (0.71) (0.70)
        Earlier year tax 4.98 0.49
        Profit for the year 44.28 30.88
        """
    )

    assert len(rows) == 1
    assert rows[0]["period_end"] == "2023-03-31"


@pytest.mark.parametrize(
    ("heading", "columns", "expected_period"),
    [
        (
            "STATEMENT OF PROFIT & LOSS",
            "Particulars Note No. 2018-19 2017-18",
            "2019-03-31",
        ),
        (
            "Profit and loss statement for the year ended",
            "Particulars Note No. 2011-12 2010-11",
            "2012-03-31",
        ),
        (
            "Statement of profit and loss for the year ended March 31,",
            "Particulars Notes 2013 2012",
            "2013-03-31",
        ),
    ],
)
def test_extract_document_recovers_period_from_split_legacy_column_headers(
    heading: str,
    columns: str,
    expected_period: str,
):
    text = f"""
    {heading}
    {columns}
    Revenue from operations 100 90
    Other income 10 9
    Total income 110 99
    Total expenses 80 75
    Profit before tax 30 24
    Tax expense 8 6
    Profit for the year 22 18
    """

    rows = extract_document(text)

    assert len(rows) == 1
    assert rows[0]["period_end"] == expected_period


@pytest.mark.parametrize(
    "heading",
    [
        "Revised Statement of Profit and Loss for the period ended 31st March, 2024",
        "PART I - STATEMENT OF PROFIT AND LOSS FOR THE YEAR ENDED 31.03.2024",
        "PART-| COMBINED PROFIT AND LOSS ACCOUNT FOR THE YEAR ENDED 31.03.2024",
    ],
)
def test_extract_document_accepts_source_shaped_primary_statement_prefixes(heading: str):
    text = f"""
    {heading}
    Rupees in Lakhs
    Revenue from operations 100 90
    Other income 10 9
    Total income 110 99
    Total expenses 80 75
    Profit before tax 30 24
    Tax expense 8 6
    Profit for the year 22 18
    """

    rows = extract_document(text)

    assert len(rows) == 1
    assert rows[0]["period_end"] == "2024-03-31"


def test_deferred_tax_asset_and_liability_rows_sum_inside_primary_tax_section():
    row = extract_document(
        """
        Profit and Loss Account for the year ended March 31, 2010
        INR in Rupees
        Particulars 2010 2009
        Revenue from operations 100,000,000 90,000,000
        Total income 110,000,000 99,000,000
        Total expenses 51,603,422 54,426,642
        Profit before tax 58,396,578 44,573,358
        Provision for: Current Tax 12,975,000 6,103,244
        Taxation for Earlier Years (553,060) -
        Deferred Tax Assets - (6,628,474)
        Deferred Tax Liability 23,008,094 -
        Profit after tax 22,966,544 50,261,098
        """
    )[0]

    assert row["deferred_tax"] == pytest.approx(23_008_094)
    assert row["tax_identity_status"] == "pass"


def test_post_pbt_deferred_tax_overrides_balance_sheet_deferred_tax_value():
    row = extract_document(
        """
        Statement of Profit and Loss for the year ended September 30, 2012
        INR in Rupees
        Particulars 2012 2011
        Deferred tax liabilities (net) 30,888,352 24,640,874
        Revenue from operations 100,000,000 90,000,000
        Total income 101,000,000 91,000,000
        Total expenses 44,826,189.09 10,000,000
        Profit before tax (56,173,810.91) (107,894,483.89)
        Tax expense
        Current tax - -
        Deferred tax 6,247,478.05 4,681,769.00
        Profit/(Loss) for the year (62,421,288.96) (112,576,252.89)
        """
    )[0]

    assert row["deferred_tax"] == pytest.approx(6_247_478.05)
    assert row["tax_identity_status"] == "pass"


def test_comparative_only_current_tax_cell_can_be_omitted_when_identity_closes():
    row = extract_document(
        """
        Statement of Profit and Loss for the year ended March 31, 2018
        INR in Rupees
        Particulars 2018 2017
        Revenue from operations 1,000,000 900,000
        Total income 1,100,000 990,000
        Total expenses 432,616 793,045
        Profit before tax 667,384 196,955
        Tax expense
        Current tax 84,150
        Deferred tax (15,138) -
        Profit/(Loss) for the period from continuing operations 682,522 112,805
        """
    )[0]

    assert row["current_tax"] == pytest.approx(84_150)
    assert row["income_tax_expense"] == pytest.approx(-15_138)
    assert row["tax_identity_status"] == "pass"


def test_profit_i_loss_ocr_separator_is_continuing_operations_net():
    row = extract_document(
        """
        Standalone Statement of Profit and Loss for the year ended March 31, 2025
        INR in Lakhs
        Revenue from operations 100.00 90.00
        Total income 110.00 100.00
        Total expenses 171.39 125.59
        Profit I (loss) Before Tax (61.39) (25.59)
        Tax Expense
        Current Tax - -
        Deferred Tax (3.62) 0.39
        Profit I (loss) for the period from Continuing Operations (65.01) (25.20)
        Share of profit of associate 12.71 57.05
        Profit I (loss) for the period (52.30) 31.85
        """
    )[0]

    assert row["profit_from_continuing_operations"] == pytest.approx(-65.01)
    assert row["tax_identity_status"] == "pass"


def test_tax_on_prior_period_income_is_an_earlier_year_component():
    row = extract_document(
        """
        Statement of Profit and Loss for the year ended March 31, 2017
        INR in Rupees
        Revenue from operations 100,000,000 90,000,000
        Total income 105,000,000 95,000,000
        Total expenses 65,289,053 71,985,223
        Profit before tax 39,710,947 23,014,777
        Less: Tax expenses
        Current tax 6,964,243 6,187,000
        Tax on Prior period Income 11,395,000 -
        Deferred tax (463,174) (424,891)
        Total Tax Expenses 17,896,069 5,762,109
        Profit/(Loss) for the year 21,814,878 17,252,668
        """
    )[0]

    assert row["earlier_year_tax"] == pytest.approx(11_395_000)
    assert row["tax_identity_status"] == "pass"


def test_ocr_braced_total_revenue_formula_is_not_selected_as_value():
    row = extract_document(
        """
        Statement of Profit and Loss for the year ended March 31, 2016
        INR in Rupees
        Particulars 2016 2015
        Revenue from operations 7,671,516 1,610,427
        Other income 1,732,350 1,438,507
        Total Revenue {1 +1) 9,403,866 3,048,934
        Finance cost 1,451,236 265,096
        Total expenses 9,314,642 2,866,619
        Profit before exceptional and extraordinary items and tax 89,224 182,315
        Exceptional and Extraordinary items - -
        Profit before tax 89,224 182,315
        Current tax 20,000 10,000
        Profit for the year 69,224 172,315
        """
    )[0]

    assert row["total_income"] == pytest.approx(9_403_866)
    assert row["operating_profit_closure_status"] == "pass"


def test_total_of_numeric_range_formula_is_not_selected_as_expense_value():
    row = extract_document(
        """
        Statement of Profit and Loss for the year ended March 31, 2022
        INR in Lakhs
        Particulars 2022 2021
        Revenue from operations 806.86 285.15
        Other income 9.49 16.91
        Total income (Total of 1 to 2) 816.35 302.06
        Finance costs 18.40 11.74
        Depreciation and amortization expenses 5.33 7.70
        Total expenses (Total of 1 to 7) 870.77 325.84
        Profit before exceptional item and tax (54.43) (23.79)
        Exceptional items - -
        Profit before tax (54.43) (23.79)
        Current tax - -
        Profit for the year (54.43) (23.79)
        """
    )[0]

    assert row["total_expenses"] == pytest.approx(870.77)
    assert row["operating_profit_closure_status"] == "pass"


def test_assets_ampersand_liabilities_heading_yields_current_borrowings():
    rows = extract_document(
        """
        Statement of Assets & Liabilities as at 31st March, 2021
        INR in Rupees
        Particulars 2021 2020
        Total Assets 39,499,824 48,037,275
        EQUITY AND LIABILITIES
        Total Equity 4,083,513 4,842,487
        Non-Current Liabilities
        (a) Borrowings, non current - -
        Current Liabilities
        (a) Borrowings, current 33,600,000 47,425,000
        Total Equity and Liabilities 39,499,824 48,037,275
        Statement of Profit and Loss for the year ended March 31, 2021
        INR in Rupees
        Revenue from operations 100,000,000 90,000,000
        Total income 100,000,000 90,000,000
        Total expenses 80,000,000 70,000,000
        Profit before tax 20,000,000 20,000,000
        Current tax 5,000,000 5,000,000
        Profit for the year 15,000,000 15,000,000
        """
    )

    assert rows[0]["current_borrowings"] == pytest.approx(33_600_000)
    assert rows[0]["total_borrowings"] == pytest.approx(33_600_000)


def test_ocr_31s_quote_march_balance_date_matches_profit_period():
    rows = extract_document(
        """
        Balance Sheet as at 31S" March, 2019
        INR in Lakhs
        Particulars 2019 2018
        TOTAL ASSETS 394,077 389,262
        EQUITY AND LIABILITIES
        Total Equity 285,360 273,605
        Non-Current Liabilities
        Financial Liabilities
        (i) Borrowings 17 43,553 60,094
        Current Liabilities
        Financial Liabilities
        (i) Borrowings 19 15,101 11,541
        TOTAL EQUITY AND LIABILITIES 394,077 389,262
        Statement of Profit and Loss for the year ended March 31, 2019
        INR in Lakhs
        Revenue from operations 100,000 90,000
        Total income 100,000 90,000
        Total expenses 80,000 70,000
        Profit before tax 20,000 20,000
        Current tax 5,000 5,000
        Profit for the year 15,000 15,000
        """
    )

    assert rows[0]["non_current_borrowings"] == pytest.approx(43_553)
    assert rows[0]["current_borrowings"] == pytest.approx(15_101)
    assert rows[0]["total_borrowings"] == pytest.approx(58_654)


def test_line_wrapped_non_current_liability_heading_sets_debt_bucket():
    rows = extract_document(
        """
        Balance Sheet as at March 31, 2024
        INR in Lakhs
        Particulars 2024 2023
        Total Assets 1,715.57 1,829.26
        EQUITY AND
        LIABILITIES
        Total Equity (195.09) 50.51
        4 Non-current
        liabilities
        (a) Financial
        Liabilities
        (i) Borrowings 11 1,488.09 1,500.00
        5 Current liabilities
        (a) Financial Liabilities
        (i) Borrowings - -
        Total Equity and Liabilities 1,715.57 1,829.26
        Statement of Profit and Loss for the year ended March 31, 2024
        INR in Lakhs
        Revenue from operations 100.00 90.00
        Total income 100.00 90.00
        Total expenses 80.00 70.00
        Profit before tax 20.00 20.00
        Current tax 5.00 5.00
        Profit for the year 15.00 15.00
        """
    )

    assert rows[0]["non_current_borrowings"] == pytest.approx(1_488.09)
    assert rows[0]["total_borrowings"] == pytest.approx(1_488.09)


def test_wide_column_spacing_preserves_grouped_amounts_after_note_column():
    row = extract_document(
        """
        Statement of Profit and Loss for the year ended June 30, 2023
        INR in Lakhs
        Particulars                                      Note                       2023                 2022
        Revenue from operations                           20                      2 47 705              2 25 616
        Other income                                      21                         2 218                    774
        Total income                                                               2 49 923              2 26 390
        Finance costs                                     25                           777                  1 053
        Depreciation and amortization expense              4                         8 069                  6 836
        Total expense                                                              2 02 633              1 85 257
        Profit before tax                                                            47 290                 41 133
        Current tax                                                                 13 000                 10 000
        Profit for the year                                                          34 290                 31 133
        """
    )[0]

    assert row["other_income"] == pytest.approx(2_218)
    assert row["finance_costs"] == pytest.approx(777)
    assert row["depreciation_amortization"] == pytest.approx(8_069)
    assert row["profit_before_tax"] == pytest.approx(47_290)
    assert row["operating_profit_closure_status"] == "pass"


def test_balance_column_geometry_drops_note_before_borrowing_values():
    row = extract_document(
        """
        Balance Sheet as at March 31, 2019
        INR in Lakhs
        Particulars                                      Note                 2019                 2018
        Total assets                                                          6,474.69             6,000.00
        Equity and liabilities
        Non-current liabilities
        Financial liabilities
        Borrowings                                       12                         -                336.00
        Current liabilities
        Financial liabilities
        Borrowings                                       14                    408.80                258.59
        Total equity and liabilities                                          6,474.69             6,000.00

        Statement of Profit and Loss for the year ended March 31, 2019
        INR in Lakhs
        Revenue from operations 100.00 90.00
        Total income 100.00 90.00
        Total expenses 80.00 70.00
        Profit before tax 20.00 20.00
        Current tax 5.00 5.00
        Profit for the year 15.00 15.00
        """
    )[0]

    assert row["non_current_borrowings"] == pytest.approx(0.0)
    assert row["current_borrowings"] == pytest.approx(408.80)
    assert row["total_borrowings"] == pytest.approx(408.80)


def test_note_plus_single_comparative_borrowing_value_stays_unresolved():
    values = in_bse._extract_balance_sheet(
        [
            "Balance Sheet as at March 31",
            "Notes 2012 2011",
            "Non-current liabilities",
            "Long-term borrowings 4 5,417,698,957",
            "Current liabilities",
            "Short-term borrowings 7 748,377,473",
            "Total Assets 14,739,766,662",
        ]
    )

    assert values.get("total_borrowings") is None


def test_balance_extractor_ignores_cash_flow_borrowing_movements():
    values = in_bse._extract_balance_sheet(
        [
            "Balance Sheet as at March 31, 2010",
            "Cash Flow Statement for the year ended March 31, 2010",
            "Proceeds from Long Term Borrowings 4,000,000,600 -",
            "Repayment of Borrowings - (2,004,000,000)",
            "Cash and cash equivalents at the end of the year 2,355,410,596 2,048,095,984",
        ]
    )

    assert values.get("non_current_borrowings") is None
    assert values.get("current_borrowings") is None
    assert values.get("total_borrowings") is None


def test_wide_column_spacing_preserves_grouped_tax_and_profit_values():
    row = extract_document(
        """
        Statement of Profit and Loss for the year ended June 30, 2023
        INR in Lakhs
        Particulars                                                               2023                 2022
        Revenue from operations                                                 100 000               90 000
        Total income                                                            100 000               90 000
        Total expenses                                                           68 580               64 663
        Profit before tax                                                        31 420               25 337
        Current tax                                                               8 661                6,502
        Deferred tax                                                              (506)                    4
        Tax for earlier years                                                       318                 (421)
        Income tax expense                                                        8 473                6 085
        Profit for the year                                                      22 947               19 252
        """
    )[0]

    assert row["profit_before_tax"] == pytest.approx(31_420)
    assert row["current_tax"] == pytest.approx(8_661)
    assert row["profit_for_period"] == pytest.approx(22_947)
    assert row["tax_identity_status"] == "pass"


def test_abbreviated_month_year_columns_do_not_shift_deferred_tax_to_prior_year():
    row = extract_document(
        """
        Statement of Profit and Loss for the year ended 31st March 2014
        INR in Crores
        Note Page Particulars                                      March-14      March-13
        Revenue from operations                                  10,000.00      9,000.00
        Total income                                             10,000.00      9,000.00
        Total expenses                                               22.15      4,495.75
        Profit before tax                                         9,977.85      4,504.25
        Current tax                                               2,925.04        969.35
        MAT credit entitlement                                       (3.13)     (455.31)
        Deferred tax                                                 89.36        362.91
        Profit for the year                                       6,966.58      3,627.30
        """
    )[0]

    assert row["deferred_tax"] == pytest.approx(89.36)
    assert row["tax_identity_status"] == "pass"


def test_current_tax_mat_uses_current_period_column_when_header_is_weak():
    row = extract_document(
        """
        Statement of Profit and Loss for the year ended March 31, 2017
        INR in Rupees
        Revenue from operations 817,422,519 700,000,000
        Total income 817,422,519 700,000,000
        Total expenses 605,429,175 633,556,622
        Profit before tax 211,993,344 66,443,378
        Current tax MAT 43,223,000 13,547,041
        Deferred tax 2,545,322 804,498
        Tax for earlier years 934,585 -
        Less: MAT credit available (43,223,000) (13,547,041)
        Profit for the year 208,513,437 65,638,880
        """
    )[0]

    assert row["current_tax"] == pytest.approx(43_223_000)
    assert row["tax_identity_status"] == "pass"


def test_earliar_ocr_tax_label_is_an_earlier_year_component():
    row = extract_document(
        """
        Consolidated Profit and Loss Statement for the year ended March 31, 2012
        INR in Rupees
        Particulars 2012 2011
        Revenue from operations 100,000,000 90,000,000
        Total income 100,000,000 90,000,000
        Total expenses 112,676,760 11,404,475
        Profit before tax (12,676,760) 78,595,525
        Current tax 2,500,000 10,066,100
        EARLIAR YEARS TAX 429,676 (64,376)
        Deferred tax (338,045) 296,845
        Profit for the period (15,268,391) 68,296,956
        """
    )[0]

    assert row["earlier_year_tax"] == pytest.approx(429_676)
    assert row["tax_identity_status"] == "pass"


def test_split_profit_ocr_does_not_turn_repeated_heading_year_into_profit():
    row = extract_document(
        """
        Statement of Profit and Loss for the year ended March 31, 2017
        INR in Rupees
        Revenue from operations 100,000,000 90,000,000
        Total income 100,000,000 90,000,000
        Total expenses 74,689,112 72,649,900
        Profit before tax 25,310,888 17,350,100
        Current tax 6,600,919 4,859,246
        Deferred tax 1,651,980 819,828
        Pro it/(Loss) from continuing operations 17,057,989 11,671,026
        Statement of Pro it and Loss for the year ended Particulars Note 2017 2016
        Pro it / (Loss) for the year 17,057,989 11,671,026
        """
    )[0]

    assert row["profit_from_continuing_operations"] == pytest.approx(17_057_989)
    assert row.get("profit_for_period") != pytest.approx(2017)
    assert row["tax_identity_status"] == "pass"


def test_continuing_operations_pre_tax_subtotal_drives_continuing_tax_identity():
    row = extract_document(
        """
        Statement of Profit and Loss for the year ended June 30, 2014
        INR in Rupees
        Revenue from operations 100,000,000 90,000,000
        Total income 100,000,000 90,000,000
        Total expenses 1,495,789,247 100,000,000
        Profit/(Loss) before tax (1,395,789,247) (10,000,000)
        Profit/(Loss) before tax for the period from continuing operations (1,410,327,386) (10,000,000)
        Current tax - -
        Deferred tax - -
        Profit/(Loss) after tax for the period from continuing operations (1,410,327,386) (10,000,000)
        Profit/(Loss) from discontinuing operations 14,538,139 -
        Profit/(Loss) after tax for the period (1,395,789,247) (10,000,000)
        """
    )[0]

    assert row["profit_before_tax_from_continuing_operations"] == pytest.approx(
        -1_410_327_386
    )
    assert row["tax_identity_status"] == "pass"


def test_equity_accounted_investee_share_closes_operating_profit_bridge():
    row = extract_document(
        """
        Consolidated Statement of Profit and Loss for the year ended March 31, 2026
        INR in Crores
        Revenue from operations 335,582 366,094
        Other income 5,787 5,437
        Total income 341,369 371,531
        Finance costs 2,827 3,901
        Depreciation and amortisation expense 19,784 21,102
        Total expenses 339,296 343,020
        Profit before share of profit in equity accounted investees, exceptional items and tax 2,073 28,511
        Share of profit in equity accounted investees 446 139
        Profit before exceptional items and tax 2,519 28,650
        Exceptional items 4,142 196
        Profit/(loss) before tax from continuing operations (1,623) 28,454
        Current tax - -
        Profit/(loss) for the year from continuing operations (1,623) 28,454
        """
    )[0]

    assert row["associate_jv_share"] == pytest.approx(446)
    assert row["operating_profit_closure_status"] == "pass"


def test_prior_period_expense_closes_operating_profit_bridge():
    row = extract_document(
        """
        Statement of Profit and Loss for the year ended March 31, 2014
        INR in Rupees
        Revenue from operations 372,139,177 328,110,642
        Other income 5,445,767 598,904
        Total revenue 377,584,944 328,709,546
        Finance cost 52,196,879 40,180,739
        Depreciation expense 27,819,821 40,695,163
        Total expenses 436,748,262 368,703,409
        Loss before prior period items and tax (59,163,318) (39,993,863)
        Prior period expense 296,000 99,455
        Loss before tax (59,459,318) (40,093,318)
        Current tax - -
        Loss for the year (59,459,318) (40,093,318)
        """
    )[0]

    assert row["prior_period_items"] == pytest.approx(296_000)
    assert row["operating_profit_closure_status"] == "pass"


def test_schedule_reference_p_dash_number_is_not_revenue_value():
    row = extract_document(
        """
        Statement of Profit and Loss for the year ended March 31, 2014
        INR in Rupees
        Particulars 2014 2013
        Revenue from operations P-1 95,911,188 70,692,045
        Other income P-2 126,467 100,500
        Total Revenue (1+2) 96,037,655 70,792,545
        Finance costs P-5 3,961,317 4,006,720
        Depreciation and amortisation expenses P-9 923,426 923,335
        Total Expenses 95,310,078 69,226,761
        Profit before exceptional items and tax 727,577 1,565,784
        Exceptional items - -
        Profit before tax 1,688,189 1,565,784
        Current tax 100,000 100,000
        Profit for the year 1,588,189 1,465,784
        """
    )[0]

    assert row["revenue_from_operations"] == pytest.approx(95_911_188)
    assert row["operating_profit_closure_status"] == "pass"


def test_ocr_tilde_before_profit_amount_is_a_negative_sign():
    row = extract_document(
        """
        Statement of Profit and Loss for the year ended March 31, 2022
        INR in Lakhs
        Revenue from operations 7,207.45 3,052.31
        Other income 359.94 275.25
        Total revenue 7,567.39 3,327.56
        Finance costs 234.26 308.45
        Depreciation and amortisation expense 32.05 37.66
        Total expenses 7,736.61 3,631.18
        Profit/(Loss) before exceptional items and tax ~169.22 (303.62)
        Profit/(Loss) before tax ~169.22 (303.62)
        Current tax - -
        Profit/(Loss) for the year ~169.22 (303.62)
        """
    )[0]

    assert row["profit_before_tax"] == pytest.approx(-169.22)
    assert row["operating_profit_closure_status"] == "pass"


def test_associate_impairment_loss_is_a_below_expense_non_operating_item():
    row = extract_document(
        """
        Consolidated Statement of Profit and Loss for the year ended March 31, 2024
        INR in Crores
        Revenue from operations 99,778 79,903
        Other income 5,469 4,097
        Total income 105,247 84,000
        Depreciation and amortization expense 7,357 4,853
        Finance costs 243 233
        Total expenses 116,446 101,304
        Loss before share of loss of associates and tax (11,199) (17,304)
        Share of loss of associates and joint ventures (377) (125)
        Loss on impairment of an associate (2,271) -
        Loss before exceptional items and tax (13,847) (17,429)
        Exceptional items (57) -
        Loss before tax (13,904) (17,429)
        Current tax - -
        Loss for the year (13,904) (17,429)
        """
    )[0]

    assert row["associate_impairment"] == pytest.approx(-2_271)
    assert row["operating_profit_closure_status"] == "pass"


def test_associate_impairment_after_preexception_subtotal_is_not_reversed_twice():
    row = extract_document(
        """
        Standalone Statement of Profit and Loss for the year ended March 31, 2024
        INR in Crores
        Revenue from operations 76,608 60,000
        Other income 5,244 4,271
        Total income 81,852 64,271
        Employee benefits expense 40,301 35,000
        Depreciation and amortization expense 7,211 4,696
        Finance costs 233 219
        Other expenses 14,151 10,000
        Total expenses 94,441 82,199
        Loss before exceptional items and tax (12,589) (17,928)
        Loss on impairment of an associate (2,096) -
        Exceptional items (77) (630)
        Loss for the year (14,762) (18,558)
        """
    )[0]

    assert row["associate_impairment"] == pytest.approx(-2_096)
    assert row["operating_profit_closure_status"] == "pass"


def test_damaged_preexception_subtotal_uses_income_expense_identity():
    row = extract_document(
        """
        Consolidated Statement of Profit and Loss for the year ended March 31, 2012
        INR in Rupees
        Revenue from operations 528,075,624 400,000,000
        Other income 7,515,166 5,000,000
        Total income 535,590,790 405,000,000
        Finance costs 14,690,809 12,730,257
        Total expenses 517,020,040 344,412,457
        Profit before exceptional and extraordinary items and
        tax (I-IV) 49,870,750 23,037,898
        Exceptional items (166,001) (30,038)
        Profit before tax unreadable 1a404749 23,007,860
        Current tax 5,981,784 7,230,817
        Deferred tax 151,896 939,717
        Profit for the period 11,950,081 14,725,416
        """
    )[0]

    assert row["profit_before_exceptional_items_and_tax"] == pytest.approx(
        18_570_750
    )
    assert row["operating_profit_closure_status"] == "pass"


def test_income_expense_subtotal_repair_does_not_replace_valid_final_pbt():
    row = extract_document(
        """
        Statement of Profit and Loss for the year ended March 31, 2012
        INR in Lakhs
        Revenue from operations 1,000.00 900.00
        Other income 20.00 10.00
        Total income 1,020.00 910.00
        Total expenses 598.58 600.00
        Profit before exceptional items and tax 421.42 310.00
        Exceptional items 15.96 -
        Profit before tax 405.46 310.00
        Current tax 108.87 100.00
        Deferred tax 16.34 10.00
        Taxation adjustments for earlier years 99.29 -
        Profit for the period 180.96 200.00
        """
    )[0]

    assert row["profit_before_tax"] == pytest.approx(405.46)
    assert row["tax_identity_status"] == "pass"


def test_three_value_total_tax_row_keeps_first_column_without_note_header():
    row = extract_document(
        """
        Statement of Profit and Loss for the year ended March 31, 2018
        INR in Lakhs
        Revenue from operations 10,000 9,000 19,000
        Total income 10,000 9,000 19,000
        Total expenses 6,627 10,063 16,690
        Profit before tax 3,373 (1,063) 2,310
        Current tax 1,312 - 1,312
        Deferred tax charge / (credit) (44) (338) (382)
        Tax pertaining to earlier years (335) - (335)
        Total tax expense 933 (338) 595
        Profit for the year 2,440 (725) 1,715
        """
    )[0]

    assert row["income_tax_expense_reported"] == pytest.approx(933)
    assert row["tax_identity_status"] == "pass"


def test_eps_rows_do_not_accumulate_into_exceptional_items():
    row = extract_document(
        """
        Consolidated Statement of Profit and Loss for the year ended March 31, 2012
        INR in Crores
        Revenue from operations 6,000.00 5,000.00
        Other income 23.98 20.00
        Total income 6,023.98 5,020.00
        Finance costs 345.09 312.05
        Depreciation and amortisation expenses 340.05 316.48
        Total expenses 5,604.67 4,942.27
        Profit before Taxation, Exceptional Items and Minority Interest 419.31 351.61
        Exceptional Items 25.71 21.24
        Profit before Taxation and Minority Interest 393.60 330.37
        Current tax 149.15 100.00
        Profit for the year 244.45 230.37
        Basic and Diluted before Exceptional Items 21.74 23.98
        Basic and Diluted after Exceptional Items 19.68 22.28
        """
    )[0]

    assert row["exceptional_items"] == pytest.approx(25.71)
    assert row["operating_profit_closure_status"] == "pass"


def test_deferred_tax_benefit_separator_is_not_a_zero_value_cell():
    row = extract_document(
        """
        Statement of Profit and Loss for the year ended March 31, 2010
        INR in Rupees
        Revenue from operations 100,000,000 90,000,000
        Total income 100,000,000 90,000,000
        Total expenses 95,284,974 80,000,000
        Profit before tax 4,715,026 10,000,000
        Current tax 1,498,931 2,000,000
        Deferred tax benefit - (357,346) (1,207,681)
        Profit after tax 3,573,441 9,207,681
        """
    )[0]

    assert row["deferred_tax"] == pytest.approx(-357_346)
    assert row["tax_identity_status"] == "pass"


def test_accounting_component_bridge_can_override_mislabeled_ebitda_row():
    row = extract_document(
        """
        Statement of Profit and Loss for the year ended March 31, 2014
        INR in Rupees
        Revenue from operations - -
        Other income 5,847 5,481
        Total income 5,847 5,481
        Finance costs 3,011,956 2,400,083
        Earning before interest, tax, depreciation and amortization (5,253,969) (4,196,391)
        Depreciation and amortization expenses 9,551 7,896
        Total expenses 5,259,816 4,209,768
        Profit before exceptional items and tax (5,263,520) (4,204,287)
        Exceptional items - -
        Profit before tax (5,263,520) (4,204,287)
        Current tax - -
        Loss for the year (5,263,520) (4,204,287)
        """
    )[0]

    assert row["operating_profit_component_source"] == (
        "total_income_less_non_operating_income_and_operating_expenses"
    )
    assert row["operating_profit_closure_status"] == "pass"


def test_balance_period_recovers_staggered_as_at_month_and_day_columns():
    rows = extract_document(
        """
        Standalone Balance Sheet
        (Amount in Rs.)
        Particulars       Note       As at March       As at March       As at April
                                  No. 31, 2018          31, 2017          01, 2016
        ASSETS
        Total assets                    2,655,752,479    4,282,897,817    5,220,732,330
        EQUITY AND LIABILITIES
        Total Equity                   (4,373,417,167)  (2,663,896,748)  (1,979,206,273)
        Non-current liabilities
        Financial liabilities
        (i) Borrowings            16       311,700,000      354,100,000    1,738,665,345
        Current liabilities
        Financial liabilities
        (i) Borrowings            19     4,935,577,564    4,874,751,704    3,869,240,445

        Standalone Statement of Profit and Loss for the year ended March 31, 2018
        (Amount in Rs.)
        Revenue from operations 100,000,000 90,000,000
        Total income 100,000,000 90,000,000
        Total expenses 80,000,000 70,000,000
        Profit before tax 20,000,000 20,000,000
        Current tax 5,000,000 5,000,000
        Profit for the year 15,000,000 15,000,000
        """
    )

    assert rows[0]["source_balance_heading"] == "Standalone Balance Sheet"
    assert rows[0]["non_current_borrowings"] == pytest.approx(311_700_000)
    assert rows[0]["current_borrowings"] == pytest.approx(4_935_577_564)
    assert rows[0]["total_borrowings"] == pytest.approx(5_247_277_564)


def test_abbreviated_side_by_side_bal_sheet_is_a_primary_statement_heading():
    rows = extract_document(
        """
        MBL Infrastructures Ltd
        Bal Sheet Profit & Lossuh t
        as at 31st March, 2011 For the year ended 31st March, 2011
        (Rs in lacs) (Rs in lacs)
        SOURCES OF FUNDS INCOME
        SHAREHOLDERS' FUNDS Income from Operations 14 99,152.88 62,888.28
        Share Capital 1 1,751.37 1,751.37 Other Income 15 92.82 66.00
        LOAN FUNDS 99,205.70 62,568.15
        Secured Loans 3 21,600.79 13,837.19 EXPENDITURE
        Unsecured Loans 4 2,657.54 5,296.56 Operating Expenses 17 83,857.43 52,358.01
        TOTAL 54,082.02 42,852.16
        Profit Before Tax 8,993.36 5,168.67
        Current Tax 2,300.00 1,170.00
        Profit After Tax 6,032.28 3,416.05
        """
    )

    assert len(rows) == 1
    assert rows[0]["source_balance_heading"] == "Bal Sheet Profit & Lossuh t"
    assert rows[0]["total_borrowings"] == pytest.approx(24_258.33)


def test_legacy_loan_components_override_adjacent_profit_total():
    values = in_bse._extract_balance_sheet(
        [
            "Consolidated Bal Sheet ConsolidatedwProfit.S Loss Account",
            "Sources of Funds INCOME",
            "LOAN FUNDS | 100,212.47 |",
            "Secured Loans 3 26,429.56 15,388.49 100,212.47 63,379.36",
            "Unsecured Loans 4 2,657.54 5,296.56 EXPENDITURE",
            "Deferred Tax Liabilities (Net) 2,078.80 1,447.79",
        ]
    )

    assert values["total_borrowings"] == pytest.approx(29_087.10)
    assert values["debt_reconciliation_status"] == "reported_total_only"


def test_note_column_detected_when_note_and_particulars_are_split_headers():
    row = extract_document(
        """
        Statement of Profit and Loss for the year ended 31st March 2023
        Rs in lakhs
        PARTCULARS
        31 ST MARCH-2023                         31 ST MARCH-2022
        SL                         NOTE
        NO                          NO
        Revenue from Operations     25                    -                  -
        Other Income                26                 0.03               0.77
        Total Revenue                                  0.03               0.77
        Employees Benefits          28                 1.50               0.79
        Depreciation and Amortization 33             255.52             272.83
        Other Expenses              29               163.14             361.24
        Total Expenses                               420.16             634.86
        Profit before exceptional and tax          (420.13)           (634.09)
        Exceptional Items                                  -                  -
        Profit before tax                          (420.13)           (634.09)
        Current Tax                                        -                  -
        Deferred Tax                                       -                  -
        Profit for the period                      (420.13)           (634.09)
        """
    )[0]

    assert row["revenue_from_operations"] == 0.0
    assert row["other_income"] == pytest.approx(0.03)
    assert row["depreciation_amortization"] == pytest.approx(255.52)
    assert row["finance_costs"] == 0.0
    assert row["operating_profit_closure_status"] == "pass"


def test_period_end_uses_end_of_explicit_date_range_without_from_word():
    row = extract_document(
        """
        Consolidated Profit & Loss Statement for the year ended
        01-April-2016 To 31-March-2017
        Amount in Rs.
        Revenue from operations 100 90
        Total income 100 90
        Total expenses 80 70
        Profit before tax 20 20
        Current tax 5 5
        Profit for the period 15 15
        """
    )[0]

    assert row["period_end"] == "2017-03-31"


def test_narrative_note_word_does_not_shift_three_value_tax_subtotal():
    values = in_bse._extract_profit_and_loss(
        [
            "In preparing its Ind AS balance sheet as at 1 April 2016",
            "This note explains the principal adjustments made by the Company",
            "Profit before tax 3,373 (1,063) 2,310",
            "Current tax 1,312 - 1,312",
            "Deferred tax charge / (credit) (44) (338) (382)",
            "Tax pertaining to earlier years (335) - (335)",
            "Total tax expense 933 (338) 595",
            "Profit for the year 2,440 (725) 1,715",
        ]
    )

    assert values["income_tax_expense_reported"] == pytest.approx(933)


def test_profit_before_tax_from_operations_consumes_complete_label():
    row = extract_document(
        """
        Statement of Profit and Loss for the year ended June 30, 2023
        Notes            Year ended June 30, 2023     Year ended June 30, 2022
        Revenue from operations 20                         3 91 790              3 90 092
        Other income 21                                      4 058                  2 432
        Total income                                       3 95 848              3 92 524
        Finance costs 25                                      1 138                  1 119
        Depreciation and amortization expense 4               5 836                  5 288
        Total expense                                      3 11 901              3 13 510
        Profit before tax from operations                    83 947                 79 014
        Current tax 27.1                                     23 052                 21 950
        Deferred tax 27.1                                    (1 208)                (1 282)
        Prior year tax adjustments 27.1                      (5 711)                    771
        Profit for the year                                  67 814                 57 575
        """
    )[0]

    assert row["profit_before_tax"] == pytest.approx(83_947)
    assert row["operating_profit_closure_status"] == "pass"
    assert row["tax_identity_status"] == "pass"


def test_broken_font_map_recovers_videocon_primary_rows_and_debt():
    rows = extract_document(
        r"""
        BALANCE SHEET AS AT 30TH JUNE, 2013
        (` in Million)
        Particulars Note No. As at 30th June, 2013 As at 31st Dec., 2011
        EQUITY AND LIABILITIES
        Non-Current Liabilities
        D /RQJ 7HUP %RUURZLQJV 4 161,449.86 60,816.22
        Current Liabilities
        a) Short Term Borrowings 7 57,527.27 77,567.71
        TOTAL 369,291.58 319,557.99

        STATEMENT OF PROFIT AND LOSS FOR THE PERIOD 1ST JANUARY, 2012 TO 30TH JUNE, 2013
        (` in Million)
        Particulars Note No. Period ended 30th June, 2013 Year ended 31st Dec., 2011
        Revenue from Operations 20 187,753.84 129,194.70
        Net Revenue from Operations 181,572.75 126,502.22
        Other Income 21 4,182.66 1,063.12
        Total Income 185,755.41 127,565.34
        Finance Costs 27 27,148.18 9,777.89
        Depreciation and Amortisation 11 8,243.50 6,075.64
        Total Expenses 186,900.57 119,831.62
        III. 3UR¿W /RVV %HIRUH 7D[ (1,145.16) 7,733.72
        Current Tax - 1,296.54
        Deferred Tax (274.25) 981.60
        Tax for earlier years (154.59) 56.47
        V. 3UR¿W /RVV IRU WKH 3HULRG (716.32) 5,399.11

        CONSOLIDATED BALANCE SHEET AS AT 30TH JUNE, 2013
        (` in Million)
        Particulars Note No. As at 30th June, 2013 As at 31st Dec., 2011
        EQUITY AND LIABILITIES
        Non-Current Liabilities
        a) /RQJ 7HUP %RUURZLQJV 5 310,786.55 143,960.69
        Current Liabilities
        a) 6KRUW 7HUP %RUURZLQJV 7 58,083.65 79,567.71
        TOTAL 494,689.50 392,717.17

        CONSOLIDATED STATEMENT OF PROFIT AND LOSS FOR THE PERIOD 1ST JANUARY, 2012 TO 30TH JUNE, 2013
        (` in Million)
        Particulars Note No. Period ended 30th June, 2013 Year ended 31st Dec., 2011
        5HYHQXH IURP 2SHUDWLRQV 22 194,238.53 136,845.07
        1HW 5HYHQXH IURP 2SHUDWLRQV 188,057.44 134,152.59
        2WKHU ,QFRPH 23 8,903.04 1,375.74
        Total Income 196,960.48 135,528.33
        )LQDQFH &RVWV 28 40,774.95 15,563.88
        'HSUHFLDWLRQ DQG $PRUWLVDWLRQ 11 13,255.70 9,160.75
        Total Expenses 239,997.05 147,256.44
        III. 3UR¿W /RVV %HIRUH 7D[ (43,036.57) (11,728.11)
        Tax Expenses 30 (12,005.08) 1,964.31
        V. 3UR¿W /RVV %HIRUH 0LQRULW\ ,QWHUHVW (32,068.79) (13,642.87)
        VI. 3UR¿W /RVV IRU WKH 3HULRG (28,261.19) (13,637.66)
        """
    )

    assert [(row["statement_scope"], row["period_end"]) for row in rows] == [
        ("standalone", "2013-06-30"),
        ("consolidated", "2013-06-30"),
    ]
    assert rows[0]["revenue_from_operations"] == pytest.approx(181_572.75)
    assert rows[0]["profit_before_tax"] == pytest.approx(-1_145.16)
    assert rows[0]["profit_for_period"] == pytest.approx(-716.32)
    assert rows[0]["total_borrowings"] == pytest.approx(218_977.13)
    assert rows[1]["revenue_from_operations"] == pytest.approx(188_057.44)
    assert rows[1]["profit_before_tax"] == pytest.approx(-43_036.57)
    assert rows[1]["profit_for_period"] == pytest.approx(-28_261.19)
    assert rows[1]["total_borrowings"] == pytest.approx(368_870.20)


def test_reported_total_income_is_preserved_when_inventory_is_the_only_income():
    row = extract_document(
        """
        Profit and Loss Statement for the year ended 31 March 2016
        Amount in Rupees
        Particulars Note No. Current period Previous period
        Revenue from operations - -
        Increase in Inventories of FG/WIP/Stock-in-trade 17 21,166,000 42,500,000
        Other income 18 - 1,413,898
        Total Revenue 21,166,000 43,913,898
        Purchases of Stock-in-Trade 19 21,166,000 42,500,000
        Employee benefits expense 20 36,000 71,000
        Finance costs 21 4,803,938 4,802,008
        Depreciation and amortization expense 10 429,982 479,980
        Other expenses 22 678,625 37,890,430
        Total expenses 27,114,545 85,743,418
        Profit before tax (5,948,545) (40,829,520)
        Deferred tax 43,543 -
        Profit for the period (5,992,088) (40,829,520)
        """
    )[0]

    assert row["total_income"] == pytest.approx(21_166_000)
    assert row["total_income_source"] == "reported"
    assert row["other_operating_income"] == pytest.approx(21_166_000)
    assert row["operating_profit_closure_status"] == "pass"


def test_explicit_profit_statement_outranks_mislabeled_balance_recovery():
    rows = extract_document(
        """
        Standalone Statement of Profit and Loss
        for the period ended 31-March-2025
        in H Crore
        Revenue from operations 25 2,079.21 2,132.79
        Other income 22.40 30.07
        Total income 2,101.61 2,162.86
        Total expenses 1,963.72 1,980.22
        Profit before tax 137.89 182.64
        Total tax expense 36.89 47.22
        Profit for the period 101.00 135.42

        Annexed to and forming part of Standalone Financial Statements as at and for the year ended 31-March-2025
        in H Crore
        Segment Revenue 866.87 864.45
        Other Income 212.52 275.79
        Total Revenue 1,079.39 1,140.24
        Total expenses 941.50 957.60
        Profit before tax 137.89 182.64
        Profit for the period 101.00 135.42
        Total Assets 558.40 520.30
        """
    )

    assert len(rows) == 1
    assert rows[0]["statement_scope"] == "standalone"
    assert rows[0]["revenue_from_operations"] == pytest.approx(2_079.21)
    assert rows[0]["total_income"] == pytest.approx(2_101.61)
    assert rows[0]["total_expenses"] == pytest.approx(1_963.72)


def test_embedded_font_control_glyph_inside_profit_is_repaired():
    row = extract_document(
        """
        STANDALONE PROFIT & LOSS STATEMENT FOR THE YEAR ENDED 31st MARCH 2023
        Amount in Lakhs
        Revenue from operations 60,530.54 35,640.53
        Other income 404.94 290.11
        Total income 60,935.48 35,930.64
        Total expenses 59,833.75 35,162.28
        Pro\x02t before extraordinary items and tax 640.73 328.17
        Extraordinary items - -
        Pro\x02t before tax 640.73 328.17
        Current tax 250.00 86.00
        Deferred tax (34.00) (6.72)
        Pro\x02t for the period from continuing operations 401.84 248.89
        """
    )[0]

    assert row["profit_before_tax"] == pytest.approx(640.73)
    assert row["profit_from_continuing_operations"] == pytest.approx(401.84)


def test_discontinued_loss_reconciles_continuing_tax_identity():
    row = extract_document(
        """
        PROFIT & LOSS STATEMENT FOR THE YEAR ENDED 31st MARCH, 2015
        Amount in Rupees
        Revenue from operations - -
        Other income 1,800,000 1,200,000
        Total income 1,800,000 1,200,000
        Total expenses 7,509,868 4,733,915
        Pro\x02t / (Loss) before Tax (5,709,868) (3,533,915)
        Current Tax - -
        Pro\x02t (Loss) for the period from Continuing Operations - -
        Pro\x02t (Loss) from Discontinuing Operations (5,709,868) (3,533,915)
        Tax Expense of Discontinuing Operations - -
        Pro\x02t (Loss) from Discontinuing Operations after tax (5,709,868) (3,533,915)
        """
    )[0]

    assert row["profit_before_tax"] == pytest.approx(-5_709_868)
    assert row["profit_from_continuing_operations"] == pytest.approx(0)
    assert row["discontinued_operations_pre_tax"] == pytest.approx(-5_709_868)
    assert row["tax_identity_status"] == "pass"
    assert row["tax_identity_profit_basis"] == (
        "derived_continuing_pbt_excluding_discontinued_operations"
    )


def test_less_doubtful_debt_bridges_preexceptional_profit_to_tax():
    row = extract_document(
        """
        Profit & Loss Account for the Year ended March 31, 2011
        Amount in Rupees
        Revenue from operations 100,000,000 80,000,000
        Other income - -
        Total income 100,000,000 80,000,000
        Total expenses 15,077,078 38,777,358
        Net Profit before tax & exceptional items 84,922,922 41,222,642
        Less: Provision for Doubtful Debt 1,563,563 -
        Provision for Taxation -
        Current Tax 15,493,794 6,203,941
        Deferred Tax Liability / (Asset) 1,483,291 (1,286,227)
        Earlier years 1,188,010 2,023,118
        Net Profit after tax & exceptional items 65,194,264 34,281,810
        """
    )[0]

    assert row["exceptional_items"] == pytest.approx(1_563_563)
    assert row["profit_before_tax"] == pytest.approx(83_359_359)
    assert row["profit_after_tax"] == pytest.approx(65_194_264)
    assert row["tax_identity_status"] == "pass"


def test_less_doubtful_debt_handles_preexceptional_minority_label():
    row = extract_document(
        """
        Consolidated Profit & Loss Account for the Year ended March 31, 2011
        Amount in Rupees
        Revenue from operations 100,000,000 80,000,000
        Other income - -
        Total income 100,000,000 80,000,000
        Total expenses 7,776,270 40,300,970
        Net Profit before tax, minority interest & exceptional items 92,223,730 39,699,030
        Less: Provision for Doubtful Debt 2,250,788 -
        Provision for Taxation -
        Current Tax 18,784,621 6,773,555
        Deferred Tax Liability / (Asset) 1,483,291 (1,286,227)
        Earlier years 2,881,709 2,023,118
        Net Profit after tax & before exceptional items & minority interest 66,823,321 32,188,584
        Minority Interest 4,036,151 -
        Net Profit after tax, exceptional items & minority interest 62,787,170 32,188,584
        """
    )[0]

    assert row["profit_before_exceptional_items_and_tax"] == pytest.approx(
        92_223_730
    )
    assert row["exceptional_items"] == pytest.approx(2_250_788)
    assert row["profit_before_tax"] == pytest.approx(89_972_942)
    assert row["profit_after_tax"] == pytest.approx(66_823_321)
    assert row["tax_identity_status"] == "pass"


def test_notes_and_page_reference_columns_are_both_skipped():
    row = extract_document(
        """
        Consolidated Statement of Profit and Loss
        for the year ended March 31, 2022
        Amount in Crore
        Particulars Notes Page For the year ended 31-Mar-2022 For the year ended 31-Mar-2021
        Revenue from Operations 31     416     42,815.67     32,703.31
        Other Income 32     422     919.96     535.55
        Total Income 43,735.63 33,238.86
        Finance costs 38     425     3,859.02     3,761.51
        Total Expenses 42,285.72 31,252.13
        Profit Before Movement in Regulatory Deferral Balances, Exceptional Items and Tax 1,449.91 686.61
        Add/(Less): Net Movement in Regulatory Deferral Balances (380.42) 454.22
        Add/(Less): Deferred Tax Recoverable/(Payable) 20     395     140.95     81.80
        Profit Before Exceptional Items, Tax and Share of Net Profit of Associates 1,210.44 1,222.63
        Share of Net Profit of Associates and Joint Ventures 1,942.83 873.39
        Profit Before Exceptional Items and Tax 3,153.27 2,096.02
        Exceptional Items (150.27) (109.29)
        Profit Before Tax for the Year from Continuing Operations 3,003.00 1,986.73
        Tax Expense/(Credit)
        Current Tax 37     426     580.30     647.57
        Current Tax in respect of earlier years 37.4     428     (105.11)     -
        Deferred Tax 37     426     133.31     (145.69)
        Deferred Tax relating to earlier years 37.4     428     (588.56)     -
        Remeasurement of Deferred Tax on account of New Tax Regime 37.4     428     359.62     -
        379.56 501.88
        Profit for the Year from Continuing Operations 2,623.44 1,658.50
        """
    )[0]

    assert row["revenue_from_operations"] == pytest.approx(42_815.67)
    assert row["other_income"] == pytest.approx(919.96)
    assert row["finance_costs"] == pytest.approx(3_859.02)
    assert row["profit_before_tax"] == pytest.approx(3_003.00)
    assert row["operating_profit_closure_status"] == "pass"


def test_narrative_statement_reference_does_not_outrank_dated_primary():
    row = extract_document(
        """
        Statement of Profit and Loss for the year ended
        on that date together with the reports of the Board.
        Date: 29th July 2014
        Books will remain closed from 8th September 2014 to 13th September 2014.
        Revenue from operations 8,684 8,562
        Other income 34 131
        Total income 8,718 8,693
        Total expenses 9,490 8,522
        Profit before tax (806) 40
        Current tax 8 -
        Profit after tax (814) 40
        Cash Flow Statement

        PROFIT AND LOSS ACCOUNT FOR THE YEAR ENDED 31st MARCH, 2014
        Amount in Rs.
        Revenue from operations 86,50,42,769 84,31,31,161
        Other Income 33,96,790 1,30,81,109
        Total Revenue 86,84,39,559 85,62,12,270
        Total Expenses 94,90,40,181 85,22,25,797
        Profit before tax 9,16,82,899 40,26,613
        Current tax 8,22,935 -
        Deferred tax 2,95,22,708 3,75,444
        Profit for the period 6,13,37,256 36,51,169
        """
    )[0]

    assert row["period_end"] == "2014-03-31"
    assert row["revenue_from_operations"] == pytest.approx(865_042_769)
    assert row["total_income"] == pytest.approx(868_439_559)


def test_reported_continuing_pbt_after_associate_keeps_post_associate_basis():
    values = {
        "revenue_from_operations": 42_815.67,
        "other_income": 919.96,
        "total_income": 43_735.63,
        "total_expenses": 42_285.72,
        "finance_costs": 3_859.02,
        "associate_jv_share": 1_942.83,
        "associate_jv_share_position": "before_profit_from_continuing_operations",
        "profit_before_tax_position": "after_associate_share",
        "profit_before_tax_from_continuing_operations": 3_003.00,
        "current_tax": 580.30,
        "profit_from_continuing_operations": 2_623.44,
    }

    in_bse._derive(values, "industrial")

    assert values["profit_before_tax"] == pytest.approx(3_003.00)
    assert values["profit_before_tax_basis"] == "after_associate_share"


def test_total_profit_row_does_not_overwrite_reported_total_expenses():
    row = extract_document(
        """
        Consolidated Statement of Profit and Loss for the year ended March 31, 2022
        All amounts in INR Crores
        Revenue from operations 24 12,433.66 8,400.04
        Other income 25 115.51 66.35
        Total Income (I + II) 12,549.17 8,466.39
        Expenses
        Finance costs 29 115.93 100.00
        Depreciation and amortisation expense 30 517.23 400.00
        Other expenses 31 9,330.46 6,353.74
        Total Expenses (IV) 9,963.62 6,853.74
        Profit before tax from continuing operations 2,585.55 1,612.65
        Current tax 657.53 357.99
        Deferred tax 39.10 56.41
        Profit for the year from continuing operations 1,888.92 1,198.25
        Total Profit for the year (VII + X) 1,888.92 1,197.94
        """
    )[0]

    assert row["total_expenses"] == pytest.approx(9_963.62)
    assert row["operating_profit_closure_status"] == "pass"


def test_blank_pbt_row_does_not_borrow_the_following_current_tax_amount():
    row = extract_document(
        """
        Statement of Profit & Loss for the year ended 31st March 2020
        Amount in Rs.
        Revenue from Operations 3,000,888,452 3,547,414,480
        Other Income 66,892,331 43,383,197
        Total Income 3,067,780,78 3,590,797,67
        Total Expenses 2,995,357,26 3,500,937,61
        Profit Before Exceptional Items and Tax 72,423,523 89,860,059
        Exceptional Items
        Profit Before Tax
        Tax Expenses
        Current Tax 15,004,000 22,500,000
        Deferred Tax (515,916) 1,942,450
        Profit for the year 57,935,440 65,417,609
        """
    )[0]

    assert row["profit_before_tax"] == pytest.approx(72_423_523)
    assert row["income_tax_expense"] == pytest.approx(14_488_084)
    assert row["tax_identity_status"] == "pass"


def test_unlabelled_reconciling_profit_subtotals_are_recovered():
    standalone = extract_document(
        """
        STANDALONE PROFIT AND LOSS ACCOUNT FOR THE YEAR ENDED 31st MARCH 2020
        Rs in Lakhs
        Revenue from operations 15 96.59 100.82
        Other Income 16 6.84 8.16
        Total Revenue 103.43 108.98
        Expenses:
        Employee benefits expense 17 49.90 59.09
        Depreciation and amortization expenses 2 2.41 1.10
        Other expenses 18 47.06 28.04
        Total expenses 99.37 88.23
        4.06 20.74
        Tax Expenses
        (1) Current tax 3.69 5.39
        (2) Deferred tax -2.04 0.00
        2.42 15.35
        """
    )[0]

    consolidated = extract_document(
        """
        CONSOLIDATED PROFIT AND LOSS ACCOUNT FOR THE YEAR ENDED 31st MARCH 2020
        Rs in Lakhs
        Revenue from operations 15 1007.53 1944.25
        Other Income 16 7.57 10.13
        1015.10 1954.38
        Expenses:
        Employee benefits expense 17 699.51 815.85
        Depreciation and amortization expenses 2 2.44 1.14
        Finance Cost 0.00 0.07
        Other expenses 18 292.71 1093.18
        994.67 1910.23
        20.43 44.15
        Exceptional Items 1.57 0.00
        18.86 44.15
        (1) Current tax 6.65 10.21
        (2) Deferred tax (2.04) 0.00
        14.26 33.94
        """
    )[0]

    assert standalone["profit_before_tax"] == pytest.approx(4.06)
    assert standalone["profit_after_tax"] == pytest.approx(2.42)
    assert standalone["tax_identity_status"] == "pass"
    assert consolidated["total_expenses"] == pytest.approx(994.67)
    assert consolidated["profit_before_tax"] == pytest.approx(18.86)
    assert consolidated["profit_after_tax"] == pytest.approx(14.26)
    assert consolidated["operating_profit_closure_status"] == "pass"
    assert consolidated["tax_identity_status"] == "pass"


def test_unlabelled_asset_total_with_enumerated_liabilities_proves_zero_debt():
    row = extract_document(
        """
        STANDALONE BALANCE SHEET AS AT 31st MARCH, 2020
        Rs in Lakhs
        Note As at 31-03-2020 As at 31-03-2019
        (1) Non-Current Assets
        Property, plant and equipment 2 19.98 10.06
        (2) Current Assets
        Cash and Cash Equivalents 7 8.92 38.64
        701.09 740.28
        (a) Equity Share Capital 10 396.00 396.00
        (b) Other Equity 292.48 287.40
        (2) Current Liabilities:
        (a) Financial liabilities
        (1) Trade Payables 12 0.19 0.00
        (b) Other Current Liabilities 13 8.74 6.82
        701.09 740.28

        STANDALONE PROFIT AND LOSS ACCOUNT FOR THE YEAR ENDED 31st MARCH 2020
        Rs in Lakhs
        Revenue from operations 96.59 100.82
        Other Income 6.84 8.16
        Total Revenue 103.43 108.98
        Total expenses 99.37 88.23
        Profit before tax 4.06 20.74
        Current tax 1.64 5.39
        Profit for the year 2.42 15.35
        """
    )[0]

    assert row["total_assets"] == pytest.approx(701.09)
    assert row["total_borrowings"] == 0.0
    assert row["debt_reconciliation_status"] == "true_zero"


def test_interleaved_contents_glyphs_recover_primary_statement_headings():
    rows = extract_document(
        """
        BCalance Sheet as at 31st March, 2020
        ontents
        INR in Lakhs
        Total Assets 397,455.62 421,722.05
        Total Equity 167,543.44 173,430.44
        statement of Profit and Loss ..................................................................................................... 87
        Non-Current Liabilities
        Financial Liabilities
        Borrowings 3,050.59 5,504.61
        Current Liabilities
        Financial Liabilities
        Borrowings 23,118.13 25,327.10

        SContentsof Profit and Loss for the year ended 31st March, 2020
        tatement
        INR in Lakhs
        Revenue from Operations 25,548.38 41,404.50
        Other Income 3,614.69 7,312.67
        Balance sheet .......................................................................................................................... 86
        Total Income 28,836.22 49,028.87
        Finance Cost 13,871.44 11,462.74
        Total Expenses 32,690.83 48,733.16
        Profit before Tax (3,854.61) 295.71
        Current Tax (251.62) -
        Deferred tax (1,840.22) (953.61)
        Profit for the Year (5,946.45) (709.44)

        CConsolidated
        ontents Balance Sheet as at 31st March, 2020
        INR in Lakhs
        Total Assets 459,822.12 472,584.72
        Total Equity 155,425.19 162,416.00
        statement of Profit and Loss ..................................................................................................... 135
        Non-Current Liabilities
        Financial Liabilities
        Borrowings 21,245.17 26,344.19
        FInAnCIAL
        Current stAteMents - stAnDALone
        Liabilities
        Financial Liabilities
        Borrowings 23,282.72 25,900.61

        CConsolidated
        ontents Statement of Profit and Loss for the year ended 31st March, 2020
        INR in Lakhs
        Revenue from Operations 27,272.55 47,078.08
        Other Income 3,845.41 7,809.54
        Balance sheet .......................................................................................................................... 134
        Total Income 31,117.96 54,887.62
        Finance Costs 13,893.87 21,832.57
        Total Expenses 34,023.68 54,918.85
        Loss before Tax (2,905.72) (31.23)
        Current Tax (298.55) (2.21)
        Deferred tax (1,428.02) (1,034.30)
        Loss after tax but before adjustments (4,632.50) (1,182.47)
        Cash Flow statement ................................................................................................................... 136
        Share of Loss from Associate / JV using equity method (1,429.10) (368.66)
        Loss for the year (6,061.60) (1,551.13)
        """
    )

    assert [row["statement_scope"] for row in rows] == [
        "standalone",
        "consolidated",
    ]
    assert rows[0]["revenue_from_operations"] == pytest.approx(25_548.38)
    assert rows[0]["total_borrowings"] == pytest.approx(26_168.72)
    assert rows[1]["revenue_from_operations"] == pytest.approx(27_272.55)
    assert rows[1]["total_borrowings"] == pytest.approx(44_527.89)
    assert rows[1]["associate_jv_share"] == pytest.approx(-1_429.10)
    assert rows[1]["profit_for_period"] == pytest.approx(-6_061.60)


def test_navigation_overlay_fragments_are_rejoined_inside_profit_rows():
    row = extract_document(
        """
        Statement of Profit and Loss for the year ended 31st March, 2020
        INR in Lakhs
        Revenue from Operations 25,548.38 41,404.50
        Other Income 3,614.69 7,312.67
        Share of
        Corporate Profit / (Loss)
        Governance of Joint
        Report Ventures and Partnership Firms (Net) (326.85) 311.70
        Total Income 28,836.22 49,028.87
        Finance Cost 13,871.44 11,462.74
        Total Expenses 32,690.83 48,733.16
        Profit before Tax (3,854.61) 295.71
        Tax Expense
        Flow Current
        Cash(a) statement
        Tax ................................................................................................................... 88
        (251.62) -
        Deferred tax (charge) / credit (1,840.22) (953.61)
        Profit for the Year (5,946.45) (709.44)
        """
    )[0]

    assert row["associate_jv_share"] == pytest.approx(-326.85)
    assert row["current_tax"] == pytest.approx(-251.62)
    assert row["income_tax_expense"] == pytest.approx(-2_091.84)
    assert row["tax_identity_status"] == "pass"


def test_ocr_thousand_rupee_header_sets_inr_scale():
    row = extract_document(
        """
        PROFIT AND LOSS ACCOUNT FOR THE YEAR ENDED MARCH 31, 2011
        (` in ‘000)
        Revenue from operations 2,500,000 2,000,000
        Other income 61,615 50,000
        Total income 2,561,615 2,050,000
        Total expenses 2,344,037 1,900,000
        Profit before tax 217,578 150,000
        Current tax 44,170 30,000
        Deferred tax 31,076 20,000
        MAT credit entitlement 6,432 -
        Profit after tax 148,764 100,000
        """
    )[0]

    assert row["currency"] == "INR"
    assert row["unit_scale"] == 1_000
    assert row["unit_scale_source"] == "statement_header"


def test_document_presentation_currency_supplies_rupee_unit_scale():
    row = extract_document(
        """
        The financial statements are presented in Indian Rupee (INR), which is
        also the Company's functional currency.

        STATEMENT OF PROFIT AND LOSS FOR THE YEAR ENDED 31 MARCH 2019
        Revenue from operations 289,376,059 250,000,000
        Other income 6,092,407 5,000,000
        Total income 295,468,466 255,000,000
        Total expenses 270,404,353 235,000,000
        Profit before tax 25,064,113 20,000,000
        Current tax 8,700,000 7,000,000
        Deferred tax (1,317,000) (1,000,000)
        Profit for the year 17,681,113 14,000,000
        """
    )[0]

    assert row["currency"] == "INR"
    assert row["currency_source"] == "document_accounting_policy"
    assert row["unit_scale"] == 1
    assert row["unit_scale_source"] == "document_presentation_currency"


def test_mat_credit_row_with_net_subtotal_preserves_gross_current_tax():
    row = extract_document(
        """
        PROFIT & LOSS ACCOUNT FOR THE YEAR ENDED 31ST MARCH, 2011
        Amount in Rs.
        Revenue from operations 4,414,118,091 3,000,000,000
        Other income 13,419,551 10,000,000
        Total income 4,427,537,642 3,010,000,000
        Total expenses 4,313,162,241 2,900,000,000
        Profit before tax 114,375,401 110,000,000
        Current Tax 22,795,618 7,606,739
        Less : MAT Credit Entitlement 5,669,082 17,126,536 7,606,739 1,161,635
        Deferred Tax Liability / (Asset) 14,907,563 10,000,000
        Profit for the period 82,341,302 91,231,626
        """
    )[0]

    assert row["current_tax"] == pytest.approx(22_795_618)
    assert row["mat_credit"] == pytest.approx(-5_669_082)
    assert row["income_tax_expense"] == pytest.approx(32_034_099)
    assert row["tax_identity_status"] == "pass"


def test_blank_deferred_tax_row_does_not_borrow_following_profit_value():
    row = extract_document(
        """
        Statement of Profit and loss for the year ended 31st March, 2016
        (Amount in z)
        Other income 11 71,98,876 74,82,356
        Total Revenue 71,98,876 74,82,356
        Employees Benefits 12 4,16,830 3,11,237
        Other expenses 13 5,73,981 2,67,449
        Total Expenses 9,90,811 5,78,686
        Profit/(Loss) before tax 62,08,065 69,03,670
        Less: Tax expense:
        (1) Current tax 19,20,000 21,35,000
        (2) Income Tax of Earlier Years (1,770) -
        (3) Deferred tax Assets/ (Liability) ‘ é
        Profit (Loss) for the Year 42,89,835 47,68,670
        """
    )[0]

    assert row.get("deferred_tax") is None
    assert row["income_tax_expense"] == pytest.approx(1_918_230)
    assert row["tax_identity_status"] == "pass"


def test_repeated_rupee_column_headers_mean_whole_rupees():
    row = extract_document(
        """
        STATEMENT OF PROFIT AND LOSS FOR THE YEAR ENDED 31 MARCH 2019
        Particulars                                      Rs.              Rs.
        Revenue from operations                    18,000,000       15,000,000
        Other income                                  515,297          400,000
        Total income                               18,515,297       15,400,000
        Total expenses                             16,000,000       13,500,000
        Profit before tax                           2,515,297        1,900,000
        Current tax                                   700,000          500,000
        Deferred tax                                  115,297          100,000
        Profit for the year                         1,700,000        1,300,000
        """
    )[0]

    assert row["currency"] == "INR"
    assert row["unit_scale"] == 1
    assert row["unit_scale_source"] == "statement_header"


def test_repeated_unpunctuated_rs_column_headers_mean_whole_rupees():
    row = extract_document(
        """
        STATEMENT OF PROFIT AND LOSS FOR THE YEAR ENDED 31 MARCH 2017
        Note  For the year ended  For the year ended
        No.   31st March 2017     31st March 2016
              Rs                  Rs
        Revenue from operations        5,24,140       2,16,623
        Other income                   3,83,882       4,27,919
        Total income                   9,08,022       6,44,542
        Total expenses                 5,70,356       6,67,207
        Profit before tax              3,37,666        (22,665)
        Current tax                            -               -
        Profit for the year            3,37,666        (22,665)
        """
    )[0]

    assert row["currency"] == "INR"
    assert row["unit_scale"] == 1
    assert row["unit_scale_source"] == "statement_header"


def test_backtick_whole_rupee_amount_header_sets_unit_scale():
    row = extract_document(
        """
        STATEMENT OF PROFIT AND LOSS FOR THE YEAR ENDED 31 MARCH 2019
        (Amount in `)
        Revenue from operations 18,000,000 15,000,000
        Other income 515,297 400,000
        Total income 18,515,297 15,400,000
        Total expenses 16,000,000 13,500,000
        Profit before tax 2,515,297 1,900,000
        Current tax 700,000 500,000
        Deferred tax 115,297 100,000
        Profit for the year 1,700,000 1,300,000
        """
    )[0]

    assert row["currency"] == "INR"
    assert row["currency_source"] == "statement_header"
    assert row["unit_scale"] == 1
    assert row["unit_scale_source"] == "statement_header"


def test_repeated_spelled_rupee_column_headers_mean_whole_rupees():
    row = extract_document(
        """
        PROFIT AND LOSS ACCOUNT FOR THE YEAR ENDED 31ST MARCH 2011
        Particulars                                Rupees             Rupees
        Revenue from operations                18,000,000         15,000,000
        Other income                               515,297            400,000
        Total income                            18,515,297         15,400,000
        Total expenses                          16,000,000         13,500,000
        Profit before tax                        2,515,297          1,900,000
        Current tax                                700,000            500,000
        Deferred tax                               115,297            100,000
        Profit for the year                      1,700,000          1,300,000
        """
    )[0]

    assert row["currency"] == "INR"
    assert row["unit_scale"] == 1
    assert row["unit_scale_source"] == "statement_header"


def test_repeated_backtick_column_headers_mean_whole_rupees():
    row = extract_document(
        """
        PROFIT & LOSS ACCOUNT FOR THE YEAR ENDED 31ST MARCH 2011
        Schedule                                      2011                 2010
                                                         `                    `
        Revenue from operations                18,000,000           15,000,000
        Other income                               515,297              400,000
        Total income                            18,515,297           15,400,000
        Total expenses                          16,000,000           13,500,000
        Profit before tax                        2,515,297            1,900,000
        Current tax                                700,000              500,000
        Deferred tax                               115,297              100,000
        Profit for the year                      1,700,000            1,300,000
        """
    )[0]

    assert row["currency"] == "INR"
    assert row["unit_scale"] == 1
    assert row["unit_scale_source"] == "statement_header"


def test_mojibake_profit_label_recovers_pbt_and_tax_identity():
    row = extract_document(
        """
        CONSOLIDATED STATEMENT OF PROFIT AND LOSS FOR THE YEAR ENDED MARCH 31, 2015
        (Rs. in Lacs)
        Revenue from operations (Net) 973774.43 891057.02
        Other income 10507.94 10497.52
        Total Revenue 984282.37 901554.54
        Finance costs 16317.66 20742.57
        Total Expenses 938358.81 872646.10
        ProÛt before tax {including proÛt of Rs. 1422.73 lacs (previous
        year Rs. 2382.84 lacs) from discontinuing operation - refer note
        no. 52} (I - II) 45923.56 28908.44
        Current tax 16351.73 11156.06
        Tax related to earlier years (74.18) (5995.21)
        Deferred tax charge 1694.26 295.77
        Total tax expense {including tax expense of Rs. 403.48 lacs
        (previous year Rs. 684.11 lacs) from discontinuing operation -
        refer note no. 52} 17971.81 5456.62
        ProÛt for the year (before adjustment for Minority Interest)
        {including proÛt of Rs. 1019.25 lacs (previous year Rs. 1698.73
        lacs) from discontinuing operation - refer note no. 52} 27951.75 23451.82
        """
    )[0]

    assert row["profit_before_tax"] == pytest.approx(45_923.56)
    assert row["income_tax_expense"] == pytest.approx(17_971.81)
    assert row["profit_for_period"] == pytest.approx(27_951.75)
    assert row["tax_identity_status"] == "pass"


def test_oci_labels_do_not_split_primary_profit_statement():
    row = extract_document(
        """
        Standalone Statement of Profit and Loss
        for the Year ended March 31, 2022
        (` in Crore)
        Note Year ended March 31, 2022 Year ended March 31, 2021
        INCOME                  Items that will be reclassified to Statement of Profit and Loss
        Revenue from Operations 67,983 42,951
        Other Income 205 400
        Total Income 68,188 43,351
        Employee Benefits Expense 2,058 1,844
        Finance Cost 1,417 1,469
        Depreciation and Amortization Expense 1,752 1,708
        This is the Standalone Statement of Profit and Loss referred in our report of even date
        Other Expenses 6,962 4,507
        Total Expenses 59,624 41,784
        Profit before Tax 8,457 1,574
        Current Tax Expense 1,496 283
        Deferred Tax Expense 1,454 298
        Profit for the year 5,507 993
        Items that will not be reclassified to Statement of Profit and Loss
        """
    )[0]

    assert row["profit_before_tax"] == pytest.approx(8_457)
    assert row["income_tax_expense"] == pytest.approx(2_950)
    assert row["tax_identity_status"] == "pass"


def test_blank_earlier_tax_row_does_not_borrow_following_profit_value():
    row = extract_document(
        """
        PROFIT & LOSS STATEMENT FOR THE YEAR ENDED 31ST MARCH, 2016
        AMOUNT (Rs.) AMOUNT (Rs.)
        Revenue from operations 809,120.00 865,180.00
        Other income 1,765,557.38 1,713,827.00
        Total revenue 2,574,677.38 2,579,007.00
        Total expenses 1,746,175.88 1,701,701.25
        Profit before tax 828,501.50 877,305.75
        TAX EXPENSE
        CURRENT TAX :
        EARLIER YEARS TAX .
        DEFERRED TAX : :
        Xl PROFIT (LOSS) FOR THE PERIOD 828,501.50 877,305.75
        """
    )[0]

    assert row.get("earlier_year_tax") is None
    assert row.get("deferred_tax") is None
    assert row["income_tax_expense"] == pytest.approx(0)
    assert row["income_tax_expense_source"] == (
        "derived_pbt_equals_net_with_explicit_tax_section"
    )
    assert row["tax_identity_status"] == "pass"


def test_single_period_column_is_not_confused_with_march_day_in_heading():
    row = extract_document(
        """
        CONSOLIDATED STATEMENT OF PROFIT AND LOSS FOR THE MARCH 31, 2015                                                                 CONSOLIDATED CASH FLOW STATEMENT FOR THE YEAR ENDED 31ST MARCH, 2015
        For year ended
        Particulars                                      Note No       31-Mar-2015
        Revenue from Operations                                18       50,93,47,125
        Other Income                                           19          19,09,799
        Total Revenue                                                   51,12,56,924
        Expenses:
        Cost of Material Consumed                              20       12,77,76,998
        Employee Benefits Expenses                             22        3,74,16,343
        Finance Cost                                           23       11,18,22,994
        Depreciation and Amortisation Expenses                           1,53,15,270
        Other Expenses                                         24        7,07,65,258
        Total Expenses                                                  36,30,96,863
        Profit before Tax                                               14,81,60,061
        Current Tax                                                      5,25,00,000
        Deferred Tax                                                       21,31,365
        Profit for the period                                             9,35,27,224
        """
    )[0]

    assert row["revenue_from_operations"] == pytest.approx(509_347_125)
    assert row["other_income"] == pytest.approx(1_909_799)
    assert row["finance_costs"] == pytest.approx(111_822_994)
    assert row["operating_profit_closure_status"] == "pass"


def test_generic_borrowings_under_financial_liabilities_is_reported_total():
    row = extract_document(
        """
        STANDALONE BALANCE SHEET AS AT 31st MARCH 2021
        PARTICULARS                                      NOTE       31 MARCH 2021       31 MARCH 2020
        ASSETS
        Financial Assets
        Cash and cash equivalents                            1          1,343,532          20,138,326
        Total Assets                                                  185,766,481         133,639,614
        EQUITY AND LIABILITIES
        Financial Liabilities
        Borrowings                                           9            286,759           2,581,649
        Trade payables                                      10             42,343              70,800
        Other financial liabilities                         11            417,126             609,488
        Total Financial Liabilities                                       746,228           3,261,937

        Standalone Statement of Profit and loss for the year ended 31st March 2021
        Rs. Rs.
        Revenue from operations 5,025,895 4,000,000
        Other income 53,520,714 45,000,000
        Total income 58,546,609 49,000,000
        Finance costs - -
        Total expenses 3,816,653 3,500,000
        Profit before tax 54,729,956 45,500,000
        Current tax 322,000 300,000
        Deferred tax 5,903 5,000
        Profit for the period 54,402,053 45,195,000
        """
    )[0]

    assert row["total_borrowings"] == pytest.approx(286_759)
    assert row["debt_reconciliation_status"] == "reported_total_only"


def test_blank_tax_section_and_equal_pre_and_post_tax_profit_implies_zero_tax():
    row = extract_document(
        """
        STATEMENT OF PROFIT & LOSS AS ON MARCH 31, 2024
        Note As at March 31, 2024 As at March 31, 2023
        Thousands Thousands
        Revenue from operations 12 - -
        Other income 13 226.29 1920.00
        Total income 226.29 1920.00
        Employee Benefits Expense 14 80.02 200.00
        Other Expenses 2302.87 865.84
        Total Expenses 2382.89 1065.84
        Profit before tax (2156.60) 854.16
        Tax Expense
        Current Tax
        Deferred Tax
        Profit for the period from continuing operations (2156.60) 854.16
        """
    )[0]

    assert row["income_tax_expense"] == pytest.approx(0)
    assert row["income_tax_expense_source"] == (
        "derived_pbt_equals_net_with_explicit_tax_section"
    )
    assert row["tax_identity_status"] == "pass"


def test_note_reference_does_not_replace_depreciation_and_before_income_tax_is_pbt():
    row = extract_document(
        """
        REVISED STANDALONE STATEMENT OF PROFIT AND LOSS
        for the year ended March 31, 2023
        ` in million
        Particulars Notes For the year ended March 31, 2023 For the year ended March 31, 2022
        Revenue from operations 28 11,284.67 5,078.07
        Other income 29 509.14 219.27
        Total income (A) 11,793.81 5,297.34
        Employee benefits expense 31 1,510.96 999.76
        Other expenses 33 3,745.26 2,099.74
        Total expenses (B) 6,733.13 4,090.74
        Earnings before interest, depreciation, amortisation and tax (EBITDA) before exceptional items (C) (A-B) 5,060.68 1,206.60
        Depreciation and amortisation expenses 2,4,6,38 1,173.09 1,184.23
        Finance costs 32 1,538.14 1,440.67
        Profit / (Loss) before exceptional items and tax (D) 2,349.45 (1,418.30)
        Exceptional items (E) 423.08 (44.58)
        Profit / (Loss) before income tax from Continuing operations (F) (D+E) 2,772.53 (1,462.88)
        Tax expense (G) 894.88 (719.53)
        Current tax (includes tax for the earlier years) 21 178.14 (37.76)
        MAT credit entitlement (178.41) -
        Deferred tax expense / (credit) 21 895.15 (681.77)
        Profit / (Loss) for the year from Continuing operations (H) (F-G) 1,877.65 (743.35)
        """
    )[0]

    assert row["depreciation_amortization"] == pytest.approx(1_173.09)
    assert row["profit_before_tax"] == pytest.approx(2_772.53)
    assert row["current_tax"] == pytest.approx(178.14)
    assert row["income_tax_expense"] == pytest.approx(894.88)
    assert row["operating_profit_closure_status"] == "pass"
    assert row["tax_identity_status"] == "pass"


def test_single_spaced_note_current_and_prior_columns_do_not_merge_as_indian_grouping():
    row = extract_document(
        """
        STATEMENT OF PROFIT AND LOSS FOR THE YEAR ENDED 31ST MARCH 2015
        Amounts in INR
        Particulars Note Number 2014-15 2013-14
        Revenue from operations 13 89237 349368
        Other income 14 800 211703
        Total Revenue 90037 561071
        Employee benefits expense 15 65000 115000
        Depreciation and amortization expenses 7 87 106—
        Other expenses 16 592368 429399
        Total expenses 657455 244505
        Profit before exceptional items and tax (567418) 16566
        Exceptional items 0 0
        Profit before tax (567418) 16566
        Current tax 0 0
        Deferred tax 0 0
        Profit for the year (567418) 16566
        """
    )[0]

    assert row["depreciation_amortization"] == pytest.approx(87)
    assert row["operating_profit_closure_status"] == "pass"


def test_legacy_loan_row_ignores_decorative_dash_before_two_amount_columns():
    values = in_bse._extract_balance_sheet(
        [
            "CONSOLIDATED BALANCE SHEET AS AT 31.03.2010",
            "SOURCES OF FUNDS",
            "LOAN FUNDS",
            "Unsecured Loans - . : 137,409,386, 160,543,679",
            "Application of Funds",
            "Total Assets 256,630,160 279,764,453",
        ]
    )

    assert values["total_borrowings"] == pytest.approx(137_409_386)
    assert values["debt_reconciliation_status"] == "reported_total_only"


def test_misspelled_profit_after_tax_and_plain_earlier_tax_reconcile():
    row = extract_document(
        """
        Consolidated Statement of Profit & Loss for the year ended 31st March 2022
        Amounts Rs. In Lakhs
        Particulars Note No. March 31, 2022 March 31, 2021
        Revenue from Operations 30 2,88,075.07 4,05,741.78
        Other Income 31 567.35 1,807.23
        Gain on disposal of subsidiaries 988.84 -
        Total Revenue (A) 2,89,631.26 4,07,549.01
        Finance Costs 35 1,033.62 1,269.82
        Other Expenses 36 2,85,592.97 4,02,850.19
        Total Expenses (B) 2,86,626.59 4,04,120.01
        Profit Before Tax [C = (A-B)] 3,004.67 3,429.00
        Less: Tax Expense:
        Current Tax 247.57 147.25
        Earlier Tax (23.91) (7.73)
        Deferred tax assets [Net] (120.20) (99.63)
        Total Tax Expense (D) 103.46 39.89
        Profir After Tax (C-D) 2,901.21 3,389.11
        """
    )[0]

    assert row["earlier_year_tax"] == pytest.approx(-23.91)
    assert row["profit_after_tax"] == pytest.approx(2_901.21)
    assert row["income_tax_expense"] == pytest.approx(103.46)
    assert row["tax_identity_status"] == "pass"


def test_profit_for_period_uses_preceding_roman_total_amount_row():
    row = extract_document(
        """
        STATEMENT OF PROFIT & LOSS FOR THE YEAR ENDED 31ST MARCH, 2013
        SR. NOTE 31.03.2013 31.03.2012
        NO. PARTICULARS NO. RS. RS.
        I Income From Operations 12 372,000.00 846,400.00
        II Other Income 13 57,750.00 26,750.00
        III Closing Stock 8 1,576,000.00 1,474,000.00
        IV Total Revenue 2,005,750.00 2,347,150.00
        V EXPENSES
        Other Expenses 1,970,175.12 2,253,355.63
        VI Profit before exceptional and extraordinary items and tax 35,574.88 93,794.37
        X Profit before tax 35,574.88 93,794.37
        XI TAX EXPENSE
        (1) Income Tax Paid 105,353.00 -
        XVI (69,778.12) 93,794.37
        Profit/(Loss) for the period (XI + XIV)
        XVII Earning per equity share
        (1) Basic 0.04 -
        """
    )[0]

    assert row["profit_for_period"] == pytest.approx(-69_778.12)
    assert row["tax_identity_status"] == "pass"


def test_misspelled_standalone_side_by_side_heading_keeps_right_column_labels():
    left = [
        "Stanadalone Balance Sheet as at 31st March 2023",
        "(Rs in lakhs)",
        "Particulars Note As at March 31, 2023 As at March 31, 2022",
        "ASSETS",
        "Total Assets 61,236.94 64,928.26",
        "EQUITY AND LIABILITIES",
        "Borrowings 10,439.79 13,152.53",
        "Total Equity and Liabilities 61,236.94 64,928.26",
    ]
    right = [
        "Stanadalone Statement of profit and loss for the year ended 31st March, 2023",
        "(Rs in lakhs)",
        "Particulars Note For the year ended March 31, 2023 For the year ended March 31, 2022",
        "Revenue from Operations 33 38,654.70 42,869.99",
        "Other Income 34 923.05 1,222.83",
        "TOTAL INCOME 39,577.74 44,092.82",
        "Finance costs 38 483.28 368.78",
        "Other expenses 35,128.20 36,653.61",
        "TOTAL EXPENSES 35,611.48 37,022.39",
        "Profit/(loss) before tax 3,966.26 7,070.42",
        "Tax Expense",
        "Current tax 1,119.45 1,909.69",
        "Deferred tax (128.74) (113.98)",
        "Adjustments for earlier years 94.38 -",
        "Total tax expense 26 1,085.09 1,795.71",
        "Profit / (Loss) for the year 2,881.17 5,274.71",
    ]
    width = max(len(left), len(right))
    statement = "\n".join(
        f"{(left[index] if index < len(left) else ''):<120}"
        f"{(right[index] if index < len(right) else '')}"
        for index in range(width)
    )

    row = extract_document(statement)[0]

    assert row["statement_scope"] == "standalone"
    assert row["current_tax"] == pytest.approx(1_119.45)
    assert row["deferred_tax"] == pytest.approx(-128.74)
    assert row["earlier_year_tax"] == pytest.approx(94.38)
    assert row["profit_for_period"] == pytest.approx(2_881.17)
    assert row["tax_identity_status"] == "pass"


def test_compact_other_income_label_preserves_operating_profit_closure():
    row = extract_document(
        """
        CONSOLIDATED STATEMENT OF PROFIT & LOSS FOR THE YEAR ENDED 31-3-2014
        Sr. Particulars Note 31/03/2014 31/03/2013
        No. Rupees Rupees Rupees Rupees
        SALES OF PRODUCTS 1,22,85,69,217 91,24,71,465
        OTHER OPERATING INCOME 19 2,58,18,917 2,67,39,070
        LESS EXCISE DUTY 8,47,24,486 7,83,18,340
        Revenue from operations 1,16,96,63,647 86,08,92,195
        OTHERINCOME 20 37,09,855 73,08,739
        TOTAL REVENUE Rs. 1,17,33,73,503 86,82,00,934
        Finance Costs 24 1,36,28,798 1,54,22,061
        Other expenses 99,57,10,360 78,49,39,601
        TOTAL EXPENSES Rs. 1,01,39,39,158 80,03,61,662
        Profit before Taxation 15,94,34,345 6,78,39,272
        Current Tax 5,36,70,000 1,73,22,215
        Deferred Tax 26,55,889 56,90,793
        Net Profit for the Year Before Minority Interest 10,31,08,456 4,40,14,378
        """
    )[0]

    assert row["other_income"] == pytest.approx(3_709_855)
    assert row["operating_profit_closure_status"] == "pass"


def test_compound_tax_note_after_including_clause_is_not_the_tax_value():
    row = extract_document(
        """
        PROFIT AND LOSS ACCOUNT
        Rs in Million except share data
        For the year ended March 31
        Schedule 2010 2009
        INCOME
        Net sales and services 229,220 215,073
        Other income, net 12 8,753 (4,804)
        Total income 237,973 210,269
        EXPENDITURE
        Interest 16 1,084 1,968
        Other expenses 180,001 172,822
        Total expenses 181,085 174,790
        PROFIT BEFORE TAXATION 56,888 35,479
        Provision for taxation including deferred tax and fringe benefit tax 18(23) 7,908 5,741
        PROFIT FOR THE PERIOD 48,980 29,738
        """
    )[0]

    assert row["current_tax"] == pytest.approx(7_908)
    assert row["income_tax_expense"] == pytest.approx(7_908)
    assert row["tax_identity_status"] == "pass"


def test_tight_march_year_balance_heading_matches_same_period_profit_statement():
    row = extract_document(
        """
        Consolidated Balance Sheet
        as at 31st March2014
        Amount in Rs.
        Particulars Notes 31.03.14 31.03.13
        EQUITY AND LIABILITIES
        NON-CURRENT LIABILITIES
        Long Term Borrowings 4 1,503,400 538,344
        CURRENT LIABILITIES
        Short Term Borrowings 7 5,918,825,503 5,082,198,480
        TOTAL 28,534,432,117 27,138,886,172
        ASSETS
        TOTAL 28,534,432,117 27,138,886,172

        Consolidated Profit & Loss Account
        for the year ended 31st March 2014
        Amount in Rs.
        Particulars Notes 31.03.14 31.03.13
        Revenue from operations 9,255,559,322 8,000,000,000
        Other income 126,990,400 100,000,000
        Total income 9,382,549,722 8,100,000,000
        Finance costs 170,046,191 150,000,000
        Other expenses 6,484,644,903 5,900,000,000
        Total expenses 6,654,691,094 6,050,000,000
        Profit before tax 2,727,858,628 2,050,000,000
        Current tax 904,287,724 700,000,000
        Deferred tax (4,256,234) (3,000,000)
        Profit for the period 1,827,827,138 1,353,000,000
        """
    )[0]

    assert row["period_end"] == "2014-03-31"
    assert row["total_borrowings"] == pytest.approx(5_920_328_903)
    assert row["debt_reconciliation_status"] == "components_only"


def test_balance_float_keeps_maturity_buckets_and_source_lines():
    values = in_bse._extract_balance_sheet([
        'Consolidated Balance Sheet', 'Non-current liabilities',
        'Trade payables 3.00 2.00', 'Contract liabilities 2.00 1.00',
        'Current liabilities', 'Trade payables 12.00 10.00',
        'Contract liabilities 7.00 6.00',
    ])
    assert values['trade_payables_non_current'] == 3
    assert values['trade_payables_current'] == 12
    assert values['current_contract_liabilities'] == 7
    assert values['non_current_contract_liabilities'] == 2
    assert values['trade_payables_current_source']['line'] == 'Trade payables 12.00 10.00'


def test_balance_total_equity_and_liabilities_is_not_equity():
    row=in_bse._extract_balance_sheet([
        'CURRENT LIABILITIES',
        'TOTAL EQUITY AND LIABILITIES (1+2+3+4) 20,76,962.34 19,12,436.73'])
    assert row.get('total_equity') is None
    row=in_bse._extract_balance_sheet([
        'TOTAL EQUITY 4,15,906.01 2,42,459.36',
        'TOTAL EQUITY AND LIABILITIES (1+2+3+4) 20,76,962.34 19,12,436.73'])
    assert row['total_equity']==415906.01


@pytest.mark.parametrize('separator',['-','–','—'])
def test_hyphenated_total_equity_is_recovered_without_combined_total(separator):
    r=in_bse._extract_balance_sheet([
        '(1) Equity',
        f'Total {separator} Equity 411,824.62 399,445.43',
        'TOTAL EQUITY AND LIABILITIES (1+2+3) 1,095,129.74 1,055,931.82'])
    assert r['total_equity']==411824.62


def test_split_trade_payables_require_both_identified_creditor_components():
    lines=['NON-CURRENT LIABILITIES','(iii) Trade Payables 29',
      '(A) Total outstanding dues of micro enterprises and small enterprises - -',
      '(B) Total outstanding dues of creditors other than micro enterprises 16,703.63 10,663.17',
      'and small enterprises.', 'CURRENT LIABILITIES','(iv) Trade Payables 37',
      '(A) Total outstanding dues of micro enterprises and small enterprises 11,469.06 12,151.24',
      '(B) Total outstanding dues of creditors other than micro enterprises 87,302.63 1,17,488.35',
      'Other Financial Liabilities 38 10,625.20 36,765.65']
    r=in_bse._extract_balance_sheet(lines)
    assert r['trade_payables_non_current']==16703.63
    assert r['trade_payables_current']==pytest.approx(98771.69)
    assert r['trade_payables_current_source']['aggregation']=='sum_disjoint_msme_other_creditors'
    incomplete=in_bse._extract_balance_sheet(lines[:3]+['Other Financial Liabilities 38 10.00 9.00'])
    assert incomplete.get('trade_payables_non_current') is None


def test_separately_presented_financing_claims_are_preserved_not_labeled_trade_payables():
    r=in_bse._extract_balance_sheet([
      'CURRENT LIABILITIES',
      '(i) Borrowings 35 1,22,654.69 1,65,309.43',
      '(iii) Acceptances / Factoring Liabilities 36 29,395.20 27,689.69',
      '(vi) Obligation Towards Investor In Subsidiary 65 1,52,600.00 1,52,600.00'])
    assert r['current_acceptances_factoring_liabilities']==29395.20
    assert r['current_investor_in_subsidiary_obligation']==152600.
    assert r.get('trade_payables_current') is None
    assert r['current_acceptances_factoring_liabilities_source']['maturity_section']=='current'


def test_equity_nci_is_separate_from_profit_attribution_and_has_locator():
    lines=['II EQUITY & LIABILITIES','1 EQUITY',
           'Equity Attributable to Owners of the Group 3,91,525.29 2,22,024.76',
           'Non Controlling Interest 24,380.72 20,434.60',
           'TOTAL EQUITY 4,15,906.01 2,42,459.36',
           'Statement of Profit and Loss', 'Non-Controlling Interest 3,946.61 1,814.55']
    r=in_bse._extract_balance_sheet(lines)
    assert r['non_controlling_interest']==24380.72
    assert r['non_controlling_interest_source']['line_index']==3
    assert r['non_controlling_interest_source']['statement_section']=='equity'
    assert 'non_controlling_interest' not in in_bse._extract_balance_sheet(lines[5:])
