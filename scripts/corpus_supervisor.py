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
import re
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


def _pid_state(pid: int) -> str:
    """Single-letter process state from ps: R/S/I running-ish, Z zombie."""
    res = subprocess.run(["ps", "-o", "state=", "-p", str(pid)],
                         capture_output=True, text=True)
    return (res.stdout or "").strip()[:1]


def pid_is_live(pid) -> bool:
    """True only for a process that is actually still working.

    A ZOMBIE is not live. os.kill(pid, 0) succeeds on one, which is how the
    panel build -- finished, exit 0, <defunct> because this supervisor never
    reaped it -- held its tracker entry at `running` for 42 hours and stalled
    everything behind it.
    """
    if pid in (None, "", 0):
        return False
    try:
        state = _pid_state(int(pid))
    except (TypeError, ValueError):
        return False
    if not state:
        return False
    return state != "Z"


def outcome_for(returncode: int) -> str:
    """Tracker status for a finished child."""
    return "done" if returncode == 0 else "failed"


def close_out(job_id: str, returncode: int) -> None:
    """Mark a supervised job finished. The supervisor must close what it opens;
    otherwise every launch leaks a zombie AND a permanently-open entry."""
    status = outcome_for(returncode)
    args = [PY, str(TRACKER_CLI), status, "--id", job_id]
    if status == "failed":
        args += ["--reason", f"exited with code {returncode}"]
    subprocess.run(args, capture_output=True)


# --- local resources -----------------------------------------------------

# This machine is 8 cores / 8 GB. Text extraction alone runs 8 pdftotext
# workers, and a large PDF can take hundreds of MB, so memory is the binding
# constraint rather than cores.
# MEMORY is the gate; load average is only a runaway backstop.
#
# macOS load average counts threads blocked on I/O, not just runnable ones. The
# live text extraction sat at load 46 on 8 cores while delivering a steady 12
# files/sec with 55% of memory free -- perfectly healthy. Successive attempts at
# cores*0.9 and cores*4 both read that as saturation and deferred forever, which
# would have made the supervisor useless exactly when work was flowing. Load is
# therefore kept only to catch a genuine runaway (fork bomb, thrash), and the
# real constraint on this 8 GB box is free memory.
LOAD_PER_CORE = 8.0
MIN_FREE_GB = 1.0


def resources_ok(load1: float, cores: int, free_gb: float,
                 load_per_core: float = LOAD_PER_CORE,
                 min_free_gb: float = MIN_FREE_GB) -> bool:
    """Whether the machine has room for another supervised job.

    Launching onto a saturated box makes BOTH jobs slower. Every sweep this week
    was hand-throttled to 2-3 workers for want of this check.
    """
    if free_gb < min_free_gb:
        return False
    return load1 < cores * load_per_core


def local_resources() -> Dict[str, float]:
    cores = os.cpu_count() or 1
    try:
        load1 = os.getloadavg()[0]
    except (OSError, AttributeError):
        load1 = 0.0
    free_gb = 0.0
    try:
        vm = subprocess.run(["vm_stat"], capture_output=True, text=True).stdout
        # Read the page size from vm_stat itself: Apple Silicon uses 16384, and
        # assuming 4096 under-reports free memory by 4x.
        match = re.search(r"page size of (\d+) bytes", vm)
        page = int(match.group(1)) if match else 4096
        free_pages = 0
        for line in vm.splitlines():
            if line.startswith(("Pages free", "Pages inactive", "Pages speculative")):
                free_pages += int(line.split(":")[1].strip().rstrip("."))
        free_gb = free_pages * page / (1024 ** 3)
    except Exception:
        free_gb = float("inf")   # unknown: do not block on it
    return {"cores": cores, "load1": load1, "free_gb": free_gb}


# --- rented GPU ----------------------------------------------------------


