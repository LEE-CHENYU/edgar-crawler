#!/usr/bin/env python3
"""Turn extracted text into embedding-ready chunks, on the HK contract.

Runs LOCALLY (pure CPU, no model) so the GPU pod only ever sees the small
artifact: 127,674 filings of text compress to a few GB, and chunk parquet is
smaller still than the 471 GB of PDFs they came from.

THE CONTRACT IS NOT TUNABLE. CLAUDE.md's cross-model trap says US 04_nlp uses
e5-large-v2 while HK/CN/JP use multilingual-e5-large, and cosine-comparing
across the two is invalid. Chunk GEOMETRY carries the same hazard: vectors built
from 1200-char windows are not comparable to vectors from 512-char windows even
with an identical model. These values are copied from the HK run's own config
snapshot so the six new corpora join the three existing ones in one comparable
space.

Usage:
    python scripts/build_chunks.py --corpus ASX_FINANCIALS
    python scripts/build_chunks.py --all
"""

from __future__ import annotations

import argparse
import gzip
import json
import sys
from pathlib import Path
from typing import Dict, Iterator, List

DERIVED = Path("/Volumes/OWC Express 1M2/datasets/MARKET_FILINGS/derived")

# Copied verbatim from markets/hk/04_nlp/hk_financials_nlp_full/config_snapshot/
# versions.json. Do not edit without re-embedding every market.
CONTRACT: Dict[str, object] = {
    "chunk_chars": 1200,
    "overlap_chars": 150,
    "min_chunk_chars": 180,
    "embedding_model_id": "intfloat/multilingual-e5-large",
    "embedding_dim": 1024,
    "normalize_embeddings": True,
    "schema_version": "hk_nlp_v1",
}

CORPORA = {
    "ASX_FINANCIALS": "au",
    "TWSE_FINANCIALS": "tw",
    "PSE_FINANCIALS": "ph",
    "IN_BSE_FINANCIALS": "in_bse",
    "NORDIC_FINANCIALS": "nordic",
}


def chunk_text(text: str) -> List[str]:
    """Sliding windows of chunk_chars with overlap_chars of shared tail.

    A window shorter than min_chunk_chars is dropped rather than embedded: a
    20-character fragment produces a vector that is pure noise but sits in the
    index looking like a result.
    """
    size = int(CONTRACT["chunk_chars"])
    overlap = int(CONTRACT["overlap_chars"])
    floor = int(CONTRACT["min_chunk_chars"])
    body = (text or "").strip()
    if len(body) < floor:
        return []
    if len(body) <= size:
        return [body]
    step = size - overlap
    out: List[str] = []
    start = 0
    while start < len(body):
        window = body[start:start + size]
        if len(window) >= floor:
            out.append(window)
        if start + size >= len(body):
            break
        start += step
    return out


def iter_text_files(corpus_dir: Path) -> Iterator[Path]:
    for sub in sorted(corpus_dir.glob("text*")):
        if sub.is_dir():
            yield from sorted(sub.rglob("*.txt.gz"))


def build(corpus: str, out_dir: Path, limit: int = 0) -> int:
    import pandas as pd

    corpus_dir = DERIVED / corpus
    rows = []
    files = list(iter_text_files(corpus_dir))
    if limit:
        files = files[:limit]
    for path in files:
        try:
            with gzip.open(path, "rt", encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except (OSError, EOFError):
            continue
        doc_id = path.name[:-7]  # strip .txt.gz
        for idx, chunk in enumerate(chunk_text(text)):
            rows.append({"corpus": corpus, "market": CORPORA.get(corpus, corpus),
                         "doc_id": doc_id, "chunk_index": idx, "text": chunk,
                         "source_path": str(path)})
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{corpus}_chunks.parquet"
    pd.DataFrame(rows).to_parquet(out, index=False)
    (out_dir / "contract.json").write_text(json.dumps(CONTRACT, indent=1))
    print(f"{corpus}: {len(files):,} documents -> {len(rows):,} chunks -> {out}", flush=True)
    return len(rows)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--corpus", choices=sorted(CORPORA))
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--out-dir", type=Path,
                    default=Path("/Users/lichenyu/datasets/panel/chunks"))
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    targets = sorted(CORPORA) if args.all else ([args.corpus] if args.corpus else [])
    if not targets:
        ap.error("pass --corpus or --all")
    total = sum(build(c, args.out_dir, args.limit) for c in targets)
    print(f"\ntotal chunks: {total:,}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
