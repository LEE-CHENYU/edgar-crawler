#!/usr/bin/env python3
"""Extract and persist full text from a market's PDFs -- the embedding input.

Stage 02 read every PDF, pulled ~20 numbers and discarded the text, so six
corpora have metrics but nothing to embed: ASX, IN_BSE, TWSE, PSE, NORDIC and
the Oslo attachments. Only HK (25,565) and DART (23,339) retain text.

Runs LOCALLY and at 8 workers on purpose. Measured on this machine, pdftotext
scales near-linearly to 8 workers -- 5.7/s at 2 workers vs 11.9/s at 8 on 2.9 MB
Indian PDFs, and 10.4 -> 26.4/s on smaller ASX ones -- so the job is CPU-bound,
not drive-bound. Every earlier sweep ran at 2-3 workers on the mistaken belief
that the volume was the constraint. Renting CPU loses badly here: the compute is
~3 hours for 127,674 PDFs, while shipping 471 GB to a cloud would take far
longer than that just to upload.

Text compresses ~40x against the source PDFs (HK: 127 GB -> 3.2 GB), which is
what makes the LATER step -- embedding on rented GPU -- cheap to feed.

Safeguards carried over from the outage that cost a 3.5-hour run:
  * consecutive unreadable files abort the run (a vanished volume is not a
    result), rather than being recorded as empty text
  * output is per-file, so resume is free and a crash costs nothing
  * an existing non-empty output is skipped

Usage:
    python scripts/market_text_extract.py --market in_bse --workers 8
"""

from __future__ import annotations

import argparse
import gzip
import importlib.util
import subprocess
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, List, Optional

REPO = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "_scale_mod", REPO / "scripts" / "market_scale_detect.py")
_scale = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_scale)
storage_action = _scale.storage_action

DATASETS = Path("/Volumes/OWC Express 1M2/datasets/MARKET_FILINGS")
DERIVED = DATASETS / "derived"

MARKETS: Dict[str, Dict[str, str]] = {
    "in_bse": {"raw": str(DATASETS / "raw" / "IN_BSE_ANNUAL_REPORTS"),
               "out": str(DERIVED / "IN_BSE_FINANCIALS" / "text")},
    "asx": {"raw": str(DATASETS / "raw" / "ASX"),
            "out": str(DERIVED / "ASX_FINANCIALS" / "text")},
    "twse": {"raw": str(DATASETS / "raw" / "TWSE_REPORTS"),
             "out": str(DERIVED / "TWSE_FINANCIALS" / "text")},
    "pse": {"raw": str(DATASETS / "raw" / "PSE_EDGE"),
            "out": str(DERIVED / "PSE_FINANCIALS" / "text")},
    "oslo": {"raw": "/Users/lichenyu/datasets/markets/no/01_raw/oslo_attachments",
             "out": str(DERIVED / "NORDIC_FINANCIALS" / "text_no")},
    "nordic_se": {"raw": "/Users/lichenyu/datasets/markets/se/01_raw/nasdaq_nordic_baltic",
                  "out": str(DERIVED / "NORDIC_FINANCIALS" / "text_se")},
    # The other six Nordic/Baltic roots. Easy to miss because they are small
    # (1,499 PDFs combined against Sweden's 3,584) and live on the boot volume,
    # but they are 6 of the 8 markets the Nordic adapter emits.
    "nordic_dk": {"raw": "/Users/lichenyu/datasets/markets/dk/01_raw/nasdaq_nordic_baltic",
                  "out": str(DERIVED / "NORDIC_FINANCIALS" / "text_dk")},
    "nordic_fi": {"raw": "/Users/lichenyu/datasets/markets/fi/01_raw/nasdaq_nordic_baltic",
                  "out": str(DERIVED / "NORDIC_FINANCIALS" / "text_fi")},
    "nordic_is": {"raw": "/Users/lichenyu/datasets/markets/is/01_raw/nasdaq_nordic_baltic",
                  "out": str(DERIVED / "NORDIC_FINANCIALS" / "text_is")},
    "nordic_lt": {"raw": "/Users/lichenyu/datasets/markets/lt/01_raw/nasdaq_nordic_baltic",
                  "out": str(DERIVED / "NORDIC_FINANCIALS" / "text_lt")},
    "nordic_lv": {"raw": "/Users/lichenyu/datasets/markets/lv/01_raw/nasdaq_nordic_baltic",
                  "out": str(DERIVED / "NORDIC_FINANCIALS" / "text_lv")},
    "nordic_ee": {"raw": "/Users/lichenyu/datasets/markets/ee/01_raw/nasdaq_nordic_baltic",
                  "out": str(DERIVED / "NORDIC_FINANCIALS" / "text_ee")},
    # HK raw lives outside MARKET_FILINGS; its text corpus is already complete
    # (25,565 files) under markets/hk/02_structured, so it is absent by design.
}


