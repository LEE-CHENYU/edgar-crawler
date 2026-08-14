#!/usr/bin/env python3
"""Re-drive document acquisition for metadata rows whose download failed.

Discovery and acquisition are decoupled in this pipeline: a row is written to
MARKET_FILINGS_METADATA.csv as soon as it is discovered, and a failed download
merely leaves `local_path` empty (`try_download_binary` returns False). The
backfill cursor then advances regardless, so a transient outage becomes a
permanent gap while the job still reports `complete=true`.

This driver closes that gap. It selects rows for a market that have no
`local_path`, re-attempts acquisition, and updates the rows in place.

TWSE contract: `doc.twse.com.tw` serves a one-shot, timestamped
`/pdf/{stem}_{YYYYMMDD}_{HHMMSS}.pdf` link produced by a `step=9` request. Those
links expire, so any `pdf_url` cached in `raw_metadata` from a previous run is
already dead. The link is therefore ALWAYS re-resolved per attempt and the
cached value is never used for download.

DART contract: OpenDART allows 20,000 calls/day. The driver counts calls and
stops cleanly at the ceiling, resuming on the next run.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional


DEFAULT_DATA_ROOT = Path("/Volumes/OWC Express 1M2/datasets/MARKET_FILINGS")
DAILY_CALL_CAP_DART = 20_000
DART_DOCUMENT_ENDPOINT = "https://opendart.fss.or.kr/api/document.xml"
SUPPORTED_MARKETS = ("TWSE_REPORTS", "DART")


@dataclass
class RescueState:
    market: str
    cursor: int = 0
    recovered: int = 0
    failed: int = 0
    calls_used: int = 0
    updated_at: str = ""


@dataclass
class RescueResult:
    ok: bool
    local_path: str = ""
    error: str = ""
    calls_used: int = 0
    skipped: bool = False


def already_acquired(path: str) -> bool:
    """True when a non-empty document is already on disk.

    A 0-byte file is a failed write, not an acquired document.
    """
    try:
        return os.path.getsize(path) > 0
    except OSError:
        return False


# --- row selection -------------------------------------------------------


def select_missing_rows(rows: List[Dict[str, str]], market: str) -> List[Dict[str, str]]:
    """Rows for `market` that discovery recorded but never acquired."""
    return [
        row
        for row in rows
        if row.get("market") == market and not str(row.get("local_path") or "").strip()
    ]


# --- TWSE ----------------------------------------------------------------


def twse_filing_from_row(row: Dict[str, str]) -> Dict[str, str]:
    """Reconstruct the listing payload the resolver expects."""
    try:
        raw = json.loads(row.get("raw_metadata") or "{}")
    except (TypeError, ValueError):
        raw = {}
    listing = raw.get("listing")
    return dict(listing) if isinstance(listing, dict) else {}


def build_twse_local_path(raw_dir: str, row: Dict[str, str]) -> str:
    """Mirror the path convention used by the original downloader."""
    filing = twse_filing_from_row(row)
    filename = str(filing.get("filename") or "report.pdf")
    filing_date = str(row.get("filing_date") or "").replace("-", "") or "unknown_date"
    return os.path.join(
        raw_dir,
        "TWSE_REPORTS",
        filing_date,
        safe_filename(str(row.get("filing_id") or "")),
        safe_filename(filename, max_length=180),
    )


def rescue_twse_row(
    row: Dict[str, str],
    raw_dir: str,
    session,
    resolver: Callable,
    downloader: Callable,
    timeout: int,
) -> RescueResult:
    """Re-resolve the one-shot link, then download it.

    Any `pdf_url` in `raw_metadata` is deliberately ignored: those links are
    timestamped one-shots and are dead by the time a rescue runs.
    """
    filing = twse_filing_from_row(row)
    if not filing:
        return RescueResult(ok=False, error="unparseable listing payload")

    local_path = build_twse_local_path(raw_dir, row)
    if already_acquired(local_path):
        return RescueResult(ok=True, local_path=local_path, skipped=True)

    pdf_url = resolver(session, filing, timeout) or ""
    if not pdf_url:
        return RescueResult(ok=False, error="missing generated PDF URL")

    headers = twse_report_headers(str(filing.get("kind") or ""))
    # Keyword args are required: try_download_binary's 5th positional
    # parameter is `params`, not `timeout`.
    if not downloader(
        session=session, url=pdf_url, path=local_path, headers=headers, timeout=timeout
    ):
        return RescueResult(ok=False, error=f"download failed for {pdf_url}")
    return RescueResult(ok=True, local_path=local_path)


def twse_report_headers(kind: str) -> Dict[str, str]:
    return {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
        ),
        "Referer": "https://doc.twse.com.tw/server-java/t57sb01",
        "Accept": "application/pdf,*/*",
        "X-Report-Kind": kind or "F",
    }


# --- DART ----------------------------------------------------------------


def dart_document_url(rcept_no: str, api_key: str) -> str:
    return f"{DART_DOCUMENT_ENDPOINT}?crtfc_key={api_key}&rcept_no={rcept_no}"


def build_dart_local_path(raw_dir: str, row: Dict[str, str]) -> str:
    rcept_no = safe_filename(str(row.get("filing_id") or "document"))
    filing_date = str(row.get("filing_date") or "").replace("-", "") or "unknown_date"
    return os.path.join(raw_dir, "DART", filing_date, f"{rcept_no}.zip")


def rescue_dart_row(
    row: Dict[str, str],
    raw_dir: str,
    session,
    api_key: str,
    downloader: Callable,
    timeout: int,
) -> RescueResult:
    rcept_no = str(row.get("filing_id") or "").strip()
    if not rcept_no:
        return RescueResult(ok=False, error="missing rcept_no")

    local_path = build_dart_local_path(raw_dir, row)
    if already_acquired(local_path):
        return RescueResult(ok=True, local_path=local_path, skipped=True)

    url = dart_document_url(rcept_no, api_key)
    headers = {"Accept": "application/zip,application/xml,*/*"}
    if not downloader(
        session=session, url=url, path=local_path, headers=headers, timeout=timeout
    ):
        return RescueResult(ok=False, error="download failed", calls_used=1)
    return RescueResult(ok=True, local_path=local_path, calls_used=1)


def quota_exhausted(calls_used: int, cap: int) -> bool:
    return calls_used >= cap


# --- state ---------------------------------------------------------------


def load_state(path: Path, market: str) -> RescueState:
    path = Path(path)
    if not path.exists():
        return RescueState(market=market)
    try:
        payload = json.loads(path.read_text())
    except (OSError, ValueError):
        return RescueState(market=market)
    return RescueState(
        market=payload.get("market", market),
        cursor=int(payload.get("cursor", 0) or 0),
        recovered=int(payload.get("recovered", 0) or 0),
        failed=int(payload.get("failed", 0) or 0),
        calls_used=int(payload.get("calls_used", 0) or 0),
        updated_at=str(payload.get("updated_at", "")),
    )


def save_state(path: Path, state: RescueState) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    state.updated_at = utc_now()
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(asdict(state), indent=2, sort_keys=True))
    tmp.replace(path)


# --- helpers -------------------------------------------------------------


def safe_filename(value: str, max_length: int = 120) -> str:
    import re
    from urllib.parse import unquote

    value = unquote(str(value or "")).strip()
    value = re.sub(r"[^\w.\-]+", "_", value)
    value = re.sub(r"_+", "_", value).strip("._")
    return (value or "unknown")[:max_length]


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0, tzinfo=None).isoformat() + "Z"


def log(message: str) -> None:
    print(f"{utc_now()} {message}", flush=True)


def read_metadata(path: Path) -> List[Dict[str, str]]:
    with open(path, newline="", encoding="utf-8") as fin:
        return list(csv.DictReader(fin))


# --- driver --------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--market", choices=SUPPORTED_MARKETS, required=True)
    parser.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)
    parser.add_argument("--state-file", type=Path, default=None)
    parser.add_argument("--limit", type=int, default=0, help="0 = no limit")
    parser.add_argument("--sleep-seconds", type=float, default=1.0)
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--log-every", type=int, default=25)
    parser.add_argument(
        "--checkpoint-every",
        type=int,
        default=1000,
        help="Rows between metadata writes. The CSV is 237 MB and is rewritten "
             "whole each time, so keep this high; already-acquired files are "
             "adopted on restart, making infrequent checkpoints safe.",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    # Imported here so the module stays importable (and side-effect free) in tests.
    import sys

    repo_dir = str(Path(__file__).resolve().parents[1])
    if repo_dir not in sys.path:
        sys.path.insert(0, repo_dir)

    from download_market_filings import (
        build_session,
        fetch_twse_report_pdf_url,
        try_download_binary,
        write_metadata,
    )

    data_root: Path = args.data_root
    metadata_path = data_root / "metadata" / "MARKET_FILINGS_METADATA.csv"
    raw_dir = str(data_root / "raw")
    state_path = args.state_file or (data_root / f"rescue_state_{args.market.lower()}.json")

    rows = read_metadata(metadata_path)
    missing = select_missing_rows(rows, market=args.market)
    state = load_state(state_path, market=args.market)
    log(f"{args.market}: {len(missing)} rows missing documents; cursor={state.cursor}")

    if args.dry_run:
        for row in missing[state.cursor : state.cursor + 5]:
            log(f"  would rescue {row.get('filing_id')} ({row.get('filing_date')})")
        return 0

    api_key = ""
    if args.market == "DART":
        api_key = read_dart_api_key()
        if not api_key:
            log("DART_API_KEY not set; aborting")
            return 2

    session = build_session()
    updated: List[Dict[str, str]] = []
    processed = 0
    skipped = 0

    for index in range(state.cursor, len(missing)):
        if args.limit and processed >= args.limit:
            log(f"reached --limit {args.limit}")
            break
        if args.market == "DART" and quota_exhausted(state.calls_used, DAILY_CALL_CAP_DART):
            log(f"DART daily cap {DAILY_CALL_CAP_DART} reached; stopping for today")
            break

        row = missing[index]
        if args.market == "TWSE_REPORTS":
            result = rescue_twse_row(
                row=row, raw_dir=raw_dir, session=session,
                resolver=fetch_twse_report_pdf_url, downloader=try_download_binary,
                timeout=args.timeout,
            )
        else:
            result = rescue_dart_row(
                row=row, raw_dir=raw_dir, session=session, api_key=api_key,
                downloader=try_download_binary, timeout=args.timeout,
            )

        state.calls_used += result.calls_used
        state.cursor = index + 1
        processed += 1

        if result.ok:
            state.recovered += 1
            if result.skipped:
                skipped += 1
            row["local_path"] = result.local_path
            updated.append(row)
        else:
            state.failed += 1
            if result.error:
                log(f"  {row.get('filing_id')}: {result.error[:120]}")

        if processed % args.log_every == 0:
            log(
                f"{args.market} {processed} attempted "
                f"({state.recovered} recovered / {state.failed} failed), "
                f"cursor={state.cursor}/{len(missing)}"
                + (f", {skipped} already on disk" if skipped else "")
            )
        if updated and processed % args.checkpoint_every == 0:
            write_metadata(str(metadata_path), updated)
            updated = []
            save_state(state_path, state)

        # No need to pace when nothing was fetched.
        if not result.skipped:
            time.sleep(args.sleep_seconds)

    if updated:
        write_metadata(str(metadata_path), updated)
    save_state(state_path, state)
    log(
        f"{args.market} done: {state.recovered} recovered "
        f"({skipped} adopted from disk), {state.failed} failed, "
        f"cursor={state.cursor}/{len(missing)}"
    )
    return 0


def read_dart_api_key() -> str:
    key = os.environ.get("DART_API_KEY", "").strip()
    if key:
        return key
    env_file = Path.home() / ".config/edgar-crawler/market_filings.env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line.startswith("export "):
                line = line[len("export "):]
            if line.startswith("DART_API_KEY="):
                return line.split("=", 1)[1].strip().strip("'\"")
    return ""


if __name__ == "__main__":
    raise SystemExit(main())
