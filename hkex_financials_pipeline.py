#!/usr/bin/env python3
"""Extract financial line-item candidates from HKEX annual-report PDFs.

HKEX does not provide an EDINET/SEC-style XBRL package for the historical
annual-report corpus we collected, so this worker uses a conservative PDF text
pipeline:

1. read a HKEX annual-report manifest;
2. download each PDF, or reuse a local path when available;
3. extract PDF text with ``pdftotext -layout`` or a PyMuPDF fallback;
4. scan financial-statement sections for canonical line items;
5. write long JSONL fact candidates and a per-filing summary.

The output is intentionally fact-candidate oriented. It preserves the raw line,
raw label, unit hints, and value positions so downstream QA can improve metric
mapping without rerunning OCR/text extraction.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha1
from pathlib import Path
from typing import Iterable, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util import Retry


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

MANIFEST_FIELDS = [
    "row_id",
    "market",
    "filing_id",
    "filing_date",
    "company_name",
    "stock_code",
    "title",
    "category",
    "document_url",
    "local_path",
    "source_url",
]

HKEX_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/123.0.0.0 Safari/537.36"
)

TITLE_RE = re.compile(
    r"\b(consolidated\s+)?statements?\s+of\s+"
    r"(financial\s+position|comprehensive\s+income|operations|income|profit\s+or\s+loss|"
    r"cash\s+flows?|changes\s+in\s+equity)\b",
    re.I,
)
CONTENTS_LINE_RE = re.compile(r"\s+\d{1,4}\s*$")
YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")
VALUE_RE = re.compile(
    r"""
    (?<![A-Za-z0-9])
    \(?\s*
    -?
    (?:
        \d{1,3}(?:,\d{3})+(?:\.\d+)? |
        \d+(?:\.\d+)?
    )
    \s*\)?
    """,
    re.X,
)

STOP_SECTION_RE = re.compile(
    r"\b(notes?\s+to\s+(the\s+)?(consolidated\s+)?financial\s+statements|"
    r"independent\s+auditor|directors'? report|corporate governance)\b",
    re.I,
)

UNIT_RE = re.compile(
    r"(?P<currency>RMB|HK\$|US\$|USD|CNY|HKD|Renminbi|Hong Kong dollars?)"
    r"[^\n]{0,40}?"
    r"(?P<scale>million|millions|RMB'?000|HK\$'?000|US\$'?000|thousand|thousands|"
    r"in thousands|in millions)?",
    re.I,
)


METRIC_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "revenue",
        re.compile(
            r"^(?:net\s+)?revenues?(?:\s+[\u4e00-\u9fff].*)?$|"
            r"^turnover(?:\s+[\u4e00-\u9fff].*)?$",
            re.I,
        ),
    ),
    ("cost_of_revenue", re.compile(r"^cost\s+of\s+(?:sales|revenues?|goods)\b", re.I)),
    ("gross_profit", re.compile(r"^gross\s+profit\b", re.I)),
    (
        "operating_profit",
        re.compile(
            r"^(?:operating\s+(?:profit|loss)|profit/\(loss\)\s+from\s+operations|"
            r"loss\s+from\s+operations|profit\s+from\s+operations)\b",
            re.I,
        ),
    ),
    (
        "finance_costs",
        re.compile(r"^(?:finance\s+costs?|interest\s+expense|interest\s+expenses)\b", re.I),
    ),
    (
        "profit_before_tax",
        re.compile(r"^(?:profit|loss|profit/\(loss\)|loss/\(profit\))\s+before\s+(?:income\s+)?tax", re.I),
    ),
    ("income_tax_expense", re.compile(r"^(?:income\s+tax\s+expense|taxation|income\s+tax)\b", re.I)),
    (
        "profit_for_year",
        re.compile(r"^(?:profit|loss|profit/\(loss\)|loss/\(profit\)|net\s+income|net\s+loss)\s+for\s+the\s+year\b|^net\s+income\b|^net\s+loss\b", re.I),
    ),
    (
        "net_income_attributable",
        re.compile(
            r"(?:attributable\s+to\s+(?:owners|equity\s+holders|shareholders|ordinary\s+equity\s+holders)"
            r"|net\s+income\s+attributable\s+to\s+the\s+company)",
            re.I,
        ),
    ),
    ("total_assets", re.compile(r"^total\s+assets\b", re.I)),
    ("current_assets", re.compile(r"^current\s+assets\b", re.I)),
    ("non_current_assets", re.compile(r"^non-current\s+assets\b", re.I)),
    ("cash_and_equivalents", re.compile(r"^cash\s+and\s+cash\s+equivalents\b|^cash\s+and\s+bank\s+balances\b", re.I)),
    ("inventories", re.compile(r"^inventor(?:y|ies)\b", re.I)),
    ("accounts_receivable", re.compile(r"^(?:accounts|trade)\s+receivables?\b", re.I)),
    ("total_liabilities", re.compile(r"^total\s+liabilit(?:y|ies)\b", re.I)),
    ("current_liabilities", re.compile(r"^current\s+liabilit(?:y|ies)\b", re.I)),
    ("non_current_liabilities", re.compile(r"^non-current\s+liabilit(?:y|ies)\b", re.I)),
    ("accounts_payable", re.compile(r"^(?:accounts|trade)\s+payables?\b", re.I)),
    ("borrowings", re.compile(r"^(?:borrowings?|bank\s+borrowings?|loans?\s+and\s+borrowings?)\b", re.I)),
    ("notes_payable", re.compile(r"^notes?\s+payable\b", re.I)),
    ("total_equity", re.compile(r"^total\s+equity\b|^total\s+shareholders'? equity\b", re.I)),
    ("net_assets", re.compile(r"^net\s+assets\b", re.I)),
    (
        "operating_cash_flow",
        re.compile(
            r"^(?:net\s+cash\s+(?:generated\s+from|provided\s+by|used\s+in)\s+operating\s+activities|"
            r"cash\s+flows?\s+from\s+operating\s+activities)\b",
            re.I,
        ),
    ),
    (
        "investing_cash_flow",
        re.compile(
            r"^(?:net\s+cash\s+(?:generated\s+from|provided\s+by|used\s+in)\s+investing\s+activities|"
            r"cash\s+flows?\s+from\s+investing\s+activities)\b",
            re.I,
        ),
    ),
    (
        "financing_cash_flow",
        re.compile(
            r"^(?:net\s+cash\s+(?:generated\s+from|provided\s+by|used\s+in)\s+financing\s+activities|"
            r"cash\s+flows?\s+from\s+financing\s+activities)\b",
            re.I,
        ),
    ),
    (
        "capex",
        re.compile(r"^(?:purchase|purchases|payment|payments)\s+(?:of|for)\s+(?:property|fixed\s+assets|plant)", re.I),
    ),
    ("basic_eps", re.compile(r"^basic\s+(?:earnings|loss)\s+per\s+share\b|^earnings\s+per\s+share,\s+basic\b", re.I)),
    ("diluted_eps", re.compile(r"^diluted\s+(?:earnings|loss)\s+per\s+share\b|^earnings\s+per\s+share,\s+diluted\b", re.I)),
]


@dataclass
class ProcessingResult:
    status: str
    fact_count: int
    metrics: dict[str, int]
    text_chars: int
    error: str = ""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--metadata", type=Path, help="MARKET_FILINGS_METADATA.csv")
    parser.add_argument("--manifest", type=Path, help="HKEX manifest CSV to process")
    parser.add_argument("--write-manifest", type=Path, help="Write HKEX-only manifest and exit")
    parser.add_argument("--output-dir", type=Path, default=Path("hkex_financials"))
    parser.add_argument("--limit", type=int)
    parser.add_argument("--newest-first", action="store_true")
    parser.add_argument("--shard-index", type=int, default=0)
    parser.add_argument("--shard-count", type=int, default=1)
    parser.add_argument("--prefer-local", action="store_true", help="Use local_path when it exists")
    parser.add_argument("--download", action="store_true", help="Download document_url when local_path is absent")
    parser.add_argument("--pdftotext", default="pdftotext")
    parser.add_argument("--pdf-text-engine", choices=["auto", "pdftotext", "pymupdf"], default="auto")
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--sleep-seconds", type=float, default=0.0)
    parser.add_argument("--log-every", type=int, default=25)
    parser.add_argument("--gzip-facts", action="store_true")
    parser.add_argument(
        "--keep-pdf",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "Persist each downloaded PDF at "
            "<output_dir>/pdfs/<date>/<filing_id>.pdf so future text "
            "re-extraction does not need to re-download. "
            "Pass --no-keep-pdf for the old temp-dir behaviour."
        ),
    )
    parser.add_argument(
        "--keep-text",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "Persist the extracted plain text at "
            "<output_dir>/text/<date>/<filing_id>.txt[.gz] so the "
            "section/chunk pipeline can skip re-running pdftotext."
        ),
    )
    parser.add_argument(
        "--gzip-text",
        action="store_true",
        help="Gzip the persisted text file (only relevant with --keep-text).",
    )
    args = parser.parse_args()

    if args.shard_count <= 0:
        parser.error("--shard-count must be positive")
    if not (0 <= args.shard_index < args.shard_count):
        parser.error("--shard-index must satisfy 0 <= shard-index < shard-count")

    if args.write_manifest:
        if not args.metadata:
            parser.error("--metadata is required with --write-manifest")
        rows = load_metadata(args.metadata)
        manifest_rows = build_manifest(rows, newest_first=args.newest_first)
        write_manifest(args.write_manifest, manifest_rows)
        print(f"wrote {len(manifest_rows)} HKEX rows to {args.write_manifest}")
        return 0

    if not args.manifest:
        parser.error("--manifest is required unless --write-manifest is used")

    if args.pdf_text_engine == "pdftotext" and shutil.which(args.pdftotext) is None:
        raise SystemExit(f"pdftotext not found: {args.pdftotext}")

    rows = load_manifest(args.manifest)
    if args.newest_first:
        rows.sort(key=lambda row: row.get("filing_date", ""), reverse=True)
    rows = [
        row
        for idx, row in enumerate(rows)
        if idx % args.shard_count == args.shard_index
    ]
    if args.limit is not None:
        rows = rows[: args.limit]

    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "logs").mkdir(parents=True, exist_ok=True)
    events_path = args.output_dir / "logs" / f"hkex_financials_shard_{args.shard_index:03d}.jsonl"

    session = build_session(timeout=args.timeout)
    started = time.time()
    totals = {"ok": 0, "skipped": 0, "error": 0, "no_facts": 0}

    for offset, row in enumerate(rows, start=1):
        result = process_row(
            row=row,
            output_dir=args.output_dir,
            session=session,
            prefer_local=args.prefer_local,
            allow_download=args.download,
            pdftotext=args.pdftotext,
            pdf_text_engine=args.pdf_text_engine,
            timeout=args.timeout,
            gzip_facts=args.gzip_facts,
            keep_pdf=args.keep_pdf,
            keep_text=args.keep_text,
            gzip_text=args.gzip_text,
        )
        totals[result.status] = totals.get(result.status, 0) + 1
        event = {
            "ts": utc_now(),
            "row_offset": offset,
            "row_total": len(rows),
            "shard_index": args.shard_index,
            "shard_count": args.shard_count,
            "filing_id": row.get("filing_id"),
            "stock_code": row.get("stock_code"),
            "filing_date": row.get("filing_date"),
            "status": result.status,
            "fact_count": result.fact_count,
            "metric_count": len(result.metrics),
            "text_chars": result.text_chars,
            "error": result.error,
        }
        append_jsonl(events_path, event)

        if offset == 1 or offset % max(1, args.log_every) == 0:
            elapsed = time.time() - started
            rate = offset / elapsed if elapsed else 0.0
            print(
                f"[{offset}/{len(rows)}] {row.get('stock_code')} {row.get('filing_date')} "
                f"{result.status} facts={result.fact_count} rate={rate:.2f}/s",
                flush=True,
            )

        if args.sleep_seconds > 0 and result.status != "skipped":
            time.sleep(args.sleep_seconds)

    print(
        json.dumps(
            {
                "rows": len(rows),
                "shard_index": args.shard_index,
                "shard_count": args.shard_count,
                "totals": totals,
                "elapsed_seconds": round(time.time() - started, 2),
                "events": str(events_path),
            },
            indent=2,
        )
    )
    return 0


def load_metadata(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as fin:
        reader = csv.DictReader(fin)
        return [dict(row) for row in reader]


def build_manifest(rows: Iterable[dict[str, str]], newest_first: bool = False) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for row in rows:
        if str(row.get("market", "")).upper() != "HKEX":
            continue
        filing_id = clean_cell(row.get("filing_id"))
        document_url = clean_cell(row.get("document_url"))
        local_path = clean_cell(row.get("local_path"))
        if not filing_id:
            filing_id = fallback_filing_id(row)
        if not filing_id or filing_id in seen:
            continue
        if not document_url and not local_path:
            continue
        seen.add(filing_id)
        out.append(
            {
                "row_id": str(len(out)),
                "market": "HKEX",
                "filing_id": filing_id,
                "filing_date": clean_cell(row.get("filing_date")),
                "company_name": clean_cell(row.get("company_name")),
                "stock_code": normalize_stock_code(row.get("stock_code")),
                "title": html_unescape(clean_cell(row.get("title"))),
                "category": html_unescape(clean_cell(row.get("category"))),
                "document_url": document_url,
                "local_path": local_path,
                "source_url": clean_cell(row.get("source_url")),
            }
        )
    out.sort(key=lambda item: (item["filing_date"], item["filing_id"]), reverse=newest_first)
    return out


def write_manifest(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fout:
        writer = csv.DictWriter(fout, fieldnames=MANIFEST_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def load_manifest(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as fin:
        reader = csv.DictReader(fin)
        return [dict(row) for row in reader]


def process_row(
    row: dict[str, str],
    output_dir: Path,
    session: requests.Session,
    prefer_local: bool,
    allow_download: bool,
    pdftotext: str,
    pdf_text_engine: str,
    timeout: int,
    gzip_facts: bool,
    keep_pdf: bool,
    keep_text: bool,
    gzip_text: bool,
) -> ProcessingResult:
    filing_date = clean_date(row.get("filing_date"))
    filing_id = safe_id(row.get("filing_id") or fallback_filing_id(row))
    summary_path = output_dir / "summaries" / filing_date / f"{filing_id}.json"
    facts_path = facts_output_path(output_dir, filing_date, filing_id, gzip_facts)
    if summary_path.exists() and summary_path.stat().st_size > 0 and facts_path.exists():
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        return ProcessingResult(
            status="skipped",
            fact_count=int(summary.get("fact_count") or 0),
            metrics=dict(summary.get("metrics") or {}),
            text_chars=int(summary.get("text_chars") or 0),
        )

    summary_path.parent.mkdir(parents=True, exist_ok=True)
    facts_path.parent.mkdir(parents=True, exist_ok=True)

    pdf_persistent_path = (
        output_dir / "pdfs" / filing_date / f"{filing_id}.pdf"
        if keep_pdf
        else None
    )
    text_persistent_path = (
        output_dir
        / "text"
        / filing_date
        / (f"{filing_id}.txt.gz" if gzip_text else f"{filing_id}.txt")
        if keep_text
        else None
    )
    if pdf_persistent_path is not None:
        pdf_persistent_path.parent.mkdir(parents=True, exist_ok=True)
    if text_persistent_path is not None:
        text_persistent_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        with tempfile.TemporaryDirectory(prefix="hkex_financials_") as tmp:
            tmp_path = Path(tmp)
            download_target = (
                pdf_persistent_path
                if pdf_persistent_path is not None
                else tmp_path / "document.pdf"
            )
            pdf_path = resolve_pdf(
                row,
                download_target,
                session,
                prefer_local,
                allow_download,
                timeout,
            )
            if pdf_path is None:
                return write_error_summary(row, summary_path, "error", "no_pdf_source")

            extract_target = (
                text_persistent_path
                if text_persistent_path is not None and not gzip_text
                else tmp_path / "document.txt"
            )
            extract_pdf_text(pdftotext, pdf_text_engine, pdf_path, extract_target, timeout)
            text = extract_target.read_text(encoding="utf-8", errors="replace")
            if text_persistent_path is not None and gzip_text:
                with gzip.open(text_persistent_path, "wt", encoding="utf-8") as fout:
                    fout.write(text)
            facts, parse_summary = extract_facts(text, row)
            write_facts(facts_path, facts, gzip_facts)
            status = "ok" if facts else "no_facts"
            summary = {
                "market": "HKEX",
                "filing_id": row.get("filing_id"),
                "filing_date": row.get("filing_date"),
                "company_name": row.get("company_name"),
                "stock_code": row.get("stock_code"),
                "title": row.get("title"),
                "document_url": row.get("document_url"),
                "status": status,
                "fact_count": len(facts),
                "metrics": parse_summary["metrics"],
                "sections": parse_summary["sections"],
                "text_chars": len(text),
                "facts_path": str(facts_path),
                "pdf_path": (
                    str(pdf_path)
                    if pdf_path is not None and not pdf_path.is_relative_to(tmp_path)
                    else ""
                ),
                "text_path": str(text_persistent_path) if text_persistent_path is not None else "",
                "created_at": utc_now(),
            }
            summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
            return ProcessingResult(
                status=status,
                fact_count=len(facts),
                metrics=parse_summary["metrics"],
                text_chars=len(text),
            )
    except Exception as exc:  # Keep long runs resumable.
        return write_error_summary(row, summary_path, "error", f"{type(exc).__name__}: {exc}")


def resolve_pdf(
    row: dict[str, str],
    download_target: Path,
    session: requests.Session,
    prefer_local: bool,
    allow_download: bool,
    timeout: int,
) -> Optional[Path]:
    local = clean_cell(row.get("local_path"))
    if prefer_local and local:
        local_path = Path(local)
        if local_path.exists() and local_path.stat().st_size > 0:
            return local_path

    if download_target.exists() and download_target.stat().st_size > 0:
        return download_target

    if not allow_download:
        return None

    url = clean_cell(row.get("document_url"))
    if not url:
        return None
    download_target.parent.mkdir(parents=True, exist_ok=True)
    response = session.get(
        url,
        headers={
            "User-Agent": HKEX_USER_AGENT,
            "Referer": "https://www1.hkexnews.hk/search/titlesearch.xhtml?lang=en",
            "Accept": "application/pdf,*/*",
        },
        stream=True,
        timeout=timeout,
    )
    response.raise_for_status()
    with download_target.open("wb") as fout:
        for chunk in response.iter_content(chunk_size=1024 * 1024):
            if chunk:
                fout.write(chunk)
    if download_target.stat().st_size == 0:
        return None
    return download_target


def extract_pdf_text(
    pdftotext: str,
    engine: str,
    pdf_path: Path,
    text_path: Path,
    timeout: int,
) -> None:
    if engine in {"auto", "pdftotext"} and shutil.which(pdftotext) is not None:
        try:
            run_pdftotext(pdftotext, pdf_path, text_path, timeout)
            return
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            if engine == "pdftotext":
                raise

    if engine == "pdftotext":
        raise FileNotFoundError(f"pdftotext not found: {pdftotext}")

    run_pymupdf(pdf_path, text_path)


def run_pdftotext(pdftotext: str, pdf_path: Path, text_path: Path, timeout: int) -> None:
    cmd = [pdftotext, "-layout", "-enc", "UTF-8", str(pdf_path), str(text_path)]
    subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)


def run_pymupdf(pdf_path: Path, text_path: Path) -> None:
    try:
        import fitz
    except ImportError as exc:
        raise RuntimeError("PyMuPDF fallback requires: pip install PyMuPDF") from exc

    with fitz.open(pdf_path) as doc, text_path.open("w", encoding="utf-8") as fout:
        for page in doc:
            fout.write(page.get_text("text"))
            fout.write("\n\f\n")


def extract_facts(text: str, row: dict[str, str]) -> tuple[list[dict], dict]:
    lines = text.splitlines()
    fiscal_year_guess = infer_fiscal_year(row)
    current_years: list[int] = []
    current_unit = ""
    current_scale = 1
    current_section: Optional[str] = None
    section_started_at = -1
    facts: list[dict] = []
    sections: dict[str, int] = {}

    for idx, raw_line in enumerate(lines, start=1):
        line = normalize_line(raw_line)
        if not line:
            continue

        title_section = section_from_title(line)
        if title_section:
            current_section = title_section
            section_started_at = idx
            sections[current_section] = sections.get(current_section, 0) + 1
            current_years = []
            continue

        if current_section and STOP_SECTION_RE.search(line):
            current_section = None
            section_started_at = -1
            continue

        years = [int(m.group(0)) for m in YEAR_RE.finditer(line)]
        if 1 <= len(years) <= 6 and not metric_for_label(strip_numeric_tail(line)):
            current_years = years

        unit_text, scale = unit_hint(line)
        if unit_text:
            current_unit = unit_text
            current_scale = scale

        if current_section is None:
            continue
        if section_started_at > 0 and idx - section_started_at > 450:
            current_section = None
            section_started_at = -1
            continue

        parsed = parse_metric_line(line)
        if parsed is None:
            continue
        metric, label, values = parsed
        if not values:
            continue

        years_for_values = assign_years(values, current_years, fiscal_year_guess)
        for value_index, value in enumerate(values):
            fact = {
                "market": "HKEX",
                "filing_id": row.get("filing_id"),
                "filing_date": row.get("filing_date"),
                "company_name": row.get("company_name"),
                "stock_code": row.get("stock_code"),
                "title": row.get("title"),
                "document_url": row.get("document_url"),
                "section": current_section,
                "metric": metric,
                "raw_label": label,
                "value": value,
                "value_index": value_index,
                "fiscal_year": years_for_values[value_index] if value_index < len(years_for_values) else None,
                "unit_text": current_unit,
                "unit_scale": current_scale,
                "line_number": idx,
                "line_text": line,
            }
            facts.append(fact)

    metric_counts: dict[str, int] = {}
    for fact in facts:
        metric = fact["metric"]
        metric_counts[metric] = metric_counts.get(metric, 0) + 1
    return facts, {"metrics": metric_counts, "sections": sections}


def section_from_title(line: str) -> Optional[str]:
    if not TITLE_RE.search(line):
        return None
    if CONTENTS_LINE_RE.search(line) and not re.search(r"\b(as at|for the year|year ended)\b", line, re.I):
        return None
    lower = line.lower()
    if "cash flow" in lower:
        return "cash_flow_statement"
    if "financial position" in lower:
        return "financial_position_statement"
    if "changes in equity" in lower:
        return "equity_statement"
    if "comprehensive income" in lower or "operations" in lower or "income" in lower or "profit or loss" in lower:
        return "income_statement"
    return "financial_statement"


def parse_metric_line(line: str) -> Optional[tuple[str, str, list[float]]]:
    matches = list(VALUE_RE.finditer(line))
    if not matches:
        return None
    values = [parse_number(m.group(0)) for m in matches]
    values = [v for v in values if v is not None]
    if not values:
        return None

    first_value_pos = matches[0].start()
    label = clean_label(line[:first_value_pos])
    if not label:
        return None
    metric = metric_for_label(label)
    if metric is None:
        return None

    values = drop_note_numbers(values)
    if not values:
        return None
    return metric, label, values


def metric_for_label(label: str) -> Optional[str]:
    cleaned = clean_label(label)
    if not cleaned:
        return None
    for metric, pattern in METRIC_PATTERNS:
        if pattern.search(cleaned):
            return metric
    return None


def drop_note_numbers(values: list[float]) -> list[float]:
    out = list(values)
    while len(out) >= 3 and abs(out[0]) <= 200 and max(abs(v) for v in out[1:]) >= 1000:
        out.pop(0)
    return out


def assign_years(values: list[float], current_years: list[int], fiscal_year_guess: Optional[int]) -> list[Optional[int]]:
    if current_years:
        if len(current_years) >= len(values):
            return current_years[: len(values)]
        years = list(current_years)
        while len(years) < len(values):
            years.append(None)
        return years
    if fiscal_year_guess:
        return [fiscal_year_guess - idx for idx in range(len(values))]
    return [None] * len(values)


def infer_fiscal_year(row: dict[str, str]) -> Optional[int]:
    title = clean_cell(row.get("title"))
    years = [int(m.group(0)) for m in YEAR_RE.finditer(title)]
    if years:
        return max(years)
    filing_date = clean_cell(row.get("filing_date"))
    try:
        filing_year = int(filing_date[:4])
        filing_month = int(filing_date[5:7])
    except Exception:
        return None
    return filing_year - 1 if filing_month <= 9 else filing_year


def unit_hint(line: str) -> tuple[str, int]:
    match = UNIT_RE.search(line)
    if not match:
        return "", 1
    unit_text = match.group(0).strip()
    scale_text = (match.group("scale") or "").lower()
    if "million" in scale_text:
        return unit_text, 1_000_000
    if "000" in scale_text or "thousand" in scale_text:
        return unit_text, 1_000
    return unit_text, 1


def parse_number(raw: str) -> Optional[float]:
    text = raw.strip()
    if not text:
        return None
    negative = text.startswith("(") and text.endswith(")")
    text = text.strip("()").replace(",", "").replace(" ", "")
    if text.startswith("-"):
        negative = True
        text = text[1:]
    try:
        value = float(text)
    except ValueError:
        return None
    return -value if negative else value


def strip_numeric_tail(line: str) -> str:
    return VALUE_RE.sub("", line).strip()


def clean_label(label: str) -> str:
    label = re.sub(r"\s+", " ", label).strip(" .:-")
    label = label.replace("—", "-").replace("–", "-")
    return label


def normalize_line(line: str) -> str:
    line = line.replace("\u00a0", " ")
    line = line.replace("’", "'").replace("‘", "'")
    line = line.replace("（", "(").replace("）", ")")
    return re.sub(r"[ \t]+", " ", line).strip()


def clean_cell(value: object) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text.lower() in {"nan", "none", "null"} else text


def html_unescape(value: str) -> str:
    import html

    return html.unescape(value)


def normalize_stock_code(value: object) -> str:
    text = clean_cell(value)
    if not text:
        return ""
    digits = re.sub(r"\D", "", text)
    return digits.zfill(5) if digits else text


def clean_date(value: object) -> str:
    text = clean_cell(value)
    if re.match(r"\d{4}-\d{2}-\d{2}$", text):
        return text.replace("-", "")
    if re.match(r"\d{8}$", text):
        return text
    return "unknown_date"


def fallback_filing_id(row: dict[str, str]) -> str:
    key = "|".join(
        clean_cell(row.get(name))
        for name in ["filing_date", "stock_code", "company_name", "title", "document_url", "local_path"]
    )
    return sha1(key.encode("utf-8")).hexdigest()[:16]


def safe_id(value: object) -> str:
    text = clean_cell(value)
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", text)
    return text or "unknown"


def facts_output_path(output_dir: Path, filing_date: str, filing_id: str, gzip_facts: bool) -> Path:
    suffix = ".jsonl.gz" if gzip_facts else ".jsonl"
    return output_dir / "facts" / filing_date / f"{filing_id}{suffix}"


def write_facts(path: Path, facts: list[dict], gzip_facts: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    opener = gzip.open if gzip_facts else open
    mode = "wt" if gzip_facts else "w"
    with opener(path, mode, encoding="utf-8") as fout:
        for fact in facts:
            fout.write(json.dumps(fact, ensure_ascii=False, separators=(",", ":")) + "\n")


def write_error_summary(row: dict[str, str], summary_path: Path, status: str, error: str) -> ProcessingResult:
    summary = {
        "market": "HKEX",
        "filing_id": row.get("filing_id"),
        "filing_date": row.get("filing_date"),
        "company_name": row.get("company_name"),
        "stock_code": row.get("stock_code"),
        "title": row.get("title"),
        "document_url": row.get("document_url"),
        "status": status,
        "fact_count": 0,
        "metrics": {},
        "sections": {},
        "text_chars": 0,
        "error": error,
        "created_at": utc_now(),
    }
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return ProcessingResult(status=status, fact_count=0, metrics={}, text_chars=0, error=error)


def append_jsonl(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fout:
        fout.write(json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n")


def build_session(timeout: int) -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=3,
        connect=3,
        read=3,
        backoff_factor=1.0,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=16, pool_maxsize=16)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    session.request = request_with_default_timeout(session.request, timeout)
    return session


def request_with_default_timeout(func, timeout: int):
    def wrapped(method, url, **kwargs):
        kwargs.setdefault("timeout", timeout)
        return func(method, url, **kwargs)

    return wrapped


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


if __name__ == "__main__":
    sys.exit(main())
