#!/usr/bin/env python3
"""One entry point for the corpus pipeline.

Nine scripts now cover acquisition, stage-02 sweeps, screening inputs and the
panel. This wraps them so the common questions have one answer each:

    python scripts/corpus.py status          where does every market stand
    python scripts/corpus.py next            what is ready to progress, and how
    python scripts/corpus.py stages          per-market stage completion grid
    python scripts/corpus.py jobs            running jobs, with liveness checked

`status` and `next` are read-only. Nothing here launches long jobs — it prints
the exact command to run, so the decision stays with a human who can see the
disk and rate-limit situation.

Practices behind these checks: docs/corpus_pipeline_practices.md
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

REPO = Path(__file__).resolve().parents[1]
DATASETS = Path("/Volumes/OWC Express 1M2/datasets")
MARKET_FILINGS = DATASETS / "MARKET_FILINGS"
METADATA = MARKET_FILINGS / "metadata" / "MARKET_FILINGS_METADATA.csv"
DERIVED = MARKET_FILINGS / "derived"
TRACKER_STATE = Path.home() / "job_tracker" / "state"
STALE_HOURS = 6

# market -> (raw corpus key in metadata, derived dir, sweep market name)
STAGE_MAP = {
    "ASX": ("ASX_FINANCIALS", "asx"),
    "TWSE_REPORTS": ("TWSE_FINANCIALS", None),
    "PSE_EDGE": ("PSE_FINANCIALS", None),
    "DART": ("DART_FINANCIALS", None),
    "IN_BSE_ANNUAL_REPORTS": ("IN_BSE_FINANCIALS", "in_bse"),
    "NO_OSLO_NEWSWEB": ("NO_OSLO_FINANCIALS", "no_oslo"),
    "NASDAQ_NORDIC_BALTIC_NEWS": ("NORDIC_FINANCIALS", "nordic"),
    "EDGAR_IE": (None, None),
    "EDGAR_DE": (None, None),
    "HKEX": (None, None),
}


def _fmt(n) -> str:
    return f"{n:,}" if isinstance(n, int) else str(n)


def acquisition_coverage() -> List[dict]:
    """Rows discovered vs rows carrying a local_path, worst gap first.

    'Complete' means acquired, not discovered -- see practices §1.1.
    """
    if not METADATA.exists():
        return []
    csv.field_size_limit(sys.maxsize)
    rows, have = Counter(), Counter()
    with open(METADATA, newline="", encoding="utf-8") as fin:
        for row in csv.DictReader(fin):
            market = row.get("market") or "UNKNOWN"
            rows[market] += 1
            if (row.get("local_path") or "").strip():
                have[market] += 1
    out = []
    for market, total in rows.items():
        got = have[market]
        out.append({
            "market": market, "discovered": total, "acquired": got,
            "missing": total - got,
            "pct": (got / total * 100) if total else 0.0,
        })
    out.sort(key=lambda r: (-r["missing"], r["market"]))
    return out


def stage02_state(derived_name: Optional[str]) -> Dict[str, object]:
    if not derived_name:
        return {"present": False, "rows": 0, "note": "no stage-02 path known"}
    d = DERIVED / derived_name
    parquet = d / "canonical_metrics_wide.parquet"
    jsonl = d / "sweep_results.jsonl"
    facts = d / "facts"
    rows = 0
    if jsonl.exists():
        try:
            rows = sum(1 for _ in open(jsonl, "rb"))
        except OSError:
            rows = 0
    elif facts.is_dir():
        rows = sum(1 for _ in facts.iterdir())
    return {
        "present": parquet.exists() or jsonl.exists() or facts.is_dir(),
        "rows": rows,
        "parquet": parquet.exists(),
        "screening": any(d.glob("*_screening_input.csv")) if d.is_dir() else False,
        "gates": any(d.glob("*_gates")) if d.is_dir() else False,
    }


def tracker_jobs() -> List[dict]:
    """Running jobs, with staleness and pid liveness both checked (§3.4)."""
    out = []
    if not TRACKER_STATE.exists():
        return out
    for path in sorted(TRACKER_STATE.glob("*.json")):
        try:
            job = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        tags = [str(t).lower() for t in (job.get("tags") or [])]
        if not any(t in tags for t in ("corpus", "rescue", "edgar", "stage02", "hk")):
            continue
        pid = job.get("pid")
        alive = None
        if pid:
            try:
                os.kill(int(pid), 0)
                alive = True
            except (ProcessLookupError, ValueError, TypeError):
                alive = False
            except PermissionError:
                alive = True
        age_h = None
        ts = job.get("updated_at")
        if ts:
            try:
                when = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
                if when.tzinfo is None:
                    when = when.replace(tzinfo=timezone.utc)
                age_h = (datetime.now(timezone.utc) - when).total_seconds() / 3600
            except ValueError:
                pass
        job["_alive"] = alive
        job["_age_h"] = age_h
        job["_suspect"] = (
            job.get("status") == "running"
            and (alive is False or (age_h is not None and age_h > STALE_HOURS))
        )
        out.append(job)
    return out


def cmd_status(_args) -> int:
    cov = acquisition_coverage()
    total_d = sum(r["discovered"] for r in cov)
    total_a = sum(r["acquired"] for r in cov)
    print(f"ACQUISITION  {_fmt(total_a)} of {_fmt(total_d)} documents "
          f"({total_a / max(total_d,1) * 100:.1f}%)\n")
    print(f"  {'market':30} {'discovered':>11} {'acquired':>10} {'missing':>9}  cov")
    for r in cov:
        flag = "  <-- gap" if r["missing"] else ""
        print(f"  {r['market'][:30]:30} {_fmt(r['discovered']):>11} "
              f"{_fmt(r['acquired']):>10} {_fmt(r['missing']):>9}  {r['pct']:5.1f}%{flag}")
    return 0


def cmd_stages(_args) -> int:
    print(f"  {'market':30} {'01_raw':>10} {'02':>10} {'screening':>10} {'gates':>7}")
    for market, (derived, _sweep) in STAGE_MAP.items():
        cov = next((r for r in acquisition_coverage() if r["market"] == market), None)
        raw = _fmt(cov["acquired"]) if cov else "-"
        s2 = stage02_state(derived)
        print(f"  {market[:30]:30} {raw:>10} "
              f"{(_fmt(s2['rows']) if s2['present'] else 'none'):>10} "
              f"{('yes' if s2.get('screening') else 'no'):>10} "
              f"{('yes' if s2.get('gates') else 'no'):>7}")
    return 0


def cmd_next(_args) -> int:
    """What is ready to progress, and the exact command."""
    actions: List[str] = []
    cov = {r["market"]: r for r in acquisition_coverage()}

    for market, (derived, sweep) in STAGE_MAP.items():
        c = cov.get(market)
        if c and c["missing"] > 0:
            actions.append(
                f"[acquire] {market}: {_fmt(c['missing'])} documents missing "
                f"({c['pct']:.1f}% acquired)\n"
                f"          python scripts/rescue_missing_documents.py --market {market}"
            )
            continue
        if not derived:
            continue
        s2 = stage02_state(derived)
        if not s2["present"] and sweep:
            actions.append(
                f"[stage02] {market}: no stage-02 output\n"
                f"          python scripts/pdf_market_sweep.py --market {sweep} --workers 4"
            )
        elif s2["present"] and not s2.get("screening"):
            short = {"IN_BSE_FINANCIALS": "in", "ASX_FINANCIALS": "au"}.get(derived, "?")
            actions.append(
                f"[stage03] {market}: stage-02 done ({_fmt(s2['rows'])} rows), no screening input\n"
                f"          python build_pdf_market_screening_input.py {short}"
            )

    for job in tracker_jobs():
        if job.get("_suspect"):
            why = "pid dead" if job.get("_alive") is False else f"silent {job['_age_h']:.0f}h"
            actions.append(
                f"[tracker] {job.get('id')}: claims running but {why} — reconcile before trusting it"
            )

    if not actions:
        print("nothing ready to progress")
        return 0
    print("READY TO PROGRESS\n")
    for a in actions:
        print(f"  {a}\n")
    return 0


def cmd_jobs(_args) -> int:
    jobs = tracker_jobs()
    if not jobs:
        print("no corpus jobs registered")
        return 0
    print(f"  {'job':26} {'status':9} {'progress':>16} {'age(h)':>7}  pid")
    for j in jobs:
        p = j.get("progress") or {}
        prog = f"{p.get('current')}/{p.get('total')}"
        alive = {True: "alive", False: "DEAD", None: "-"}[j.get("_alive")]
        flag = "  <-- SUSPECT" if j.get("_suspect") else ""
        age = f"{j['_age_h']:.1f}" if j.get("_age_h") is not None else "-"
        print(f"  {str(j.get('id'))[:26]:26} {str(j.get('status')):9} "
              f"{prog:>16} {age:>7}  {j.get('pid')} {alive}{flag}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", help="acquisition coverage per market")
    sub.add_parser("stages", help="per-market stage completion grid")
    sub.add_parser("next", help="what is ready to progress, with commands")
    sub.add_parser("jobs", help="registered jobs with liveness checked")
    args = parser.parse_args()
    return {
        "status": cmd_status, "stages": cmd_stages,
        "next": cmd_next, "jobs": cmd_jobs,
    }[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
