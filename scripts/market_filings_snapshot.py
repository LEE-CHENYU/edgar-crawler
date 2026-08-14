#!/usr/bin/env python3
"""Persist a point-in-time MARKET_FILINGS crawl status snapshot.

The snapshot is written under the external corpus so crawler progress survives
terminal, Codex, and repo checkout context loss.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


DEFAULT_DATA_ROOT = Path("/Volumes/OWC Express 1M2/datasets/MARKET_FILINGS")
DEFAULT_OUTPUT_DIR = "monitoring"
START_DATE = date(2008, 1, 1)


@dataclass(frozen=True)
class JobSpec:
    key: str
    screen: str
    state_name: str
    log_name: str
    market: str
    state_kind: str
    state_key: str


JOBS = [
    JobSpec(
        key="hkex",
        screen="market_backfill_hkex_annual",
        state_name="backfill_state_hkex_reports.json",
        log_name="market_backfill_hkex_annual.log",
        market="HKEX",
        state_kind="date_next_end",
        state_key="hkex_next_end",
    ),
    JobSpec(
        key="dart",
        screen="market_backfill_dart",
        state_name="backfill_state_dart.json",
        log_name="market_backfill_dart.log",
        market="DART",
        state_kind="date_next_end",
        state_key="dart_next_end",
    ),
    JobSpec(
        key="pse_edge",
        screen="market_backfill_pse_edge",
        state_name="backfill_state_pse_edge.json",
        log_name="market_backfill_pse_edge.log",
        market="PSE_EDGE",
        state_kind="date_next_end",
        state_key="pse_edge_next_end",
    ),
    JobSpec(
        key="twse_reports",
        screen="market_backfill_twse_reports",
        state_name="backfill_state_twse_reports_0.json",
        log_name="market_backfill_twse_reports.log",
        market="TWSE_REPORTS",
        state_kind="year_index",
        state_key="twse_reports_next_year",
    ),
    JobSpec(
        key="asx",
        screen="market_backfill_asx_annual",
        state_name="backfill_state_asx.json",
        log_name="market_backfill_asx_annual.log",
        market="ASX",
        state_kind="asx",
        state_key="asx_next_year",
    ),
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Defaults to DATA_ROOT/monitoring.",
    )
    parser.add_argument("--logs-dir", type=Path, default=repo_dir() / "logs")
    parser.add_argument("--append", action="store_true", help="Append to progress_snapshots.jsonl.")
    parser.add_argument("--no-markdown", action="store_true")
    parser.add_argument("--tail-lines", type=int, default=8)
    args = parser.parse_args()

    data_root = args.data_root
    output_dir = args.output_dir or data_root / DEFAULT_OUTPUT_DIR
    if not data_root.is_dir():
        raise SystemExit(f"data root is missing: {data_root}")

    snapshot = build_snapshot(
        data_root=data_root,
        logs_dir=args.logs_dir,
        tail_lines=max(0, args.tail_lines),
    )
    output_dir.mkdir(parents=True, exist_ok=True)

    current_json = output_dir / "current_status.json"
    write_json(current_json, snapshot)

    if args.append:
        append_jsonl(output_dir / "progress_snapshots.jsonl", snapshot)

    if not args.no_markdown:
        (output_dir / "current_status.md").write_text(
            render_markdown(snapshot),
            encoding="utf-8",
        )

    print(f"wrote {current_json}")
    if not args.no_markdown:
        print(f"wrote {output_dir / 'current_status.md'}")
    if args.append:
        print(f"appended {output_dir / 'progress_snapshots.jsonl'}")
    print(render_console(snapshot))
    return 0


def build_snapshot(data_root: Path, logs_dir: Path, tail_lines: int) -> Dict[str, Any]:
    active_screens = screen_names()
    processes = active_process_lines()
    jobs = [
        job_snapshot(job, data_root, logs_dir, active_screens, tail_lines)
        for job in JOBS
    ]
    metadata_path = data_root / "metadata" / "MARKET_FILINGS_METADATA.csv"
    raw_root = data_root / "raw"
    return {
        "schema_version": 1,
        "captured_at": timestamp(),
        "host": hostname(),
        "repo_dir": str(repo_dir()),
        "data_root": str(data_root),
        "active_screens": sorted(active_screens),
        "active_processes": processes,
        "jobs": jobs,
        "counts": {
            "metadata": metadata_counts(metadata_path),
            "raw_files": raw_counts(raw_root),
        },
        "notes": [
            "EDINET/Japan is intentionally not managed by this local annual-filings queue.",
            "HKEX is complete when hkex_next_end is earlier than 2008-01-01.",
            "Snapshots are append-only in monitoring/progress_snapshots.jsonl when --append is used.",
        ],
    }


def job_snapshot(
    job: JobSpec,
    data_root: Path,
    logs_dir: Path,
    active_screens: set[str],
    tail_lines: int,
) -> Dict[str, Any]:
    state_path = data_root / job.state_name
    log_path = logs_dir / job.log_name
    state = read_json(state_path)
    return {
        "key": job.key,
        "market": job.market,
        "screen": job.screen,
        "running": job.screen in active_screens,
        "complete": is_complete(job, state),
        "state_file": str(state_path),
        "state_file_mtime": file_mtime(state_path),
        "state": state,
        "progress": progress_text(job, state, data_root),
        "log_file": str(log_path),
        "log_file_mtime": file_mtime(log_path),
        "recent_log": tail_file(log_path, tail_lines),
    }


def is_complete(job: JobSpec, state: Dict[str, Any]) -> bool:
    if job.state_kind == "date_next_end":
        value = state.get(job.state_key)
        parsed = parse_date(value)
        return bool(parsed and parsed < START_DATE)
    if job.state_kind == "year_index":
        year = as_int(state.get(job.state_key))
        return bool(year is not None and year < START_DATE.year)
    if job.state_kind == "asx":
        year = as_int(state.get("asx_next_year"))
        return state.get("asx_status") == "complete" or bool(
            year is not None and year < START_DATE.year
        )
    return False


def progress_text(job: JobSpec, state: Dict[str, Any], data_root: Path) -> str:
    if job.key in {"hkex", "dart", "pse_edge"}:
        value = state.get(job.state_key, "unset")
        return f"next_end={value}"
    if job.key == "twse_reports":
        year = state.get("twse_reports_next_year", "unset")
        index = as_int(state.get("twse_reports_company_index")) or 0
        total = twse_company_count(data_root)
        if total:
            pct = 100 * index / total
            return f"year={year} company_index={index}/{total} ({pct:.1f}% of current year)"
        return f"year={year} company_index={index}"
    if job.key == "asx":
        year = state.get("asx_next_year", "unset")
        index = as_int(state.get("asx_symbol_index")) or 0
        total = as_int(state.get("asx_symbol_count")) or 0
        status = state.get("asx_status", "unset")
        if total:
            pct = 100 * index / total
            return f"year={year} symbol_index={index}/{total} ({pct:.1f}% of current year) status={status}"
        return f"year={year} symbol_index={index} status={status}"
    return "unknown"


def metadata_counts(metadata_path: Path) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "path": str(metadata_path),
        "exists": metadata_path.exists(),
        "total_rows": 0,
        "total_acquired": 0,
        "by_market": {},
        "acquired_by_market": {},
        "mtime": file_mtime(metadata_path),
    }
    if not metadata_path.exists():
        return result

    try:
        csv.field_size_limit(sys.maxsize)
    except OverflowError:
        csv.field_size_limit(2**31 - 1)

    by_market: Dict[str, int] = {}
    acquired_by_market: Dict[str, int] = {}
    with open(metadata_path, newline="", encoding="utf-8") as fin:
        for row in csv.DictReader(fin):
            market = (row.get("market") or "UNKNOWN").upper()
            by_market[market] = by_market.get(market, 0) + 1
            result["total_rows"] += 1
            # A row exists from discovery; only a local_path proves the
            # document was actually acquired.
            if str(row.get("local_path") or "").strip():
                acquired_by_market[market] = acquired_by_market.get(market, 0) + 1
                result["total_acquired"] += 1
    result["by_market"] = dict(sorted(by_market.items()))
    result["acquired_by_market"] = dict(sorted(acquired_by_market.items()))
    return result


def coverage_rows(metadata: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Per-market acquisition coverage, worst gap first.

    Discovery completeness and document completeness are different things; the
    queue only tracks the former. This surfaces the latter.
    """
    acquired_by_market = metadata.get("acquired_by_market") or {}
    rows = []
    for market, total in (metadata.get("by_market") or {}).items():
        acquired = int(acquired_by_market.get(market, 0) or 0)
        total = int(total or 0)
        rows.append({
            "market": market,
            "rows": total,
            "acquired": acquired,
            "missing": total - acquired,
            "pct": (acquired / total * 100) if total else 0.0,
        })
    rows.sort(key=lambda r: (-r["missing"], r["market"]))
    return rows


