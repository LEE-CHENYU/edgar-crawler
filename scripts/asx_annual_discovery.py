#!/usr/bin/env python3
"""Backfill ASX annual financial announcements.

This worker uses ASX's public yearly announcement pages, filters for annual
financial filings, resolves the hidden PDF URL from the announcement terms
page, and appends rows to the shared MARKET_FILINGS metadata CSV.
"""

from __future__ import annotations

import argparse
import csv
import fcntl
import json
import os
import re
import shutil
import tempfile
import time
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import parse_qs, urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util import Retry


DEFAULT_DATA_ROOT = Path("/Volumes/OWC Express 1M2/datasets/MARKET_FILINGS")
ASX_DIRECTORY_URL = "https://asx.api.markitdigital.com/asx-research/1.0/companies/directory"
ASX_ANNOUNCEMENTS_URL = "https://www.asx.com.au/asx/v2/statistics/announcements.do"
ASX_DISPLAY_URL = "https://www.asx.com.au/asx/v2/statistics/displayAnnouncement.do"
ASX_BASE_URL = "https://www.asx.com.au"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/123.0.0.0 Safari/537.36"
)

METADATA_FIELDS = [
    "market",
    "filing_id",
    "filing_date",
    "company_name",
    "stock_code",
    "title",
    "category",
    "source_url",
    "document_url",
    "local_path",
    "downloaded_at",
    "raw_metadata",
]

FALLBACK_SYMBOLS = [
    "BHP",
    "CBA",
    "CSL",
    "NAB",
    "WBC",
    "ANZ",
    "MQG",
    "WES",
    "GMG",
    "FMG",
    "RIO",
    "TLS",
    "WOW",
    "ALL",
    "COL",
    "WDS",
    "QBE",
    "SUN",
    "S32",
    "STO",
    "ORG",
    "REA",
    "XRO",
    "CPU",
    "RMD",
    "JHX",
    "AMC",
    "TCL",
    "WTC",
    "CAR",
    "SEK",
    "IAG",
    "SCG",
    "MGR",
    "GPT",
    "DXS",
    "VCX",
    "LLC",
    "REH",
    "COH",
    "SHL",
    "PME",
    "NXT",
    "ALD",
    "MPL",
    "TWE",
    "AGL",
    "APA",
    "SGP",
    "QAN",
    "JBH",
    "HVN",
    "BXB",
    "MIN",
    "NST",
    "EVN",
    "CHC",
    "AZJ",
    "BOQ",
    "BEN",
    "AMP",
    "ASX",
]


@dataclass(frozen=True)
class Company:
    symbol: str
    display_name: str = ""


