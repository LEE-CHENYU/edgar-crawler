"""AU/TW/PH/KR adapter.

These four markets already share an identical *_screening_input.csv header, so
this adapter is a column mapping rather than extraction.
"""
from __future__ import annotations

import csv
import sys
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


def rows_from_screening_csv(path, market: str, cadence: str = "annual") -> List[dict]:
    csv.field_size_limit(sys.maxsize)
    out: List[dict] = []
    with open(path, newline="", encoding="utf-8-sig", errors="replace") as fin:
        for raw in csv.DictReader(fin):
            ticker = str(raw.get("Ticker") or "").strip()
            year = raw.get("bsns_year")
            if not ticker or not is_plausible_fiscal_year(year):
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
