"""canonical_metrics_wide.parquet adapter (annual only).

Named for its first consumer (IN_BSE) but it is the generic reader for every
`canonical_metrics_wide.parquet` in MARKET_FILINGS/derived: in_bse, au, tw, ph
and kr all go through it. Those four were originally pointed at
`*_screening_input.csv` instead, which holds exactly ONE row per ticker -- so
au/tw/ph/kr had no time series at all (mean rows per (market, local_id) was
exactly 1.000, against cn 116.5 / hk 11.3 / jp 8.5). The period-level parquet
named in the spec's Sec 1 table sat unused in the same directories.

Schema differences the adapter absorbs (all verified live 2026-08-17):
  au  27,665 rows FY2007-2025  ticker/filing_date/fiscal_year/doc_id
  tw   1,304 rows FY2015-2025  same
  ph     447 rows              same but NO doc_id column
  kr   9,193 rows              DIFFERENT: corp_code/stock_code/company_name/
                               bsns_year/fs_div/status, no doc_id, no
                               filing_date, plus operating_profit/borrowings
KR's identity/period columns are aliased (stock_code -> ticker, bsns_year ->
fiscal_year); the extra metric columns have no canonical slot and are ignored,
not mismapped.
"""
from __future__ import annotations

import re
from collections import Counter
from typing import List, Optional

import pandas as pd

from panel.schema import METRIC_COLUMNS, is_plausible_fiscal_year, period_type_for, to_number

# KR's DART parquet names the same concepts differently. Aliases are applied
# only when the canonical name is absent, so a frame carrying both is unchanged.
CANONICAL_ALIASES = {
    "stock_code": "ticker",
    "bsns_year": "fiscal_year",
}


def _aliased(df: pd.DataFrame) -> pd.DataFrame:
    renames = {
        src: dst for src, dst in CANONICAL_ALIASES.items()
        if src in df.columns and dst not in df.columns
    }
    return df.rename(columns=renames) if renames else df


def year_from_filing_date(value) -> Optional[int]:
    """Leading 4-digit year of a filing_date, if it is a plausible fiscal year.

    filing_date is 'YYYYMMDD' in the au/tw/ph parquets and a bare 'YYYY' in
    in_bse's, so take the first four digits of whatever digits are present.
    """
    digits = re.sub(r"\D", "", str(value or ""))
    if len(digits) < 4:
        return None
    year = int(digits[:4])
    return year if is_plausible_fiscal_year(year) else None


def rows_from_canonical_metrics(
    df: pd.DataFrame,
    market: str,
    currency: str,
    drops: Optional[Counter] = None,
    source_artifact: Optional[str] = None,
) -> List[dict]:
    """Build panel rows from a canonical_metrics_wide.parquet (annual only).

    Args:
        df: frame with ticker (or stock_code), fiscal_year (or bsns_year),
            optional filing_date / doc_id / company_name / fs_div, and metric
            columns.
        market: market identifier (e.g. "au", "kr", "in_bse").
        currency: the NATIVE reporting currency of this parquet.
        drops: optional Counter incremented per dropped row, keyed by reason
            (spec Sec 6: never silently skipped).
        source_artifact: provenance string recorded on every row.

    Raises:
        ValueError: if (ticker, doc_id) is not unique -- that would mean two
            rows for the same company from the same document.

    Fiscal-year rescue (spec Sec 5.1): a row whose fiscal_year is implausible
    but whose filing_date yields a plausible year keeps the derived year and is
    marked fiscal_year_source="derived:filing_date". Only a row where NEITHER
    works is dropped. PH is the live case: 2 rows carry fiscal_year=430 with
    filing_date 20250502.
    """
    if drops is None:
        drops = Counter()
    df = _aliased(df)

    # doc_id alone is NOT unique in the ASX parquet and that is legitimate: one
    # filing can cover several stapled/co-listed tickers (verified 2026-08-17 --
    # doc_id 02969892 covers HDN/HCW/DGT/HMC, doc_id 01203318 covers INV/ARC;
    # 12 rows across 4 doc_ids). The row identity is (ticker, doc_id), so guard
    # that instead; a bare-doc_id guard would raise and drop the whole market.
    if "doc_id" in df.columns and "ticker" in df.columns:
        pairs = df[["ticker", "doc_id"]].dropna()
        if pairs.duplicated().any():
            dupes = sorted(
                set(map(tuple, pairs[pairs.duplicated()].to_numpy()))
            )[:5]
            raise ValueError(f"duplicate (ticker, doc_id) in {market}: {dupes}")

    artifact = source_artifact or (
        f"derived/{market.upper()}_FINANCIALS/canonical_metrics_wide.parquet"
    )
    out: List[dict] = []
    for record in df.to_dict("records"):
        ticker = str(record.get("ticker") or "").strip()
        if not ticker or ticker.lower() == "nan":
            drops["missing_ticker"] += 1
            continue
        year = record.get("fiscal_year")
        fiscal_year_source = "reported"
        if not is_plausible_fiscal_year(year):
            derived = year_from_filing_date(record.get("filing_date"))
            if derived is None:
                drops["implausible_fiscal_year"] += 1
                continue
            year = derived
            fiscal_year_source = "derived:filing_date"
            drops["fiscal_year_derived_from_filing_date"] += 1
        fiscal_year = int(year)
        period_end = f"{fiscal_year}-12-31"
        company_name = record.get("company_name")
        if company_name is None or pd.isna(company_name):
            company_name = None
        else:
            company_name = str(company_name).strip() or None
        doc_id = record.get("doc_id")
        if doc_id is not None and pd.isna(doc_id):
            doc_id = None
        row = {
            "market": market,
            "local_id": ticker,
            "company_name": company_name,
            "currency": currency,
            "fiscal_year": fiscal_year,
            "fiscal_year_source": fiscal_year_source,
            "period_end": period_end,
            "period_type": period_type_for(period_end, cadence="annual"),
            "source_doc_id": doc_id,
            "source_artifact": artifact,
        }
        if "fs_div" in record:
            # KR consolidated (CFS) vs separate/parent (OFS) financial
            # statements -- the same distinction as CN Typrep and JP
            # has_consolidated_statements. Mapped to reporting_basis in build.
            fs_div = record.get("fs_div")
            row["fs_div"] = None if fs_div is None or pd.isna(fs_div) else str(fs_div)
        for metric in METRIC_COLUMNS:
            if metric in record:
                value = record.get(metric)
                row[metric] = None if pd.isna(value) else to_number(value)
        out.append(row)
    return out
