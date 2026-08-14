#!/usr/bin/env python3
"""Durable resume ledger for the corpus program.

Answers one question after any interruption -- reboot, crash, a session
ending, a month of neglect: **where do we pick up?**

Existing surfaces each answer part of it and none survive alone:

- The job queue reports `complete=true` on *discovery*, so it says "done"
  while 16% of documents are on disk.
- The menubar job tracker shows live jobs, but entries go stale (a dead
  Nordic job displayed "running" for 34 days) and it holds no next-action.
- `/tmp/job_status/*.json` mirrors are erased by every reboot.
- Per-job state files know their own cursor and nothing about the program.

The ledger is written to the data root (durable, travels with the corpus)
as both JSON (for tooling) and Markdown (for humans and for pasting into a
new session). It is derived state: safe to delete and regenerate.

Usage:
    python scripts/corpus_ledger.py --data-root "<root>"          # refresh
    python scripts/corpus_ledger.py --data-root "<root>" --print  # show
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


REPO_DIR = Path(__file__).resolve().parents[1]
DEFAULT_DATA_ROOT = Path("/Volumes/OWC Express 1M2/datasets/MARKET_FILINGS")
TRACKER_STATE_DIR = Path.home() / "job_tracker" / "state"
STALE_AFTER_HOURS = 6


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def hours_since(timestamp: Optional[str]) -> Optional[float]:
    if not timestamp:
        return None
    text = str(timestamp).replace("Z", "+00:00")
    try:
        when = datetime.fromisoformat(text)
    except ValueError:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - when).total_seconds() / 3600.0


def is_stale(job: Dict[str, Any], stale_after_hours: float = STALE_AFTER_HOURS) -> bool:
    """A job claiming to run but silent for hours is not actually running.

    The Nordic backfill displayed `running` for 34 days after its process
    died. Freshness of the last update is the only honest signal.
    """
    if job.get("status") != "running":
        return False
    age = hours_since(job.get("updated_at"))
    return age is not None and age > stale_after_hours


def process_alive(pid: Optional[int]) -> Optional[bool]:
    """True/False if determinable, None when there is no pid to check."""
    if not pid:
        return None
    try:
        import os

        os.kill(int(pid), 0)
        return True
    except (ProcessLookupError, ValueError, TypeError):
        return False
    except PermissionError:
        return True
    except OSError:
        return None


def coverage_from_metadata(metadata_path: Path) -> List[Dict[str, Any]]:
    """Per-market acquisition coverage, worst gap first."""
    sys.path.insert(0, str(REPO_DIR))
    from scripts.market_filings_snapshot import coverage_rows, metadata_counts

    return coverage_rows(metadata_counts(metadata_path))


def read_tracker_jobs(state_dir: Path = TRACKER_STATE_DIR) -> List[Dict[str, Any]]:
    jobs = []
    if not state_dir.exists():
        return jobs
    for path in sorted(state_dir.glob("*.json")):
        try:
            jobs.append(json.loads(path.read_text()))
        except (OSError, ValueError):
            continue
    return jobs


def relevant_jobs(jobs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Corpus jobs only -- skip unrelated work sharing the tracker."""
    out = []
    for job in jobs:
        tags = [str(t).lower() for t in (job.get("tags") or [])]
        blob = f"{job.get('id','')} {job.get('title','')}".lower()
        if "corpus" in tags or "rescue" in tags or "market_filings" in blob or "backfill" in blob:
            out.append(job)
    return out


def next_actions(coverage: List[Dict[str, Any]], jobs: List[Dict[str, Any]]) -> List[str]:
    """The point of the ledger: what to do next, in priority order."""
    actions = []
    for job in jobs:
        if is_stale(job):
            alive = process_alive(job.get("pid"))
            if alive is False:
                actions.append(
                    f"STALE: `{job.get('id')}` shows running but pid "
                    f"{job.get('pid')} is dead -- mark failed and resume it"
                )
    for row in coverage:
        if row["missing"] > 0:
            actions.append(
                f"{row['market']}: {row['missing']} documents missing "
                f"({row['pct']:.1f}% acquired) -- run the rescue driver"
            )
    if not actions:
        actions.append("No gaps detected; nothing queued.")
    return actions


