"""Panel row schema and period typing."""
from __future__ import annotations

import re
from datetime import datetime
from typing import Optional

METRIC_COLUMNS = [
    "revenue", "cost_of_revenue", "gross_profit", "profit_before_tax",
    "income_tax_expense", "net_income", "basic_eps", "cash_and_equivalents",
    "accounts_receivable", "inventories", "current_assets", "non_current_assets",
    "total_assets", "current_liabilities", "non_current_liabilities",
    "total_liabilities", "total_equity", "operating_cash_flow",
    "investing_cash_flow", "financing_cash_flow",
]

PANEL_COLUMNS = [
    "spine_key", "market", "local_id", "period_end", "period_type", "fiscal_year",
    # reporting_basis distinguishes consolidated vs parent-company rows that
    # otherwise share the same (spine_key, period_end, period_type) key --
    # CN's Typrep A/B and JP's has_consolidated_statements both legitimately
    # emit both. It is part of the panel's uniqueness key (see views.py).
    "reporting_basis",
    "company_name", "currency", "fx_rate", "fx_asof", "source_artifact",
] + METRIC_COLUMNS

CADENCES = ("annual", "semiannual", "quarterly")
_QUARTER_ENDS = {"03-31", "06-30", "09-30"}


def normalize_period_end(value) -> Optional[str]:
    text = str(value or "").strip()
    if not text:
        return None
    text = text.split(" ")[0]
    if re.fullmatch(r"\d{8}", text):
        text = f"{text[:4]}-{text[4:6]}-{text[6:]}"
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return None
    try:
        datetime.strptime(text, "%Y-%m-%d")
    except ValueError:
        return None
    return text


def period_type_for(period_end: str, cadence: str) -> str:
    """Q/H/A/OPEN for a period end.

    A January 1st period end is an opening balance, never a quarter: CN
    publishes restated year-start balances that would otherwise double-count
    against the prior December close.
    """
    if cadence not in CADENCES:
        raise ValueError(f"unknown cadence: {cadence}")
    normalized = normalize_period_end(period_end)
    if normalized is None:
        raise ValueError(f"unusable period_end: {period_end!r}")
    md = normalized[5:]
    if md == "01-01":
        return "OPEN"
    if md == "12-31":
        return "A"
    if cadence == "quarterly" and md in _QUARTER_ENDS:
        return "Q"
    if cadence == "semiannual" and md == "06-30":
        return "H"
    return "A" if cadence == "annual" else ("H" if md == "06-30" else "Q")


def is_plausible_fiscal_year(year) -> bool:
    try:
        value = int(year)
    except (TypeError, ValueError):
        return False
    return 1990 <= value <= datetime.now().year + 1


def to_number(value) -> Optional[float]:
    """Coerce a raw cell to float, or None when it is not a number.

    Stdlib-only by design (no pandas) so every downstream adapter can import
    this module freely without pulling in a heavy dependency.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    text = text.replace(",", "")
    try:
        return float(text)
    except ValueError:
        return None