def raw_counts(raw_root: Path) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "path": str(raw_root),
        "exists": raw_root.exists(),
        "total_files": 0,
        "by_market": {},
        "mtime": file_mtime(raw_root),
    }
    if not raw_root.exists():
        return result

    by_market: Dict[str, int] = {}
    for child in sorted(raw_root.iterdir()):
        if not child.is_dir():
            continue
        count = sum(1 for path in child.rglob("*") if path.is_file())
        by_market[child.name.upper()] = count
        result["total_files"] += count
    result["by_market"] = by_market
    return result


def screen_names() -> set[str]:
    result = subprocess.run(
        ["screen", "-ls"],
        text=True,
        capture_output=True,
        check=False,
    )
    names: set[str] = set()
    for line in result.stdout.splitlines() + result.stderr.splitlines():
        first = line.strip().split(maxsplit=1)[0] if line.strip() else ""
        if "." in first:
            names.add(first.split(".", 1)[1])
    return names


def active_process_lines() -> List[str]:
    terms = (
        "backfill_market_filings.py",
        "download_market_filings.py",
        "asx_annual_discovery.py",
        "edinet_xbrl",
    )
    result = subprocess.run(
        ["ps", "-axo", "pid,etime,command"],
        text=True,
        capture_output=True,
        check=False,
    )
    lines = []
    for line in result.stdout.splitlines():
        if any(term in line for term in terms) and "market_filings_snapshot.py" not in line:
            lines.append(line.strip())
    return lines


