#!/usr/bin/env python3
"""Stage-02 sweep for PDF markets, generalised over target discovery.

`asx_financials_sweep.py` hardcodes its output directory and derives
identifiers from a filename regex (`{TICKER}-{YYYYMMDD}-{docid}.pdf`). IN_BSE
-- 67,994 documents and 308 GB, the largest unprocessed corpus -- instead
carries its identifiers in the path: `{scrip_code}/{year}/{file}.pdf`.

The extractor itself (`asx_financials_extract.extract_pdf`) is
market-agnostic: PDF text -> canonical metrics. Only target discovery and the
output location differ, so those are what this parameterises.

Output contract matches the existing markets: `sweep_results.jsonl` plus
`canonical_metrics_wide.parquet`, resumable via the `doc_id` set already in
the JSONL.

Usage:
    python scripts/pdf_market_sweep.py --market in_bse --limit 20   # smoke
    python scripts/pdf_market_sweep.py --market in_bse --workers 4
"""

from __future__ import annotations

import argparse
import json
import importlib.util
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, List, Optional, Set

REPO_DIR = Path(__file__).resolve().parents[1]
if str(REPO_DIR) not in sys.path:
    sys.path.insert(0, str(REPO_DIR))

DATASETS = Path("/Volumes/OWC Express 1M2/datasets/MARKET_FILINGS")

MARKETS: Dict[str, Dict[str, str]] = {
    "in_bse": {
        "raw_root": str(DATASETS / "raw" / "IN_BSE_ANNUAL_REPORTS"),
        "out_dir": str(DATASETS / "derived" / "IN_BSE_FINANCIALS"),
        "builder": "in_bse",
    },
    "asx": {
        "raw_root": str(DATASETS / "raw" / "ASX"),
        "out_dir": str(DATASETS / "derived" / "ASX_FINANCIALS"),
        "builder": "asx",
    },
    "no_oslo": {
        "raw_root": str(DATASETS / "raw" / "NO_OSLO_NEWSWEB"),
        "out_dir": str(DATASETS / "derived" / "NO_OSLO_FINANCIALS"),
        "builder": "oslo",
    },
    "twse": {
        "raw_root": str(DATASETS / "raw" / "TWSE_REPORTS"),
        "out_dir": str(DATASETS / "derived" / "TWSE_FINANCIALS"),
        "builder": "twse",
    },
    "nordic": {
        # Folded out of the orphaned NASDAQ_NORDIC_BALTIC_NEWS tree; lives on the
        # boot volume across seven country dirs, so raw_root is set per run.
        "raw_root": "/Users/lichenyu/datasets/markets/se/01_raw/nasdaq_nordic_baltic",
        "out_dir": str(DATASETS / "derived" / "NORDIC_FINANCIALS"),
        "builder": "nordic",
    },
}

_conv_spec = importlib.util.spec_from_file_location(
    "_sweep_conventions", Path(__file__).resolve().parent / "market_conventions.py")
_conventions = importlib.util.module_from_spec(_conv_spec)
_conv_spec.loader.exec_module(_conventions)

_ASX_FNAME = re.compile(r"^([A-Z0-9]+)-(\d{8})-(\d+)\.pdf$", re.IGNORECASE)
_YEAR = re.compile(r"^(19|20)\d{2}$")
_DATE8 = re.compile(r"^\d{8}$")
_lock = threading.Lock()


# --- target discovery ---------------------------------------------------


def fiscal_year_from_bse_name(filename: str, year_dir: str) -> int:
    """The directory is authoritative.

    IN_BSE filenames encode a date too (`5301450313.pdf` -> scrip 530145,
    03/13), but it is ambiguous and occasionally absent, so the year directory
    wins.
    """
    return int(year_dir)


def build_targets_in_bse(raw_root: Path, years: Optional[Set[str]]) -> List[dict]:
    """Identifiers from the path: {scrip_code}/{year}/{file}.pdf."""
    out: List[dict] = []
    raw_root = Path(raw_root)
    for pdf in raw_root.rglob("*.pdf"):
        try:
            rel = pdf.relative_to(raw_root)
        except ValueError:
            continue
        parts = rel.parts
        if len(parts) < 3:
            # Needs at least {scrip}/{year}/{file}; anything shallower has no
            # year and would produce a bogus target.
            continue
        scrip, year_dir = parts[0], parts[1]
        if not _YEAR.match(year_dir):
            continue
        if years and year_dir not in years:
            continue
        out.append({
            "ticker": scrip,
            "filing_date": year_dir,
            # Stem alone can repeat across years in some corpora; qualify it.
            "doc_id": f"{scrip}_{year_dir}_{pdf.stem}" if pdf.stem.startswith(scrip) is False else pdf.stem,
            "fiscal_year": fiscal_year_from_bse_name(pdf.name, year_dir),
            "pdf_path": str(pdf),
        })
    return out


