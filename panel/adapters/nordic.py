"""Nordic/Baltic adapter: 8 countries from one swept corpus.

Covers Norway (Oslo announcement attachments) and Nasdaq Nordic/Baltic
(se/dk/fi/is/ee/lv/lt). Each country becomes its OWN market rather than a
"nordic" blob: they use different currencies, list on different exchanges, and
a shared identity is precisely what let five markets sit behind a single US
universe hash for months.

THE YEAR DEFECT. The sweep's `fiscal_year` is the PUBLICATION year -- the
directory an announcement was filed under -- not the year the statements cover.
Measured on the live corpus, the filename year is directory-year MINUS ONE for:

    no 85.1%   se 57%   dk 47%   fi 61%

with 28-33% carrying no filename year at all. Taking the directory year at face
value misdates most of the corpus by a FULL YEAR -- worse than any period-end
defect found in this program. So the year is read from the filename where it
exists, and otherwise derived as publication-minus-one, with the provenance
recorded either way.

Currency is already read per filing by the sweep (`currency_source` is
"declared" for 74% of rows) and is carried through unchanged. Unit scale has
never been detected for this corpus, so it stays None -- unknown, not 1.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import List, Optional, Tuple

import pandas as pd

from panel.schema import METRIC_COLUMNS, is_plausible_fiscal_year, period_type_for, to_number

# Nordic statutory financial years end 31 December. The source carries no
# period-end column, so this is a convention and is labelled as one.
NORDIC_COUNTRIES = {"no", "se", "dk", "fi", "is", "ee", "lv", "lt"}

_YEAR = re.compile(r"(?:19|20)\d{2}")
_MIN_YEAR, _MAX_YEAR = 1990, 2026


def fiscal_year_from_doc_id(doc_id: str, ticker, publication_year) -> Tuple[int, str]:
    """(fiscal_year, provenance) for one document.

    The doc_id is "{ticker}_{publication_year}_{...filename}", so the prefix is
    stripped before looking for a year -- otherwise the publication year is
    always found and the defect is invisible (it hid from the first check run
    against this corpus for exactly that reason).
    """
    pub = int(publication_year)
    text = str(doc_id or "")
    prefix = f"{ticker}_{pub}_"
    tail = text[len(prefix):] if text.startswith(prefix) else text
    # Oslo filenames start with messageId_attachmentId, which are not years.
    tail = re.sub(r"^\d+_\d+_", "", tail)
    years = [int(m.group(0)) for m in _YEAR.finditer(tail)]
    years = [y for y in years if _MIN_YEAR <= y <= _MAX_YEAR]
    if years:
        return max(years), "derived:filename"
    return pub - 1, "derived:publication-year-minus-1"


def rows_from_nordic(df: pd.DataFrame, drops: Optional[Counter] = None,
                     source_artifact: Optional[str] = None) -> List[dict]:
    """Panel rows from NORDIC_FINANCIALS/canonical_metrics_wide.parquet."""
    if drops is None:
        drops = Counter()
    artifact = source_artifact or "derived/NORDIC_FINANCIALS/canonical_metrics_wide.parquet"
    out: List[dict] = []
    for rec in df.to_dict("records"):
        country = rec.get("country")
        if country is None or pd.isna(country) or str(country) not in NORDIC_COUNTRIES:
            drops["missing_country"] += 1
            continue
        if not rec.get("n_metrics"):
            drops["no_metrics"] += 1
            continue
        ticker = str(rec.get("ticker") or "").strip()
        if not ticker or ticker.lower() == "nan":
            drops["missing_ticker"] += 1
            continue
        pub = rec.get("fiscal_year")
        if not is_plausible_fiscal_year(pub):
            drops["implausible_fiscal_year"] += 1
            continue
        fiscal_year, fy_source = fiscal_year_from_doc_id(rec.get("doc_id"), ticker, pub)
        period_end = f"{fiscal_year}-12-31"
        market = str(country)
        currency = rec.get("currency")
        row = {
            "market": market,
            "local_id": ticker,
            "company_name": None,
            "currency": None if currency is None or pd.isna(currency) else str(currency),
            "currency_source": rec.get("currency_source"),
            "fiscal_year": fiscal_year,
            "fiscal_year_source": fy_source,
            "period_end": period_end,
            "period_end_source": f"convention:{market}",
            "period_type": period_type_for(period_end, cadence="annual"),
            # No scale detection has been run over this corpus. Unknown, not 1.
            "unit_scale": None,
            "unit_scale_source": "unknown",
            "source_doc_id": rec.get("doc_id"),
            "source_artifact": artifact,
        }
        for metric in METRIC_COLUMNS:
            if metric in rec:
                value = rec.get(metric)
                row[metric] = None if pd.isna(value) else to_number(value)
        out.append(row)
    return out