def read_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as fin:
        return json.load(fin)


def write_json(path: Path, payload: Dict[str, Any]) -> None:
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temp_path, path)


def append_jsonl(path: Path, payload: Dict[str, Any]) -> None:
    with open(path, "a", encoding="utf-8") as fout:
        fout.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")


def tail_file(path: Path, line_count: int) -> List[str]:
    if line_count <= 0 or not path.exists():
        return []
    with open(path, "rb") as fin:
        fin.seek(0, os.SEEK_END)
        size = fin.tell()
        block_size = 4096
        blocks: List[bytes] = []
        lines_found = 0
        position = size
        while position > 0 and lines_found <= line_count:
            read_size = min(block_size, position)
            position -= read_size
            fin.seek(position)
            block = fin.read(read_size)
            blocks.append(block)
            lines_found += block.count(b"\n")
    data = b"".join(reversed(blocks)).decode("utf-8", errors="replace")
    return data.splitlines()[-line_count:]


def render_console(snapshot: Dict[str, Any]) -> str:
    lines = [f"{snapshot['captured_at']} MARKET_FILINGS status"]
    for job in snapshot["jobs"]:
        lines.append(
            f"- {job['key']}: running={str(job['running']).lower()} "
            f"complete={str(job['complete']).lower()} {job['progress']}"
        )
    metadata = snapshot["counts"]["metadata"]
    raw = snapshot["counts"]["raw_files"]
    lines.append(
        f"- metadata_rows={metadata['total_rows']} "
        f"acquired={metadata.get('total_acquired', 0)} "
        f"raw_files={raw['total_files']}"
    )
    gaps = [row for row in coverage_rows(metadata) if row["missing"] > 0]
    if gaps:
        lines.append("- markets with missing documents:")
        for row in gaps[:8]:
            lines.append(
                f"  - {row['market']}: {row['acquired']}/{row['rows']} acquired "
                f"({row['pct']:.1f}%), {row['missing']} missing"
            )
    return "\n".join(lines)


