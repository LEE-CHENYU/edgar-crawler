#!/usr/bin/env python3
"""Extract Korean DART XML filings into text and financial fact candidates.

This is the processing companion to ``download_market_filings.py``'s OpenDART
backfill. It intentionally works from local ZIPs only: the downloader owns API
access, while this stage turns accumulated raw files into reusable text,
summary, and candidate line-item layers.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import json
import re
import time
import zipfile
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator

from lxml import etree


DEFAULT_METADATA = (
    "/Volumes/OWC Express 1M2/datasets/MARKET_FILINGS/metadata/"
    "MARKET_FILINGS_METADATA.csv"
)

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
    "raw_metadata",
]

YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")
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

METRIC_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("revenue", re.compile(r"^(매출액|수익|영업수익|매출)\b")),
    ("cost_of_revenue", re.compile(r"^(매출원가|영업비용)\b")),
    ("gross_profit", re.compile(r"^(매출총이익|매출총손익)\b")),
    ("operating_profit", re.compile(r"^(영업이익|영업손실|영업손익)\b")),
    ("profit_before_tax", re.compile(r"^(법인세비용차감전순이익|법인세비용차감전순손익|세전이익|세전손실)\b")),
    ("income_tax_expense", re.compile(r"^(법인세비용|법인세수익)\b")),
    ("net_income", re.compile(r"^(당기순이익|당기순손실|분기순이익|반기순이익|연결당기순이익)\b")),
    ("total_assets", re.compile(r"^(자산총계|총자산)\b")),
    ("current_assets", re.compile(r"^(유동자산)\b")),
    ("non_current_assets", re.compile(r"^(비유동자산)\b")),
    ("cash_and_equivalents", re.compile(r"^(현금및현금성자산|현금 및 현금성자산)\b")),
    ("inventories", re.compile(r"^(재고자산)\b")),
    ("accounts_receivable", re.compile(r"^(매출채권|매출채권및기타채권|매출채권 및 기타채권)\b")),
    ("total_liabilities", re.compile(r"^(부채총계|총부채)\b")),
    ("current_liabilities", re.compile(r"^(유동부채)\b")),
    ("non_current_liabilities", re.compile(r"^(비유동부채)\b")),
    ("borrowings", re.compile(r"^(차입금|단기차입금|장기차입금|사채)\b")),
    ("total_equity", re.compile(r"^(자본총계|총자본|지배기업 소유주지분|자본)\b")),
    ("operating_cash_flow", re.compile(r"^(영업활동.*현금흐름|영업활동으로 인한 현금흐름|영업활동현금흐름)\b")),
    ("investing_cash_flow", re.compile(r"^(투자활동.*현금흐름|투자활동으로 인한 현금흐름|투자활동현금흐름)\b")),
    ("financing_cash_flow", re.compile(r"^(재무활동.*현금흐름|재무활동으로 인한 현금흐름|재무활동현금흐름)\b")),
    ("basic_eps", re.compile(r"^(기본주당이익|기본주당순이익|기본주당손익)\b")),
    ("diluted_eps", re.compile(r"^(희석주당이익|희석주당순이익|희석주당손익)\b")),
]


@dataclass(frozen=True)
class ZipText:
    text: str
    member_count: int
    members: list[str]
    raw_bytes: int


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--metadata", default=DEFAULT_METADATA)
    parser.add_argument("--manifest", default="")
    parser.add_argument("--write-manifest", default="")
    parser.add_argument("--output-dir", default="dart_financials")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--newest-first", action="store_true")
    parser.add_argument("--shard-index", type=int, default=0)
    parser.add_argument("--shard-count", type=int, default=1)
    parser.add_argument("--gzip-text", action="store_true")
    parser.add_argument("--gzip-facts", action="store_true")
    parser.add_argument("--log-every", type=int, default=25)
    args = parser.parse_args()

    if args.shard_count <= 0:
        parser.error("--shard-count must be positive")
    if not 0 <= args.shard_index < args.shard_count:
        parser.error("--shard-index must satisfy 0 <= shard-index < shard-count")

    rows = load_manifest(args.manifest) if args.manifest else build_manifest(load_metadata(Path(args.metadata)))
    rows.sort(key=lambda row: (row.get("filing_date", ""), row.get("filing_id", "")), reverse=args.newest_first)

    if args.write_manifest:
        write_manifest(Path(args.write_manifest), rows)
        print(f"wrote {len(rows)} DART rows to {args.write_manifest}")
        return 0

    rows = [row for idx, row in enumerate(rows) if idx % args.shard_count == args.shard_index]
    if args.offset:
        rows = rows[args.offset :]
    if args.limit:
        rows = rows[: args.limit]

    output_dir = Path(args.output_dir)
    (output_dir / "logs").mkdir(parents=True, exist_ok=True)
    events_path = output_dir / "logs" / f"dart_financials_shard_{args.shard_index:03d}.jsonl"
    started = time.time()
    totals: Counter[str] = Counter()

    for index, row in enumerate(rows, start=1):
        event = process_filing(row, output_dir, gzip_text=args.gzip_text, gzip_facts=args.gzip_facts)
        event.update({"row_offset": index, "row_total": len(rows), "shard_index": args.shard_index, "shard_count": args.shard_count})
        append_jsonl(events_path, event)
        totals[event["status"]] += 1
        if index == 1 or index % max(1, args.log_every) == 0 or index == len(rows):
            elapsed = time.time() - started
            rate = index / elapsed if elapsed else 0.0
            print(
                f"[{index}/{len(rows)}] {row.get('stock_code')} {row.get('filing_date')} "
                f"{event['status']} facts={event.get('fact_count', 0)} rate={rate:.2f}/s",
                flush=True,
            )

    print(json.dumps({"rows": len(rows), "totals": dict(totals), "events": str(events_path)}, ensure_ascii=False, indent=2))
    return 0


def load_metadata(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8", errors="replace") as fin:
        return [dict(row) for row in csv.DictReader(fin)]


def load_manifest(path: str) -> list[dict[str, str]]:
    with Path(path).open(newline="", encoding="utf-8", errors="replace") as fin:
        return [dict(row) for row in csv.DictReader(fin)]


def build_manifest(rows: Iterable[dict[str, str]]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for row in rows:
        if clean(row.get("market")).upper() != "DART":
            continue
        filing_id = safe_id(row.get("filing_id"))
        local_path = clean(row.get("local_path"))
        if not filing_id or filing_id in seen:
            continue
        if not local_path or not Path(local_path).exists():
            continue
        seen.add(filing_id)
        out.append(
            {
                "row_id": str(len(out)),
                "market": "DART",
                "filing_id": filing_id,
                "filing_date": clean_date(row.get("filing_date")),
                "company_name": clean(row.get("company_name")),
                "stock_code": clean(row.get("stock_code")),
                "title": clean(row.get("title")),
                "category": clean(row.get("category")),
                "document_url": clean(row.get("document_url")),
                "local_path": local_path,
                "source_url": clean(row.get("source_url")),
                "raw_metadata": clean(row.get("raw_metadata")),
            }
        )
    return out


def write_manifest(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    with temp_path.open("w", newline="", encoding="utf-8") as fout:
        writer = csv.DictWriter(fout, fieldnames=MANIFEST_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    temp_path.replace(path)


def process_filing(row: dict[str, str], output_dir: Path, gzip_text: bool = False, gzip_facts: bool = False) -> dict:
    filing_date = clean_date(row.get("filing_date"))
    filing_id = safe_id(row.get("filing_id"))
    summary_path = output_dir / "summaries" / filing_date / f"{filing_id}.json"
    facts_path = output_dir / "facts" / filing_date / (f"{filing_id}.jsonl.gz" if gzip_facts else f"{filing_id}.jsonl")
    text_path = output_dir / "text" / filing_date / (f"{filing_id}.txt.gz" if gzip_text else f"{filing_id}.txt")

    event = {
        "ts": utc_now(),
        "market": "DART",
        "filing_id": filing_id,
        "filing_date": filing_date,
        "company_name": clean(row.get("company_name")),
        "stock_code": clean(row.get("stock_code")),
        "title": clean(row.get("title")),
        "summary_path": str(summary_path),
        "facts_path": str(facts_path),
        "text_path": str(text_path),
    }
    try:
        if summary_path.exists() and facts_path.exists() and text_path.exists():
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            event.update({"status": "cached", "fact_count": int(summary.get("fact_count") or 0), "text_chars": int(summary.get("text_chars") or 0)})
            return event

        zip_path = Path(clean(row.get("local_path")))
        if not zip_path.exists():
            event.update({"status": "error", "error": "missing_zip"})
            return event

        zip_text = read_dart_zip_text(zip_path)
        facts, parse_summary = extract_facts_from_text(zip_text.text, row)

        summary_path.parent.mkdir(parents=True, exist_ok=True)
        facts_path.parent.mkdir(parents=True, exist_ok=True)
        text_path.parent.mkdir(parents=True, exist_ok=True)
        write_text(text_path, zip_text.text, gzip_text)
        write_jsonl(facts_path, facts, gzip_facts)
        summary = {
            "market": "DART",
            "filing_id": filing_id,
            "filing_date": filing_date,
            "company_name": clean(row.get("company_name")),
            "stock_code": clean(row.get("stock_code")),
            "title": clean(row.get("title")),
            "category": clean(row.get("category")),
            "document_url": clean(row.get("document_url")),
            "local_path": str(zip_path),
            "status": "ok" if facts else "no_facts",
            "fact_count": len(facts),
            "metrics": parse_summary["metrics"],
            "text_chars": len(zip_text.text),
            "xml_members": zip_text.member_count,
            "members": zip_text.members,
            "raw_bytes": zip_text.raw_bytes,
            "facts_path": str(facts_path),
            "text_path": str(text_path),
            "created_at": utc_now(),
        }
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        event.update({"status": summary["status"], "fact_count": len(facts), "text_chars": len(zip_text.text), "xml_members": zip_text.member_count})
        return event
    except Exception as exc:
        event.update({"status": "error", "error": f"{type(exc).__name__}: {exc}"})
        return event


def read_dart_zip_text(zip_path: Path) -> ZipText:
    texts: list[str] = []
    members: list[str] = []
    raw_bytes = 0
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            if info.is_dir() or not is_text_member(info.filename):
                continue
            raw = zf.read(info.filename)
            raw_bytes += len(raw)
            text = extract_xml_text(raw)
            if text:
                members.append(info.filename)
                texts.append(f"\n\n===== {info.filename} =====\n{text}")
    return ZipText(text="\n".join(texts).strip(), member_count=len(members), members=members, raw_bytes=raw_bytes)


def extract_xml_text(raw: bytes) -> str:
    decoded = decode_xml_bytes(raw)
    decoded = strip_invalid_xml_chars(decoded)
    parser = etree.XMLParser(recover=True, huge_tree=True, remove_blank_text=False)
    try:
        root = etree.fromstring(decoded.encode("utf-8"), parser=parser)
    except etree.XMLSyntaxError:
        return normalize_multiline(decoded)
    parts = [normalize_ws(part) for part in root.itertext()]
    parts = [part for part in parts if part]
    return normalize_multiline("\n".join(parts))


def decode_xml_bytes(raw: bytes) -> str:
    for encoding in ("utf-8", "cp949", "euc-kr"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def extract_facts_from_text(text: str, row: dict[str, str]) -> tuple[list[dict], dict]:
    facts: list[dict] = []
    current_years: list[int] = []
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = normalize_ws(raw_line)
        if not line:
            continue
        years = [int(match.group(0)) for match in YEAR_RE.finditer(line)]
        if 1 <= len(years) <= 8:
            current_years = years
        parsed = parse_metric_line(line)
        if parsed is None:
            continue
        metric, raw_label, values = parsed
        years_for_values = assign_years(values, current_years)
        for index, value in enumerate(values):
            facts.append(
                {
                    "market": "DART",
                    "filing_id": clean(row.get("filing_id")),
                    "filing_date": clean_date(row.get("filing_date")),
                    "company_name": clean(row.get("company_name")),
                    "stock_code": clean(row.get("stock_code")),
                    "title": clean(row.get("title")),
                    "metric": metric,
                    "raw_label": raw_label,
                    "numeric_value": value,
                    "value_index": index,
                    "fiscal_year": years_for_values[index] if index < len(years_for_values) else None,
                    "line_number": line_number,
                    "line_text": line,
                }
            )
    metrics = Counter(fact["metric"] for fact in facts)
    return facts, {"metrics": dict(metrics)}


def parse_metric_line(line: str) -> tuple[str, str, list[float]] | None:
    matches = list(VALUE_RE.finditer(line))
    if not matches:
        return None
    label = clean_label(line[: matches[0].start()])
    metric = metric_for_label(label)
    if not metric:
        return None
    values = [parse_number(match.group(0)) for match in matches]
    values = [value for value in values if value is not None]
    values = drop_note_numbers(values)
    if not values:
        return None
    return metric, label, values


def metric_for_label(label: str) -> str | None:
    cleaned = clean_label(label)
    for metric, pattern in METRIC_PATTERNS:
        if pattern.search(cleaned):
            return metric
    return None


def parse_number(value: str) -> float | None:
    raw = value.strip()
    negative = raw.startswith("(") and raw.endswith(")")
    cleaned = raw.replace(",", "").replace(" ", "").replace("(", "").replace(")", "").replace("−", "-")
    cleaned = re.sub(r"[^0-9.+-]", "", cleaned)
    if not cleaned or cleaned in {"+", "-", ".", "+.", "-."}:
        return None
    try:
        parsed = float(cleaned)
    except ValueError:
        return None
    return -parsed if negative and parsed > 0 else parsed


def assign_years(values: list[float], current_years: list[int]) -> list[int | None]:
    if current_years:
        return list(current_years[: len(values)]) + [None] * max(0, len(values) - len(current_years))
    return [None] * len(values)


def drop_note_numbers(values: list[float]) -> list[float]:
    out = list(values)
    while len(out) >= 3 and abs(out[0]) <= 300 and max(abs(v) for v in out[1:]) >= 1000:
        out.pop(0)
    return out


def is_text_member(name: str) -> bool:
    lowered = name.lower()
    return lowered.endswith((".xml", ".html", ".htm", ".txt"))


def write_text(path: Path, text: str, compress: bool) -> None:
    if compress:
        with gzip.open(path, "wt", encoding="utf-8") as fout:
            fout.write(text)
    else:
        path.write_text(text, encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict], compress: bool) -> None:
    opener = gzip.open if compress else open
    mode = "wt"
    with opener(path, mode, encoding="utf-8") as fout:
        for row in rows:
            fout.write(json.dumps(row, ensure_ascii=False))
            fout.write("\n")


def append_jsonl(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fout:
        fout.write(json.dumps(row, ensure_ascii=False))
        fout.write("\n")


def strip_invalid_xml_chars(text: str) -> str:
    return "".join(ch for ch in text if ch in "\t\n\r" or ord(ch) >= 32)


def normalize_multiline(text: str) -> str:
    lines = [normalize_ws(line) for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def normalize_ws(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def clean_label(value: str) -> str:
    cleaned = normalize_ws(value)
    cleaned = re.sub(r"^[\d\s.\-()]+", "", cleaned)
    return cleaned.strip(" :：\t")


def clean(value: object) -> str:
    return str(value or "").strip()


def clean_date(value: object) -> str:
    text = clean(value)
    if re.fullmatch(r"\d{8}", text):
        return f"{text[:4]}-{text[4:6]}-{text[6:]}"
    return text or "unknown"


def safe_id(value: object) -> str:
    text = clean(value)
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", text)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


if __name__ == "__main__":
    raise SystemExit(main())
