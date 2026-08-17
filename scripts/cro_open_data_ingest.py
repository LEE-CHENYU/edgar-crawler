#!/usr/bin/env python3
"""Ingest the CRO (Ireland) open-data portal -- all retrievable years.

Ireland had zero rows in the corpus. Probing `cro.ie` and `core.cro.ie`
returned 403 and `oam.centralbank.ie` did not connect, so the gap looked
closed. A web search surfaced `opendata.cro.ie`, a CKAN portal published under
**CC BY 4.0** that I had never probed.

It carries two datasets:

- `financial-statements` -- 2022, 2023, 2024 (230,410 rows for 2024 alone)
- `companies` -- Company Records

**This is an index, not a document corpus.** Each row names a PDF (e.g.
`130097420.pdf`) whose image is the paid part of the CRO service. That
distinction drives a deliberate choice: these rows are NOT merged into
MARKET_FILINGS_METADATA.csv. Roughly 690,000 rows with no `local_path` would
report Ireland as 0% acquired and swamp every genuine gap in the coverage
surface built for exactly that purpose. They land in their own tree instead,
with a manifest and an inventory entry.

What the index is good for: knowing precisely which Irish company filed which
accounts when, so any later acquisition (paid per-document, or targeted) is
aimed rather than speculative.

Attribution required by the licence: Companies Registration Office (Ireland),
opendata.cro.ie, CC BY 4.0.

Usage:
    python scripts/cro_open_data_ingest.py --dry-run
    python scripts/cro_open_data_ingest.py
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import requests
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

CKAN_BASE = "https://opendata.cro.ie"
DATASETS = ("financial-statements", "companies")
# New corpora land on the boot volume; OWC is at 94%.
DEFAULT_DATA_ROOT = Path("/Users/lichenyu/datasets")
INVENTORY = Path("/Volumes/OWC Express 1M2/datasets/markets/_meta/inventory.json")
USER_AGENT = "edgar-crawler/1.0 (personal securities research; fretin13@gmail.com)"
ATTRIBUTION = (
    "Companies Registration Office (Ireland), opendata.cro.ie, CC BY 4.0"
)
_YEAR_RE = re.compile(r"(?<!\d)(19|20)\d{2}(?!\d)")


# --- ckan ---------------------------------------------------------------


def package_url(dataset: str) -> str:
    return f"{CKAN_BASE}/api/3/action/package_show?id={dataset}"


def resource_url(resource: Dict[str, Any]) -> str:
    return str(resource.get("url") or "")


def select_resources(package: Dict[str, Any]) -> List[Dict[str, Any]]:
    """CSV resources that actually carry a URL."""
    result = package.get("result") or {}
    out = []
    for res in result.get("resources") or []:
        if str(res.get("format") or "").upper() != "CSV":
            continue
        if not resource_url(res):
            continue
        out.append(res)
    return out


def year_of_resource(resource: Dict[str, Any]) -> str:
    """Year from the resource name, falling back to its URL."""
    for text in (str(resource.get("name") or ""), resource_url(resource)):
        m = _YEAR_RE.search(text)
        if m:
            return m.group(0)
    return "undated"


def local_path_for(data_root: str, dataset: str, year: str) -> str:
    return os.path.join(
        str(data_root), "markets", "ie", "01_raw", "cro_open_data", dataset, f"{year}.csv"
    )


def is_index_only(dataset: str) -> bool:
    """CRO datasets index filings; they are not the documents themselves."""
    return dataset in DATASETS


# --- summary ------------------------------------------------------------


def looks_like_zip(path: Path) -> bool:
    """True when the file is really a ZIP.

    CKAN declares Company Records as `format: CSV` but serves a ZIP, which made
    csv.DictReader fail with "line contains NUL" on the PK header. Sniff the
    magic bytes rather than trusting the declared format.
    """
    try:
        with open(path, "rb") as fin:
            return fin.read(4) == b"PK\x03\x04"
    except OSError:
        return False


def extract_if_zip(path: Path) -> Path:
    """Replace a mislabelled ZIP with the CSV inside it."""
    if not looks_like_zip(path):
        return path
    import zipfile

    with zipfile.ZipFile(path) as zf:
        names = [n for n in zf.namelist() if n.lower().endswith(".csv")] or zf.namelist()
        if not names:
            return path
        inner = names[0]
        target = path.with_name(path.stem + "_extracted.csv")
        with zf.open(inner) as src, open(target, "wb") as out:
            while True:
                chunk = src.read(1024 * 1024)
                if not chunk:
                    break
                out.write(chunk)
    log(f"    resource was a ZIP despite format=CSV; extracted {inner}")
    return target


def summarise(csv_path: Path) -> Dict[str, Any]:
    """Row/company/document counts, tolerating the BOM in the live header."""
    csv.field_size_limit(sys.maxsize)
    rows = 0
    companies = set()
    named = 0
    with open(csv_path, newline="", encoding="utf-8-sig", errors="replace") as fin:
        for row in csv.DictReader(fin):
            rows += 1
            num = (row.get("company_num") or "").strip()
            if num:
                companies.add(num)
            fname = (row.get("file_name") or row.get("﻿file_name") or "").strip()
            if fname:
                named += 1
    return {"rows": rows, "companies": len(companies), "documents_named": named}


# --- helpers ------------------------------------------------------------


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def log(msg: str) -> None:
    print(f"{utc_now()} {msg}", flush=True)


def fetch_json(url: str, timeout: int = 60) -> Dict[str, Any]:
    # requests carries certifi's CA bundle; urllib in this venv cannot verify
    # opendata.cro.ie's chain.
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def download(url: str, path: Path, timeout: int = 300) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    written = 0
    with requests.get(
        url, headers={"User-Agent": USER_AGENT}, timeout=timeout, stream=True
    ) as resp:
        resp.raise_for_status()
        with open(tmp, "wb") as fout:
            for chunk in resp.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    fout.write(chunk)
                    written += len(chunk)
    tmp.replace(path)
    return written


# --- driver -------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    manifest: Dict[str, Any] = {
        "retrieved_at": utc_now(),
        "source": CKAN_BASE,
        "attribution": ATTRIBUTION,
        "index_only": True,
        "note": (
            "Index of filings, not the documents. Rows name a PDF whose image is "
            "the paid part of the CRO service. Deliberately NOT merged into "
            "MARKET_FILINGS_METADATA.csv so the acquisition-coverage surface "
            "stays meaningful."
        ),
        "datasets": {},
    }

    for dataset in DATASETS:
        pkg = fetch_json(package_url(dataset))
        title = (pkg.get("result") or {}).get("title", dataset)
        licence = (pkg.get("result") or {}).get("license_title", "?")
        resources = select_resources(pkg)
        log(f"{title} [{licence}]: {len(resources)} CSV resources")
        entries = []
        for res in resources:
            year = year_of_resource(res)
            path = Path(local_path_for(str(args.data_root), dataset, year))
            url = resource_url(res)
            if args.dry_run:
                log(f"  would fetch {year} -> {path}")
                continue
            size = download(url, path)
            path = extract_if_zip(path)
            stats = summarise(path)
            log(
                f"  {year}: {size:,} bytes, {stats['rows']:,} rows, "
                f"{stats['companies']:,} companies, {stats['documents_named']:,} docs named"
            )
            entries.append({
                "year": year, "url": url, "local_path": str(path),
                "size_bytes": size, **stats,
            })
        manifest["datasets"][dataset] = entries

    if args.dry_run:
        log("(dry run; nothing written)")
        return 0

    meta_dir = Path(args.data_root) / "markets" / "ie" / "01_raw" / "cro_open_data"
    meta_dir.mkdir(parents=True, exist_ok=True)
    (meta_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True))
    log(f"wrote {meta_dir / 'manifest.json'}")

    update_inventory(manifest, meta_dir)
    totals = {
        k: sum(e.get("rows", 0) for e in v) for k, v in manifest["datasets"].items()
    }
    log(f"done: {totals}")
    return 0


def update_inventory(manifest: Dict[str, Any], meta_dir: Path) -> None:
    if not INVENTORY.exists():
        log(f"  inventory not found at {INVENTORY}; skipping")
        return
    try:
        inv = json.loads(INVENTORY.read_text())
    except (OSError, ValueError) as exc:
        log(f"  could not read inventory: {exc}")
        return
    entry = inv.setdefault("ie", {})
    stage = entry.setdefault("01_raw", {})
    roots = stage.setdefault("live_roots", [])
    if str(meta_dir) not in roots:
        roots.append(str(meta_dir))
    stage["status"] = "live"
    stage["cro_open_data"] = {
        "index_only": True,
        "attribution": ATTRIBUTION,
        "rows_by_dataset": {
            k: sum(e.get("rows", 0) for e in v) for k, v in manifest["datasets"].items()
        },
        "retrieved_at": manifest["retrieved_at"],
    }
    INVENTORY.write_text(json.dumps(inv, indent=2, sort_keys=True))
    log(f"  updated {INVENTORY} (ie/01_raw)")


if __name__ == "__main__":
    raise SystemExit(main())
