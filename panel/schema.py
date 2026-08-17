"""Panel row schema and period typing."""
from __future__ import annotations

import re
from calendar import monthrange
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
    "company_name", "currency", "fx_rate", "fx_asof",
    # Provenance and unit columns. fiscal_year_source records whether
    # fiscal_year came from the source or was re-derived from filing_date
    # (spec Sec 5.1 rescue). source_doc_id traces a row to its upstream
    # filing. unit_scale is the multiplier a consumer must apply to the metric
    # values (1 unless the source declares a scale like "HK$Million"); values
    # are NEVER pre-multiplied here -- see panel/adapters/hk.py.
    "fiscal_year_source", "source_doc_id", "unit_scale",
    "source_artifact",
] + METRIC_COLUMNS

# write_panel fills these with a default rather than None when an adapter did
# not set them, so a consumer never has to distinguish "no scale" from "unknown".
PANEL_COLUMN_DEFAULTS = {"unit_scale": 1, "fiscal_year_source": "reported"}

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


def is_month_end(period_end) -> bool:
    """True when period_end is the last calendar day of its own month."""
    normalized = normalize_period_end(period_end)
    if normalized is None:
        return False
    year, month, day = (int(part) for part in normalized.split("-"))
    return day == monthrange(year, month)[1]


# Months a period_type may legitimately end in. 'A' is deliberately open: real
# fiscal years end in many months (JP commonly 3, AU commonly 6), so the
# month-end test is the only constraint an annual row must pass.
_ALLOWED_MONTHS = {"Q": {3, 6, 9}, "H": {6}}


def period_end_consistency_error(period_end, period_type) -> Optional[str]:
    """Why period_end and period_type disagree, or None if they agree.

    INDEPENDENT of period_type by construction. views.check_invariants used to
    derive `cadence` FROM the row's own period_type and then assert
    period_type_for(period_end, cadence) == period_type, which for pt='A'
    returns 'A' for every date except 01-01 -- a tautology. It passed live JP
    rows with period_end 2019-01-20, 2020-03-05 and 2021-04-12 (52 distinct
    month-days in all). The real constraints are: a reporting period ends on a
    month boundary, and an interim type only ends in certain months.
    """
    normalized = normalize_period_end(period_end)
    if normalized is None:
        return f"unusable period_end {period_end!r}"
    month_day = normalized[5:]
    if period_type == "OPEN":
        if month_day != "01-01":
            return (
                f"period_type OPEN requires a 01-01 period_end, got {normalized}"
            )
        return None
    if period_type not in ("A", "H", "Q"):
        return f"unknown period_type {period_type!r} for period_end {normalized}"
    if month_day == "01-01":
        return (
            f"period_end {normalized} is an opening balance (01-01) but "
            f"period_type is {period_type}, not OPEN"
        )
    if not is_month_end(normalized):
        return (
            f"period_end {normalized} is not a month end, so it cannot be the "
            f"end of a {period_type} reporting period"
        )
    month = int(normalized[5:7])
    allowed = _ALLOWED_MONTHS.get(period_type)
    if allowed is not None and month not in allowed:
        return (
            f"period_type {period_type} cannot end in month {month:02d} "
            f"(period_end {normalized}); allowed months {sorted(allowed)}"
        )
    return None


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