@dataclass(frozen=True)
class Announcement:
    symbol: str
    company_name: str
    year: int
    filing_date: str
    filing_time: str
    title: str
    ids_id: str
    source_url: str
    terms_url: str
    pages: str
    file_size: str
    raw_text: str

    @property
    def filing_id(self) -> str:
        return f"{self.symbol}_{self.filing_date.replace('-', '')}_{self.ids_id}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)
    parser.add_argument("--state-file", type=Path)
    parser.add_argument("--company-file", type=Path)
    parser.add_argument("--metadata")
    parser.add_argument("--start-year", type=int, default=2008)
    parser.add_argument("--end-year", type=int, default=date.today().year)
    parser.add_argument("--sleep-seconds", type=float, default=1.5)
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--log-every", type=int, default=25)
    parser.add_argument("--max-symbols", type=int)
    parser.add_argument("--symbols")
    parser.add_argument("--no-download", action="store_true")
    parser.add_argument(
        "--include-preliminary-annual-financials",
        action="store_true",
        help="Also include Appendix 4E, preliminary final, and full-year results announcements.",
    )
    parser.add_argument("--force-company-refresh", action="store_true")
    args = parser.parse_args()

    data_root = args.data_root
    state_file = args.state_file or data_root / "backfill_state_asx.json"
    company_file = args.company_file or data_root / "asx_company_codes.json"
    metadata_path = Path(args.metadata or data_root / "metadata/MARKET_FILINGS_METADATA.csv")

    if not data_root.is_dir():
        raise SystemExit(f"data root is missing: {data_root}")

    session = build_session()
    companies = selected_companies(
        session=session,
        company_file=company_file,
        timeout=args.timeout,
        sleep_seconds=min(args.sleep_seconds, 0.5),
        force_refresh=args.force_company_refresh,
        explicit_symbols=args.symbols,
    )
    if args.max_symbols is not None:
        companies = companies[: args.max_symbols]
    if not companies:
        raise SystemExit("no ASX symbols available")

    seen_keys = load_existing_metadata_keys(metadata_path)
    state = load_state(state_file)
    next_year = int(state.get("asx_next_year") or args.end_year)
    symbol_index = int(state.get("asx_symbol_index") or 0)
    processed_symbols = 0
    downloaded_rows = 0
    matched_rows = 0

    print(
        f"{timestamp()} ASX annual worker starting "
        f"year={next_year} symbol_index={symbol_index}/{len(companies)} "
        f"download={str(not args.no_download).lower()}"
    )

    while next_year >= args.start_year:
        while symbol_index < len(companies):
            company = companies[symbol_index]
            source_url = announcements_url(company.symbol, next_year)
            try:
                announcements = collect_annual_announcements(
                    session=session,
                    company=company,
                    year=next_year,
                    timeout=args.timeout,
                    include_preliminary=args.include_preliminary_annual_financials,
                )
            except requests.RequestException as exc:
                print(
                    f"{timestamp()} ASX {next_year} {company.symbol}: "
                    f"request failed: {exc}"
                )
                save_state(
                    state_file,
                    next_year,
                    symbol_index,
                    len(companies),
                    company.symbol,
                    "request_failed",
                )
                sleep(args.sleep_seconds)
                symbol_index += 1
                continue

            rows: List[Dict[str, str]] = []
            for announcement in announcements:
                document_url = ""
                local_path = ""
                if not args.no_download:
                    try:
                        document_url = resolve_pdf_url(
                            session=session,
                            terms_url=announcement.terms_url,
                            timeout=args.timeout,
                        )
                        local_path = download_pdf(
                            session=session,
                            announcement=announcement,
                            document_url=document_url,
                            raw_dir=data_root / "raw",
                            timeout=args.timeout,
                        )
                        downloaded_rows += 1
                    except requests.RequestException as exc:
                        print(
                            f"{timestamp()} ASX {next_year} {company.symbol} "
                            f"{announcement.ids_id}: download failed: {exc}"
                        )
                        document_url = announcement.terms_url

                rows.append(
                    metadata_row(
                        announcement=announcement,
                        document_url=document_url or announcement.terms_url,
                        local_path=local_path,
                    )
                )

            if rows:
                new_rows = append_metadata(metadata_path, rows, seen_keys)
                matched_rows += new_rows
                print(
                    f"{timestamp()} ASX {next_year} {company.symbol}: "
                    f"matched={len(rows)} new_metadata={new_rows} source={source_url}"
                )

            processed_symbols += 1
            symbol_index += 1
            save_state(
                state_file,
                next_year,
                symbol_index,
                len(companies),
                company.symbol,
                "running",
            )

            if args.log_every and processed_symbols % args.log_every == 0:
                print(
                    f"{timestamp()} ASX progress "
                    f"year={next_year} symbol_index={symbol_index}/{len(companies)} "
                    f"new_metadata={matched_rows} downloaded={downloaded_rows}"
                )

            sleep(args.sleep_seconds)

        next_year -= 1
        symbol_index = 0
        save_state(
            state_file,
            next_year,
            symbol_index,
            len(companies),
            "",
            "running" if next_year >= args.start_year else "complete",
        )

    print(
        f"{timestamp()} ASX annual worker complete "
        f"new_metadata={matched_rows} downloaded={downloaded_rows}"
    )
    return 0


def selected_companies(
    session: requests.Session,
    company_file: Path,
    timeout: int,
    sleep_seconds: float,
    force_refresh: bool,
    explicit_symbols: Optional[str],
) -> List[Company]:
    if explicit_symbols:
        return [
            Company(symbol=clean_symbol(symbol))
            for symbol in explicit_symbols.split(",")
            if clean_symbol(symbol)
        ]

    if company_file.exists() and not force_refresh:
        data = load_json(company_file)
        companies = parse_company_payload(data)
        if companies:
            return companies

    try:
        companies = fetch_asx_directory(
            session=session, timeout=timeout, sleep_seconds=sleep_seconds
        )
    except requests.RequestException as exc:
        print(f"{timestamp()} ASX directory fetch failed: {exc}; using fallback symbols")
        companies = []

    if not companies:
        companies = [Company(symbol=symbol) for symbol in FALLBACK_SYMBOLS]

    write_json(
        company_file,
        {
            "fetched_at": timestamp(),
            "company_count": len(companies),
            "companies": [
                {"symbol": company.symbol, "displayName": company.display_name}
                for company in companies
            ],
        },
    )
    return companies


