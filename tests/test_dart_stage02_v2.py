"""Statement-aware OpenDART -> Stage-02 V2 contract tests."""

from dart_stage02_v2 import extract_canonical_row, period_end_from_report_name


def _account(sj, account_id, name, amount, *, currency="KRW"):
    return {
        "sj_div": sj,
        "account_id": account_id,
        "account_nm": name,
        "thstrm_amount": amount,
        "currency": currency,
        "rcept_no": "20260315000123",
    }


def _target():
    return {
        "corp_code": "00126380",
        "stock_code": "005930",
        "company_name": "삼성전자",
        "report_nm": "사업보고서 (2025.12)",
        "filing_id": "20260315000123",
        "filing_date": "2026-03-15",
    }


def _closed_accounts():
    return [
        _account("BS", "ifrs-full_Assets", "자산총계", "1,000"),
        _account("BS", "ifrs-full_Liabilities", "부채총계", "400"),
        _account("BS", "ifrs-full_Equity", "자본총계", "600"),
        _account("BS", "ifrs-full_CashAndCashEquivalents", "현금및현금성자산", "90"),
        _account("CIS", "ifrs-full_Revenue", "매출액", "1,000"),
        _account("CIS", "ifrs-full_CostOfSales", "매출원가", "600"),
        _account("CIS", "ifrs-full_GrossProfit", "매출총이익", "400"),
        _account("CIS", "dart_SellingGeneralAdministrativeExpenses", "판매비와관리비", "100"),
        _account("CIS", "dart_OperatingIncomeLoss", "영업이익", "300"),
        _account("CIS", "ifrs-full_ProfitLossBeforeTax", "법인세비용차감전순이익", "250"),
        _account("CIS", "ifrs-full_IncomeTaxExpenseContinuingOperations", "법인세비용", "50"),
        _account("CIS", "ifrs-full_ProfitLoss", "당기순이익", "200"),
    ]


def test_period_end_from_report_name_is_exact_calendar_month_end():
    assert period_end_from_report_name("사업보고서 (2025.12)") == "2025-12-31"
    assert period_end_from_report_name("사업보고서 (2024.02)") == "2024-02-29"
    assert period_end_from_report_name("사업보고서") is None


def test_extract_maps_identity_metrics_scope_and_field_provenance():
    row = extract_canonical_row(_closed_accounts(), _target(), fs_div="CFS")

    assert row["market"] == "DART"
    assert row["filing_id"] == "20260315000123"
    assert row["ticker"] == "005930"
    assert row["period_end"] == "2025-12-31"
    assert row["statement_scope"] == "consolidated"
    assert row["currency"] == "KRW"
    assert row["unit_scale"] == 1.0
    assert row["revenue_krw"] == 1000.0
    assert row["operating_profit_krw"] == 300.0
    assert row["profit_before_tax_krw"] == 250.0
    assert row["income_tax_expense_krw"] == 50.0
    assert row["net_income_krw"] == 200.0
    assert row["total_assets_krw"] == 1000.0
    assert row["field_provenance"]["revenue"]["account_id"] == "ifrs-full_Revenue"


def test_extract_aggregates_balance_sheet_debt_and_excludes_cash_flow_movements():
    accounts = _closed_accounts() + [
        _account("BS", "ifrs-full_ShorttermBorrowings", "단기차입금", "10"),
        _account("BS", "ifrs-full_CurrentPortionOfLongtermBorrowings", "유동성장기차입금", "20"),
        _account("BS", "dart_LongTermBorrowingsGross", "장기차입금", "30"),
        _account("BS", "dart_BondsIssued", "사채", "40"),
        _account("BS", "ifrs-full_CurrentLeaseLiabilities", "유동리스부채", "5"),
        _account("BS", "ifrs-full_NoncurrentLeaseLiabilities", "비유동리스부채", "15"),
        _account("CF", "ifrs-full_ProceedsFromBorrowingsClassifiedAsFinancingActivities",
                 "차입금의 차입", "999"),
    ]

    row = extract_canonical_row(accounts, _target(), fs_div="CFS")

    assert row["current_borrowings_krw"] == 30.0
    assert row["non_current_borrowings_krw"] == 70.0
    assert row["total_borrowings_krw"] == 100.0
    assert row["total_lease_liabilities_krw"] == 20.0
    assert row["total_interest_bearing_debt_krw"] == 120.0
    assert row["debt_reconciliation_status"] == "resolved_components"


def test_extract_runs_independent_statement_identity_gates():
    accounts = _closed_accounts() + [
        _account("BS", "ifrs-full_ShorttermBorrowings", "단기차입금", "10"),
    ]

    row = extract_canonical_row(accounts, _target(), fs_div="CFS")

    assert row["operating_profit_closure_status"] == "pass"
    assert row["tax_identity_status"] == "pass"
    assert row["balance_identity_status"] == "pass"
    assert row["screen_eligible"] is True


def test_tax_identity_selects_reported_add_or_subtract_sign_convention():
    accounts = _closed_accounts() + [
        _account("BS", "ifrs-full_ShorttermBorrowings", "단기차입금", "10"),
    ]
    for account in accounts:
        if account["account_id"] == "ifrs-full_ProfitLossBeforeTax":
            account["thstrm_amount"] = "-100"
        elif account["account_id"] == "ifrs-full_IncomeTaxExpenseContinuingOperations":
            account["thstrm_amount"] = "20"
        elif account["account_id"] == "ifrs-full_ProfitLoss":
            account["thstrm_amount"] = "-80"

    row = extract_canonical_row(accounts, _target(), fs_div="CFS")

    assert row["tax_identity_status"] == "pass"
    assert row["tax_sign_convention"] == "add"


def test_spac_is_not_forced_through_industrial_operating_and_debt_contracts():
    target = {**_target(), "company_name": "교보20호스팩"}

    row = extract_canonical_row(_closed_accounts(), target, fs_div="OFS")

    assert row["industry_template"] == "financial_services"
    assert row["industry_template_source"] == "company_name_heuristic"
    assert row["operating_profit_closure_status"] == "not_applicable"
    assert row["debt_reconciliation_status"] == "unresolved_not_reported"
    assert row["screen_eligible"] is True


def test_extract_never_turns_unreported_debt_into_zero():
    row = extract_canonical_row(_closed_accounts(), _target(), fs_div="OFS")

    assert row["statement_scope"] == "standalone"
    assert row["total_borrowings_krw"] is None
    assert row["total_interest_bearing_debt_krw"] is None
    assert row["debt_reconciliation_status"] == "unresolved_not_reported"
    assert row["screen_eligible"] is False


def test_extract_rejects_mixed_or_missing_reported_currency():
    mixed = _closed_accounts()
    mixed[-1] = {**mixed[-1], "currency": "USD"}

    row = extract_canonical_row(mixed, _target(), fs_div="CFS")

    assert row["currency"] is None
    assert row["currency_resolution_status"] == "mixed_or_missing"
    assert row["screen_eligible"] is False
