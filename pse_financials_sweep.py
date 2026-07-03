#!/usr/bin/env python3
"""Sweep PSE EDGE (Philippines) annual-report PDFs -> canonical financials.

Small, sparse market (~447 PDFs, English / PFRS≈IFRS) — reuses the ASX
pdftotext+regex extractor. Filenames are descriptive rather than structured
(e.g. 'Apr_15_2025_AEV_SEC_FORM_17-A_..._FY25.pdf'), so ticker/year are parsed
best-effort. Resume-safe; consolidates to parquet. First-pass quality (like ASX).
"""
from __future__ import annotations
import argparse, json, re, threading, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import asx_financials_extract as ax

RAW_ROOT = Path("/Volumes/OWC Express 1M2/datasets/MARKET_FILINGS/raw/PSE_EDGE")
OUT_DIR = Path("/Volumes/OWC Express 1M2/datasets/MARKET_FILINGS/derived/PSE_FINANCIALS")
RESULTS_JSONL = OUT_DIR / "sweep_results.jsonl"
PARQUET_OUT = OUT_DIR / "canonical_metrics_wide.parquet"

_MONTHS = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}
_DATE = re.compile(r"^([A-Z][a-z]{2})_(\d{1,2})_(\d{4})_")
_TICKER = re.compile(r"_([A-Z]{2,6})_")          # first all-caps token after the date
_FY = re.compile(r"FY[_ ]?(\d{2,4})|Annual[_ ]Report[_ ](\d{4})|(\d{4})[_ ]Annual", re.IGNORECASE)
_STOPWORDS = {"SEC", "FORM", "PSE", "PDF", "AND", "THE", "FY"}
_lock = threading.Lock()


def _parse(name: str) -> dict:
    filing_date, fy, ticker = None, None, None
    d = _DATE.match(name)
    if d and d.group(1) in _MONTHS:
        filing_date = f"{d.group(3)}{_MONTHS[d.group(1)]:02d}{int(d.group(2)):02d}"
    for tok in _TICKER.findall(name):
        if tok not in _STOPWORDS:
            ticker = tok
            break
    f = _FY.search(name)
    if f:
        yy = next(g for g in f.groups() if g)
        fy = int(yy) if len(yy) == 4 else 2000 + int(yy)
    elif filing_date:
        fy = int(filing_date[:4]) - 1     # 17-A filed year N covers FY N-1
    return {"ticker": ticker, "filing_date": filing_date, "fiscal_year": fy}


def build_targets(raw_root: Path) -> list[dict]:
    out = []
    for pdf in raw_root.rglob("*.pdf"):
        meta = _parse(pdf.name)
        out.append({**meta, "doc_id": pdf.stem[:80], "pdf_path": str(pdf)})
    return out


def load_done(results: Path) -> set[str]:
    done = set()
    if results.exists():
        for line in results.read_text(encoding="utf-8").splitlines():
            try:
                done.add(json.loads(line)["pdf_path"])
            except (json.JSONDecodeError, KeyError):
                pass
    return done


def process(t: dict) -> dict:
    m = ax.extract_pdf(t["pdf_path"])
    return {"ticker": t["ticker"], "filing_date": t["filing_date"],
            "fiscal_year": t["fiscal_year"], "pdf_path": t["pdf_path"],
            "n_metrics": len(m), **m}


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
    id_cols = ["ticker", "filing_date", "fiscal_year", "n_metrics"]
    metric_cols = [c for c in ax.CANONICAL_METRICS if c in df.columns]
    df = df[[c for c in id_cols if c in df.columns] + metric_cols]
    df.to_parquet(PARQUET_OUT, index=False)
    return len(df)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--limit", type=int, default=0)
    args = p.parse_args()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    targets = build_targets(RAW_ROOT)
    done = load_done(RESULTS_JSONL)
    pending = [t for t in targets if t["pdf_path"] not in done]
    if args.limit:
        pending = pending[: args.limit]
    print(f"targets={len(targets)} done={len(done)} pending={len(pending)}", flush=True)
    n_ok = 0
    t0 = time.time()

    def _run(t):
        try:
            rec = process(t)
        except Exception as exc:
            rec = {"pdf_path": t["pdf_path"], "n_metrics": 0, "status": f"error:{type(exc).__name__}"}
        append_result(rec)
        return rec

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        for i, fut in enumerate(as_completed([ex.submit(_run, t) for t in pending]), 1):
            if fut.result().get("n_metrics", 0) > 0:
                n_ok += 1
    n = consolidate()
    print(f"DONE. with_metrics={n_ok}/{len(pending)} in {time.time()-t0:.0f}s. "
          f"parquet rows={n} -> {PARQUET_OUT}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
