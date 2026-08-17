"""IN_BSE adapter over canonical_metrics_wide.parquet (annual only)."""
from __future__ import annotations

from typing import List

import pandas as pd

from panel.schema import METRIC_COLUMNS, is_plausible_fiscal_year, period_type_for, to_number


def rows_from_canonical_metrics(df: pd.DataFrame, market: str, currency: str) -> List[dict]:
    """Build panel rows from canonical_metrics_wide.parquet (annual only).

    Args:
        df: DataFrame with columns: ticker, fiscal_year, doc_id, and metric columns
        market: market identifier (e.g., "in_bse")
        currency: currency code (e.g., "INR")

    Returns:
        List of dicts ready for panel ingestion.

    Raises:
        ValueError: if duplicate doc_ids are found.
    """
    if "doc_id" in df.columns:
        ids = df["doc_id"].dropna()
        if ids.duplicated().any():
            dupes = sorted(set(ids[ids.duplicated()]))[:5]
            raise ValueError(f"duplicate doc_id in {market}: {dupes}")

    out: List[dict] = []
    for record in df.to_dict("records"):
        year = record.get("fiscal_year")
        ticker = str(record.get("ticker") or "").strip()
        if not ticker or not is_plausible_fiscal_year(year):
            continue
        fiscal_year = int(year)
        period_end = f"{fiscal_year}-12-31"
        row = {
            "market": market,
            "local_id": ticker,
            "company_name": None,
            "currency": currency,
            "fiscal_year": fiscal_year,
            "period_end": period_end,
            "period_type": period_type_for(period_end, cadence="annual"),
            "source_doc_id": record.get("doc_id"),
            "source_artifact": f"derived/{market.upper()}_FINANCIALS/canonical_metrics_wide.parquet",
        }
        for metric in METRIC_COLUMNS:
            if metric in record:
                value = record.get(metric)
                row[metric] = None if pd.isna(value) else to_number(value)
        out.append(row)
    return out
