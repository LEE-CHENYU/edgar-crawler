#!/usr/bin/env python3
"""Supervise the annual-financial filing backfill queue.

This queue deliberately excludes EDINET/Japan. It only manages annual-financial
jobs that are already implemented and safe to resume locally.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional


REPO_DIR = Path(__file__).resolve().parents[1]
DEFAULT_DATA_ROOT = Path("/Volumes/OWC Express 1M2/datasets/MARKET_FILINGS")
DEFAULT_ENV_FILE = Path.home() / ".config/edgar-crawler/market_filings.env"
DEFAULT_START_DATE = date(2008, 1, 1)
REQUIRED_CORPUS_FILES = (
    Path("metadata/MARKET_FILINGS_METADATA.csv"),
    Path("backfill_state_dart.json"),
)


@dataclass(frozen=True)
class Job:
    key: str
    screen: str
    log_name: str
    state_name: str
    priority: int
    script_name: str
    build_args: Callable[[Path, date], List[str]]
    is_complete: Callable[[Dict, Path, date], bool]
    status_text: Callable[[Dict, Path], str]
    needs_env: bool = False


@dataclass(frozen=True)
class BacklogTarget:
    key: str
    status: str
    reason: str
    counts_toward_worker_slots: bool = False


def hkex_args(data_root: Path, end_date: date) -> List[str]:
    return [
        "--markets",
        "hkex",
        "--start-date",
        DEFAULT_START_DATE.isoformat(),
        "--end-date",
        end_date.isoformat(),
        "--state-file",
        str(data_root / "backfill_state_hkex_reports.json"),
        "--hkex-days-per-chunk",
        "1",
        "--request-timeout",
        "90",
        "--sleep-seconds",
        "2",
    ]


def dart_args(data_root: Path, end_date: date) -> List[str]:
    return [
        "--markets",
        "dart",
        "--start-date",
        DEFAULT_START_DATE.isoformat(),
        "--end-date",
        end_date.isoformat(),
        "--state-file",
        str(data_root / "backfill_state_dart.json"),
        "--dart-days-per-chunk",
        "7",
        "--request-timeout",
        "120",
        "--sleep-seconds",
        "2",
    ]


def pse_edge_args(data_root: Path, end_date: date) -> List[str]:
    return [
        "--markets",
        "pse_edge",
        "--start-date",
        DEFAULT_START_DATE.isoformat(),
        "--end-date",
        end_date.isoformat(),
        "--state-file",
        str(data_root / "backfill_state_pse_edge.json"),
        "--pse-edge-days-per-chunk",
        "31",
        "--request-timeout",
        "120",
        "--sleep-seconds",
        "2",
    ]


def twse_reports_args(data_root: Path, end_date: date) -> List[str]:
    return [
        "--markets",
        "twse_reports",
        "--start-date",
        DEFAULT_START_DATE.isoformat(),
        "--end-date",
        end_date.isoformat(),
        "--state-file",
        str(data_root / "backfill_state_twse_reports_0.json"),
        "--company-file",
        str(data_root / "twse_company_codes.json"),
        "--twse-report-company-batch-size",
        "3",
        "--request-timeout",
        "120",
        "--sleep-seconds",
        "2",
    ]


def asx_args(data_root: Path, end_date: date) -> List[str]:
    return [
        "--data-root",
        str(data_root),
        "--state-file",
        str(data_root / "backfill_state_asx.json"),
        "--company-file",
        str(data_root / "asx_company_codes.json"),
        "--metadata",
        str(data_root / "metadata/MARKET_FILINGS_METADATA.csv"),
        "--start-year",
        str(DEFAULT_START_DATE.year),
        "--end-year",
        str(end_date.year),
        "--sleep-seconds",
        "0.25",
        "--timeout",
        "120",
        "--log-every",
        "25",
    ]


def date_job_complete(state_key: str) -> Callable[[Dict, Path, date], bool]:
    def complete(state: Dict, _data_root: Path, _end_date: date) -> bool:
        value = state.get(state_key)
        if not value:
            return False
        return parse_date(value) < DEFAULT_START_DATE

    return complete


def twse_reports_complete(state: Dict, _data_root: Path, _end_date: date) -> bool:
    value = state.get("twse_reports_next_year")
    if value is None:
        return False
    return int(value) < DEFAULT_START_DATE.year


def asx_complete(state: Dict, _data_root: Path, _end_date: date) -> bool:
    if state.get("asx_status") == "complete":
        return True
    value = state.get("asx_next_year")
    if value is None:
        return False
    return int(value) < DEFAULT_START_DATE.year


def date_status(state_key: str) -> Callable[[Dict, Path], str]:
    def status(state: Dict, _data_root: Path) -> str:
        value = state.get(state_key)
        return f"next_end={value or 'unset'}"

    return status


def twse_reports_status(state: Dict, data_root: Path) -> str:
    year = state.get("twse_reports_next_year", "unset")
    index = int(state.get("twse_reports_company_index", 0) or 0)
    total = twse_company_count(data_root)
    if total:
        return f"year={year} company_index={index}/{total}"
    return f"year={year} company_index={index}"


def asx_status(state: Dict, _data_root: Path) -> str:
    year = state.get("asx_next_year", "unset")
    index = int(state.get("asx_symbol_index", 0) or 0)
    total = int(state.get("asx_symbol_count", 0) or 0)
    status = state.get("asx_status", "unset")
    if total:
        return f"year={year} symbol_index={index}/{total} status={status}"
    return f"year={year} symbol_index={index} status={status}"


JOBS: List[Job] = [
    Job(
        key="hkex",
        screen="market_backfill_hkex_annual",
        log_name="market_backfill_hkex_annual.log",
        state_name="backfill_state_hkex_reports.json",
        priority=10,
        script_name="backfill_market_filings.py",
        build_args=hkex_args,
        is_complete=date_job_complete("hkex_next_end"),
        status_text=date_status("hkex_next_end"),
        needs_env=True,
    ),
    Job(
        key="twse_reports",
        screen="market_backfill_twse_reports",
        log_name="market_backfill_twse_reports.log",
        state_name="backfill_state_twse_reports_0.json",
        priority=20,
        script_name="backfill_market_filings.py",
        build_args=twse_reports_args,
        is_complete=twse_reports_complete,
        status_text=twse_reports_status,
        needs_env=False,
    ),
    Job(
        key="dart",
        screen="market_backfill_dart",
        log_name="market_backfill_dart.log",
        state_name="backfill_state_dart.json",
        priority=30,
        script_name="backfill_market_filings.py",
        build_args=dart_args,
        is_complete=date_job_complete("dart_next_end"),
        status_text=date_status("dart_next_end"),
        needs_env=True,
    ),
    Job(
        key="pse_edge",
        screen="market_backfill_pse_edge",
        log_name="market_backfill_pse_edge.log",
        state_name="backfill_state_pse_edge.json",
        priority=35,
        script_name="backfill_market_filings.py",
        build_args=pse_edge_args,
        is_complete=date_job_complete("pse_edge_next_end"),
        status_text=date_status("pse_edge_next_end"),
        needs_env=False,
    ),
    Job(
        key="asx",
        screen="market_backfill_asx_annual",
        log_name="market_backfill_asx_annual.log",
        state_name="backfill_state_asx.json",
        priority=40,
        script_name="scripts/asx_annual_discovery.py",
        build_args=asx_args,
        is_complete=asx_complete,
        status_text=asx_status,
        needs_env=False,
    ),
]


BACKLOG_TARGETS = [
    BacklogTarget(
        key="nzx",
        status="queued_not_implemented",
        reason="validated announcement JSON and attachment PDFs; needs annual-report worker",
    ),
    BacklogTarget(
        key="sgx",
        status="unfetchable_now",
        reason="annual report source is implemented, but current API probe returns 403",
    ),
    BacklogTarget(
        key="set_thailand",
        status="unfetchable_now",
        reason="public page is reachable, but API calls returned Incapsula blocks",
    ),
    BacklogTarget(
        key="bursa",
        status="unfetchable_now",
        reason="announcement search is blocked by Cloudflare from this environment",
    ),
    BacklogTarget(
        key="idx",
        status="unfetchable_now",
        reason="announcement API endpoints returned Cloudflare blocks from this environment",
    ),
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=["status", "ensure", "supervise", "deferred", "queue"],
        help="status reports jobs; ensure starts missing incomplete jobs; supervise loops.",
    )
    parser.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)
    parser.add_argument("--interval-seconds", type=int, default=300)
    parser.add_argument("--end-date", default=date.today().isoformat())
    parser.add_argument("--max-workers", type=int, default=3)
    parser.add_argument(
        "--no-snapshot",
        action="store_true",
        help="Do not append MARKET_FILINGS monitoring snapshots.",
    )
    args = parser.parse_args()

    end_date = parse_date(args.end_date)
    if args.command in {"deferred", "queue"}:
        print_backlog()
        return 0

    check_environment(args.data_root, args.env_file)

    if args.command == "status":
        print_status(args.data_root, end_date)
        return 0

    if args.command == "ensure":
        ensure_jobs(args.data_root, args.env_file, end_date, args.max_workers)
        print_status(args.data_root, end_date)
        if not args.no_snapshot:
            write_snapshot()
        return 0

    while True:
        problem = environment_problem(args.data_root, args.env_file)
        if problem:
            print(f"{timestamp()} waiting: {problem}")
            time.sleep(max(30, args.interval_seconds))
            continue
        ensure_jobs(args.data_root, args.env_file, end_date, args.max_workers)
        print_status(args.data_root, end_date)
        if not args.no_snapshot:
            write_snapshot()
        if all(job_complete(job, args.data_root, end_date) for job in JOBS):
            print(f"{timestamp()} all implemented annual-financial jobs are complete")
            return 0
        time.sleep(max(30, args.interval_seconds))


def check_environment(data_root: Path, env_file: Path) -> None:
    problem = environment_problem(data_root, env_file)
    if problem:
        raise SystemExit(problem)


def environment_problem(data_root: Path, env_file: Path) -> Optional[str]:
    if not data_root.is_dir():
        return f"data root is missing: {data_root}"
    missing_required = [
        str(relative_path)
        for relative_path in REQUIRED_CORPUS_FILES
        if not (data_root / relative_path).exists()
    ]
    if missing_required:
        return (
            f"data root does not look like the external corpus: {data_root} "
            f"missing={','.join(missing_required)}"
        )
    if not (REPO_DIR / ".venv/bin/python").exists():
        return "repo venv is missing: .venv/bin/python"
    if not env_file.exists():
        return f"env file is missing: {env_file}"
    if not shutil_exists("screen"):
        return "screen is required but was not found"
    return None


def ensure_jobs(
    data_root: Path, env_file: Path, end_date: date, max_workers: int
) -> None:
    active_screens = screen_names()
    incomplete_jobs = [
        job for job in sorted(JOBS, key=lambda item: item.priority)
        if not job_complete(job, data_root, end_date)
    ]
    running_count = sum(1 for job in incomplete_jobs if job.screen in active_screens)
    available_slots = max(0, max_workers - running_count)

    for job in sorted(JOBS, key=lambda item: item.priority):
        if job_complete(job, data_root, end_date):
            print(f"{timestamp()} {job.key}: complete")
            continue
        if job.screen in active_screens:
            print(f"{timestamp()} {job.key}: already running screen={job.screen}")
            continue
        if available_slots <= 0:
            print(
                f"{timestamp()} {job.key}: waiting for worker slot "
                f"max_workers={max_workers}"
            )
            continue
        start_job(job, data_root, env_file, end_date)
        available_slots -= 1

    if available_slots > 0:
        queued = [target for target in BACKLOG_TARGETS if not target.counts_toward_worker_slots]
        if queued:
            print(
                f"{timestamp()} queue: {available_slots} worker slot(s) available, "
                "but no additional fetchable implemented market is ready"
            )
            for target in queued:
                print(
                    f"{timestamp()} queue: {target.key} status={target.status} "
                    f"reason={target.reason}"
                )


def start_job(job: Job, data_root: Path, env_file: Path, end_date: date) -> None:
    logs_dir = REPO_DIR / "logs"
    logs_dir.mkdir(exist_ok=True)
    args = " ".join(shell_quote(arg) for arg in job.build_args(data_root, end_date))
    env_block = f"set -a; . {shell_quote(str(env_file))}; set +a\n" if job.needs_env else ""
    command = (
        f"cd {shell_quote(str(REPO_DIR))}\n"
        ". .venv/bin/activate\n"
        f"{env_block}"
        f"python -u {shell_quote(job.script_name)} "
        f"{args} >> {shell_quote(str(logs_dir / job.log_name))} 2>&1\n"
    )
    subprocess.run(["screen", "-dmS", job.screen, "bash", "-lc", command], check=True)
    print(f"{timestamp()} {job.key}: started screen={job.screen}")


def print_status(data_root: Path, end_date: date) -> None:
    active_screens = screen_names()
    print(f"{timestamp()} annual-financial queue status")
    for job in sorted(JOBS, key=lambda item: item.priority):
        state = load_state(data_root / job.state_name)
        running = job.screen in active_screens
        complete = job.is_complete(state, data_root, end_date)
        status = job.status_text(state, data_root)
        print(
            f"- {job.key}: running={str(running).lower()} "
            f"complete={str(complete).lower()} {status}"
        )
    print("- queued targets:")
    for target in BACKLOG_TARGETS:
        print(
            f"  - {target.key}: status={target.status} "
            f"counts_toward_worker_slots={str(target.counts_toward_worker_slots).lower()} "
            f"reason={target.reason}"
        )


def print_backlog() -> None:
    print("Queued/deferred annual-financial targets:")
    for target in BACKLOG_TARGETS:
        print(
            f"- {target.key}: status={target.status} "
            f"counts_toward_worker_slots={str(target.counts_toward_worker_slots).lower()} "
            f"reason={target.reason}"
        )


def job_complete(job: Job, data_root: Path, end_date: date) -> bool:
    return job.is_complete(load_state(data_root / job.state_name), data_root, end_date)


def screen_names() -> set[str]:
    result = subprocess.run(
        ["screen", "-ls"], text=True, capture_output=True, check=False
    )
    names: set[str] = set()
    for line in result.stdout.splitlines() + result.stderr.splitlines():
        line = line.strip()
        if "." not in line or line.startswith("No Sockets"):
            continue
        first = line.split()[0]
        if "." in first:
            names.add(first.split(".", 1)[1])
    return names


def load_state(path: Path) -> Dict:
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as fin:
        return json.load(fin)


def parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def twse_company_count(data_root: Path) -> int:
    path = data_root / "twse_company_codes.json"
    if not path.exists():
        return 0
    data = load_state(path)
    return len(data.get("company_codes") or [])


def write_snapshot() -> None:
    snapshot_script = REPO_DIR / "scripts/market_filings_snapshot.py"
    if not snapshot_script.exists():
        return
    subprocess.run(
        [
            sys.executable,
            str(snapshot_script),
            "--append",
            "--tail-lines",
            "5",
        ],
        cwd=REPO_DIR,
        check=False,
    )


def shell_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


def shutil_exists(command: str) -> bool:
    for folder in os.environ.get("PATH", "").split(os.pathsep):
        if (Path(folder) / command).exists():
            return True
    return False


def timestamp() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


if __name__ == "__main__":
    sys.exit(main())