def fetch_asx_directory(
    session: requests.Session, timeout: int, sleep_seconds: float
) -> List[Company]:
    companies: List[Company] = []
    seen: set[str] = set()
    page = 1
    total_count = None

    while True:
        response = session.get(
            ASX_DIRECTORY_URL,
            params={"page": page, "items": 25},
            headers=default_headers("application/json"),
            timeout=timeout,
        )
        response.raise_for_status()
        payload = response.json().get("data", {})
        total_count = int(payload.get("count") or total_count or 0)
        items = payload.get("items") or []
        if not items:
            break

        for item in items:
            symbol = clean_symbol(item.get("symbol", ""))
            if symbol and symbol not in seen:
                seen.add(symbol)
                companies.append(
                    Company(
                        symbol=symbol,
                        display_name=str(item.get("displayName") or "").strip(),
                    )
                )

        print(
            f"{timestamp()} ASX directory page={page} "
            f"companies={len(companies)}/{total_count or '?'}"
        )
        if total_count and len(companies) >= total_count:
            break
        page += 1
        sleep(sleep_seconds)

    return companies


def collect_annual_announcements(
    session: requests.Session,
    company: Company,
    year: int,
    timeout: int,
    include_preliminary: bool = False,
) -> List[Announcement]:
    url = announcements_url(company.symbol, year)
    response = session.get(
        url,
        headers=default_headers("text/html,application/xhtml+xml"),
        timeout=timeout,
    )
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")
    announcements: List[Announcement] = []
    for link in soup.find_all("a", href=True):
        href = str(link.get("href") or "")
        if "displayAnnouncement.do" not in href:
            continue

        title = extract_link_title(link)
        if not is_annual_financial_title(
            title, include_preliminary=include_preliminary
        ):
            continue

        terms_url = urljoin(ASX_BASE_URL, href)
        ids_id = extract_ids_id(terms_url)
        if not ids_id:
            continue

        row = link.find_parent("tr")
        row_text = clean_spaces(row.get_text(" ", strip=True) if row else link.get_text(" ", strip=True))
        filing_date, filing_time = parse_announcement_datetime(row_text)
        if not filing_date:
            filing_date = f"{year}-01-01"
        pages, file_size = parse_page_and_size(row_text)

        announcements.append(
            Announcement(
                symbol=company.symbol,
                company_name=company.display_name,
                year=year,
                filing_date=filing_date,
                filing_time=filing_time,
                title=title,
                ids_id=ids_id,
                source_url=url,
                terms_url=terms_url,
                pages=pages,
                file_size=file_size,
                raw_text=row_text,
            )
        )

    return announcements


def is_annual_financial_title(
    title: str, include_preliminary: bool = False
) -> bool:
    text = title.lower()
    strict_annual_terms = (
        "annual report",
        "annual financial report",
        "annual financial statements",
    )
    preliminary_terms = (
        "appendix 4e",
        "preliminary final report",
        "preliminary final",
        "full year results",
        "full-year results",
        "full year financial",
        "full-year financial",
        "full year profit",
        "full-year profit",
        "full year report",
        "full-year report",
        "year end results",
        "year-end results",
    )
    has_strict_annual_term = any(term in text for term in strict_annual_terms)
    has_preliminary_term = any(term in text for term in preliminary_terms)
    if not has_strict_annual_term and not (
        include_preliminary and has_preliminary_term
    ):
        return False

    if has_strict_annual_term:
        return True

    exclusions = (
        "presentation",
        "media release",
        "pillar 3",
        "sustainability",
        "corporate governance",
        "notice of meeting",
        "notice of annual general meeting",
        "agm",
        "dividend",
        "distribution",
        "buy-back",
        "buyback",
        "substantial holder",
        "ceasing to be",
        "becoming a substantial",
        "change in substantial",
        "issued capital",
        "notification of",
        "quarter",
        "quarterly",
        "half year",
        "half-year",
        "interim",
    )
    return not any(term in text for term in exclusions)


