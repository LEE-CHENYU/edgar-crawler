"""OpenDART structured-financials fetcher (fnlttSinglAcntAll).

Korea's clean structured-source path — the analog of CSMAR for CN and iXBRL for
JP. Replaces the broken text extractor (dart_financials_pipeline.py) whose facts
captured footnote reference numbers (주15 -> 15), not statement values.

Pulls tagged line items from OpenDART's "전체 재무제표" endpoint and maps IFRS /
DART XBRL account tags to the same canonical 3-statement metric set the CN/HK/JP
adapters use. Pure parsing/mapping here; the HTTP call is a thin wrapper.

API: GET https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json
  crtfc_key, corp_code, bsns_year, reprt_code (11011=annual), fs_div (CFS|OFS)
Docs: https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json
"""
from __future__ import annotations
import re
from typing import Optional

API_URL = "https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json"
REPRT_ANNUAL = "11011"

CANONICAL_METRICS = [
    "revenue", "cost_of_revenue", "gross_profit", "operating_profit",
    "profit_before_tax", "income_tax_expense", "net_income", "basic_eps",
    "total_assets", "current_assets", "non_current_assets",
    "cash_and_equivalents", "accounts_receivable", "inventories",
    "total_liabilities", "current_liabilities", "non_current_liabilities",
    "borrowings", "total_equity",
    "operating_cash_flow", "investing_cash_flow", "financing_cash_flow",
]

# XBRL account tag -> canonical metric. IFRS taxonomy tags plus DART-specific
# (dart_*) tags for concepts IFRS lacks a single tag for (operating income).
TAG_MAP = {
    # Income statement
    "ifrs-full_Revenue": "revenue",
    "ifrs-full_RevenueFromContractsWithCustomers": "revenue",
    "ifrs-full_CostOfSales": "cost_of_revenue",
    "ifrs-full_GrossProfit": "gross_profit",
    "dart_OperatingIncomeLoss": "operating_profit",
    "ifrs-full_ProfitLossFromOperatingActivities": "operating_profit",
    "ifrs-full_ProfitLossBeforeTax": "profit_before_tax",
    "ifrs-full_IncomeTaxExpenseContinuingOperations": "income_tax_expense",
    "ifrs-full_ProfitLoss": "net_income",
    "ifrs-full_BasicEarningsLossPerShare": "basic_eps",
    # Balance sheet
    "ifrs-full_Assets": "total_assets",
    "ifrs-full_CurrentAssets": "current_assets",
    "ifrs-full_NoncurrentAssets": "non_current_assets",
    "ifrs-full_CashAndCashEquivalents": "cash_and_equivalents",
    "ifrs-full_TradeAndOtherCurrentReceivables": "accounts_receivable",
    "ifrs-full_CurrentTradeReceivables": "accounts_receivable",
    "ifrs-full_Inventories": "inventories",
    "ifrs-full_Liabilities": "total_liabilities",
    "ifrs-full_CurrentLiabilities": "current_liabilities",
    "ifrs-full_NoncurrentLiabilities": "non_current_liabilities",
    "ifrs-full_Borrowings": "borrowings",
    "ifrs-full_Equity": "total_equity",
    # Cash flow
    "ifrs-full_CashFlowsFromUsedInOperatingActivities": "operating_cash_flow",
    "ifrs-full_CashFlowsFromUsedInInvestingActivities": "investing_cash_flow",
    "ifrs-full_CashFlowsFromUsedInFinancingActivities": "financing_cash_flow",
}

