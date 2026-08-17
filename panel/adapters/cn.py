"""CN adapter over the CSMAR-style parquet trio.

CN is already period-level (`Accper`), including restated year-start opening
balances which are typed OPEN rather than mapped to a quarter. These rows
carry values distinct from the prior December close (restatements) and must
be retained, not dropped, so a later query can compute a restated-vs-
originally-reported delta. `Typrep` ('A' consolidated vs 'B' parent-company)
is recorded per row as `cn_typrep` so the two reporting bases are never
silently mixed together.
"""
from __future__ import annotations

from typing import List, Optional

import pandas as pd

from panel.schema import normalize_period_end, period_type_for, to_number

CN_FIELD_MAP = {
    "A001000000": "total_assets",
    "A001100000": "current_assets",
    "A001101000": "cash_and_equivalents",
    "A001111000": "inventories",
    "A001110000": "accounts_receivable",
    "A002000000": "total_liabilities",
    "A002100000": "current_liabilities",
    "A003000000": "total_equity",
    "B001100000": "revenue",
    "B001200000": "cost_of_revenue",
    "B001000000": "profit_before_tax",
    "B002000000": "net_income",
    "C001000000": "operating_cash_flow",
    "C002000000": "investing_cash_flow",
    "C003000000": "financing_cash_flow",
}


def _merge(frames: List[Optional[pd.DataFrame]]) -> pd.DataFrame:
    present = [f for f in frames if f is not None and len(f)]
    if not present:
        return pd.DataFrame()
    merged = present[0]
    for extra in present[1:]:
        keys = [k for k in ("Stkcd", "Accper", "Typrep") if k in extra.columns]
        merged = merged.merge(extra, on=keys, how="outer", suffixes=("", "_dup"))
    return merged


def rows_from_cn_frames(
    balance_sheet: pd.DataFrame,
    income_statement: Optional[pd.DataFrame] = None,
    cash_flow: Optional[pd.DataFrame] = None,
) -> List[dict]:
    merged = _merge([balance_sheet, income_statement, cash_flow])
    out: List[dict] = []
    for record in merged.to_dict("records"):
        period_end = normalize_period_end(record.get("Accper"))
        if period_end is None:
            continue
        row = {
            "market": "cn",
            "local_id": str(record.get("Stkcd") or "").strip(),
            "company_name": record.get("ShortName") or None,
            "currency": "CNY",
            "fiscal_year": int(period_end[:4]),
            "period_end": period_end,
            "period_type": period_type_for(period_end, cadence="quarterly"),
            "cn_typrep": record.get("Typrep"),
            "source_artifact": "markets/cn/02_structured",
        }
        for code, name in CN_FIELD_MAP.items():
            if code in record:
                value = record.get(code)
                row[name] = None if pd.isna(value) else to_number(value)
        out.append(row)
    return out