def resolve_pdf_url(session: requests.Session, terms_url: str, timeout: int) -> str:
    response = session.get(
        terms_url,
        headers=default_headers("text/html,application/xhtml+xml"),
        timeout=timeout,
    )
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    field = soup.find("input", attrs={"name": "pdfURL"})
    if field and field.get("value"):
        return str(field["value"])
    raise requests.RequestException(f"ASX pdfURL not found at {terms_url}")


def download_pdf(
    session: requests.Session,
    announcement: Announcement,
    document_url: str,
    raw_dir: Path,
    timeout: int,
) -> str:
    local_path = (
        raw_dir
        / "ASX"
        / announcement.filing_date.replace("-", "")
        / safe_filename(announcement.symbol)
        / f"{safe_filename(announcement.filing_id)}.pdf"
    )
    if local_path.exists() and local_path.stat().st_size > 0:
        return str(local_path)

    local_path.parent.mkdir(parents=True, exist_ok=True)
    response = session.get(
        document_url,
        headers=default_headers("application/pdf,*/*"),
        timeout=timeout,
        stream=True,
    )
    response.raise_for_status()
    content_type = response.headers.get("content-type", "").lower()
    if "pdf" not in content_type:
        raise requests.RequestException(
            f"expected PDF for {document_url}, got {content_type or 'unknown content type'}"
        )

    temp_fd, temp_path = tempfile.mkstemp(prefix=".download_", dir=local_path.parent)
    try:
        with os.fdopen(temp_fd, "wb") as fout:
            for chunk in response.iter_content(chunk_size=1024 * 256):
                if chunk:
                    fout.write(chunk)
        shutil.move(temp_path, local_path)
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)
    return str(local_path)


def metadata_row(
    announcement: Announcement, document_url: str, local_path: str
) -> Dict[str, str]:
    raw_metadata = {
        "ids_id": announcement.ids_id,
        "year": announcement.year,
        "filing_time": announcement.filing_time,
        "pages": announcement.pages,
        "file_size": announcement.file_size,
        "terms_url": announcement.terms_url,
        "raw_text": announcement.raw_text,
    }
    return {
        "market": "ASX",
        "filing_id": announcement.filing_id,
        "filing_date": announcement.filing_date,
        "company_name": announcement.company_name,
        "stock_code": announcement.symbol,
        "title": announcement.title,
        "category": "annual_financial",
        "source_url": announcement.source_url,
        "document_url": document_url,
        "local_path": local_path,
        "downloaded_at": timestamp(),
        "raw_metadata": json.dumps(raw_metadata, ensure_ascii=False, sort_keys=True),
    }


def append_metadata(
    metadata_path: Path, rows: Sequence[Dict[str, str]], seen_keys: set[Tuple[str, str, str]]
) -> int:
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = metadata_path.with_suffix(metadata_path.suffix + ".lock")
    new_rows = []
    for row in rows:
        key = metadata_key(row)
        if key not in seen_keys:
            seen_keys.add(key)
            new_rows.append(row)
    if not new_rows:
        return 0

    with open(lock_path, "w") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        file_exists = metadata_path.exists() and metadata_path.stat().st_size > 0
        with open(metadata_path, "a", newline="", encoding="utf-8") as fout:
            writer = csv.DictWriter(fout, fieldnames=METADATA_FIELDS)
            if not file_exists:
                writer.writeheader()
            for row in new_rows:
                writer.writerow({field: row.get(field, "") for field in METADATA_FIELDS})
        fcntl.flock(lock_file, fcntl.LOCK_UN)
    return len(new_rows)


def load_existing_metadata_keys(metadata_path: Path) -> set[Tuple[str, str, str]]:
    keys: set[Tuple[str, str, str]] = set()
    if not metadata_path.exists():
        return keys
    with open(metadata_path, newline="", encoding="utf-8") as fin:
        for row in csv.DictReader(fin):
            keys.add(metadata_key(row))
    return keys


def metadata_key(row: Dict[str, str]) -> Tuple[str, str, str]:
    return (row.get("market", ""), row.get("filing_id", ""), row.get("document_url", ""))


def announcements_url(symbol: str, year: int) -> str:
    return (
        f"{ASX_ANNOUNCEMENTS_URL}?by=asxCode&asxCode={symbol}"
        f"&timeframe=Y&year={year}"
    )


