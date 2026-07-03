#!/usr/bin/env python3
"""Sweep OpenDART fnlttSinglAcntAll for all DART annual filings -> canonical
three-statement parquet. Korea's structured backfill (CN/CSMAR-equivalent),
replacing the broken text extractor.

For each unique (corp_code, bsns_year): fetch CFS (consolidated); if that has no
structured data, fall back to OFS (separate); map XBRL tags -> canonical metrics.

Resume-safe: appends one JSON line per company-year to results.jsonl and skips
those on re-run. Stops gracefully if OpenDART's daily 20k-call cap (status 020)
is hit — just re-run tomorrow to finish.

Usage:
    python3 dart_financials_sweep.py                 # full sweep
    python3 dart_financials_sweep.py --limit 50      # smoke test
    python3 dart_financials_sweep.py --workers 4
"""
from __future__ import annotations
import argparse
import csv
import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import dart_financials_api as api

MANIFEST = Path("/Volumes/OWC Express 1M2/datasets/MARKET_FILINGS/derived/"
                "DART_FINANCIALS/dart_local_manifest.csv")
OUT_DIR = Path("/Volumes/OWC Express 1M2/datasets/MARKET_FILINGS/derived/"
               "DART_FINANCIALS/three_statement_parquet")
RESULTS_JSONL = OUT_DIR / "sweep_results.jsonl"
PARQUET_OUT = OUT_DIR / "canonical_metrics_wide.parquet"

_lock = threading.Lock()
_stop = threading.Event()   # set on rate-limit -> drain in-flight, stop scheduling


def load_key() -> str:
    k = os.environ.get("DART_API_KEY", "")
    if not k:
        env = Path(__file__).with_name(".env")
        if env.exists():
            for line in env.read_text().splitlines():
                if line.startswith("DART_API_KEY="):
                    k = line.split("=", 1)[1].strip()
    if not k:
        raise SystemExit("DART_API_KEY not set (env or edgar-crawler/.env)")
    return k


def build_targets(manifest: Path) -> list[dict]:
    """Unique (corp_code, bsns_year) targets from the DART manifest. Multiple
    filings of the same company-year collapse to one API call."""
    seen: dict[tuple, dict] = {}
    with manifest.open(encoding="utf-8", errors="replace") as fin:
        for row in csv.DictReader(fin):
            if row.get("market") != "DART":
                continue
            try:
                rm = json.loads(row.get("raw_metadata") or "{}")
            except json.JSONDecodeError:
                rm = {}
            corp = (rm.get("corp_code") or "").strip()
            year = api.parse_fiscal_year(rm.get("report_nm") or row.get("title"))
            if not corp or not year:
                continue
            key = (corp, year)
            if key not in seen:
                seen[key] = {
                    "corp_code": corp,
                    "stock_code": (rm.get("stock_code") or row.get("stock_code") or "").strip(),
                    "company_name": (rm.get("corp_name") or row.get("company_name") or "").strip(),
                    "bsns_year": year,
                }
    return list(seen.values())


def load_done(results: Path) -> set[tuple]:
    done: set[tuple] = set()
    if results.exists():
        for line in results.read_text(encoding="utf-8").splitlines():
            try:
                d = json.loads(line)
                done.add((d["corp_code"], d["bsns_year"]))
            except (json.JSONDecodeError, KeyError):
                continue
    return done


def process(target: dict, key: str, timeout: int = 45,
            retries: int = 4, backoff: float = 2.0) -> dict:
    """Fetch CFS then OFS fallback; map to canonical. Returns a result record."""
    corp, year = target["corp_code"], target["bsns_year"]
    fs_used, status, metrics = None, None, {}
    for fs in ("CFS", "OFS"):
        rows, status = api.fetch_accounts(corp, year, key, fs_div=fs,
                                          timeout=timeout, retries=retries, backoff=backoff)
        if status == "020":              # daily cap hit
            _stop.set()
            return {**target, "status": "020_rate_limited", "fs_div": None, "n_metrics": 0}
        if rows:
            fs_used = fs
            metrics = api.map_to_canonical(rows)
            break
    return {**target, "status": status, "fs_div": fs_used,
            "n_metrics": len(metrics), **metrics}


def append_result(rec: dict) -> None:
    with _lock:
        with RESULTS_JSONL.open("a", encoding="utf-8") as fout:
            fout.write(json.dumps(rec, ensure_ascii=False) + "\n")


def consolidate() -> int:
    """results.jsonl -> canonical_metrics_wide.parquet. Returns row count."""
    import pandas as pd
    if not RESULTS_JSONL.exists():
        return 0
    recs = [json.loads(l) for l in RESULTS_JSONL.read_text(encoding="utf-8").splitlines() if l.strip()]
    df = pd.DataFrame(recs)
    id_cols = ["corp_code", "stock_code", "company_name", "bsns_year", "fs_div", "status", "n_metrics"]
    metric_cols = [c for c in api.CANONICAL_METRICS if c in df.columns]
    df = df[[c for c in id_cols if c in df.columns] + metric_cols]
    df.to_parquet(PARQUET_OUT, index=False)
    return len(df)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--limit", type=int, default=0, help="cap targets (smoke test)")
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--pace", type=float, default=0.05, help="sleep per task (politeness)")
    p.add_argument("--log-every", type=int, default=100)
    p.add_argument("--timeout", type=int, default=15, help="per-call timeout (fail hangs fast)")
    p.add_argument("--retries", type=int, default=3)
    p.add_argument("--backoff", type=float, default=1.0)
    args = p.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    key = load_key()
    targets = build_targets(MANIFEST)
    done = load_done(RESULTS_JSONL)
    pending = [t for t in targets if (t["corp_code"], t["bsns_year"]) not in done]
    if args.limit:
        pending = pending[: args.limit]
    print(f"targets={len(targets)} done={len(done)} pending={len(pending)} "
          f"workers={args.workers}", flush=True)

    n_ok = n_empty = n_err = 0
    t0 = time.time()

    def _run(t):
        if _stop.is_set():
            return None
        try:
            rec = process(t, key, timeout=args.timeout, retries=args.retries, backoff=args.backoff)
        except Exception as exc:            # log-and-skip (never halt the sweep)
            rec = {**t, "status": f"error:{type(exc).__name__}", "fs_div": None, "n_metrics": 0}
        append_result(rec)
        time.sleep(args.pace)
        return rec

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futures = {ex.submit(_run, t): t for t in pending}
        for i, fut in enumerate(as_completed(futures), 1):
            rec = fut.result()
            if rec is None:
                continue
            if rec.get("n_metrics", 0) > 0:
                n_ok += 1
            elif str(rec.get("status", "")).startswith(("error", "020")):
                n_err += 1
            else:
                n_empty += 1
            if i % args.log_every == 0 or _stop.is_set():
                rate = i / max(1e-9, time.time() - t0)
                print(f"[{i}/{len(pending)}] ok={n_ok} empty={n_empty} err={n_err} "
                      f"{rate:.1f}/s", flush=True)
            if _stop.is_set():
                print("RATE LIMITED (status 020) — stopping; re-run tomorrow to resume.", flush=True)
                break

    n = consolidate()
    print(f"DONE. ok={n_ok} empty={n_empty} err/limited={n_err}. "
          f"parquet rows={n} -> {PARQUET_OUT}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
