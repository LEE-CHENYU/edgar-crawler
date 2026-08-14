#!/usr/bin/env python3
"""Download and parse EDINET XBRL ZIPs for Japanese annual filings.

This worker is intentionally provider-neutral. It can run locally, on RunPod,
or on any VM with Python, requests, lxml, and beautifulsoup4 installed.

Typical local manifest creation:
    python edinet_xbrl_pipeline.py \
        --metadata /Volumes/OWC\ Express\ 1M2/datasets/MARKET_FILINGS/metadata/MARKET_FILINGS_METADATA.csv \
        --write-manifest /tmp/edinet_annual_manifest.csv \
        --dry-run

Typical RunPod/VM execution:
    EDINET_API_KEY=... python edinet_xbrl_pipeline.py \
        --manifest /workspace/edinet_annual_manifest.csv \
        --output-dir /workspace/edinet_xbrl \
        --newest-first
"""

from __future__ import annotations

import argparse
import csv
import gzip
import json
import os
import re
import time
import zipfile
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple

import requests
from lxml import etree

EDINET_DOCUMENT_URL = "https://api.edinet-fsa.go.jp/api/v2/documents/{doc_id}"
DEFAULT_METADATA = (
    "/Volumes/OWC Express 1M2/datasets/MARKET_FILINGS/metadata/"
    "MARKET_FILINGS_METADATA.csv"
)

MANIFEST_FIELDS = [
    "doc_id",
    "filing_date",
    "company_name",
    "stock_code",
    "title",
    "category",
    "document_url",
    "pdf_path",
    "raw_metadata",
]

INLINE_FACT_NAMES = {"nonfraction", "nonnumeric"}


@dataclass(frozen=True)
class Filing:
    doc_id: str
    filing_date: str
    company_name: str
    stock_code: str
    title: str
    category: str
    document_url: str
    pdf_path: str
    raw_metadata: str


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Download EDINET type=1 XBRL ZIPs and parse iXBRL facts."
    )
    parser.add_argument("--metadata", default=DEFAULT_METADATA)
    parser.add_argument("--manifest", default="")
    parser.add_argument("--write-manifest", default="")
    parser.add_argument("--output-dir", default="edinet_xbrl_output")
    parser.add_argument("--api-key-env", default="EDINET_API_KEY")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--newest-first", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--skip-download", action="store_true")
    parser.add_argument("--timeout", type=int, default=90)
    parser.add_argument("--sleep-seconds", type=float, default=0.25)
    parser.add_argument("--log-every", type=int, default=25)
    parser.add_argument(
        "--delete-zip-after-parse",
        action="store_true",
        help="Remove each downloaded ZIP after facts/contexts/units are written.",
    )
    parser.add_argument(
        "--gzip-facts",
        action="store_true",
        help="Write fact JSONL as .jsonl.gz and treat either .jsonl or .jsonl.gz as cached.",
    )
    args = parser.parse_args()

    filings = load_filings(args.manifest, args.metadata)
    if args.newest_first:
        filings.sort(key=lambda row: (row.filing_date, row.doc_id), reverse=True)
    else:
        filings.sort(key=lambda row: (row.filing_date, row.doc_id))

    if args.offset:
        filings = filings[args.offset :]
    if args.limit:
        filings = filings[: args.limit]

    if args.write_manifest:
        write_manifest(filings, Path(args.write_manifest))

    print(
        f"{utc_now()} loaded rows={len(filings)} "
        f"manifest={bool(args.manifest)} output_dir={args.output_dir}"
    )

    if args.dry_run:
        for filing in filings[:5]:
            print(
                "\t".join(
                    [
                        filing.doc_id,
                        filing.filing_date,
                        filing.stock_code,
                        filing.company_name,
                        filing.title,
                    ]
                )
            )
        return

    api_key = os.environ.get(args.api_key_env, "")
    if not api_key and not args.skip_download:
        raise SystemExit(f"Set {args.api_key_env} or pass --skip-download")

    output_dir = Path(args.output_dir)
    ensure_dirs(output_dir)
    session = build_session()
    counters: Counter[str] = Counter()

    print(f"{utc_now()} starting EDINET XBRL pipeline rows={len(filings)}")
    for index, filing in enumerate(filings, start=1):
        result = process_filing(
            filing=filing,
            output_dir=output_dir,
            session=session,
            api_key=api_key,
            timeout=args.timeout,
            skip_download=args.skip_download,
            delete_zip_after_parse=args.delete_zip_after_parse,
            gzip_facts=args.gzip_facts,
        )
        counters[result["status"]] += 1
        append_jsonl(output_dir / "logs" / "xbrl_pipeline_events.jsonl", result)

        if args.sleep_seconds and not args.skip_download and result["status"] != "cached":
            time.sleep(args.sleep_seconds)

        if index == 1 or index % args.log_every == 0 or index == len(filings):
            print(
                f"{utc_now()} progress index={index}/{len(filings)} "
                f"ok={counters['parsed']} downloaded={counters['downloaded']} "
                f"cached={counters['cached']} errors={counters['error']}"
            )

    print(f"{utc_now()} complete {dict(counters)}")


