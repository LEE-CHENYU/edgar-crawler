"""CN adapter over the CSMAR-style parquet trio.

CN is already period-level (`Accper`), including restated year-start opening
balances which are typed OPEN rather than mapped to a quarter. These rows
carry values distinct from the prior December close (restatements) and must
be retained, not dropped, so a later query can compute a restated-vs-
originally-reported delta. `Typrep` ('A' consolidated vs 'B' parent-company)
is recorded per row as `cn_typrep` so the two reporting bases are never
silently mixed together.

OPEN-row fiscal_year semantics (deliberate, ruled on 2026-08-17): an OPEN
row's `fiscal_year` is `int(period_end[:4])` — the calendar year of its
`period_end` (a 2024-01-01 opening balance gets fiscal_year=2024), NOT the
year it restates (2023). This is uniform with every other adapter, which
all derive fiscal_year from period_end the same way; special-casing OPEN
rows to year-1 would be a surprise. A restatement-delta query must therefore
join an OPEN row at YYYY-01-01 against the A row at (YYYY-1)-12-31 on
`period_end`, never on `fiscal_year`.
"""
from __future__ import annotations

from collections import Counter
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
    """Outer-join the balance sheet / income statement / cash flow frames on
    (Stkcd, Accper, Typrep).

    bs/inc/cf all carry the same three metadata columns (ShortName,
    IfCorrect, DeclareDate). Merging sequentially with pandas' default
    suffixes=("", "_dup") only survives ONE collision: the first merge
    creates "<col>_dup", but the second merge tries to create "<col>_dup"
    again and collides with itself, producing duplicate column labels that
    `to_dict("records")` silently drops data from (pandas emits "DataFrame
    columns are not unique, some columns will be omitted"). No financial
    metric is lost today only because every A*/B*/C* code happens to be
    unique per statement -- that's luck, not a guarantee, so drop each right
    frame's already-present non-key columns before merging instead of
    relying on suffixes. The left (first) frame's value always wins, which
    matches the existing dup-metric-code precedence.
    """
    present = [f for f in frames if f is not None and len(f)]
    if not present:
        return pd.DataFrame()
    merged = present[0]
    for extra in present[1:]:
        keys = [k for k in ("Stkcd", "Accper", "Typrep") if k in extra.columns]
        overlap = [c for c in extra.columns if c in merged.columns and c not in keys]
        if overlap:
            extra = extra.drop(columns=overlap)
        merged = merged.merge(extra, on=keys, how="outer")
    return merged


def rows_from_cn_frames(
    balance_sheet: pd.DataFrame,
    income_statement: Optional[pd.DataFrame] = None,
    cash_flow: Optional[pd.DataFrame] = None,
    drops: Optional[Counter] = None,
) -> List[dict]:
    """Build panel rows from the CSMAR balance sheet / income statement / cash
    flow trio (only `balance_sheet` is required).

    See the module docstring for OPEN-row fiscal_year semantics: fiscal_year
    is always the calendar year of period_end, including for OPEN rows.
    """
    if drops is None:
        drops = Counter()
    merged = _merge([balance_sheet, income_statement, cash_flow])
    out: List[dict] = []
    for record in merged.to_dict("records"):
        period_end = normalize_period_end(record.get("Accper"))
        if period_end is None:
            drops["unusable_accper"] += 1
            continue
        local_id = str(record.get("Stkcd") or "").strip()
        if not local_id or local_id.lower() == "nan":
            drops["missing_stkcd"] += 1
            continue
        row = {
            "market": "cn",
            "local_id": local_id,
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