def extract_link_title(link) -> str:
    text_nodes = []
    for child in link.children:
        name = getattr(child, "name", None)
        if name == "br":
            break
        if name in {"img", "span"}:
            continue
        if hasattr(child, "get_text"):
            text_nodes.append(child.get_text(" ", strip=True))
        else:
            text_nodes.append(str(child))
    title = clean_spaces(" ".join(text_nodes))
    if not title:
        title = clean_spaces(link.get_text(" ", strip=True))
    title = re.sub(r"<br\s*/?>", " ", title, flags=re.I)
    title = re.sub(r"\s+\d+\s+pages?\s+[\d.]+\s*[KMGT]?B$", "", title, flags=re.I)
    return title.strip()


def extract_ids_id(url: str) -> str:
    values = parse_qs(urlparse(url).query).get("idsId") or []
    return safe_filename(values[0], max_length=32) if values else ""


def parse_announcement_datetime(row_text: str) -> Tuple[str, str]:
    match = re.search(
        r"(\d{2})/(\d{2})/(\d{4})\s+(\d{1,2}:\d{2}\s*[ap]m)",
        row_text,
        flags=re.I,
    )
    if not match:
        return "", ""
    day, month, year, time_text = match.groups()
    try:
        parsed = datetime.strptime(
            f"{day}/{month}/{year} {time_text.upper().replace(' ', '')}",
            "%d/%m/%Y %I:%M%p",
        )
    except ValueError:
        return "", ""
    return parsed.date().isoformat(), parsed.strftime("%H:%M")


def parse_page_and_size(row_text: str) -> Tuple[str, str]:
    pages_match = re.search(r"(\d+)\s+pages?", row_text, flags=re.I)
    size_match = re.search(r"(\d+(?:\.\d+)?)\s*([KMGT]?B)", row_text, flags=re.I)
    pages = pages_match.group(1) if pages_match else ""
    file_size = "".join(size_match.groups()) if size_match else ""
    return pages, file_size


def parse_company_payload(data: Dict) -> List[Company]:
    raw_companies = data.get("companies") or data.get("company_codes") or []
    companies: List[Company] = []
    seen: set[str] = set()
    for item in raw_companies:
        if isinstance(item, str):
            symbol = clean_symbol(item)
            display_name = ""
        else:
            symbol = clean_symbol(item.get("symbol") or item.get("code") or "")
            display_name = str(
                item.get("displayName") or item.get("display_name") or item.get("name") or ""
            ).strip()
        if symbol and symbol not in seen:
            seen.add(symbol)
            companies.append(Company(symbol=symbol, display_name=display_name))
    return companies


def build_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=3,
        connect=3,
        read=3,
        backoff_factor=0.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET",),
    )
    adapter = HTTPAdapter(max_retries=retry, pool_maxsize=4)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


def default_headers(accept: str) -> Dict[str, str]:
    return {"User-Agent": USER_AGENT, "Accept": accept}


def clean_symbol(value: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())


def safe_filename(value: str, max_length: int = 120) -> str:
    value = re.sub(r"[^\w.\-]+", "_", str(value or "")).strip("._")
    return (value or "unknown")[:max_length]


def clean_spaces(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def load_state(path: Path) -> Dict:
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as fin:
        return json.load(fin)


def save_state(
    path: Path,
    next_year: int,
    symbol_index: int,
    symbol_count: int,
    last_symbol: str,
    status: str,
) -> None:
    write_json(
        path,
        {
            "asx_next_year": next_year,
            "asx_symbol_index": symbol_index,
            "asx_symbol_count": symbol_count,
            "asx_last_symbol": last_symbol,
            "asx_status": status,
            "updated_at": timestamp(),
        },
    )


def load_json(path: Path) -> Dict:
    with open(path, encoding="utf-8") as fin:
        return json.load(fin)


def write_json(path: Path, payload: Dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_fd, temp_path = tempfile.mkstemp(prefix=".write_", dir=path.parent, text=True)
    try:
        with os.fdopen(temp_fd, "w", encoding="utf-8") as fout:
            json.dump(payload, fout, ensure_ascii=False, indent=2, sort_keys=True)
            fout.write("\n")
        shutil.move(temp_path, path)
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)


def sleep(seconds: float) -> None:
    if seconds > 0:
        time.sleep(seconds)


def timestamp() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


if __name__ == "__main__":
    raise SystemExit(main())