def pod_alerts(pods: Sequence[dict], wallet_usd: float,
               idle_hours: float = 4.0) -> List[str]:
    """Warnings about rented GPU pods. A pod left running is money.

    The HK embedding runbook logged $0.40 of wasted stub instances, and the
    standing rule is to archive results BEFORE destroying an instance -- so a
    long-lived pod is surfaced, never auto-killed.
    """
    out: List[str] = []
    burn = 0.0
    for pod in pods or []:
        if str(pod.get("status", "")).upper() not in ("ACTIVE", "RUNNING"):
            continue
        price = float(pod.get("price_hr") or 0)
        burn += price
        age = float(pod.get("age_hours") or 0)
        if age >= idle_hours:
            out.append(f"pod {pod.get('id')} ({pod.get('gpu_type')}) has run "
                       f"{age:.1f}h at ${price:.2f}/hr = ${age*price:.2f} -- "
                       f"still needed?")
    if burn and wallet_usd / burn < 2.0:
        out.append(f"wallet balance ${wallet_usd:.2f} funds only "
                   f"{wallet_usd/burn:.1f}h at the current ${burn:.2f}/hr burn")
    return out


def prime_pods() -> List[dict]:
    """Running pods from the Prime Intellect CLI, or [] if unavailable."""
    prime = Path.home() / ".local" / "bin" / "prime"
    if not prime.exists():
        return []
    try:
        res = subprocess.run([str(prime), "--plain", "pods", "list",
                              "--output", "json"],
                             capture_output=True, text=True, timeout=60)
        data = json.loads(res.stdout or "{}")
    except Exception:
        return []
    out = []
    for pod in data.get("pods", []) or []:
        out.append({"id": pod.get("id"), "status": pod.get("status"),
                    "gpu_type": pod.get("gpu_type") or pod.get("gpuType"),
                    "price_hr": pod.get("price_hr") or pod.get("priceHr") or 0,
                    "age_hours": pod.get("age_hours") or 0})
    return out


def prime_wallet() -> float:
    prime = Path.home() / ".local" / "bin" / "prime"
    if not prime.exists():
        return float("inf")
    try:
        res = subprocess.run([str(prime), "--plain", "wallet"],
                             capture_output=True, text=True, timeout=60)
        m = re.search(r"Balance:\s*\$([0-9.]+)", res.stdout or "")
        return float(m.group(1)) if m else float("inf")
    except Exception:
        return float("inf")


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
        if pid_is_live(job.get("pid")):
            alive.add(str(job.get("id")))
    return alive


def guard_passes(job: Job) -> bool:
    if not job.guard:
        return True
    return subprocess.run(["bash", "-lc", job.guard],
                          capture_output=True).returncode == 0


def launch(job: Job) -> subprocess.Popen:
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
    return proc


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
    ap.add_argument("--watch-gpu", action="store_true", default=True,
                    help="report rented Prime Intellect pods and wallet burn")
    ap.add_argument("--no-watch-gpu", dest="watch_gpu", action="store_false")
    args = ap.parse_args()

    jobs = build_jobs()
    last_report = ""
    children: Dict[str, subprocess.Popen] = {}
    seen_alerts: Set[str] = set()
    while True:
        # Reap first: a finished child must be recorded before the next
        # decision, or it counts as running for another whole cycle.
        for job_id, proc in list(children.items()):
            rc = proc.poll()
            if rc is not None:
                close_out(job_id, rc)
                print(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} FINISHED {job_id} rc={rc}", flush=True)
                children.pop(job_id, None)

        state, running = tracker_state(), live_jobs()

        # Rented GPU accounting: surfaced every cycle, never auto-terminated --
        # results must be archived before an instance is destroyed.
        if args.watch_gpu:
            alerts = pod_alerts(prime_pods(), prime_wallet())
            for alert in alerts:
                if alert not in seen_alerts:
                    print(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} GPU: {alert}", flush=True)
                    seen_alerts.add(alert)

        res = local_resources()
        if not resources_ok(res["load1"], int(res["cores"]), res["free_gb"]):
            msg = (f"deferring: load {res['load1']:.1f} on {int(res['cores'])} cores, "
                   f"{res['free_gb']:.1f} GB free")
            if msg != last_report:
                print(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {msg}", flush=True)
                last_report = msg
            if args.once:
                return 0
            time.sleep(args.interval)
            continue

        ready = [j for j in jobs if j.id in decide(jobs, state, running, args.cap)]
        ready = [j for j in ready if guard_passes(j)]

        if ready:
            for job in ready:
                if args.dry_run:
                    print(f"[dry-run] would launch {job.id}: {' '.join(job.command)}", flush=True)
                else:
                    proc = launch(job)
                    children[job.id] = proc
                    print(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} LAUNCHED {job.id} pid={proc.pid}", flush=True)
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
