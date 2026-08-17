"""JP adapter over markets/jp/02_structured/processed/edinet_xbrl/edinet_xbrl.duckdb.

`annual_metrics_wide` already holds one row per filing with a real
`metric_period_end`, so this is a column mapping. Japanese fiscal years commonly
end 31 March, so period_end must come from the data, never assumed as 12-31.
"""
from __future__ import annotations

from collections import Counter
from typing import Dict, List, Optional

import pandas as pd

from panel.schema import (
    METRIC_COLUMNS, is_plausible_fiscal_year, normalize_period_end, period_type_for,
    to_number,
)

JP_COLUMN_MAP: Dict[str, str] = {
    "assets": "total_assets",
    "liabilities": "total_liabilities",
    # net_assets (純資産) -> total_equity: CORRECT, not a name-match shortcut.
    # Post-2006 JP GAAP 純資産 = shareholders' equity + AOCI + subscription
    # rights + non-controlling interests -- i.e. it is exactly the
    # balance-sheet-identity equity figure (total_assets - total_liabilities)
    # that reconciles against `assets`/`liabilities` above. The narrower
    # `shareholders_equity` column (株主資本, owners-of-parent only) would
    # BREAK that identity if used instead. This does not contradict hk.py's
    # refusal to alias its own net_assets column: HK's net_assets comes from
    # a free-text extracted label with no guaranteed definition, whereas
    # JP's comes from a structured EDINET XBRL tag with standardized
    # semantics. Different provenance, different confidence -- not an
    # inconsistency between the two adapters.
    "net_assets": "total_equity",
    "net_sales": "revenue",
    # income_before_taxes (税引前当期純利益) -> profit_before_tax: CORRECT,
    # the true pretax line. `operating_income` (営業利益) excludes
    # non-operating items and `ordinary_income` (経常利益) adds some back but
    # still isn't pretax profit -- only income_before_taxes is the direct
    # counterpart of canonical profit_before_tax. Both operating_income and
    # ordinary_income are deliberately left UNMAPPED: canonical
    # METRIC_COLUMNS has no operating-income slot, and overloading
    # profit_before_tax with operating profit is exactly the bug this
    # mapping used to have (fixed 2026-08-17 review; see git history).
    "income_before_taxes": "profit_before_tax",
    "profit_loss": "net_income",
    "basic_eps": "basic_eps",
    # cash_and_deposits (現金及び預金) -> cash_and_equivalents: CORRECT even
    # though the table ALSO has a literally-named `cash_and_equivalents`
    # column. cash_and_deposits is the balance-sheet asset line; the
    # identically-named column is the cash-flow statement's end-of-period
    # figure, whose scope can differ from the BS line. Every other adapter
    # sources this canonical field from a balance-sheet line (e.g. cn.py's
    # A001101000), so taking the CFS column purely because its name matches
    # would break cross-adapter consistency -- do not "fix" this by name.
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
# wrong source column.


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


def rows_from_jp_frame(df: pd.DataFrame, drops: Optional[Counter] = None) -> List[dict]:
    if drops is None:
        drops = Counter()
    out: List[dict] = []
    for record in df.to_dict("records"):
        code = str(record.get("stock_code") or "").strip()
        period_end = normalize_period_end(record.get("metric_period_end"))
        year = record.get("fiscal_year")
        if not code or code == "None":
            drops["missing_stock_code"] += 1
            continue
        if period_end is None:
            drops["unusable_period_end"] += 1
            continue
        if not is_plausible_fiscal_year(year):
            drops["implausible_fiscal_year"] += 1
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
            # has_consolidated_statements distinguishes full consolidated
            # filings from parent-company-only filings that can otherwise
            # collide on (stock_code, metric_period_end) -- verified live
            # 2026-08-17: stock_code 85950, period 2021-03-31 has two
            # doc_ids, one True (assets=2.62e11) and one False
            # (assets=4.35e8). Not an amendment; a genuine dual reporting
            # basis, the same pattern as CN's Typrep A/B.
            "jp_has_consolidated_statements": record.get("has_consolidated_statements"),
            "source_doc_id": record.get("doc_id"),
            "source_artifact": "markets/jp/02_structured/processed/edinet_xbrl",
        }
        for src, dst in JP_COLUMN_MAP.items():
            if src in record and dst in METRIC_COLUMNS:
                value = record.get(src)
                row[dst] = None if pd.isna(value) else to_number(value)
        out.append(row)
    return out
