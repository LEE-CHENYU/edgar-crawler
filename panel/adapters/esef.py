"""ESEF adapter: 27 European markets from inline XBRL.

The first source in this panel where NOTHING has to be inferred. period_end is
read from the XBRL context, currency from the unit, and the decimal scale was
applied during extraction. Every other market in the panel needed a convention,
a derivation or an assumption for at least one of those:

    au / in_bse   period end from a fiscal-calendar convention
    hk            period end derived from filing month (~20% were misdated)
    jp            period end read, but only after a defect was found
    hk / in / au  unit scale recovered from statement captions, 84-99%
    nordic        currency read per filing, fiscal year derived from filename

Two things this adapter must state precisely:

  * period_end_source is "reported" -- and this is the first market that can
    honestly claim it. Mislabelling this misdated 37,669 JP rows and 83,144 HK
    rows earlier in this program.
  * unit_scale is 1 because the iXBRL `scale` attribute was ALREADY applied.
    That is a different claim from "unknown": these rows belong in level
    comparisons, whereas an unrecovered scale must be excluded from them.

Identity is the LEI. ESEF filings carry no ticker, so spine resolution will
mostly fall back to surrogate keys -- recorded honestly rather than papered over
with a guessed exchange code.
"""

from __future__ import annotations

from collections import Counter
from typing import List, Optional

import pandas as pd

from panel.schema import METRIC_COLUMNS, period_type_for, to_number


def rows_from_esef(df: pd.DataFrame, drops: Optional[Counter] = None,
                   source_artifact: Optional[str] = None) -> List[dict]:
    """Panel rows from a markets/<cc>/02_structured/esef parquet."""
    if drops is None:
        drops = Counter()
    out: List[dict] = []
    for rec in df.to_dict("records"):
        lei = rec.get("lei")
        if lei is None or pd.isna(lei) or not str(lei).strip():
            drops["missing_lei"] += 1
            continue
        period_end = rec.get("period_end")
        if period_end is None or pd.isna(period_end) or not str(period_end).strip():
            drops["missing_period_end"] += 1
            continue
        if not rec.get("n_metrics"):
            drops["no_metrics"] += 1
            continue
        period_end = str(period_end)[:10]
        country = str(rec.get("market") or "").strip().lower()
        # Namespaced: Sweden/Denmark/Norway/Finland etc. also appear in the
        # Nasdaq Nordic corpus with TICKER identifiers and real exchange codes.
        # Sharing a market key would mix two identifier spaces and force those
        # ticker-keyed rows to be treated as surrogate-keyed.
        market = f"esef_{country}" if country else "esef_unknown"
        currency = rec.get("currency")
        row = {
            "market": market,
            "local_id": str(lei).strip(),
            "company_name": None,
            "currency": None if currency is None or pd.isna(currency) else str(currency),
            "currency_source": rec.get("currency_source"),
            "fiscal_year": int(period_end[:4]),
            "fiscal_year_source": "reported",
            "period_end": period_end,
            "period_end_source": "reported",
            "period_type": period_type_for(period_end, cadence="annual"),
            # The iXBRL scale attribute was applied at extraction, so values are
            # absolute. NOT "unknown" -- these rows are level-comparable.
            "unit_scale": 1,
            "unit_scale_source": "applied:ixbrl-scale",
            "source_doc_id": rec.get("source_path"),
            "source_artifact": source_artifact or
                               f"markets/{country}/02_structured/esef/canonical_metrics_wide.parquet",
        }
        for metric in METRIC_COLUMNS:
            if metric in rec:
                value = rec.get(metric)
                row[metric] = None if pd.isna(value) else to_number(value)
        out.append(row)
    return out