def render_markdown(snapshot: Dict[str, Any]) -> str:
    metadata = snapshot["counts"]["metadata"]
    raw = snapshot["counts"]["raw_files"]
    lines = [
        "# MARKET_FILINGS Current Status",
        "",
        f"- Captured at: `{snapshot['captured_at']}`",
        f"- Host: `{snapshot['host']}`",
        f"- Data root: `{snapshot['data_root']}`",
        f"- Metadata rows: `{metadata['total_rows']}`",
        f"- Raw files: `{raw['total_files']}`",
        "",
        "## Jobs",
        "",
        "| Job | Running | Complete | Progress |",
        "| --- | --- | --- | --- |",
    ]
    for job in snapshot["jobs"]:
        lines.append(
            f"| `{job['key']}` | `{str(job['running']).lower()}` | "
            f"`{str(job['complete']).lower()}` | {job['progress']} |"
        )
    lines.extend([
        "",
        "## Document Coverage",
        "",
        "Rows come from discovery; `acquired` counts rows with a `local_path`.",
        "A job can report `complete=true` while documents are still missing --",
        "the queue's completion is defined on discovery, not acquisition.",
        "",
        "| Market | Rows | Acquired | Missing | Coverage |",
        "| --- | ---: | ---: | ---: | ---: |",
    ])
    for row in coverage_rows(metadata):
        flag = " :warning:" if row["missing"] else ""
        lines.append(
            f"| `{row['market']}` | {row['rows']} | {row['acquired']} | "
            f"{row['missing']}{flag} | {row['pct']:.1f}% |"
        )
    lines.extend(["", "## Metadata Rows By Market", ""])
    for market, count in metadata["by_market"].items():
        lines.append(f"- `{market}`: `{count}`")
    lines.extend(["", "## Raw Files By Market", ""])
    for market, count in raw["by_market"].items():
        lines.append(f"- `{market}`: `{count}`")
    lines.extend(["", "## Active Screens", ""])
    for screen in snapshot["active_screens"] or ["none"]:
        lines.append(f"- `{screen}`")
    lines.extend(["", "## Notes", ""])
    for note in snapshot["notes"]:
        lines.append(f"- {note}")
    lines.append("")
    return "\n".join(lines)


def twse_company_count(data_root: Path) -> int:
    payload = read_json(data_root / "twse_company_codes.json")
    return len(payload.get("company_codes") or [])


def parse_date(value: Any) -> Optional[date]:
    if not value:
        return None
    try:
        return datetime.strptime(str(value), "%Y-%m-%d").date()
    except ValueError:
        return None


def as_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def file_mtime(path: Path) -> Optional[str]:
    if not path.exists():
        return None
    return datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds")


def repo_dir() -> Path:
    return Path(__file__).resolve().parents[1]


def hostname() -> str:
    return subprocess.run(
        ["hostname"],
        text=True,
        capture_output=True,
        check=False,
    ).stdout.strip()


def timestamp() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


if __name__ == "__main__":
    sys.exit(main())
