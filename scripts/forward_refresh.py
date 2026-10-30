#!/usr/bin/env python
"""Forward-date refresh driver around the EXISTING download_market_filings.py.

The historical backfill (backfill_market_filings.py) walks newest->oldest and its
state files only ever move backwards, so nothing re-runs DISCOVERY for periods
after the newest date on disk.  This thin driver re-uses the existing per-market
collectors unchanged and adds only:

  * a forward date range, run in fixed-size chunks (resumable via a state file),
  * a global >=1 s spacing between HTTP GETs (protocol: <=1 req/s per host),
  * --dry-run: discovery only, no download, no metadata write; prints NEW docs
    (not already in MARKET_FILINGS_METADATA.csv) per chunk,
  * progress/finish reporting into ~/job_tracker.

Usage:
  python scripts/forward_refresh.py --market edinet --start 2026-04-25 --end 2026-10-28 --dry-run
  python scripts/forward_refresh.py --market edinet --start ... --end ... --job-id t37_edinet
"""
import argparse
import copy
import csv
import json
import os
import subprocess
import sys
import time
from datetime import date, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
import requests  # noqa: E402

DATA_ROOT = Path("/Volumes/OWC Express 1M2/datasets/MARKET_FILINGS")
META = DATA_ROOT / "metadata" / "MARKET_FILINGS_METADATA.csv"


def set_data_root(root):
    """Point raw/ and metadata/ at another root (e.g. a boot-volume staging corpus)."""
    global DATA_ROOT, META
    DATA_ROOT = Path(root)
    META = DATA_ROOT / "metadata" / "MARKET_FILINGS_METADATA.csv"

TRACKER = Path.home() / "job_tracker" / "cli.py"
MIN_INTERVAL = 1.0
_last = [0.0]


def throttle_patch():
    orig = requests.Session.get

    def get(self, *a, **k):
        wait = MIN_INTERVAL - (time.time() - _last[0])
        if wait > 0:
            time.sleep(wait)
        try:
            return orig(self, *a, **k)
        finally:
            _last[0] = time.time()

    requests.Session.get = get


def known_ids(market):
    csv.field_size_limit(10**9)
    ids = set()
    if not META.exists():
        return ids
    with open(META, newline="") as f:
        for r in csv.DictReader(f):
            if r["market"].upper() == market.upper():
                ids.add(r["filing_id"])
    return ids


def tracker(*args):
    try:
        subprocess.run([sys.executable, str(TRACKER), *args], check=False,
                       stdout=subprocess.DEVNULL)
    except OSError:
        pass


def chunks(start, end, days):
    cur = start
    while cur <= end:
        yield cur, min(end, cur + timedelta(days=days - 1))
        cur += timedelta(days=days)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", required=True)
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--chunk-days", type=int, default=7)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--job-id")
    ap.add_argument("--state", help="resume state json")
    ap.add_argument("--data-root", help="override corpus root (raw/ + metadata/); default OWC MARKET_FILINGS")
    args = ap.parse_args()
    if args.data_root:
        set_data_root(args.data_root)

    throttle_patch()
    import download_market_filings as dmf

    cfg = dmf.load_config(str(REPO / "market_filings_config.json"))
    mcfg = copy.deepcopy(cfg["markets"][args.market])
    mcfg["enabled"] = True
    mcfg["max_filings"] = 10**6
    mcfg["delay_seconds"] = 0.0 if args.dry_run else 1.0
    timeout = int(cfg.get("request_timeout", 30))
    raw_dir = str(DATA_ROOT / "raw")
    start, end = date.fromisoformat(args.start), date.fromisoformat(args.end)
    known = known_ids(args.market)
    print(f"known {args.market} ids in metadata: {len(known)}", flush=True)

    state = {}
    if args.state and Path(args.state).exists():
        state = json.loads(Path(args.state).read_text())
    done_through = date.fromisoformat(state["done_through"]) if state.get("done_through") else None
    total_new = state.get("new_docs", 0)
    acquired = state.get("acquired", 0)

    try:
        for cs, ce in chunks(start, end, args.chunk_days):
            if done_through and ce <= done_through:
                continue
            mc = dict(mcfg, start_date=cs.isoformat(), end_date=ce.isoformat())
            rows = dmf.collect_market(args.market, mc, raw_dir,
                                      download_documents=not args.dry_run, timeout=timeout)
            new = [r for r in rows if r.filing_id not in known]
            got = [r for r in new if r.local_path]
            total_new += len(new)
            acquired += len(got)
            print(f"{cs}..{ce}: listed {len(rows)} new {len(new)} acquired {len(got)}", flush=True)
            if not args.dry_run:
                failed = [r for r in new if not r.local_path]
                if new:
                    dmf.write_metadata(str(META), [dmf.asdict(r) for r in new])
                if failed:
                    print(f"WARN {len(failed)} downloads failed in chunk; rows kept with empty "
                          f"local_path for rescue_missing_documents", flush=True)
                known.update(r.filing_id for r in new)
                if args.job_id:
                    tracker("update", "--id", args.job_id, "--current", str(acquired),
                            "--details", f"through {ce}; new {total_new}")
            if args.state:
                state.update(done_through=ce.isoformat(), new_docs=total_new, acquired=acquired)
                Path(args.state).write_text(json.dumps(state))
        print(f"DONE total_new={total_new} acquired={acquired}", flush=True)
        if args.job_id and not args.dry_run:
            tracker("done", "--id", args.job_id)
    except BaseException as exc:  # noqa: BLE001
        print(f"FAILED {exc!r}", flush=True)
        if args.job_id and not args.dry_run:
            tracker("fail", "--id", args.job_id, "--reason",
                    f"{exc!r}; resume with same command (state file keeps done_through)")
        raise


if __name__ == "__main__":
    main()
