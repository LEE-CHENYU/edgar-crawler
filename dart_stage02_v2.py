"""Statement-aware OpenDART account rows -> Stage-02 V2 canonical rows.

The OpenDART endpoint is already structured, so this extractor never OCRs or
regexes rendered report prose.  It keeps statement scope, exact annual period,
currency, account-level provenance and independently testable identities.  In
particular, balance-sheet debt components are aggregated without admitting
cash-flow borrowing movements and absence is never converted to zero.
"""

from __future__ import annotations

import json
import re
from calendar import monthrange
from typing import Any, Iterable, Optional

from dart_financials_api import parse_amount
from stage02_v2 import valid_annual_period_end


METRIC_TAGS: dict[str, tuple[str, ...]] = {
    "revenue": (
        "ifrs-full_Revenue",
        "ifrs-full_RevenueFromContractsWithCustomers",
    ),
    "cost_of_revenue": ("ifrs-full_CostOfSales",),
    "gross_profit": ("ifrs-full_GrossProfit",),
    "selling_general_administrative_expenses": (
        "dart_SellingGeneralAdministrativeExpenses",
    ),
    "operating_profit": (
        "dart_OperatingIncomeLoss",
        "ifrs-full_ProfitLossFromOperatingActivities",
    ),
    "profit_before_tax": ("ifrs-full_ProfitLossBeforeTax",),
    "income_tax_expense": (
        "ifrs-full_IncomeTaxExpenseContinuingOperations",
        "ifrs-full_IncomeTaxExpense",
    ),
    "net_income": ("ifrs-full_ProfitLoss",),
    "total_assets": ("ifrs-full_Assets",),
    "current_assets": ("ifrs-full_CurrentAssets",),
    "non_current_assets": ("ifrs-full_NoncurrentAssets",),
    "cash_and_equivalents": ("ifrs-full_CashAndCashEquivalents",),
    "total_liabilities": ("ifrs-full_Liabilities",),
    "current_liabilities": ("ifrs-full_CurrentLiabilities",),
    "non_current_liabilities": ("ifrs-full_NoncurrentLiabilities",),
    "total_equity": ("ifrs-full_Equity",),
    "operating_cash_flow": (
        "ifrs-full_CashFlowsFromUsedInOperatingActivities",
    ),
    "investing_cash_flow": (
        "ifrs-full_CashFlowsFromUsedInInvestingActivities",
    ),
    "financing_cash_flow": (
        "ifrs-full_CashFlowsFromUsedInFinancingActivities",
    ),
}

METRIC_NAMES = {
    "매출액": "revenue",
    "수익(매출액)": "revenue",
    "영업수익": "revenue",
    "매출원가": "cost_of_revenue",
    "매출총이익": "gross_profit",
    "판매비와관리비": "selling_general_administrative_expenses",
    "영업이익": "operating_profit",
    "영업이익(손실)": "operating_profit",
    "법인세비용차감전순이익": "profit_before_tax",
    "법인세비용": "income_tax_expense",
    "당기순이익": "net_income",
    "당기순이익(손실)": "net_income",
    "자산총계": "total_assets",
    "유동자산": "current_assets",
    "비유동자산": "non_current_assets",
    "현금및현금성자산": "cash_and_equivalents",
    "부채총계": "total_liabilities",
    "유동부채": "current_liabilities",
    "비유동부채": "non_current_liabilities",
    "자본총계": "total_equity",
    "영업활동현금흐름": "operating_cash_flow",
    "투자활동현금흐름": "investing_cash_flow",
    "재무활동현금흐름": "financing_cash_flow",
}

TAG_TO_METRIC = {
    tag: metric for metric, tags in METRIC_TAGS.items() for tag in tags
}

