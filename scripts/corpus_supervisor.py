#!/usr/bin/env python3
"""Start corpus jobs when their preconditions are met, and otherwise sit still.

The pipeline is a dependency chain -- acquire, then stage 02, then screening
input, then gates, then the panel -- and each stage sat idle overnight waiting
for a human to notice the previous one had finished. This closes that gap.

DESIGN CONSTRAINT, from the supervisor this replaces: the annual-financials
supervisor restarts every ~80 seconds with nothing to do. A busy loop on a
saturated external volume burns the I/O the real jobs need and fills the log so
actual events hide in it. This one checks on a slow interval, logs only
transitions, and launches nothing unless something is genuinely ready.

Four rules, each with a test:
  - never launch a job whose preconditions are unmet
  - never launch a job already running (relaunching a live downloader doubles
    the load on a rate-limited host)
  - never re-launch a completed job
  - respect a concurrency cap -- one external drive is the bottleneck

State comes from the job tracker, which is the durable record; this process
holds no state of its own and can be killed and restarted freely.

Usage:
    python scripts/corpus_supervisor.py --once        # evaluate and exit
    python scripts/corpus_supervisor.py --interval 600
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set

REPO = Path(__file__).resolve().parents[1]
TRACKER_STATE = Path.home() / "job_tracker" / "state"
TRACKER_CLI = Path.home() / "job_tracker" / "cli.py"
PY = str(Path.home() / "miniconda3" / "envs" / "python310" / "bin" / "python")
LOGS = REPO / "logs"


@dataclass
class Job:
    id: str
    title: str
    command: Sequence[str]
    needs: List[str] = field(default_factory=list)
    purpose: str = ""
    total: Optional[int] = None
    unit: str = "items"
    # A shell predicate that must succeed for the job to be considered ready.
    # Used for conditions the tracker cannot express, e.g. "the input exists".
    guard: Optional[str] = None
    # The artifact this job produces, and the row count that means it finished.
    # Completion is defined on the ARTIFACT, never on a status flag: Oslo
    # stage-02 wrote 15,972 rows while its tracker entry still read `running`
    # because the sweep has no finish hook, and the whole chain stalled behind
    # that stale flag.
    produces: Optional[str] = None
    min_rows: int = 1


def artifact_satisfied(path: Optional[str], min_rows: int = 1) -> bool:
    """True when the named artifact exists and carries at least min_rows.

    Row count comes from the parquet footer, so this is cheap enough to run on
    every supervisor cycle.
    """
    if not path:
        return False
    target = Path(path)
    if not target.exists():
        return False
    try:
        import pyarrow.parquet as pq
        return pq.ParquetFile(target).metadata.num_rows >= min_rows
    except Exception:
        return target.stat().st_size > 0 and min_rows <= 1


def is_satisfied(job: Job, state: Dict[str, str],
                 jobs: Optional[Sequence["Job"]] = None) -> bool:
    """True when every named predecessor has finished.

    A predecessor counts as finished if the tracker says `done`, OR if it
    declares an artifact and that artifact is present with enough rows. The
    second path exists because a tracker flag is not evidence: Oslo stage-02
    completed 10,889 filings and wrote 15,972 rows while its entry still read
    `running`, and without an artifact check the chain waits forever.

    A FAILED predecessor blocks rather than releases: running a sweep over a
    corpus whose acquisition failed produces confident output from missing
    documents, which is worse than not running at all.
    """
    by_id = {j.id: j for j in (jobs or [])}
    for dep in job.needs:
        if state.get(dep) == "done":
            continue
        if state.get(dep) == "failed":
            return False
        upstream = by_id.get(dep)
        if upstream is not None and artifact_satisfied(upstream.produces,
                                                       upstream.min_rows):
            continue
        return False
    return True


def decide(jobs: Sequence[Job], state: Dict[str, str], running: Set[str],
           cap: int) -> List[str]:
    """Job ids to launch now, in declaration order, honouring the cap."""
    slots = max(cap - len(running), 0)
    out: List[str] = []
    for job in jobs:
        if slots <= 0:
            break
        if job.id in running or state.get(job.id) in ("done", "running"):
            continue
        if not is_satisfied(job, state, jobs):
            continue
        out.append(job.id)
        slots -= 1
    return out


# --- tracker integration -------------------------------------------------


def tracker_state() -> Dict[str, str]:
    out: Dict[str, str] = {}
    if not TRACKER_STATE.exists():
        return out
    for path in TRACKER_STATE.glob("*.json"):
        try:
            job = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        out[str(job.get("id"))] = str(job.get("status"))
    return out


def live_jobs() -> Set[str]:
    """Ids whose tracker pid is actually alive -- a status of `running` is not
    proof; the Nordic backfill read `running` for 34 days after it exited."""
    alive: Set[str] = set()
    for path in TRACKER_STATE.glob("*.json"):
        try:
            job = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if job.get("status") != "running":
            continue
        pid = job.get("pid")
        try:
            os.kill(int(pid), 0)
            alive.add(str(job.get("id")))
        except (TypeError, ValueError, ProcessLookupError):
            continue
        except PermissionError:
            alive.add(str(job.get("id")))
    return alive


def guard_passes(job: Job) -> bool:
    if not job.guard:
        return True
    return subprocess.run(["bash", "-lc", job.guard],
                          capture_output=True).returncode == 0


def launch(job: Job) -> Optional[int]:
    LOGS.mkdir(parents=True, exist_ok=True)
    log = LOGS / f"{job.id}.log"
    with open(log, "ab") as fout:
        proc = subprocess.Popen(list(job.command), cwd=str(REPO),
                                stdout=fout, stderr=subprocess.STDOUT,
                                start_new_session=True)
    subprocess.run([PY, str(TRACKER_CLI), "register", "--id", job.id,
                    "--title", job.title, "--purpose", job.purpose or job.title,
                    "--agent", "corpus_supervisor", "--pid", str(proc.pid),
                    "--log", str(log), "--progress-total", str(job.total or 0),
                    "--progress-unit", job.unit, "--tags", "corpus"],
                   capture_output=True)
    return proc.pid


# --- the pipeline --------------------------------------------------------

def build_jobs() -> List[Job]:
    D = "/Volumes/OWC Express 1M2/datasets/MARKET_FILINGS/derived"
    return [
        Job(id="oslo_stage02_attachments", title="Oslo stage-02 (real annual reports)",
            purpose="Stage 02 over the re-acquired Oslo attachments.",
            command=[PY, "scripts/pdf_market_sweep.py", "--market", "nordic",
                     "--raw-root", "/Users/lichenyu/datasets/markets/no/01_raw/oslo_attachments",
                     "--workers", "2", "--log-every", "250"],
            produces=f"{D}/NORDIC_FINANCIALS/canonical_metrics_wide.parquet",
            min_rows=10000, total=10889, unit="filings"),
        Job(id="panel_rebuild_auto", title="Panel rebuild",
            purpose="Rebuild the panel once its inputs settle: TWSE stage-02 (15,300 rows), "
                    "HK derived period ends, AU/HK/IN unit-scale tables.",
            command=[PY, "-m", "panel.build"],
            needs=["oslo_stage02_attachments"], total=8, unit="markets"),
        Job(id="screening_inputs_auto", title="Screening inputs (live FX)",
            purpose="Rebuild au/tw/ph/in screening inputs on live FX after stage-02 changes.",
            command=[PY, "build_pdf_market_screening_input.py", "au", "tw", "ph", "in"],
            needs=["panel_rebuild_auto"], total=4, unit="markets",
            guard=f'test -f "{D}/TWSE_FINANCIALS/canonical_metrics_wide.parquet"'),
    ]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--interval", type=int, default=600,
                    help="seconds between checks; deliberately slow, see module docstring")
    ap.add_argument("--cap", type=int, default=2,
                    help="max concurrent supervised jobs (one external drive)")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    jobs = build_jobs()
    last_report = ""
    while True:
        state, running = tracker_state(), live_jobs()
        ready = [j for j in jobs if j.id in decide(jobs, state, running, args.cap)]
        ready = [j for j in ready if guard_passes(j)]

        if ready:
            for job in ready:
                if args.dry_run:
                    print(f"[dry-run] would launch {job.id}: {' '.join(job.command)}", flush=True)
                else:
                    pid = launch(job)
                    print(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} LAUNCHED {job.id} pid={pid}", flush=True)
        else:
            # Log only when the picture CHANGES. The supervisor this replaces
            # printed every cycle and became unreadable.
            waiting = {j.id: [d for d in j.needs if state.get(d) != "done"]
                       for j in jobs if state.get(j.id) not in ("done",)}
            report = json.dumps(waiting, sort_keys=True)
            if report != last_report:
                print(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} waiting: {waiting}", flush=True)
                last_report = report

        if args.once:
            return 0
        time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
