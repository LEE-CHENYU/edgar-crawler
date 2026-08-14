#!/usr/bin/env python3
"""Run CPU named-entity extraction over HKEX annual-report PDFs.

The worker is designed for cheap RunPod CPU pods:
1. read the HKEX annual-report manifest;
2. download each PDF, or reuse `local_path` when available;
3. extract text with Poppler `pdftotext -layout`;
4. run a spaCy NER model over paragraph chunks;
5. write per-filing aggregated entities and summaries.

Outputs are resumable. If both the summary and entity file exist, the row is
skipped on restart.
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
from collections import Counter
from dataclasses import dataclass, field
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

DEFAULT_ENTITY_LABELS = {
    "ORG",
    "PERSON",
    "GPE",
    "LOC",
    "FAC",
    "NORP",
    "PRODUCT",
    "EVENT",
    "LAW",
    "WORK_OF_ART",
}

BOILERPLATE_ENTITY_TEXT = {
    "annual report",
    "hong kong",
    "hkex",
    "stock exchange",
    "the stock exchange",
    "the company",
    "the group",
    "the board",
    "board of directors",
    "directors",
    "independent auditor",
    "auditor",
}

YEAR_RE = re.compile(r"^(?:19|20)\d{2}$")
ONLY_NUMBER_RE = re.compile(r"^[\d\s,.$%()/+-]+$")
WHITESPACE_RE = re.compile(r"\s+")


@dataclass
class EntityBucket:
    text: str
    label: str
    mention_count: int = 0
    surface_forms: Counter = field(default_factory=Counter)
    first_char: int = 0
    last_char: int = 0
    examples: list[dict] = field(default_factory=list)


@dataclass
class ProcessingResult:
    status: str
    entity_count: int
    mention_count: int
    labels: dict[str, int]
    text_chars: int
    error: str = ""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--metadata", type=Path, help="MARKET_FILINGS_METADATA.csv")
    parser.add_argument("--manifest", type=Path, help="HKEX manifest CSV to process")
    parser.add_argument("--write-manifest", type=Path, help="Write HKEX-only manifest and exit")
    parser.add_argument("--output-dir", type=Path, default=Path("hkex_ner"))
    parser.add_argument("--limit", type=int)
    parser.add_argument("--newest-first", action="store_true")
    parser.add_argument("--shard-index", type=int, default=0)
    parser.add_argument("--shard-count", type=int, default=1)
    parser.add_argument("--prefer-local", action="store_true")
    parser.add_argument("--download", action="store_true")
    parser.add_argument("--pdftotext", default="pdftotext")
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--sleep-seconds", type=float, default=0.0)
    parser.add_argument("--log-every", type=int, default=25)
    parser.add_argument("--gzip-entities", action="store_true")
    parser.add_argument("--spacy-model", default="en_core_web_sm")
    parser.add_argument(
        "--entity-labels",
        default=",".join(sorted(DEFAULT_ENTITY_LABELS)),
        help="Comma-separated spaCy labels to keep",
    )
    parser.add_argument("--min-entity-len", type=int, default=3)
    parser.add_argument("--max-examples", type=int, default=3)
    parser.add_argument("--chunk-chars", type=int, default=2500)
    parser.add_argument("--max-chars", type=int, default=0, help="0 means no cap")
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

    if shutil.which(args.pdftotext) is None:
        raise SystemExit(f"pdftotext not found: {args.pdftotext}")

    rows = load_manifest(args.manifest)
    if args.newest_first:
        rows.sort(key=lambda row: row.get("filing_date", ""), reverse=True)
    rows = [row for idx, row in enumerate(rows) if idx % args.shard_count == args.shard_index]
    if args.limit is not None:
        rows = rows[: args.limit]

    labels = {label.strip() for label in args.entity_labels.split(",") if label.strip()}
    if not rows:
        print("no rows to process")
        return 0

    nlp = load_spacy_model(args.spacy_model)
    nlp.max_length = max(nlp.max_length, max(1_000_000, args.chunk_chars * 4))

    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "logs").mkdir(parents=True, exist_ok=True)
    events_path = args.output_dir / "logs" / f"hkex_ner_shard_{args.shard_index:03d}.jsonl"

    session = build_session(timeout=args.timeout)
    started = time.time()
    totals = {"ok": 0, "skipped": 0, "error": 0, "no_entities": 0}

    for offset, row in enumerate(rows, start=1):
        result = process_row(
            row=row,
            output_dir=args.output_dir,
            session=session,
            prefer_local=args.prefer_local,
            allow_download=args.download,
            pdftotext=args.pdftotext,
            timeout=args.timeout,
            gzip_entities=args.gzip_entities,
            nlp=nlp,
            labels=labels,
            min_entity_len=args.min_entity_len,
            max_examples=args.max_examples,
            chunk_chars=args.chunk_chars,
            max_chars=args.max_chars,
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
            "entity_count": result.entity_count,
            "mention_count": result.mention_count,
            "labels": result.labels,
            "text_chars": result.text_chars,
            "error": result.error,
        }
        append_jsonl(events_path, event)

        if offset == 1 or offset % max(1, args.log_every) == 0:
            elapsed = time.time() - started
            rate = offset / elapsed if elapsed else 0.0
            print(
                f"[{offset}/{len(rows)}] {row.get('stock_code')} {row.get('filing_date')} "
                f"{result.status} entities={result.entity_count} mentions={result.mention_count} "
                f"rate={rate:.2f}/s",
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


def load_spacy_model(model_name: str):
    try:
        import spacy
    except ImportError as exc:
        raise SystemExit("spaCy is required. Install with: python -m pip install spacy") from exc

    try:
        return spacy.load(
            model_name,
            exclude=["tagger", "parser", "attribute_ruler", "lemmatizer"],
        )
    except OSError as exc:
        raise SystemExit(
            f"spaCy model not found: {model_name}. Install with: python -m spacy download {model_name}"
        ) from exc


def load_metadata(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as fin:
        reader = csv.DictReader(fin)
        return [dict(row) for row in reader]


def build_manifest(rows: Iterable[dict[str, str]], newest_first: bool) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for idx, row in enumerate(rows):
        if clean_cell(row.get("market")).upper() != "HKEX":
            continue
        title = clean_cell(row.get("title"))
        category = clean_cell(row.get("category"))
        if "annual" not in f"{title} {category}".lower():
            continue
        out.append(
            {
                "row_id": str(idx),
                "market": "HKEX",
                "filing_id": clean_cell(row.get("filing_id")),
                "filing_date": clean_date(row.get("filing_date")),
                "company_name": clean_cell(row.get("company_name")),
                "stock_code": clean_cell(row.get("stock_code")),
                "title": title,
                "category": category,
                "document_url": clean_cell(row.get("document_url")),
                "local_path": clean_cell(row.get("local_path")),
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
    timeout: int,
    gzip_entities: bool,
    nlp,
    labels: set[str],
    min_entity_len: int,
    max_examples: int,
    chunk_chars: int,
    max_chars: int,
) -> ProcessingResult:
    filing_date = clean_date(row.get("filing_date"))
    filing_id = safe_id(row.get("filing_id") or fallback_filing_id(row))
    summary_path = output_dir / "summaries" / filing_date / f"{filing_id}.json"
    entities_path = entities_output_path(output_dir, filing_date, filing_id, gzip_entities)
    if summary_path.exists() and summary_path.stat().st_size > 0 and entities_path.exists():
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        return ProcessingResult(
            status="skipped",
            entity_count=int(summary.get("entity_count") or 0),
            mention_count=int(summary.get("mention_count") or 0),
            labels=dict(summary.get("labels") or {}),
            text_chars=int(summary.get("text_chars") or 0),
        )

    summary_path.parent.mkdir(parents=True, exist_ok=True)
    entities_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        with tempfile.TemporaryDirectory(prefix="hkex_ner_") as tmp:
            tmp_path = Path(tmp)
            pdf_path = resolve_pdf(row, tmp_path, session, prefer_local, allow_download, timeout)
            if pdf_path is None:
                return write_error_summary(row, summary_path, "error", "no_pdf_source")

            text_path = tmp_path / "document.txt"
            run_pdftotext(pdftotext, pdf_path, text_path, timeout)
            text = text_path.read_text(encoding="utf-8", errors="replace")
            if max_chars > 0 and len(text) > max_chars:
                text = text[:max_chars]

            entities = extract_entities(
                text=text,
                row=row,
                nlp=nlp,
                labels=labels,
                min_entity_len=min_entity_len,
                max_examples=max_examples,
                chunk_chars=chunk_chars,
            )
            write_entities(entities_path, entities, gzip_entities)
            label_counts = Counter(entity["label"] for entity in entities)
            mention_count = sum(int(entity["mention_count"]) for entity in entities)
            status = "ok" if entities else "no_entities"
            summary = {
                "market": "HKEX",
                "filing_id": row.get("filing_id"),
                "filing_date": row.get("filing_date"),
                "company_name": row.get("company_name"),
                "stock_code": row.get("stock_code"),
                "title": row.get("title"),
                "document_url": row.get("document_url"),
                "status": status,
                "entity_count": len(entities),
                "mention_count": mention_count,
                "labels": dict(sorted(label_counts.items())),
                "text_chars": len(text),
                "entities_path": str(entities_path),
                "created_at": utc_now(),
            }
            summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
            return ProcessingResult(
                status=status,
                entity_count=len(entities),
                mention_count=mention_count,
                labels=dict(label_counts),
                text_chars=len(text),
            )
    except Exception as exc:
        return write_error_summary(row, summary_path, "error", f"{type(exc).__name__}: {exc}")


def resolve_pdf(
    row: dict[str, str],
    tmp_path: Path,
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

    if not allow_download:
        return None

    url = clean_cell(row.get("document_url"))
    if not url:
        return None
    out_path = tmp_path / "document.pdf"
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
    with out_path.open("wb") as fout:
        for chunk in response.iter_content(chunk_size=1024 * 1024):
            if chunk:
                fout.write(chunk)
    if out_path.stat().st_size == 0:
        return None
    return out_path


def run_pdftotext(pdftotext: str, pdf_path: Path, text_path: Path, timeout: int) -> None:
    cmd = [pdftotext, "-layout", "-enc", "UTF-8", str(pdf_path), str(text_path)]
    subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)


def extract_entities(
    text: str,
    row: dict[str, str],
    nlp,
    labels: set[str],
    min_entity_len: int,
    max_examples: int,
    chunk_chars: int,
) -> list[dict]:
    buckets: dict[tuple[str, str], EntityBucket] = {}
    for chunk, start_offset in iter_text_chunks(text, chunk_chars):
        doc = nlp(chunk)
        for ent in doc.ents:
            if ent.label_ not in labels:
                continue
            surface = clean_entity_surface(ent.text)
            if not valid_entity(surface, min_entity_len):
                continue
            canonical = canonical_entity(surface)
            if canonical in BOILERPLATE_ENTITY_TEXT:
                continue
            key = (canonical, ent.label_)
            abs_start = start_offset + ent.start_char
            abs_end = start_offset + ent.end_char
            bucket = buckets.get(key)
            if bucket is None:
                bucket = EntityBucket(text=canonical, label=ent.label_, first_char=abs_start, last_char=abs_end)
                buckets[key] = bucket
            bucket.mention_count += 1
            bucket.surface_forms[surface] += 1
            bucket.last_char = abs_end
            if len(bucket.examples) < max_examples:
                bucket.examples.append(
                    {
                        "surface": surface,
                        "char_start": abs_start,
                        "char_end": abs_end,
                        "context": context_window(text, abs_start, abs_end),
                    }
                )

    filing_id = row.get("filing_id")
    filing_date = row.get("filing_date")
    stock_code = row.get("stock_code")
    out: list[dict] = []
    for bucket in buckets.values():
        top_surface = bucket.surface_forms.most_common(1)[0][0] if bucket.surface_forms else bucket.text
        out.append(
            {
                "market": "HKEX",
                "filing_id": filing_id,
                "filing_date": filing_date,
                "stock_code": stock_code,
                "company_name": row.get("company_name"),
                "entity_id": stable_entity_id(bucket.label, bucket.text),
                "label": bucket.label,
                "canonical_text": bucket.text,
                "top_surface": top_surface,
                "mention_count": bucket.mention_count,
                "surface_forms": dict(bucket.surface_forms.most_common(10)),
                "first_char": bucket.first_char,
                "last_char": bucket.last_char,
                "examples": bucket.examples,
            }
        )

    out.sort(key=lambda item: (-int(item["mention_count"]), item["label"], item["canonical_text"]))
    return out


def iter_text_chunks(text: str, chunk_chars: int) -> Iterable[tuple[str, int]]:
    start = 0
    buffer: list[str] = []
    buffer_start = 0
    buffer_len = 0
    for raw in text.splitlines(keepends=True):
        line = normalize_line(raw)
        raw_len = len(raw)
        if not line:
            start += raw_len
            continue
        if not buffer:
            buffer_start = start
        if buffer_len + len(line) + 1 > chunk_chars and buffer:
            yield "\n".join(buffer), buffer_start
            buffer = []
            buffer_len = 0
            buffer_start = start
        buffer.append(line)
        buffer_len += len(line) + 1
        start += raw_len
    if buffer:
        yield "\n".join(buffer), buffer_start


def valid_entity(text: str, min_entity_len: int) -> bool:
    stripped = text.strip(" \t\r\n\f.,;:()[]{}<>|")
    if len(stripped) < min_entity_len:
        return False
    if ONLY_NUMBER_RE.match(stripped):
        return False
    if YEAR_RE.match(stripped):
        return False
    if stripped.lower() in BOILERPLATE_ENTITY_TEXT:
        return False
    if len(stripped.split()) > 12:
        return False
    return True


def clean_entity_surface(text: str) -> str:
    return WHITESPACE_RE.sub(" ", text.replace("\f", " ")).strip(" \t\r\n.,;:()[]{}<>|")


def canonical_entity(text: str) -> str:
    text = clean_entity_surface(text)
    text = re.sub(r"^[Tt]he\s+", "", text)
    return WHITESPACE_RE.sub(" ", text).strip()


def context_window(text: str, start: int, end: int, radius: int = 180) -> str:
    left = max(0, start - radius)
    right = min(len(text), end + radius)
    return WHITESPACE_RE.sub(" ", text[left:right]).strip()


def normalize_line(text: str) -> str:
    return WHITESPACE_RE.sub(" ", text).strip()


def build_session(timeout: int) -> requests.Session:
    retry = Retry(
        total=4,
        connect=4,
        read=4,
        backoff_factor=0.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET"]),
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=8, pool_maxsize=8)
    session = requests.Session()
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    session.request = request_with_default_timeout(session.request, timeout)
    return session


def request_with_default_timeout(func, timeout: int):
    def wrapper(method, url, **kwargs):
        kwargs.setdefault("timeout", timeout)
        return func(method, url, **kwargs)

    return wrapper


def write_entities(path: Path, entities: list[dict], gzip_entities: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    opener = gzip.open if gzip_entities else open
    mode = "wt"
    with opener(path, mode, encoding="utf-8") as fout:
        for entity in entities:
            fout.write(json.dumps(entity, ensure_ascii=False, sort_keys=True))
            fout.write("\n")


def write_error_summary(
    row: dict[str, str],
    summary_path: Path,
    status: str,
    error: str,
) -> ProcessingResult:
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary = {
        "market": "HKEX",
        "filing_id": row.get("filing_id"),
        "filing_date": row.get("filing_date"),
        "company_name": row.get("company_name"),
        "stock_code": row.get("stock_code"),
        "title": row.get("title"),
        "document_url": row.get("document_url"),
        "status": status,
        "entity_count": 0,
        "mention_count": 0,
        "labels": {},
        "text_chars": 0,
        "error": error,
        "created_at": utc_now(),
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return ProcessingResult(status=status, entity_count=0, mention_count=0, labels={}, text_chars=0, error=error)


def append_jsonl(path: Path, item: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fout:
        fout.write(json.dumps(item, ensure_ascii=False, sort_keys=True))
        fout.write("\n")


def entities_output_path(output_dir: Path, filing_date: str, filing_id: str, gzip_entities: bool) -> Path:
    suffix = ".jsonl.gz" if gzip_entities else ".jsonl"
    return output_dir / "entities" / filing_date / f"{filing_id}{suffix}"


def stable_entity_id(label: str, text: str) -> str:
    digest = sha1(f"{label}\0{text.lower()}".encode("utf-8")).hexdigest()[:20]
    return f"{label.lower()}_{digest}"


def fallback_filing_id(row: dict[str, str]) -> str:
    source = "|".join(
        [
            clean_cell(row.get("stock_code")),
            clean_cell(row.get("filing_date")),
            clean_cell(row.get("document_url")),
            clean_cell(row.get("title")),
        ]
    )
    return sha1(source.encode("utf-8")).hexdigest()[:16]


def safe_id(value: str) -> str:
    value = clean_cell(value)
    value = re.sub(r"[^A-Za-z0-9_.-]+", "_", value)
    return value or "unknown"


def clean_date(value: Optional[str]) -> str:
    value = clean_cell(value)
    return re.sub(r"[^0-9]", "", value) or "unknown_date"


def clean_cell(value: Optional[str]) -> str:
    if value is None:
        return ""
    return str(value).strip()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


if __name__ == "__main__":
    sys.exit(main())

