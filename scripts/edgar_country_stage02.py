#!/usr/bin/env python3
"""Stage-02 for country-pull EDGAR filings (Ireland, Germany).

716 documents -- EDGAR_IE 407 and EDGAR_DE 309 -- were acquired and never
processed. The blocker was never the extractor: ExtractItems.resolve_source_path
already honours an explicit `source_path`, and its docstring names this exact
case ("Country-pull documents live under market/CIK/accession"). What was
missing was the adapter turning MARKET_FILINGS metadata rows into the dicts it
expects.

Three traps live in the mapping rather than the extraction:

  1. Our raw layout is {CIK}/{accession}/{file}.htm, which the legacy
     Type/filename resolution cannot express -- source_path must be explicit or
     the extractor looks in the wrong place and quietly finds nothing.
  2. The filing TYPE selects the item set. A 20-F has different items from a
     10-K, and Irish and German issuers file both; getting it wrong extracts
     the wrong sections without an error.
  3. A row with no local_path was never acquired. Feeding it produces a
     confident empty extraction, which is worse than a gap.

Usage:
    python scripts/edgar_country_stage02.py --markets EDGAR_IE EDGAR_DE
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Dict, List, Optional

REPO = Path(__file__).resolve().parents[1]
METADATA = Path("/Volumes/OWC Express 1M2/datasets/MARKET_FILINGS/metadata/"
                "MARKET_FILINGS_METADATA.csv")
OUT_ROOT = Path("/Users/lichenyu/datasets/derived/EDGAR_COUNTRY")

_TYPES = ("20-F", "40-F", "10-K", "10-Q", "8-K")


def filing_type_for(category: Optional[str], filename: str = "") -> Optional[str]:
    """The SEC form type, from the category text or failing that the filename.

    Returns None rather than defaulting to 10-K: extracting 10-K items from a
    20-F silently yields the wrong sections.
    """
    text = str(category or "").upper()
    for t in _TYPES:
        if t in text:
            return t
    name = str(filename or "").lower()
    if re.search(r"20-?f", name):
        return "20-F"
    if re.search(r"40-?f", name):
        return "40-F"
    if re.search(r"10-?k", name):
        return "10-K"
    if re.search(r"10-?q", name):
        return "10-Q"
    return None


def metadata_row(row: Dict[str, str]) -> Optional[Dict[str, object]]:
    """One extract_items metadata dict, or None if the row is unusable."""
    local = str(row.get("local_path") or "").strip()
    if not local:
        return None
    path = Path(local)
    ftype = filing_type_for(row.get("category"), path.name)
    if ftype is None:
        return None
    cik = str(row.get("stock_code") or path.parent.parent.name).strip()
    company = (row.get("company_name") or "").strip()
    date = (row.get("filing_date") or "").strip()
    # extract_items reads BOTH casings depending on the code path (cik/CIK,
    # company/Company, sic/SIC ...). Supplying only one raises KeyError deep in
    # the extraction, after the file has already been read.
    return {
        "cik": cik, "CIK": cik,
        "company": company, "Company": company,
        "sic": "", "SIC": "",
        "state_location": "", "State location": "",
        "state_of_inc": "", "State of Inc": "",
        "fiscal_year_end": "", "Fiscal Year End": "",
        "period_of_report": date, "filing_date": date,
        "html_index": "", "htm_filing_link": row.get("document_url") or "",
        "complete_text_filing_link": row.get("document_url") or "",
        "filing_type": filing_type_for(row.get("category"), path.name),
        "Type": ftype,
        "Date": (row.get("filing_date") or "").strip(),
        "filename": path.name,
        # Authoritative: bypasses the legacy Type/filename layout.
        "source_path": str(path),
        "Period of Report": (row.get("filing_date") or "").strip(),
        "SIC": "", "State of Inc": "", "State location": "",
        "Fiscal Year End": "", "filing_html_index": "",
        "htm_file_link": row.get("document_url") or "",
        "complete_text_file_link": row.get("document_url") or "",
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--markets", nargs="*", default=["EDGAR_IE", "EDGAR_DE"])
    ap.add_argument("--out-dir", type=Path, default=OUT_ROOT)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    sys.path.insert(0, str(REPO))
    from extract_items import ExtractItems

    csv.field_size_limit(sys.maxsize)
    wanted = set(args.markets)
    rows: List[Dict[str, object]] = []
    drops: Counter = Counter()
    with open(METADATA, newline="", encoding="utf-8") as fin:
        for raw in csv.DictReader(fin):
            if raw.get("market") not in wanted:
                continue
            md = metadata_row(raw)
            if md is None:
                drops["unusable_row"] += 1
                continue
            rows.append(md)
            if args.limit and len(rows) >= args.limit:
                break
    print(f"{len(rows):,} filings to extract ({dict(drops)})", flush=True)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    extractor = ExtractItems(
        remove_tables=True, items_to_extract=[], include_signature=False,
        raw_files_folder=str(Path(METADATA).parent.parent / "raw"),
        extracted_files_folder=str(args.out_dir),
        skip_extracted_filings=True,
    )
    tally: Counter = Counter()
    for i, md in enumerate(rows, 1):
        try:
            extractor.process_filing(md)
            tally["ok"] += 1
        except Exception as exc:
            tally[f"error:{type(exc).__name__}"] += 1
        if i % 50 == 0:
            print(f"  {i:,}/{len(rows):,} {dict(tally)}", flush=True)
    print(f"\nEDGAR country stage-02: {dict(tally)} -> {args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