def build_ledger(data_root: Path) -> Dict[str, Any]:
    metadata_path = data_root / "metadata" / "MARKET_FILINGS_METADATA.csv"
    coverage = coverage_from_metadata(metadata_path) if metadata_path.exists() else []
    jobs = relevant_jobs(read_tracker_jobs())
    annotated = []
    for job in jobs:
        annotated.append({
            "id": job.get("id"),
            "title": job.get("title"),
            "status": job.get("status"),
            "stale": is_stale(job),
            "pid": job.get("pid"),
            "pid_alive": process_alive(job.get("pid")),
            "updated_at": job.get("updated_at"),
            "hours_since_update": round(hours_since(job.get("updated_at")) or 0, 1),
            "progress": job.get("progress"),
            "log_path": job.get("log_path"),
        })
    return {
        "generated_at": utc_now(),
        "data_root": str(data_root),
        "coverage": coverage,
        "jobs": annotated,
        "next_actions": next_actions(coverage, jobs),
        "totals": {
            "rows": sum(r["rows"] for r in coverage),
            "acquired": sum(r["acquired"] for r in coverage),
            "missing": sum(r["missing"] for r in coverage),
        },
    }


def render_markdown(ledger: Dict[str, Any]) -> str:
    totals = ledger["totals"]
    lines = [
        "# Corpus Resume Ledger",
        "",
        "**Where to pick up after any interruption.** Regenerate with:",
        "",
        "```",
        'python scripts/corpus_ledger.py --data-root "<data root>"',
        "```",
        "",
        f"- Generated: `{ledger['generated_at']}`",
        f"- Data root: `{ledger['data_root']}`",
        f"- Documents: `{totals['acquired']}` acquired of `{totals['rows']}` "
        f"discovered (`{totals['missing']}` missing)",
        "",
        "## Next actions",
        "",
    ]
    for action in ledger["next_actions"]:
        lines.append(f"1. {action}")
    lines.extend([
        "",
        "## Jobs",
        "",
        "A job is only running if its last update is recent AND its pid is alive.",
        "",
        "| Job | Status | Updated (h ago) | pid alive | Progress |",
        "| --- | --- | ---: | --- | --- |",
    ])
    for job in ledger["jobs"]:
        progress = job.get("progress") or {}
        current, total = progress.get("current", "?"), progress.get("total", "?")
        status = job.get("status")
        if job.get("stale"):
            status = f"{status} :warning: STALE"
        lines.append(
            f"| `{job['id']}` | {status} | {job['hours_since_update']} | "
            f"{job['pid_alive']} | {current}/{total} |"
        )
    lines.extend(["", "## Coverage by market", "",
                  "| Market | Discovered | Acquired | Missing | Coverage |",
                  "| --- | ---: | ---: | ---: | ---: |"])
    for row in ledger["coverage"]:
        flag = " :warning:" if row["missing"] else ""
        lines.append(
            f"| `{row['market']}` | {row['rows']} | {row['acquired']} | "
            f"{row['missing']}{flag} | {row['pct']:.1f}% |"
        )
    lines.append("")
    return "\n".join(lines)


def write_ledger(data_root: Path, ledger: Dict[str, Any]) -> Dict[str, Path]:
    out_dir = data_root / "monitoring"
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "RESUME_LEDGER.json"
    md_path = out_dir / "RESUME_LEDGER.md"
    json_path.write_text(json.dumps(ledger, indent=2, sort_keys=True))
    md_path.write_text(render_markdown(ledger))
    return {"json": json_path, "markdown": md_path}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)
    parser.add_argument("--print", action="store_true", dest="show")
    args = parser.parse_args()

    ledger = build_ledger(args.data_root)
    paths = write_ledger(args.data_root, ledger)
    if args.show:
        print(render_markdown(ledger))
    else:
        print(f"wrote {paths['markdown']}")
        print(f"wrote {paths['json']}")
        for action in ledger["next_actions"][:5]:
            print(f"- {action}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
