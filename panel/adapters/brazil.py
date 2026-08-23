"""Brazil CVM adapter — DFP annual filings, 2010-2026.

Needs its own adapter rather than reusing rows_from_canonical_metrics because
that one derives period_end from a fiscal-year label via the conventions table.
Pointing Brazil at it would DISCARD the real DT_FIM_EXERC and substitute a
fabricated 12-31 -- throwing away the field that makes this source good.

CVM open data ships currency (MOEDA), unit scale (ESCALA_MOEDA), a real period
end (DT_FIM_EXERC) and consolidated/individual as separate files. Together with
ESEF it is one of only two sources in the panel where nothing is inferred.
"""

from __future__ import annotations

from collections import Counter
from typing import List, Optional

import pandas as pd

from panel.schema import METRIC_COLUMNS, period_type_for, to_number


def rows_from_brazil(df: pd.DataFrame, drops: Optional[Counter] = None,
                     source_artifact: Optional[str] = None) -> List[dict]:
    if drops is None:
        drops = Counter()
    artifact = source_artifact or "derived/BR_CVM_FINANCIALS/canonical_metrics_wide.parquet"
    out: List[dict] = []
    for rec in df.to_dict("records"):
        period_end = rec.get("period_end")
        if period_end is None or pd.isna(period_end) or not str(period_end).strip():
            drops["missing_period_end"] += 1
            continue
        if not rec.get("n_metrics"):
            drops["no_metrics"] += 1
            continue
        ticker = str(rec.get("ticker") or "").strip()
        if not ticker or ticker.lower() == "nan":
            drops["missing_ticker"] += 1
            continue
        period_end = str(period_end)[:10]
        scale = rec.get("unit_scale")
        company = rec.get("company_name")
        row = {
            "market": "br",
            "local_id": ticker,
            "company_name": None if company is None or pd.isna(company) else str(company).strip(),
            "currency": rec.get("currency"),
            "currency_source": "declared",
            "fiscal_year": int(period_end[:4]),
            "fiscal_year_source": "reported",
            "period_end": period_end,
            "period_end_source": "reported",
            "period_type": period_type_for(period_end, cadence="annual"),
            "reporting_basis": rec.get("reporting_basis") or "unknown",
            # Unknown stays None, never 1 -- consistent with the rest of the panel.
            "unit_scale": None if scale is None or pd.isna(scale) else int(scale),
            "unit_scale_source": rec.get("unit_scale_source") or "unknown",
            "source_doc_id": rec.get("doc_id"),
            "source_artifact": artifact,
        }
        for metric in METRIC_COLUMNS:
            if metric in rec:
                value = rec.get(metric)
                row[metric] = None if pd.isna(value) else to_number(value)
        out.append(row)
    return out
