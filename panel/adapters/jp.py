"""JP adapter over markets/jp/02_structured/processed/edinet_xbrl/edinet_xbrl.duckdb.

`annual_metrics_wide` already holds one row per filing with a real
`metric_period_end`, so this is a column mapping. Japanese fiscal years commonly
end 31 March, so period_end must come from the data, never assumed as 12-31.
"""
from __future__ import annotations

from typing import Dict, List

import pandas as pd

from panel.schema import (
    METRIC_COLUMNS, is_plausible_fiscal_year, normalize_period_end, period_type_for,
    to_number,
)

JP_COLUMN_MAP: Dict[str, str] = {
    "assets": "total_assets",
    "liabilities": "total_liabilities",
    "net_assets": "total_equity",
    "net_sales": "revenue",
    "operating_income": "profit_before_tax",
    "profit_loss": "net_income",
    "basic_eps": "basic_eps",
    "cash_and_deposits": "cash_and_equivalents",
    "operating_cash_flow": "operating_cash_flow",
    "investing_cash_flow": "investing_cash_flow",
    "financing_cash_flow": "financing_cash_flow",
}
# Not present in `annual_metrics_wide` (verified live 2026-08-17): cost_of_sales,
# gross_profit, income_taxes, inventories, current_assets, current_liabilities.
# The live table has no equivalent column for any of these (no COGS/gross-profit
# split, no tax-expense line, no current/non-current asset or liability split),
# so they are simply absent from the panel for JP rather than mismapped onto a
# wrong source column. `income_before_taxes` and `ordinary_income` also exist in
# the live schema but are deliberately left unmapped here (out of scope for this
# task) rather than silently reassigning `operating_income` -> profit_before_tax.


def read_jp_wide(duckdb_path, limit: int = 0) -> pd.DataFrame:
    import duckdb

    query = "select * from annual_metrics_wide"
    if limit:
        query += f" limit {int(limit)}"
    con = duckdb.connect(str(duckdb_path), read_only=True)
    try:
        return con.execute(query).fetchdf()
    finally:
        con.close()


def rows_from_jp_frame(df: pd.DataFrame) -> List[dict]:
    out: List[dict] = []
    for record in df.to_dict("records"):
        code = str(record.get("stock_code") or "").strip()
        period_end = normalize_period_end(record.get("metric_period_end"))
        year = record.get("fiscal_year")
        if not code or code == "None" or period_end is None:
            continue
        if not is_plausible_fiscal_year(year):
            continue
        row = {
            "market": "jp",
            "local_id": code,
            "company_name": record.get("company_name") or None,
            "currency": "JPY",
            "fiscal_year": int(year),
            "period_end": period_end,
            "period_type": period_type_for(period_end, cadence="annual"),
            "jp_edinet_code": record.get("edinet_code"),
            "jp_accounting_standards": record.get("accounting_standards"),
            "source_doc_id": record.get("doc_id"),
            "source_artifact": "markets/jp/02_structured/processed/edinet_xbrl",
        }
        for src, dst in JP_COLUMN_MAP.items():
            if src in record and dst in METRIC_COLUMNS:
                value = record.get(src)
                row[dst] = None if pd.isna(value) else to_number(value)
        out.append(row)
    return out