CURRENT_BORROWING_TAGS = {
    "ifrs-full_ShorttermBorrowings",
    "ifrs-full_CurrentBorrowings",
    "ifrs-full_CurrentPortionOfLongtermBorrowings",
    "dart_ShortTermBorrowings",
    "dart_CurrentPortionOfLongTermBorrowings",
}
NONCURRENT_BORROWING_TAGS = {
    "ifrs-full_LongtermBorrowings",
    "ifrs-full_NoncurrentBorrowings",
    "dart_LongTermBorrowingsGross",
}
CURRENT_BOND_TAGS = {
    "ifrs-full_CurrentBondsIssued",
    "ifrs-full_CurrentPortionOfNoncurrentBondsIssued",
    "dart_CurrentPortionOfBondsIssued",
}
NONCURRENT_BOND_TAGS = {
    "ifrs-full_NoncurrentBondsIssued",
    "ifrs-full_NoncurrentPortionOfNoncurrentBondsIssued",
    "dart_BondsIssued",
}
CURRENT_LEASE_TAGS = {
    "ifrs-full_CurrentLeaseLiabilities",
    "dart_CurrentLeaseLiabilities",
}
NONCURRENT_LEASE_TAGS = {
    "ifrs-full_NoncurrentLeaseLiabilities",
    "dart_NoncurrentLeaseLiabilities",
}
GENERIC_BORROWING_TAGS = {"ifrs-full_Borrowings"}

_FINANCIAL_NAME_RE = re.compile(
    r"(?:은행|보험|증권|금융|캐피탈|카드|저축|리츠|스팩|"
    r"investment|bank|insurance|\bspac\b)",
    re.IGNORECASE,
)


def _norm(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or ""))


def period_end_from_report_name(report_name: Any) -> Optional[str]:
    match = re.search(r"\((?P<year>\d{4})\.(?P<month>\d{2})\)", str(report_name or ""))
    if not match:
        return None
    year = int(match.group("year"))
    month = int(match.group("month"))
    if not 1 <= month <= 12:
        return None
    return f"{year:04d}-{month:02d}-{monthrange(year, month)[1]:02d}"


def _metric_rows(accounts: Iterable[dict[str, Any]]) -> tuple[dict[str, float], dict[str, dict[str, Any]]]:
    values: dict[str, float] = {}
    provenance: dict[str, dict[str, Any]] = {}
    for account in accounts:
        account_id = str(account.get("account_id") or "").strip()
        metric = TAG_TO_METRIC.get(account_id)
        if metric is None:
            metric = METRIC_NAMES.get(_norm(account.get("account_nm")))
        if metric is None or metric in values:
            continue
        value = parse_amount(account.get("thstrm_amount"))
        if value is None:
            continue
        values[metric] = value
        provenance[metric] = {
            "account_id": account_id,
            "account_name": account.get("account_nm"),
            "statement": account.get("sj_div"),
            "source_field": "thstrm_amount",
        }
    return values, provenance


def _sum_present(values: Iterable[Optional[float]]) -> Optional[float]:
    present = [value for value in values if value is not None]
    return sum(present) if present else None