# Korean statement label -> canonical, for non-standard filers that omit the
# XBRL account_id ("-표준계정코드 미사용-"). Matched by normalized (space-stripped) name.
NAME_MAP = {
    "매출액": "revenue", "수익(매출액)": "revenue", "영업수익": "revenue",
    "매출원가": "cost_of_revenue", "매출총이익": "gross_profit",
    "영업이익": "operating_profit", "영업이익(손실)": "operating_profit",
    "법인세비용차감전순이익": "profit_before_tax", "법인세비용": "income_tax_expense",
    "당기순이익": "net_income", "당기순이익(손실)": "net_income",
    "기본주당이익": "basic_eps", "기본주당이익(손실)": "basic_eps",
    "자산총계": "total_assets", "유동자산": "current_assets", "비유동자산": "non_current_assets",
    "현금및현금성자산": "cash_and_equivalents", "매출채권": "accounts_receivable",
    "매출채권및기타채권": "accounts_receivable", "재고자산": "inventories",
    "부채총계": "total_liabilities", "유동부채": "current_liabilities",
    "비유동부채": "non_current_liabilities", "자본총계": "total_equity",
    "영업활동현금흐름": "operating_cash_flow", "투자활동현금흐름": "investing_cash_flow",
    "재무활동현금흐름": "financing_cash_flow",
}


def parse_fiscal_year(report_nm: Optional[str]) -> Optional[int]:
    """Business year for the OpenDART call, from a DART report name like
    '사업보고서 (2025.12)' -> 2025. Returns None when no (YYYY.MM) is present."""
    m = re.search(r"\((\d{4})\.\d{2}\)", report_nm or "")
    return int(m.group(1)) if m else None


def parse_amount(s: Optional[str]) -> Optional[float]:
    """Parse an OpenDART amount string. Handles commas, sign, accounting
    parens; returns None for blank/'-'/None."""
    if s is None:
        return None
    s = s.strip()
    if s in ("", "-"):
        return None
    neg = False
    if s.startswith("(") and s.endswith(")"):
        neg = True
        s = s[1:-1]
    s = s.replace(",", "").strip()
    if not s or s == "-":
        return None
    try:
        v = float(s)
    except ValueError:
        return None
    return -v if neg else v


def parse_response(payload: dict) -> tuple[list[dict], str]:
    """Return (rows, status). status '000' = ok; '013' = no data. Any non-000
    yields an empty row list (caller decides fallback)."""
    status = str(payload.get("status", ""))
    if status != "000":
        return [], status
    return list(payload.get("list", []) or []), status


def _norm(s: Optional[str]) -> str:
    return re.sub(r"\s+", "", s or "")


def map_to_canonical(rows: list[dict]) -> dict:
    """Map statement rows -> {canonical_metric: value}. Prefers the XBRL
    account_id; falls back to the Korean account_nm. First match wins (rows
    arrive in statement order, so the top-level total precedes sub-lines)."""
    out: dict[str, float] = {}
    for row in rows:
        metric = TAG_MAP.get((row.get("account_id") or "").strip())
        if metric is None:
            metric = NAME_MAP.get(_norm(row.get("account_nm")))
        if metric is None or metric in out:
            continue
        val = parse_amount(row.get("thstrm_amount"))
        if val is not None:
            out[metric] = val
    return out


def fetch_accounts(corp_code: str, bsns_year: int, api_key: str,
                   reprt_code: str = REPRT_ANNUAL, fs_div: str = "CFS",
                   timeout: int = 45, retries: int = 4,
                   backoff: float = 2.0) -> tuple[list[dict], str]:
    """Live GET against fnlttSinglAcntAll, with retry/backoff — the OpenDART
    server intermittently drops connections and cold-starts slowly. requests is
    imported lazily so the pure logic stays dependency-free and unit-testable."""
    import time
    import requests
    params = {
        "crtfc_key": api_key, "corp_code": corp_code,
        "bsns_year": str(bsns_year), "reprt_code": reprt_code, "fs_div": fs_div,
    }
    last_exc: Optional[Exception] = None
    for attempt in range(retries):
        try:
            resp = requests.get(API_URL, params=params, timeout=timeout)
            resp.raise_for_status()
            return parse_response(resp.json())
        except (requests.exceptions.RequestException, ValueError) as exc:
            last_exc = exc
            if attempt < retries - 1:
                time.sleep(backoff * (2 ** attempt))
    raise RuntimeError(f"OpenDART fetch failed after {retries} tries "
                       f"({corp_code}/{bsns_year}): {last_exc}")
