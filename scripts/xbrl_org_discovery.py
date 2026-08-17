#!/usr/bin/env python3
"""Complete the filings.xbrl.org pull (Workstream D1).

The source catalogue holds 25,675 filings; 15,875 are already on disk from an
ad-hoc pass that left no state file and no script in either repo, so ~9,800
were never fetched -- including UK, Poland and Germany.

This worker enumerates the JSON:API catalogue, diffs it against the filing ids
already in MARKET_FILINGS_METADATA.csv, and downloads only what is missing.

Layout and naming deliberately mirror the existing 15,875 rows: market
`{TAXONOMY}_{COUNTRY}` (e.g. ESEF_GB), `filing_id` = `fxo_id`, and packages
under `markets/{cc}/01_raw/europe_annual_reports/xbrl_org/{CC}/...`.

New packages land on the boot volume (536 GB free) rather than the OWC drive
(116 GB free), so a country's raw stage can span two physical volumes; that is
recorded as `live_roots` in `markets/_meta/inventory.json`.

Packages average ~16 MB, so the outstanding ~9,800 are roughly 160 GB.

Usage:
    python scripts/xbrl_org_discovery.py --dry-run
    python scripts/xbrl_org_discovery.py --limit 5      # smoke test
    python scripts/xbrl_org_discovery.py               # full run
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set

import requests
from requests.adapters import HTTPAdapter
from urllib3.util import Retry


API_BASE = "https://filings.xbrl.org"
API_FILINGS = f"{API_BASE}/api/filings"
DEFAULT_METADATA = Path(
    "/Volumes/OWC Express 1M2/datasets/MARKET_FILINGS/metadata/MARKET_FILINGS_METADATA.csv"
)
# New packages go to the boot volume; OWC is at 94%.
DEFAULT_DATA_ROOT = Path("/Users/lichenyu/datasets")
DEFAULT_STATE = DEFAULT_DATA_ROOT / "backfill_state_xbrl_org.json"
USER_AGENT = (
    "edgar-crawler/1.0 (personal securities research; contact fretin13@gmail.com)"
)
METADATA_FIELDS = [
    "market", "filing_id", "filing_date", "company_name", "stock_code",
    "title", "category", "source_url", "document_url", "local_path",
    "downloaded_at", "raw_metadata",
]


@dataclass
class XbrlState:
    page: int = 1
    downloaded: int = 0
    skipped_existing: int = 0
    no_package: int = 0
    failed: int = 0
    updated_at: str = ""


# --- naming -------------------------------------------------------------


def taxonomy_of(fxo_id: str) -> str:
    """Taxonomy segment of an fxo_id, e.g. ESEF or UAIFRS.

    Format is `{entity}-{period_end}-{TAXONOMY}-{COUNTRY}-{seq}`, and the
    entity part may itself contain dashes (`EDRPOU-32033791-...`), so read
    from the right.
    """
    parts = str(fxo_id or "").split("-")
    if len(parts) < 3:
        return "UNKNOWN"
    return parts[-3] or "UNKNOWN"


def market_for(attrs: Dict[str, Any]) -> str:
    country = str(attrs.get("country") or "XX").upper()
    return f"{taxonomy_of(attrs.get('fxo_id', ''))}_{country}"


def local_path_for(data_root: str, attrs: Dict[str, Any]) -> str:
    """Mirror the layout the existing 15,875 rows already use."""
    fxo_id = str(attrs.get("fxo_id") or "")
    country = str(attrs.get("country") or "XX").upper()
    period_end = str(attrs.get("period_end") or "unknown")
    entity = fxo_id.split(f"-{period_end}-")[0] if period_end in fxo_id else fxo_id
    return os.path.join(
        str(data_root), "markets", country.lower(), "01_raw",
        "europe_annual_reports", "xbrl_org", country, entity, period_end,
        fxo_id, f"{entity}-{period_end}.zip",
    )


def absolute_url(url: str) -> str:
    url = str(url or "")
    if url.startswith("http"):
        return url
    return f"{API_BASE}{url}"


# --- diffing ------------------------------------------------------------


def has_package(attrs: Dict[str, Any]) -> bool:
    return str(attrs.get("package_url") or "None").strip() not in ("None", "", "null")


def select_missing(
    filings: Iterable[Dict[str, Any]], existing_ids: Set[str]
) -> List[Dict[str, Any]]:
    """Filings we do not have and that actually offer a package."""
    out = []
    for filing in filings:
        attrs = filing.get("attributes", {})
        fxo_id = str(attrs.get("fxo_id") or "")
        if not fxo_id or fxo_id in existing_ids:
            continue
        if not has_package(attrs):
            continue
        out.append(filing)
    return out


# --- integrity ----------------------------------------------------------


def sha256_status(expected: Optional[str], actual: Optional[str]) -> str:
    """MISMATCH must mean "we have a file and its digest is wrong".

    A failed download has no digest at all, so calling that MISMATCH would
    conflate corruption with absence and send someone hunting a data-integrity
    problem that is really a network failure.
    """
    if not expected or not actual:
        return "unverified"
    return "match" if str(expected) == str(actual) else "MISMATCH"


def sha256_of(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fin:
        for chunk in iter(lambda: fin.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


# --- rows ---------------------------------------------------------------


def build_row(
    filing: Dict[str, Any], local_path: str, entity_name: str, sha256_actual: str
) -> Dict[str, str]:
    attrs = filing.get("attributes", {})
    fxo_id = str(attrs.get("fxo_id") or "")
    period_end = str(attrs.get("period_end") or "")
    entity = fxo_id.split(f"-{period_end}-")[0] if period_end and period_end in fxo_id else fxo_id
    document_url = absolute_url(attrs.get("package_url") or "")
    expected = attrs.get("sha256")
    raw = {
        "api_url": f"/api/filings/{filing.get('id')}",
        "country": attrs.get("country"),
        "date_added": attrs.get("date_added"),
        "download_kind": "package",
        "entity_identifier": entity,
        "entity_name": entity_name,
        "error_count": attrs.get("error_count"),
        "filing_id": filing.get("id"),
        "fxo_id": fxo_id,
        "inconsistency_count": attrs.get("inconsistency_count"),
        "json_url": attrs.get("json_url"),
        "local_path": local_path,
        "market": market_for(attrs),
        "package_url": attrs.get("package_url"),
        "period_end": period_end,
        "report_url": attrs.get("report_url"),
        "retrieved_at": utc_now(),
        "sha256": expected,
        "sha256_actual": sha256_actual,
        "sha256_expected": expected,
        "sha256_status": sha256_status(expected, sha256_actual),
        "source": "filings.xbrl.org",
        "source_url": absolute_url(attrs.get("report_url") or ""),
        "status": "downloaded" if local_path else "failed",
        "warning_count": attrs.get("warning_count"),
    }
    return {
        "market": market_for(attrs),
        "filing_id": fxo_id,
        "filing_date": str(attrs.get("date_added") or "")[:10],
        "company_name": entity_name,
        "stock_code": entity,
        "title": f"Annual financial report {period_end}",
        "category": taxonomy_of(fxo_id),
        "source_url": absolute_url(attrs.get("report_url") or ""),
        "document_url": document_url,
        "local_path": local_path,
        "downloaded_at": utc_now(),
        "raw_metadata": json.dumps(raw, ensure_ascii=False, sort_keys=True),
    }


# --- helpers ------------------------------------------------------------


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0, tzinfo=None).isoformat() + "Z"


def log(message: str) -> None:
    print(f"{utc_now()} {message}", flush=True)


def build_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=5, read=5, connect=5, backoff_factor=1.0,
        status_forcelist=(429, 500, 502, 503, 504), allowed_methods=("GET",),
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update({"User-Agent": USER_AGENT})
    return session


def existing_filing_ids(metadata_path: Path) -> Set[str]:
    """fxo_ids already recorded, so a rerun is a cheap diff."""
    csv.field_size_limit(sys.maxsize)
    ids: Set[str] = set()
    if not metadata_path.exists():
        return ids
    with open(metadata_path, newline="", encoding="utf-8") as fin:
        for row in csv.DictReader(fin):
            if "_" in row.get("market", "") and row.get("filing_id"):
                ids.add(row["filing_id"])
    return ids


def entity_names(payload: Dict[str, Any]) -> Dict[str, str]:
    """Map entity resource id -> name from the `included` block."""
    names = {}
    for item in payload.get("included", []) or []:
        if item.get("type") == "entity":
            names[item.get("id")] = (item.get("attributes", {}) or {}).get("name", "")
    return names


def entity_name_for(filing: Dict[str, Any], names: Dict[str, str]) -> str:
    rel = (filing.get("relationships", {}) or {}).get("entity", {}) or {}
    data = rel.get("data") or {}
    return names.get(data.get("id"), "")


def load_state(path: Path) -> XbrlState:
    if not Path(path).exists():
        return XbrlState()
    try:
        d = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return XbrlState()
    return XbrlState(
        page=int(d.get("page", 1) or 1),
        downloaded=int(d.get("downloaded", 0) or 0),
        skipped_existing=int(d.get("skipped_existing", 0) or 0),
        no_package=int(d.get("no_package", 0) or 0),
        failed=int(d.get("failed", 0) or 0),
        updated_at=str(d.get("updated_at", "")),
    )


def save_state(path: Path, state: XbrlState) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    state.updated_at = utc_now()
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(asdict(state), indent=2, sort_keys=True))
    tmp.replace(path)


def report(job_id: str, state: XbrlState, total: Optional[int]) -> None:
    """Push to the menubar tracker; never raises."""
    try:
        home = str(Path.home())
        if home not in sys.path:
            sys.path.insert(0, home)
        from job_tracker import update_job
        update_job(
            id=job_id, current=state.downloaded, total=total, status="running",
            details=[
                f"{state.downloaded} packages downloaded",
                f"{state.skipped_existing} already had, {state.no_package} offer no package",
                f"{state.failed} failed; catalogue page {state.page}",
            ],
        )
    except Exception:
        return


def register(job_id: str, total: Optional[int], log_path: str) -> None:
    try:
        home = str(Path.home())
        if home not in sys.path:
            sys.path.insert(0, home)
        from job_tracker import register_job
        register_job(
            id=job_id, title="filings.xbrl.org completion (UK/PL/DE + rest)",
            purpose=(
                "The source holds 25,675 filings; 15,875 were pulled by an "
                "ad-hoc pass with no state file. Fetch the ~9,800 never "
                "downloaded, including UK, Poland and Germany. Packages "
                "average ~16 MB so expect ~160 GB, landing on the boot volume."
            ),
            agent="claude-opus-5", pid=os.getpid(), log_path=log_path,
            progress_total=total, progress_unit="packages",
            tags=["corpus", "xbrl_org", "europe"],
        )
    except Exception:
        return


# --- driver -------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--metadata", type=Path, default=DEFAULT_METADATA)
    parser.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)
    parser.add_argument("--state-file", type=Path, default=DEFAULT_STATE)
    parser.add_argument("--page-size", type=int, default=200)
    parser.add_argument("--limit", type=int, default=0, help="0 = no limit")
    parser.add_argument("--sleep-seconds", type=float, default=1.0)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--metadata-batch", type=int, default=100)
    parser.add_argument("--log-every", type=int, default=25)
    parser.add_argument("--job-id", default="xbrl_org_completion")
    parser.add_argument("--log-path", default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from download_market_filings import try_download_binary, write_metadata

    existing = existing_filing_ids(args.metadata)
    log(f"existing filing ids in metadata: {len(existing)}")

    session = build_session()
    first = session.get(
        API_FILINGS, params={"page[size]": 1}, timeout=args.timeout,
        headers={"Accept": "application/vnd.api+json"},
    )
    first.raise_for_status()
    total_catalogue = int((first.json().get("meta") or {}).get("count") or 0)
    log(f"catalogue reports {total_catalogue} filings")

    state = load_state(args.state_file)
    log_path = str(args.log_path or Path(__file__).resolve().parents[1] / "logs" / "xbrl_org.log")
    register(args.job_id, max(total_catalogue - len(existing), 0), log_path)

    pending: List[Dict[str, str]] = []
    processed = 0
    page = state.page

    while True:
        if args.limit and processed >= args.limit:
            log(f"reached --limit {args.limit}")
            break
        resp = session.get(
            API_FILINGS,
            params={"page[size]": args.page_size, "page[number]": page, "include": "entity"},
            timeout=args.timeout, headers={"Accept": "application/vnd.api+json"},
        )
        resp.raise_for_status()
        payload = resp.json()
        rows = payload.get("data") or []
        if not rows:
            log(f"page {page} empty; catalogue exhausted")
            break

        names = entity_names(payload)
        missing = select_missing(rows, existing)
        state.skipped_existing += sum(
            1 for f in rows if str(f.get("attributes", {}).get("fxo_id")) in existing
        )
        state.no_package += sum(1 for f in rows if not has_package(f.get("attributes", {})))

        log(f"page {page}: {len(rows)} filings, {len(missing)} to fetch")
        if args.dry_run:
            for f in missing[:5]:
                a = f["attributes"]
                log(f"  would fetch {a['fxo_id']} ({a['country']}) -> {local_path_for(str(args.data_root), a)}")
            if page >= 3:
                log("(dry run stopping after 3 pages)")
                break
            page += 1
            continue

        for filing in missing:
            if args.limit and processed >= args.limit:
                break
            attrs = filing["attributes"]
            path = local_path_for(str(args.data_root), attrs)
            url = absolute_url(attrs.get("package_url"))
            name = entity_name_for(filing, names)

            if os.path.exists(path) and os.path.getsize(path) > 0:
                digest = sha256_of(path)
                pending.append(build_row(filing, path, name, digest))
                state.downloaded += 1
            elif try_download_binary(
                session=session, url=url, path=path, headers=None, timeout=args.timeout
            ):
                digest = sha256_of(path) if os.path.exists(path) else ""
                row = build_row(filing, path, name, digest)
                if json.loads(row["raw_metadata"])["sha256_status"] == "MISMATCH":
                    log(f"  SHA256 MISMATCH for {attrs['fxo_id']} -- keeping file, flagged in metadata")
                pending.append(row)
                state.downloaded += 1
            else:
                state.failed += 1
                pending.append(build_row(filing, "", name, ""))
                log(f"  failed {attrs['fxo_id']}")

            existing.add(str(attrs.get("fxo_id")))
            processed += 1
            if processed % args.log_every == 0:
                log(
                    f"{state.downloaded} downloaded / {state.failed} failed "
                    f"(page {page}, {processed} this run)"
                )
                report(args.job_id, state, max(total_catalogue - state.skipped_existing, 0))
            if len(pending) >= args.metadata_batch:
                write_metadata(str(args.metadata), pending)
                pending = []
                state.page = page
                save_state(args.state_file, state)
            time.sleep(args.sleep_seconds)

        if not (payload.get("links") or {}).get("next"):
            log("no next page; catalogue exhausted")
            state.page = page
            break
        page += 1
        state.page = page

    if pending:
        write_metadata(str(args.metadata), pending)
    save_state(args.state_file, state)
    log(
        f"done: {state.downloaded} downloaded, {state.failed} failed, "
        f"{state.skipped_existing} already had, {state.no_package} no package"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