def _debt_components(accounts: Iterable[dict[str, Any]]) -> dict[str, Any]:
    categories: dict[str, list[tuple[float, dict[str, Any]]]] = {
        "current_borrowings": [],
        "non_current_borrowings": [],
        "current_bonds": [],
        "non_current_bonds": [],
        "current_lease_liabilities": [],
        "non_current_lease_liabilities": [],
        "generic_borrowings": [],
    }
    seen: set[tuple[str, str, str]] = set()
    for account in accounts:
        if str(account.get("sj_div") or "").upper() != "BS":
            continue
        account_id = str(account.get("account_id") or "").strip()
        account_name = _norm(account.get("account_nm"))
        dedupe_key = (account_id, account_name, str(account.get("thstrm_amount")))
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        value = parse_amount(account.get("thstrm_amount"))
        if value is None:
            continue
        category = None
        if account_id in CURRENT_BORROWING_TAGS:
            category = "current_borrowings"
        elif account_id in NONCURRENT_BORROWING_TAGS:
            category = "non_current_borrowings"
        elif account_id in CURRENT_BOND_TAGS:
            category = "current_bonds"
        elif account_id in NONCURRENT_BOND_TAGS:
            category = "non_current_bonds"
        elif account_id in CURRENT_LEASE_TAGS:
            category = "current_lease_liabilities"
        elif account_id in NONCURRENT_LEASE_TAGS:
            category = "non_current_lease_liabilities"
        elif account_id in GENERIC_BORROWING_TAGS:
            category = "generic_borrowings"
        if category:
            categories[category].append((value, {
                "account_id": account_id,
                "account_name": account.get("account_nm"),
            }))

    sums = {
        name: _sum_present(value for value, _ in items)
        for name, items in categories.items()
    }
    split_borrowings = _sum_present((
        sums["current_borrowings"], sums["non_current_borrowings"],
        sums["current_bonds"], sums["non_current_bonds"],
    ))
    total_borrowings = (
        split_borrowings
        if split_borrowings is not None
        else sums["generic_borrowings"]
    )
    current_borrowings = _sum_present((
        sums["current_borrowings"], sums["current_bonds"],
    ))
    non_current_borrowings = _sum_present((
        sums["non_current_borrowings"], sums["non_current_bonds"],
    ))
    total_leases = _sum_present((
        sums["current_lease_liabilities"],
        sums["non_current_lease_liabilities"],
    ))
    total_debt = _sum_present((total_borrowings, total_leases))
    resolved = total_debt is not None
    return {
        "current_borrowings": current_borrowings,
        "non_current_borrowings": non_current_borrowings,
        "total_borrowings": total_borrowings,
        "current_lease_liabilities": sums["current_lease_liabilities"],
        "non_current_lease_liabilities": sums["non_current_lease_liabilities"],
        "total_lease_liabilities": total_leases,
        "total_interest_bearing_debt": total_debt,
        "debt_reconciliation_status": (
            "resolved_components" if resolved else "unresolved_not_reported"
        ),
        "debt_component_provenance": {
            name: [source for _, source in items]
            for name, items in categories.items() if items
        },
    }


def _identity_status(actual: Optional[float], derived: Optional[float]) -> tuple[str, Optional[float]]:
    if actual is None or derived is None:
        return "insufficient_components", None
    denominator = max(abs(actual), abs(derived), 1.0)
    ratio = abs(actual - derived) / denominator
    return ("pass" if ratio <= 0.01 else "fail"), ratio


def _tax_identity(
    pbt: Optional[float], tax: Optional[float], net_income: Optional[float]
) -> tuple[str, Optional[float], Optional[str]]:
    """Test both source sign conventions without changing reported values."""
    if pbt is None or tax is None or net_income is None:
        return "insufficient_components", None, None
    candidates = {
        "subtract": pbt - tax,
        "add": pbt + tax,
    }
    convention, derived = min(
        candidates.items(),
        key=lambda item: abs(item[1] - net_income)
        / max(abs(item[1]), abs(net_income), 1.0),
    )
    denominator = max(abs(derived), abs(net_income), 1.0)
    ratio = abs(derived - net_income) / denominator
    return ("pass" if ratio <= 0.01 else "fail"), ratio, convention


def _industry_template(target: dict[str, Any]) -> tuple[str, str]:
    explicit = target.get("industry_template")
    if explicit in {"industrial", "financial_services"}:
        return str(explicit), "manifest"
    if _FINANCIAL_NAME_RE.search(str(target.get("company_name") or "")):
        return "financial_services", "company_name_heuristic"
    return "industrial", "default_nonfinancial"


