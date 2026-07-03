#!/usr/bin/env python3
"""Sweep ASX annual-report PDFs -> canonical financials parquet.

Walks the ASX raw corpus, runs the pdftotext+regex extractor on each PDF, and
writes canonical metrics. Resume-safe (results.jsonl), and supports splitting
work by --years (for distributing across machines) or --limit (small batches).

Usage:
    python3 asx_financials_sweep.py --limit 25            # small proof batch
    python3 asx_financials_sweep.py --years 2008,2009,2010 --workers 4
    python3 asx_financials_sweep.py --raw-root /workspace/ASX   # on a remote pod
"""
from __future__ import annotations
import argparse
import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import asx_financials_extract as ax

RAW_ROOT = Path("/Volumes/OWC Express 1M2/datasets/MARKET_FILINGS/raw/ASX")
OUT_DIR = Path("/Volumes/OWC Express 1M2/datasets/MARKET_FILINGS/derived/ASX_FINANCIALS")
RESULTS_JSONL = OUT_DIR / "sweep_results.jsonl"
PARQUET_OUT = OUT_DIR / "canonical_metrics_wide.parquet"

# {TICKER}_{YYYYMMDD}_{announcement_id}.pdf
_FNAME = re.compile(r"^([A-Z0-9]+)_(\d{8})_(.+)\.pdf$")
_lock = threading.Lock()


def _fiscal_year(yyyymmdd: str) -> int:
    """Australian FY ends 30 Jun; annual reports file Aug–Nov. Report filed in
    H2 of year Y -> FY Y; filed in H1 -> FY Y-1 (late / Dec-end approximation)."""
    y, mo = int(yyyymmdd[:4]), int(yyyymmdd[4:6])
    return y if mo >= 7 else y - 1


def build_targets(raw_root: Path, years: set[str] | None) -> list[dict]:
    out: list[dict] = []
    for pdf in raw_root.rglob("*.pdf"):
        m = _FNAME.match(pdf.name)
        if not m:
            continue
        ticker, date, doc_id = m.group(1), m.group(2), m.group(3)
        if years and date[:4] not in years:
            continue
        out.append({"ticker": ticker, "filing_date": date, "doc_id": doc_id,
                    "fiscal_year": _fiscal_year(date), "pdf_path": str(pdf)})
    return out


def load_done(results: Path) -> set[str]:
    done: set[str] = set()
    if results.exists():
        for line in results.read_text(encoding="utf-8").splitlines():
            try:
                done.add(json.loads(line)["doc_id"])
            except (json.JSONDecodeError, KeyError):
                continue
    return done


def process(target: dict) -> dict:
    metrics = ax.extract_pdf(target["pdf_path"])
    return {"ticker": target["ticker"], "filing_date": target["filing_date"],
            "doc_id": target["doc_id"], "fiscal_year": target["fiscal_year"],
            "n_metrics": len(metrics), **metrics}


def append_result(rec: dict) -> None:
    with _lock:
        with RESULTS_JSONL.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def consolidate() -> int:
    import pandas as pd
    if not RESULTS_JSONL.exists():
        return 0
    recs = [json.loads(l) for l in RESULTS_JSONL.read_text(encoding="utf-8").splitlines() if l.strip()]
    df = pd.DataFrame(recs)
    id_cols = ["ticker", "filing_date", "fiscal_year", "doc_id", "n_metrics"]
    metric_cols = [c for c in ax.CANONICAL_METRICS if c in df.columns]
    df = df[[c for c in id_cols if c in df.columns] + metric_cols]
    df.to_parquet(PARQUET_OUT, index=False)
    return len(df)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--raw-root", default=str(RAW_ROOT))
    p.add_argument("--years", default="", help="comma-separated year prefixes to include (split)")
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--log-every", type=int, default=100)
    args = p.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    years = set(y.strip() for y in args.years.split(",") if y.strip()) or None
    targets = build_targets(Path(args.raw_root), years)
    done = load_done(RESULTS_JSONL)
    pending = [t for t in targets if t["doc_id"] not in done]
    if args.limit:
        pending = pending[: args.limit]
    print(f"targets={len(targets)} done={len(done)} pending={len(pending)} "
          f"years={sorted(years) if years else 'ALL'} workers={args.workers}", flush=True)

    n_ok = n_empty = 0
    t0 = time.time()

    def _run(t):
        try:
            rec = process(t)
        except Exception as exc:
            rec = {"ticker": t["ticker"], "doc_id": t["doc_id"],
                   "fiscal_year": t["fiscal_year"], "n_metrics": 0,
                   "status": f"error:{type(exc).__name__}"}
        append_result(rec)
        return rec

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futures = [ex.submit(_run, t) for t in pending]
        for i, fut in enumerate(as_completed(futures), 1):
            rec = fut.result()
            if rec.get("n_metrics", 0) > 0:
                n_ok += 1
            else:
                n_empty += 1
            if i % args.log_every == 0 or i == len(pending):
                rate = i / max(1e-9, time.time() - t0)
                print(f"[{i}/{len(pending)}] with_metrics={n_ok} empty={n_empty} "
                      f"{rate:.1f}/s", flush=True)

    n = consolidate()
    print(f"DONE. with_metrics={n_ok} empty={n_empty}. parquet rows={n} -> {PARQUET_OUT}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
