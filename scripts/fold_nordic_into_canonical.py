#!/usr/bin/env python3
"""Fold the orphaned Nasdaq Nordic/Baltic corpus into the canonical layout.

The backfill wrote to `<boot>/datasets/MARKET_FILINGS/raw/NASDAQ_NORDIC_BALTIC_NEWS/{CC}/`
with its own metadata CSV, so 13+ GB and 5,142 rows were invisible to
`markets/_meta/inventory.json` and to every coverage surface.

This moves each country directory into the canonical tree and rewrites the
`local_path` of the affected metadata rows to match, then merges those rows
into the canonical CSV.

Source and destination are on the same filesystem, so the move is an atomic
rename per country -- instant, no copying of 13 GB.

Usage:
    python scripts/fold_nordic_into_canonical.py --dry-run
    python scripts/fold_nordic_into_canonical.py --execute
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple


MARKET = "NASDAQ_NORDIC_BALTIC_NEWS"
ORPHAN_ROOT = Path("/Users/lichenyu/datasets/MARKET_FILINGS")
CANONICAL_MARKETS = Path("/Users/lichenyu/datasets/markets")
CANONICAL_METADATA = Path(
    "/Volumes/OWC Express 1M2/datasets/MARKET_FILINGS/metadata/MARKET_FILINGS_METADATA.csv"
)
INVENTORY = Path("/Volumes/OWC Express 1M2/datasets/markets/_meta/inventory.json")
SUBDIR = "nasdaq_nordic_baltic"

# Nasdaq Nordic/Baltic exchange countries -> canonical market codes.
COUNTRY_DIRS = ("SE", "DK", "FI", "IS", "LT", "LV", "EE")

METADATA_FIELDS = [
    "market", "filing_id", "filing_date", "company_name", "stock_code",
    "title", "category", "source_url", "document_url", "local_path",
    "downloaded_at", "raw_metadata",
]


def canonical_dir_for(country: str) -> Path:
    """Where a country's raw documents belong in the canonical tree."""
    return CANONICAL_MARKETS / country.lower() / "01_raw" / SUBDIR


def rewrite_local_path(local_path: str) -> Optional[str]:
    """Map an orphan-tree path to its canonical destination.

    Returns None when the path is not in the orphan tree (leave it alone).
    """
    if not local_path:
        return None
    marker = f"/raw/{MARKET}/"
    if marker not in local_path:
        return None
    tail = local_path.split(marker, 1)[1]
    parts = tail.split("/", 1)
    if len(parts) != 2:
        return None
    country, remainder = parts
    if country.upper() not in COUNTRY_DIRS:
        return None
    return str(canonical_dir_for(country) / remainder)


def read_rows(path: Path) -> List[Dict[str, str]]:
    csv.field_size_limit(sys.maxsize)
    with open(path, newline="", encoding="utf-8") as fin:
        return list(csv.DictReader(fin))


def plan_moves() -> List[Tuple[Path, Path]]:
    moves = []
    src_root = ORPHAN_ROOT / "raw" / MARKET
    for country in COUNTRY_DIRS:
        src = src_root / country
        if src.is_dir():
            moves.append((src, canonical_dir_for(country)))
    return moves


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if not (args.dry_run or args.execute):
        parser.error("pass --dry-run or --execute")

    orphan_meta = ORPHAN_ROOT / "metadata" / "MARKET_FILINGS_METADATA.csv"
    rows = read_rows(orphan_meta)
    moves = plan_moves()

    print(f"orphan rows: {len(rows)}")
    rewritten = 0
    unmapped = 0
    for row in rows:
        new_path = rewrite_local_path(row.get("local_path", ""))
        if new_path:
            row["local_path"] = new_path
            rewritten += 1
        elif row.get("local_path"):
            unmapped += 1
    print(f"local_path rewritten: {rewritten}, left alone: {unmapped}")
    print("moves:")
    for src, dst in moves:
        count = sum(1 for _ in src.rglob("*") if _.is_file())
        print(f"  {src.name}: {count} files -> {dst}")

    if args.dry_run:
        print("\n(dry run; nothing changed)")
        return 0

    # Move first, then write metadata: a row must never point at a path that
    # does not exist yet.
    for src, dst in moves:
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists():
            print(f"  SKIP {dst} already exists")
            continue
        shutil.move(str(src), str(dst))
        print(f"  moved {src.name} -> {dst}")

    # Carry the manifest and log along as provenance.
    meta_dst = CANONICAL_MARKETS / "_meta" / SUBDIR
    meta_dst.mkdir(parents=True, exist_ok=True)
    for name in ("manifest.jsonl", "download.log", "screen_run.log"):
        src = ORPHAN_ROOT / "raw" / MARKET / name
        if src.exists():
            shutil.move(str(src), str(meta_dst / name))
            print(f"  moved {name} -> {meta_dst}")

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from download_market_filings import write_metadata

    write_metadata(str(CANONICAL_METADATA), rows)
    print(f"merged {len(rows)} rows into {CANONICAL_METADATA}")

    update_inventory(moves)
    return 0


def update_inventory(moves: List[Tuple[Path, Path]]) -> None:
    """Record each country's live_root so the corpus is discoverable."""
    if not INVENTORY.exists():
        print(f"  inventory not found at {INVENTORY}; skipping")
        return
    inv = json.loads(INVENTORY.read_text())
    for _, dst in moves:
        country = dst.parents[1].name
        entry = inv.setdefault(country, {})
        stage = entry.setdefault("01_raw", {})
        roots = stage.setdefault("live_roots", [])
        if str(dst) not in roots:
            roots.append(str(dst))
        stage["status"] = "live"
        stage["note"] = (
            "Nasdaq Nordic/Baltic annual reports folded in from the orphaned "
            "MARKET_FILINGS/raw/NASDAQ_NORDIC_BALTIC_NEWS tree; lives on the "
            "boot volume, not the OWC drive"
        )
    INVENTORY.write_text(json.dumps(inv, indent=2, sort_keys=True))
    print(f"  updated {INVENTORY} with live_roots for {len(moves)} countries")


if __name__ == "__main__":
    raise SystemExit(main())