def load_filings(manifest_path: str, metadata_path: str) -> List[Filing]:
    path = Path(manifest_path or metadata_path)
    if not path.exists():
        raise FileNotFoundError(path)

    rows: List[Filing] = []
    with path.open(newline="", encoding="utf-8") as fin:
        reader = csv.DictReader(fin)
        for row in reader:
            filing = row_to_filing(row, manifest=bool(manifest_path))
            if filing:
                rows.append(filing)
    return rows


def row_to_filing(row: Dict[str, str], manifest: bool) -> Optional[Filing]:
    if manifest:
        doc_id = clean(row.get("doc_id") or row.get("filing_id"))
        pdf_path = clean(row.get("pdf_path") or row.get("local_path"))
    else:
        if clean(row.get("market")) != "EDINET":
            return None
        doc_id = clean(row.get("filing_id"))
        pdf_path = clean(row.get("local_path"))

    title = clean(row.get("title"))
    stock_code = clean(row.get("stock_code"))
    if not doc_id or not stock_code:
        return None
    if "有価証券報告書" not in title:
        return None
    if any(token in title for token in ("内国投資信託", "訂正", "四半期", "半期")):
        return None

    document_url = clean(row.get("document_url"))
    if document_url:
        document_url = re.sub(r"[?&]type=\d+", "?type=1", document_url)

    return Filing(
        doc_id=doc_id,
        filing_date=clean(row.get("filing_date")),
        company_name=clean(row.get("company_name")),
        stock_code=stock_code,
        title=title,
        category=clean(row.get("category")),
        document_url=document_url,
        pdf_path=pdf_path,
        raw_metadata=clean(row.get("raw_metadata")),
    )