_NORDIC_COUNTRY = re.compile(r"/markets/([a-z]{2})/01_raw/")


def country_from_raw_root(raw_root: Path) -> Optional[str]:
    """Country code from the corpus path, or None.

    The Nordic corpus spans seven country roots that all feed ONE output
    directory. Nothing downstream recorded which country a row came from, so the
    first sweep lost it entirely -- and country is what selects the FX rate and
    the fallback currency.
    """
    m = _NORDIC_COUNTRY.search(str(raw_root).replace("\\", "/") + "/")
    return m.group(1) if m else None


def build_targets_nordic(raw_root: Path, years: Optional[Set[str]]) -> List[dict]:
    """Nordic/Baltic layout: {year}/{company_id}/{file}.pdf.

    Note the ordering is the REVERSE of IN_BSE ({scrip}/{year}/): pointing this
    tree at the in_bse builder finds zero targets and sweeps nothing silently.
    """
    out: List[dict] = []
    raw_root = Path(raw_root)
    country = country_from_raw_root(raw_root)
    for pdf in raw_root.rglob("*.pdf"):
        try:
            parts = pdf.relative_to(raw_root).parts
        except ValueError:
            continue
        if len(parts) < 3:
            continue
        year_dir, company = parts[0], parts[1]
        if not _YEAR.match(year_dir):
            continue
        if years and year_dir not in years:
            continue
        out.append({
            "ticker": company,
            "filing_date": year_dir,
            "doc_id": f"{company}_{year_dir}_{pdf.stem}",
            "fiscal_year": int(year_dir),
            "country": country,
            "pdf_path": str(pdf),
        })
    return out


_TWSE_FNAME = re.compile(r"^(\d{4})_(\w+?)_(\d{8}F\d+)$")


def build_targets_twse(raw_root: Path, years: Optional[Set[str]]) -> List[dict]:
    """TWSE layout: {filing_date}/{filing_id}/{fiscal}_{ticker}_{date}F04.pdf.

    A fifth distinct layout. The identity is in the FILENAME, not the path --
    the directory above is the long filing_id
    ("TWSE_REPORT_F_1717_2022_2021_1717_20220623F04"), and the date directory is
    the filing date, not the fiscal year. 14,940 TWSE PDFs sat unswept because
    the sweeper had no builder for this shape (only 1,304 rows existed in
    stage 02, from an older path).

    `years` filters on FISCAL year, matching the other builders.
    """
    out: List[dict] = []
    raw_root = Path(raw_root)
    for pdf in raw_root.rglob("*.pdf"):
        m = _TWSE_FNAME.match(pdf.stem)
        if not m:
            continue
        fiscal, ticker, _stamp = m.groups()
        if years and fiscal not in years:
            continue
        try:
            parts = pdf.relative_to(raw_root).parts
        except ValueError:
            continue
        filing_date = parts[0] if parts and _DATE8.match(parts[0]) else ""
        out.append({
            "ticker": ticker,
            "filing_date": filing_date,
            "doc_id": pdf.stem,
            "fiscal_year": int(fiscal),
            "pdf_path": str(pdf),
        })
    return out


def build_targets_oslo(raw_root: Path, years: Optional[Set[str]]) -> List[dict]:
    """Oslo Newsweb layout: {year}/{month}/{disclosure_id}.pdf."""
    out: List[dict] = []
    raw_root = Path(raw_root)
    for pdf in raw_root.rglob("*.pdf"):
        try:
            parts = pdf.relative_to(raw_root).parts
        except ValueError:
            continue
        if len(parts) < 3:
            continue
        year_dir, month_dir = parts[0], parts[1]
        if not _YEAR.match(year_dir):
            continue
        if years and year_dir not in years:
            continue
        out.append({
            "ticker": pdf.stem,
            "filing_date": f"{year_dir}-{month_dir}",
            "doc_id": f"{year_dir}{month_dir}_{pdf.stem}",
            "fiscal_year": int(year_dir),
            "pdf_path": str(pdf),
        })
    return out


def build_targets_asx(raw_root: Path, years: Optional[Set[str]]) -> List[dict]:
    """Preserve the existing ASX filename-regex behaviour."""
    out: List[dict] = []
    for pdf in Path(raw_root).rglob("*.pdf"):
        m = _ASX_FNAME.match(pdf.name)
        if not m:
            continue
        ticker, date, doc_id = m.group(1), m.group(2), m.group(3)
        if years and date[:4] not in years:
            continue
        year = int(date[:4])
        out.append({
            "ticker": ticker, "filing_date": date, "doc_id": doc_id,
            "fiscal_year": year if int(date[4:6]) >= 7 else year - 1,
            "pdf_path": str(pdf),
        })
    return out


