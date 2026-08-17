"""*_screening_input.csv adapter.

NO LONGER USED BY ANY MARKET (2026-08-17). au/tw/ph/kr read it until the final
review wave found that it holds exactly ONE row per ticker -- those four
markets had no time series at all. They now read the period-level
canonical_metrics_wide.parquet in the same directories (see
panel/adapters/in_bse.py). This adapter is kept because the screening CSVs
remain a legitimate one-row-per-ticker snapshot source, but anything using it
must accept that it carries no history and that its figures are already
USD-converted upstream at an unrecorded rate.
"""
from __future__ import annotations

import csv
import sys
from collections import Counter
from pathlib import Path
from typing import Dict, List, Optional

from panel.schema import is_plausible_fiscal_year, period_type_for, to_number

SCREENING_COLUMN_MAP: Dict[str, str] = {
    "Total Assets": "total_assets",
    "Total Liabilities": "total_liabilities",
    "Total Equity": "total_equity",
    "Current Assets": "current_assets",
    "Current Liabilities": "current_liabilities",
    "Cash (Balance Sheet)": "cash_and_equivalents",
    "Revenue": "revenue",
    "Net Income": "net_income",
    "Accounts Receivable": "accounts_receivable",
    "Inventory": "inventories",
}


def rows_from_screening_csv(path, market: str, cadence: str = "annual",
                            drops: Optional[Counter] = None) -> List[dict]:
    if drops is None:
        drops = Counter()
    csv.field_size_limit(sys.maxsize)
    out: List[dict] = []
    with open(path, newline="", encoding="utf-8-sig", errors="replace") as fin:
        for raw in csv.DictReader(fin):
            ticker = str(raw.get("Ticker") or "").strip()
            year = raw.get("bsns_year")
            if not ticker:
                drops["missing_ticker"] += 1
                continue
            if not is_plausible_fiscal_year(year):
                drops["implausible_fiscal_year"] += 1
                continue
            fiscal_year = int(year)
            period_end = f"{fiscal_year}-12-31"
            row = {
                "market": market,
                "local_id": ticker,
                "company_name": str(raw.get("Company Name") or "").strip() or None,
                "currency": str(raw.get("Currency") or "").strip() or None,
                "fiscal_year": fiscal_year,
                "period_end": period_end,
                "period_type": period_type_for(period_end, cadence=cadence),
                "source_artifact": str(Path(path)),
            }
            for src, dst in SCREENING_COLUMN_MAP.items():
                if src in raw:
                    row[dst] = to_number(raw.get(src))
            out.append(row)
    return out