def extract_canonical_row(
    accounts: Iterable[dict[str, Any]],
    target: dict[str, Any],
    *,
    fs_div: str,
) -> dict[str, Any]:
    accounts = [dict(account) for account in accounts]
    metrics, provenance = _metric_rows(accounts)
    debt = _debt_components(accounts)
    period_end = period_end_from_report_name(target.get("report_nm"))
    currencies = {
        str(account.get("currency") or "").strip()
        for account in accounts
        if str(account.get("currency") or "").strip()
    }
    currency = next(iter(currencies)) if len(currencies) == 1 else None
    template, template_source = _industry_template(target)

    if template == "financial_services":
        op_status, op_ratio = "not_applicable", None
        op_bridge = None
    else:
        gross = metrics.get("gross_profit")
        sga = metrics.get("selling_general_administrative_expenses")
        op_bridge = gross - sga if gross is not None and sga is not None else None
        op_status, op_ratio = _identity_status(metrics.get("operating_profit"), op_bridge)
    pbt = metrics.get("profit_before_tax")
    tax = metrics.get("income_tax_expense")
    tax_status, tax_ratio, tax_convention = _tax_identity(
        pbt, tax, metrics.get("net_income")
    )
    liabilities = metrics.get("total_liabilities")
    equity = metrics.get("total_equity")
    balance_bridge = (
        liabilities + equity
        if liabilities is not None and equity is not None else None
    )
    balance_status, balance_ratio = _identity_status(
        metrics.get("total_assets"), balance_bridge
    )

    filing_id = str(
        target.get("filing_id")
        or next((account.get("rcept_no") for account in accounts if account.get("rcept_no")), "")
    )
    row: dict[str, Any] = {
        "market": "DART",
        "filing_id": filing_id,
        "corp_code": target.get("corp_code"),
        "ticker": str(target.get("stock_code") or ""),
        "company_name": target.get("company_name"),
        "publication_date": target.get("filing_date"),
        "report_name": target.get("report_nm"),
        "period_end": period_end,
        "statement_scope": (
            "consolidated" if str(fs_div).upper() == "CFS"
            else "standalone" if str(fs_div).upper() == "OFS"
            else "unknown"
        ),
        "fs_div": str(fs_div).upper(),
        "currency": currency,
        "currency_resolution_status": (
            "reported_consistent" if currency is not None else "mixed_or_missing"
        ),
        "unit_scale": 1.0,
        "unit_scale_source": "opendart_api_amounts_in_reported_currency",
        "industry_template": template,
        "industry_template_source": template_source,
        "account_row_count": len(accounts),
        "field_provenance": provenance,
        "field_provenance_json": json.dumps(
            provenance, ensure_ascii=False, sort_keys=True
        ),
        "operating_profit_pbt_bridge": op_bridge,
        "operating_profit_closure_status": op_status,
        "operating_profit_closure_ratio": op_ratio,
        "tax_identity_status": tax_status,
        "tax_identity_ratio": tax_ratio,
        "tax_sign_convention": tax_convention,
        "balance_identity_status": balance_status,
        "balance_identity_ratio": balance_ratio,
        **debt,
    }
    for metric, value in metrics.items():
        row[metric] = value
        row[f"{metric}_krw"] = value if currency == "KRW" else None
    for metric in (
        "current_borrowings", "non_current_borrowings", "total_borrowings",
        "current_lease_liabilities", "non_current_lease_liabilities",
        "total_lease_liabilities", "total_interest_bearing_debt",
    ):
        row[f"{metric}_krw"] = row.get(metric) if currency == "KRW" else None
    debt_required = (
        template != "industrial"
        or row["debt_reconciliation_status"] == "resolved_components"
    )
    row["screen_eligible"] = bool(
        filing_id
        and target.get("stock_code")
        and valid_annual_period_end(period_end)
        and row["statement_scope"] in {"consolidated", "standalone"}
        and currency == "KRW"
        and op_status in {"pass", "not_applicable"}
        and tax_status == "pass"
        and balance_status == "pass"
        and debt_required
    )
    return row