_BUILDERS = {
    "twse": build_targets_twse,
    "in_bse": build_targets_in_bse,
    "asx": build_targets_asx,
    "nordic": build_targets_nordic,
    "oslo": build_targets_oslo,
}


def targets_for(market: str, raw_root: Path, years: Optional[Set[str]]) -> List[dict]:
    cfg = MARKETS[market]
    return _BUILDERS[cfg["builder"]](raw_root, years)


# --- sweep --------------------------------------------------------------


def load_done(results: Path) -> Set[str]:
    done: Set[str] = set()
    if results.exists():
        for line in results.read_text(encoding="utf-8").splitlines():
            try:
                done.add(json.loads(line)["doc_id"])
            except (json.JSONDecodeError, KeyError):
                continue
    return done


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--market", choices=sorted(MARKETS), required=True)
    parser.add_argument("--raw-root", default=None)
    parser.add_argument("--out-dir", default=None)
    parser.add_argument("--years", default="")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--log-every", type=int, default=100)
    args = parser.parse_args()

    import asx_financials_extract as ax

    cfg = MARKETS[args.market]
    raw_root = Path(args.raw_root or cfg["raw_root"])
    out_dir = Path(args.out_dir or cfg["out_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)
    results_jsonl = out_dir / "sweep_results.jsonl"
    parquet_out = out_dir / "canonical_metrics_wide.parquet"

    years = set(y.strip() for y in args.years.split(",") if y.strip()) or None
    targets = targets_for(args.market, raw_root, years)
    done = load_done(results_jsonl)
    pending = [t for t in targets if t["doc_id"] not in done]
    if args.limit:
        pending = pending[: args.limit]
    print(
        f"market={args.market} targets={len(targets)} done={len(done)} "
        f"pending={len(pending)} workers={args.workers}", flush=True
    )

    def append(rec: dict) -> None:
        with _lock:
            with results_jsonl.open("a", encoding="utf-8") as fout:
                fout.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def run(t: dict) -> dict:
        try:
            metrics = ax.extract_pdf(t["pdf_path"])
            rec = {
                "ticker": t["ticker"], "filing_date": t["filing_date"],
                "doc_id": t["doc_id"], "fiscal_year": t["fiscal_year"],
                "n_metrics": len(metrics), **metrics,
            }
            if t.get("country") is not None:
                # Currency is READ from the filing, with country as a labelled
                # last resort. Assigning currency by country would repeat the HK
                # defect (hardcoded HKD, ~40% of filers report RMB): Finnish and
                # Baltic issuers report in EUR, and so do some Swedish ones.
                text = ax.pdf_to_text(t["pdf_path"])
                currency, currency_source = _conventions.detect_currency(
                    text[:40000], country=t["country"])
                rec["country"] = t["country"]
                rec["currency"] = currency
                rec["currency_source"] = currency_source
        except Exception as exc:  # a single bad PDF must not kill the sweep
            rec = {
                "ticker": t["ticker"], "doc_id": t["doc_id"],
                "fiscal_year": t["fiscal_year"], "n_metrics": 0,
                "country": t.get("country"),
                "status": f"error:{type(exc).__name__}",
            }
        append(rec)
        return rec

    n_ok = n_empty = 0
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(run, t) for t in pending]
        for i, fut in enumerate(as_completed(futures), 1):
            rec = fut.result()
            if rec.get("n_metrics", 0) > 0:
                n_ok += 1
            else:
                n_empty += 1
            if i % args.log_every == 0 or i == len(pending):
                rate = i / max(time.time() - t0, 1e-9)
                print(
                    f"{i}/{len(pending)} ok={n_ok} empty={n_empty} "
                    f"{rate:.1f}/s", flush=True
                )

    consolidate(results_jsonl, parquet_out, ax)
    return 0


def consolidate(results_jsonl: Path, parquet_out: Path, ax) -> int:
    import pandas as pd

    if not results_jsonl.exists():
        return 0
    recs = [
        json.loads(l)
        for l in results_jsonl.read_text(encoding="utf-8").splitlines()
        if l.strip()
    ]
    df = pd.DataFrame(recs)
    id_cols = ["ticker", "filing_date", "fiscal_year", "doc_id", "n_metrics"]
    metric_cols = [c for c in ax.CANONICAL_METRICS if c in df.columns]
    df = df[[c for c in id_cols if c in df.columns] + metric_cols]
    df.to_parquet(parquet_out, index=False)
    print(f"consolidated {len(df)} rows -> {parquet_out}", flush=True)
    return len(df)


if __name__ == "__main__":
    raise SystemExit(main())
