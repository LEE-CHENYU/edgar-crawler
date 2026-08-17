#!/usr/bin/env python3
"""Pull annual reports for a country's filers from EDGAR.

Built for Ireland, which has zero rows in the corpus and no reachable domestic
source (probed 2026-08-17: `oam.centralbank.ie` does not connect, CRO and CORE
return 403, the CBI OAM page 404s, and filings.xbrl.org returns
`meta.count: 0` for `filter[country]=IE`).

400+ Ireland-based filers submit 20-F/10-K to the SEC instead, and unlike
Bundesanzeiger those are full annual reports rather than pointer notices.

Enumerates filers by EDGAR's `State` code (IE = L2), reads each one's
submissions JSON, selects annual forms, and downloads the primary document
into `raw/EDGAR_{CC}/{cik}/{accession}/`.

EDGAR asks for a descriptive User-Agent with contact details and rate limits at
10 requests/second; this stays well under that.

Usage:
    python scripts/edgar_country_pull.py --country IE --limit 5   # smoke
    python scripts/edgar_country_pull.py --country IE
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import requests
from requests.adapters import HTTPAdapter
from urllib3.util import Retry


REPO_DIR = Path(__file__).resolve().parents[1]
DEFAULT_DATA_ROOT = Path("/Volumes/OWC Express 1M2/datasets/MARKET_FILINGS")
USER_AGENT = "edgar-crawler/1.0 (personal securities research; fretin13@gmail.com)"

# EDGAR "State" codes for non-US locations.
COUNTRY_STATE_CODES = {"IE": "L2", "DE": "2M", "GB": "X0", "NL": "P7"}
ANNUAL_FORMS = ("20-F", "10-K", "40-F", "10-K405", "20-FR")

_CIK_RE = re.compile(r"<cik>(\d+)</cik>")


@dataclass
class PullState:
    country: str
    cik_index: int = 0
    downloaded: int = 0
    skipped: int = 0
    failed: int = 0
    updated_at: str = ""


# --- enumeration --------------------------------------------------------


def parse_ciks(atom_xml: str) -> List[str]:
    """CIKs from an EDGAR company-search atom feed.

    The `<company-info>` tag carries attributes, so matching on
    `<company-info>` finds nothing; read the `<cik>` elements instead.
    """
    seen: List[str] = []
    for cik in _CIK_RE.findall(atom_xml or ""):
        if cik not in seen:
            seen.append(cik)
    return seen


def market_for_country(country: str) -> str:
    return f"EDGAR_{str(country).upper()}"


# --- form selection -----------------------------------------------------


def is_annual_form(form: str) -> bool:
    """Annual reports, including amendments (`20-F/A`)."""
    base = str(form or "").split("/")[0].strip().upper()
    return bool(base) and base in ANNUAL_FORMS


def select_annual_filings(submissions: Dict[str, Any]) -> List[Dict[str, str]]:
    recent = ((submissions.get("filings") or {}).get("recent") or {})
    forms = recent.get("form") or []
    out: List[Dict[str, str]] = []
    for i, form in enumerate(forms):
        if not is_annual_form(form):
            continue

        def at(key: str) -> str:
            seq = recent.get(key) or []
            return str(seq[i]) if i < len(seq) else ""

        out.append({
            "form": str(form),
            "accession": at("accessionNumber"),
            "filing_date": at("filingDate"),
            "primary_document": at("primaryDocument"),
            "report_date": at("reportDate"),
        })
    return out


# --- urls and paths -----------------------------------------------------


def accession_no_dashes(accession: str) -> str:
    return str(accession or "").replace("-", "")


def document_url(cik: str, accession: str, primary_document: str) -> str:
    return (
        f"https://www.sec.gov/Archives/edgar/data/{str(cik).lstrip('0')}"
        f"/{accession_no_dashes(accession)}/{primary_document}"
    )


def local_path_for(
    data_root: str, country: str, cik: str, accession: str, primary_document: str
) -> str:
    return os.path.join(
        str(data_root), "raw", market_for_country(country),
        str(cik).lstrip("0"), str(accession), primary_document or "document.htm",
    )


# --- rows ---------------------------------------------------------------


def build_row(
    filing: Dict[str, str], cik: str, company: str, country: str, local_path: str
) -> Dict[str, str]:
    accession = filing.get("accession", "")
    raw = {
        "accession": accession,
        "cik": str(cik).lstrip("0"),
        "country": str(country).upper(),
        "form": filing.get("form"),
        "primary_document": filing.get("primary_document"),
        "report_date": filing.get("report_date"),
        "retrieved_at": utc_now(),
        "source": "sec.gov/EDGAR",
        "state_code": COUNTRY_STATE_CODES.get(str(country).upper()),
        "status": "downloaded" if local_path else "failed",
    }
    return {
        "market": market_for_country(country),
        "filing_id": accession,
        "filing_date": filing.get("filing_date", ""),
        "company_name": company,
        "stock_code": str(cik).lstrip("0"),
        "title": f"{filing.get('form','')} {filing.get('report_date','')}".strip(),
        "category": str(filing.get("form") or ""),
        "source_url": (
            f"https://www.sec.gov/Archives/edgar/data/{str(cik).lstrip('0')}"
            f"/{accession_no_dashes(accession)}/"
        ),
        "document_url": document_url(cik, accession, filing.get("primary_document", "")),
        "local_path": local_path,
        "downloaded_at": utc_now(),
        "raw_metadata": json.dumps(raw, ensure_ascii=False, sort_keys=True),
    }


# --- helpers ------------------------------------------------------------


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0, tzinfo=None).isoformat() + "Z"


def log(msg: str) -> None:
    print(f"{utc_now()} {msg}", flush=True)


def build_session() -> requests.Session:
    s = requests.Session()
    retry = Retry(total=5, read=5, connect=5, backoff_factor=1.0,
                  status_forcelist=(429, 500, 502, 503, 504), allowed_methods=("GET",))
    s.mount("https://", HTTPAdapter(max_retries=retry))
    s.headers.update({"User-Agent": USER_AGENT, "Accept-Encoding": "gzip, deflate"})
    return s


def load_state(path: Path, country: str) -> PullState:
    if not Path(path).exists():
        return PullState(country=country)
    try:
        d = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return PullState(country=country)
    return PullState(
        country=d.get("country", country),
        cik_index=int(d.get("cik_index", 0) or 0),
        downloaded=int(d.get("downloaded", 0) or 0),
        skipped=int(d.get("skipped", 0) or 0),
        failed=int(d.get("failed", 0) or 0),
        updated_at=str(d.get("updated_at", "")),
    )


def save_state(path: Path, state: PullState) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    state.updated_at = utc_now()
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(asdict(state), indent=2, sort_keys=True))
    tmp.replace(path)


def existing_ids(metadata_path: Path, market: str) -> Set[str]:
    import csv
    csv.field_size_limit(sys.maxsize)
    ids: Set[str] = set()
    if not Path(metadata_path).exists():
        return ids
    with open(metadata_path, newline="", encoding="utf-8") as fin:
        for row in csv.DictReader(fin):
            if row.get("market") == market and row.get("local_path", "").strip():
                ids.add(row.get("filing_id", ""))
    return ids


def enumerate_ciks(session: requests.Session, state_code: str, timeout: int) -> List[str]:
    ciks: List[str] = []
    start = 0
    while start < 2000:
        url = (
            "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany"
            f"&State={state_code}&type=&dateb=&owner=include&count=100"
            f"&start={start}&output=atom"
        )
        try:
            resp = session.get(url, timeout=timeout)
            resp.raise_for_status()
        except requests.RequestException as exc:
            log(f"  cik enumeration stopped at start={start}: {exc}")
            break
        page = parse_ciks(resp.text)
        new = [c for c in page if c not in ciks]
        ciks.extend(new)
        if len(page) < 100:
            break
        start += 100
        time.sleep(0.3)
    return ciks


# --- driver -------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--country", default="IE", choices=sorted(COUNTRY_STATE_CODES))
    parser.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)
    parser.add_argument("--state-file", type=Path, default=None)
    parser.add_argument("--limit", type=int, default=0, help="cap filers (smoke test)")
    parser.add_argument("--sleep-seconds", type=float, default=0.3)
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--metadata-batch", type=int, default=100)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if str(REPO_DIR) not in sys.path:
        sys.path.insert(0, str(REPO_DIR))
    from download_market_filings import try_download_binary, write_metadata

    country = args.country.upper()
    market = market_for_country(country)
    metadata_path = args.data_root / "metadata" / "MARKET_FILINGS_METADATA.csv"
    state_path = args.state_file or (args.data_root / f"backfill_state_edgar_{country.lower()}.json")

    session = build_session()
    state = load_state(state_path, country)
    have = existing_ids(metadata_path, market)
    log(f"{market}: {len(have)} accessions already acquired")

    ciks = enumerate_ciks(session, COUNTRY_STATE_CODES[country], args.timeout)
    log(f"{market}: {len(ciks)} filers found via EDGAR State={COUNTRY_STATE_CODES[country]}")
    if args.limit:
        ciks = ciks[: args.limit]

    pending: List[Dict[str, str]] = []
    for idx in range(state.cik_index, len(ciks)):
        cik = ciks[idx]
        state.cik_index = idx
        try:
            resp = session.get(
                f"https://data.sec.gov/submissions/CIK{cik}.json", timeout=args.timeout
            )
            resp.raise_for_status()
            subs = resp.json()
        except (requests.RequestException, ValueError) as exc:
            log(f"  CIK {cik}: submissions failed: {exc}")
            state.failed += 1
            continue

        company = str(subs.get("name") or "")
        filings = select_annual_filings(subs)
        if args.dry_run:
            log(f"  {cik} {company[:40]}: {len(filings)} annual filings")
            time.sleep(args.sleep_seconds)
            continue

        for filing in filings:
            if filing["accession"] in have:
                state.skipped += 1
                continue
            path = local_path_for(
                str(args.data_root), country, cik, filing["accession"],
                filing["primary_document"],
            )
            if os.path.exists(path) and os.path.getsize(path) > 0:
                pending.append(build_row(filing, cik, company, country, path))
                state.downloaded += 1
            elif try_download_binary(
                session=session,
                url=document_url(cik, filing["accession"], filing["primary_document"]),
                path=path, headers=None, timeout=args.timeout,
            ):
                pending.append(build_row(filing, cik, company, country, path))
                state.downloaded += 1
            else:
                pending.append(build_row(filing, cik, company, country, ""))
                state.failed += 1
            have.add(filing["accession"])
            time.sleep(args.sleep_seconds)

        log(
            f"  [{idx + 1}/{len(ciks)}] {company[:38]}: "
            f"{state.downloaded} downloaded, {state.failed} failed"
        )
        if len(pending) >= args.metadata_batch:
            write_metadata(str(metadata_path), pending)
            pending = []
            save_state(state_path, state)

    if pending:
        write_metadata(str(metadata_path), pending)
    state.cik_index = len(ciks)
    save_state(state_path, state)
    log(
        f"done: {state.downloaded} downloaded, {state.skipped} already had, "
        f"{state.failed} failed across {len(ciks)} filers"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