def out_path(pdf: Path, raw_root: Path, out_root: Path) -> Path:
    """Mirror the source tree so a filing's text is findable from its path."""
    try:
        rel = pdf.relative_to(raw_root)
    except ValueError:
        rel = Path(pdf.name)
    return out_root / rel.with_suffix(".txt.gz")


def extract(pdf: Path, dest: Path, timeout: int = 300) -> str:
    """"ok" | "empty" | "unreadable" for one PDF."""
    try:
        done = subprocess.run(["pdftotext", "-q", "-enc", "UTF-8", str(pdf), "-"],
                              capture_output=True, timeout=timeout)
    except (subprocess.TimeoutExpired, OSError):
        return "unreadable"
    text = done.stdout.decode("utf-8", "replace")
    if not text.strip():
        return "empty"
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    with gzip.open(tmp, "wt", encoding="utf-8") as fout:
        fout.write(text)
    tmp.replace(dest)
    return "ok"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--market", choices=sorted(MARKETS), required=True)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--log-every", type=int, default=500)
    ap.add_argument("--unreadable-abort", type=int, default=25)
    args = ap.parse_args()

    cfg = MARKETS[args.market]
    raw_root, out_root = Path(cfg["raw"]), Path(cfg["out"])
    pdfs = []
    for p in raw_root.rglob("*.pdf"):
        pdfs.append(p)
        if args.limit and len(pdfs) >= args.limit:
            break
    todo = [p for p in pdfs
            if not (out_path(p, raw_root, out_root).exists()
                    and out_path(p, raw_root, out_root).stat().st_size > 0)]
    print(f"{args.market}: {len(pdfs):,} PDFs, {len(todo):,}need extraction "
          f"({len(pdfs)-len(todo):,} already have text)", flush=True)

    tally: Counter = Counter()
    consecutive = 0
    start = time.time()
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(extract, p, out_path(p, raw_root, out_root)): p
                   for p in todo}
        for i, fut in enumerate(as_completed(futures), 1):
            status = fut.result()
            tally[status] += 1
            consecutive = consecutive + 1 if status == "unreadable" else 0
            if storage_action(consecutive, args.unreadable_abort) == "abort":
                print(f"\nABORT after {consecutive} consecutive unreadable files -- "
                      f"the volume is gone, not the PDFs. {tally['ok']:,} extracted "
                      f"so far; re-run to resume.", flush=True)
                for f in futures:
                    f.cancel()
                pool.shutdown(wait=False, cancel_futures=True)
                return 2
            if i % args.log_every == 0:
                rate = i / max(time.time() - start, 1e-9)
                left = (len(todo) - i) / max(rate, 1e-9) / 60
                print(f"  {i:,}/{len(todo):,} {rate:.1f}/s ~{left:.0f}min left "
                      f"{dict(tally)}", flush=True)

    print(f"\n{args.market}: {dict(tally)} -> {out_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