def write_manifest(filings: Iterable[Filing], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    with temp_path.open("w", newline="", encoding="utf-8") as fout:
        writer = csv.DictWriter(fout, fieldnames=MANIFEST_FIELDS)
        writer.writeheader()
        for filing in filings:
            writer.writerow({field: getattr(filing, field) for field in MANIFEST_FIELDS})
    temp_path.replace(path)


def process_filing(
    filing: Filing,
    output_dir: Path,
    session: requests.Session,
    api_key: str,
    timeout: int,
    skip_download: bool,
    delete_zip_after_parse: bool,
    gzip_facts: bool,
) -> Dict[str, Any]:
    event: Dict[str, Any] = {
        "ts": utc_now(),
        "doc_id": filing.doc_id,
        "filing_date": filing.filing_date,
        "company_name": filing.company_name,
        "stock_code": filing.stock_code,
        "title": filing.title,
    }
    try:
        zip_path = zip_path_for(output_dir, filing)
        facts_path = facts_path_for(output_dir, filing)
        facts_gzip_path = facts_path.with_suffix(facts_path.suffix + ".gz")
        contexts_path = contexts_path_for(output_dir, filing)
        units_path = units_path_for(output_dir, filing)

        cached_facts_path = existing_nonempty_path(facts_path, facts_gzip_path)
        if cached_facts_path:
            event.update(
                {
                    "status": "cached",
                    "zip_path": str(zip_path),
                    "facts_path": str(cached_facts_path),
                }
            )
            return event

        if not zip_path.exists() or not zip_path.stat().st_size:
            if skip_download:
                event.update({"status": "error", "error": "missing_zip"})
                return event
            download_xbrl_zip(session, filing.doc_id, api_key, zip_path, timeout)
            event["downloaded"] = True

        parsed = parse_xbrl_zip(zip_path, filing)
        output_facts_path = facts_gzip_path if gzip_facts else facts_path
        write_jsonl(output_facts_path, parsed["facts"])
        write_json(contexts_path, parsed["contexts"])
        write_json(units_path, parsed["units"])
        write_json(summary_path_for(output_dir, filing), parsed["summary"])
        if delete_zip_after_parse:
            zip_path.unlink(missing_ok=True)

        event.update(
            {
                "status": "downloaded" if event.get("downloaded") else "parsed",
                "zip_path": str(zip_path),
                "facts_path": str(output_facts_path),
                "facts_compressed": gzip_facts,
                "facts": len(parsed["facts"]),
                "contexts": len(parsed["contexts"]),
                "units": len(parsed["units"]),
                "ixbrl_html": parsed["summary"]["ixbrl_html"],
            }
        )
        if event["status"] == "downloaded":
            event["status"] = "parsed"
        return event
    except Exception as exc:
        event.update({"status": "error", "error": f"{type(exc).__name__}: {exc}"})
        return event


def download_xbrl_zip(
    session: requests.Session,
    doc_id: str,
    api_key: str,
    output_path: Path,
    timeout: int,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    response = session.get(
        EDINET_DOCUMENT_URL.format(doc_id=doc_id),
        params={"type": "1", "Subscription-Key": api_key},
        timeout=timeout,
    )
    response.raise_for_status()
    content_type = response.headers.get("content-type", "")
    if not response.content.startswith(b"PK"):
        preview = response.text[:300] if "text" in content_type or "json" in content_type else ""
        raise RuntimeError(f"EDINET did not return a ZIP for {doc_id}: {content_type} {preview}")
    temp_path = output_path.with_suffix(output_path.suffix + ".tmp")
    temp_path.write_bytes(response.content)
    temp_path.replace(output_path)


def parse_xbrl_zip(zip_path: Path, filing: Filing) -> Dict[str, Any]:
    facts: List[Dict[str, Any]] = []
    contexts: Dict[str, Any] = {}
    units: Dict[str, Any] = {}
    html_count = 0
    concept_counts: Counter[str] = Counter()

    with zipfile.ZipFile(zip_path) as zf:
        for name in zf.namelist():
            if not is_ixbrl_html(name):
                continue
            html_count += 1
            raw = zf.read(name)
            root = parse_xml(raw)
            contexts.update(extract_contexts(root))
            units.update(extract_units(root))
            for fact in extract_facts(root, filing, name):
                facts.append(fact)
                concept_counts[fact["concept"]] += 1

    for fact in facts:
        context = contexts.get(fact.get("context_ref") or "")
        if context:
            fact["context"] = context
        unit = units.get(fact.get("unit_ref") or "")
        if unit:
            fact["unit"] = unit

    summary = {
        "doc_id": filing.doc_id,
        "filing_date": filing.filing_date,
        "company_name": filing.company_name,
        "stock_code": filing.stock_code,
        "ixbrl_html": html_count,
        "facts": len(facts),
        "unique_concepts": len(concept_counts),
        "contexts": len(contexts),
        "units": len(units),
        "top_concepts": concept_counts.most_common(20),
    }
    return {"facts": facts, "contexts": contexts, "units": units, "summary": summary}


def parse_xml(raw: bytes) -> etree._Element:
    parser = etree.XMLParser(recover=True, huge_tree=True, remove_blank_text=False)
    return etree.fromstring(raw, parser=parser)


def extract_facts(root: etree._Element, filing: Filing, source_file: str) -> Iterator[Dict[str, Any]]:
    for elem in root.iter():
        local = local_name(elem.tag).lower()
        if local not in INLINE_FACT_NAMES:
            continue
        fact_type = "nonFraction" if local == "nonfraction" else "nonNumeric"
        concept = attr(elem, "name")
        raw_value = normalize_ws("".join(elem.itertext()))
        numeric_value = parse_numeric(raw_value, attr(elem, "scale"), attr(elem, "sign")) if fact_type == "nonFraction" else None
        yield {
            "doc_id": filing.doc_id,
            "filing_date": filing.filing_date,
            "company_name": filing.company_name,
            "stock_code": filing.stock_code,
            "title": filing.title,
            "concept": concept,
            "concept_namespace": concept.split(":", 1)[0] if ":" in concept else "",
            "concept_local_name": concept.split(":", 1)[-1],
            "fact_type": fact_type,
            "context_ref": attr(elem, "contextRef"),
            "unit_ref": attr(elem, "unitRef"),
            "decimals": attr(elem, "decimals"),
            "scale": attr(elem, "scale"),
            "sign": attr(elem, "sign"),
            "format": attr(elem, "format"),
            "nil": attr(elem, "nil") or attr(elem, "xsi:nil"),
            "raw_value": raw_value,
            "numeric_value": numeric_value,
            "source_file": source_file,
        }


def extract_contexts(root: etree._Element) -> Dict[str, Any]:
    contexts: Dict[str, Any] = {}
    for elem in root.iter():
        if local_name(elem.tag).lower() != "context":
            continue
        context_id = attr(elem, "id")
        if not context_id:
            continue
        context: Dict[str, Any] = {"id": context_id}
        for child in elem.iter():
            lname = local_name(child.tag).lower()
            text = normalize_ws("".join(child.itertext()))
            if lname == "identifier":
                context["identifier"] = text
                context["identifier_scheme"] = attr(child, "scheme")
            elif lname == "startdate":
                context["start_date"] = text
            elif lname == "enddate":
                context["end_date"] = text
            elif lname == "instant":
                context["instant"] = text

        dimensions = []
        for member in elem.iter():
            lname = local_name(member.tag).lower()
            if lname == "explicitmember":
                dimensions.append(
                    {
                        "type": "explicit",
                        "dimension": attr(member, "dimension"),
                        "value": normalize_ws("".join(member.itertext())),
                    }
                )
            elif lname == "typedmember":
                dimensions.append(
                    {
                        "type": "typed",
                        "dimension": attr(member, "dimension"),
                        "value": normalize_ws("".join(member.itertext())),
                    }
                )
        if dimensions:
            context["dimensions"] = dimensions
        contexts[context_id] = context
    return contexts


def extract_units(root: etree._Element) -> Dict[str, Any]:
    units: Dict[str, Any] = {}
    for elem in root.iter():
        if local_name(elem.tag).lower() != "unit":
            continue
        unit_id = attr(elem, "id")
        if not unit_id:
            continue
        measures = [
            normalize_ws("".join(child.itertext()))
            for child in elem.iter()
            if local_name(child.tag).lower() == "measure"
        ]
        units[unit_id] = {"id": unit_id, "measures": [m for m in measures if m]}
    return units


def parse_numeric(raw_value: str, scale: str, sign: str) -> Optional[float]:
    if not raw_value:
        return None
    cleaned = (
        raw_value.replace(",", "")
        .replace("，", "")
        .replace("△", "-")
        .replace("−", "-")
        .replace("\u00a0", "")
        .strip()
    )
    if cleaned.startswith("(") and cleaned.endswith(")"):
        cleaned = "-" + cleaned[1:-1]
    cleaned = re.sub(r"[^0-9.+\\-]", "", cleaned)
    if not cleaned or cleaned in {"+", "-", ".", "+.", "-."}:
        return None
    try:
        value = float(cleaned)
    except ValueError:
        return None
    if sign == "-":
        value *= -1
    try:
        if scale:
            value *= 10 ** int(scale)
    except ValueError:
        pass
    return value


def attr(elem: etree._Element, name: str) -> str:
    if name in elem.attrib:
        return str(elem.attrib.get(name) or "")
    lower_name = name.lower()
    for key, value in elem.attrib.items():
        if local_name(key).lower() == lower_name:
            return str(value or "")
    return ""


def local_name(name: Any) -> str:
    text = str(name)
    if text.startswith("{"):
        return text.rsplit("}", 1)[-1]
    if ":" in text:
        return text.rsplit(":", 1)[-1]
    return text


def clean(value: Optional[str]) -> str:
    return str(value or "").strip()


def normalize_ws(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def is_ixbrl_html(name: str) -> bool:
    lower = name.lower()
    return lower.endswith((".htm", ".html", ".xhtml"))


def build_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": "edgar-crawler EDINET XBRL research",
            "Accept": "application/zip,application/octet-stream,*/*",
        }
    )
    return session


def ensure_dirs(output_dir: Path) -> None:
    for child in ("zips", "facts", "contexts", "units", "summaries", "logs"):
        (output_dir / child).mkdir(parents=True, exist_ok=True)


def date_key(filing: Filing) -> str:
    return filing.filing_date.replace("-", "") or "unknown_date"


def zip_path_for(output_dir: Path, filing: Filing) -> Path:
    return output_dir / "zips" / date_key(filing) / filing.doc_id / f"{filing.doc_id}.zip"


def facts_path_for(output_dir: Path, filing: Filing) -> Path:
    return output_dir / "facts" / date_key(filing) / f"{filing.doc_id}.jsonl"


def existing_nonempty_path(*paths: Path) -> Optional[Path]:
    for path in paths:
        if path.exists() and path.stat().st_size:
            return path
    return None


def contexts_path_for(output_dir: Path, filing: Filing) -> Path:
    return output_dir / "contexts" / date_key(filing) / f"{filing.doc_id}.json"


def units_path_for(output_dir: Path, filing: Filing) -> Path:
    return output_dir / "units" / date_key(filing) / f"{filing.doc_id}.json"


def summary_path_for(output_dir: Path, filing: Filing) -> Path:
    return output_dir / "summaries" / date_key(filing) / f"{filing.doc_id}.json"


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(
        json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2),
        encoding="utf-8",
    )
    temp_path.replace(path)


def write_jsonl(path: Path, rows: Iterable[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(temp_path, "wt", encoding="utf-8") as fout:
        for row in rows:
            fout.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    temp_path.replace(path)


def append_jsonl(path: Path, row: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fout:
        fout.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


if __name__ == "__main__":
    main()
