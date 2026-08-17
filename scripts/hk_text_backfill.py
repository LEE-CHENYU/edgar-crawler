#!/usr/bin/env python3
"""Extract text for HK filings that have facts but no text.

The HK corpus has facts for 26,482 filings but extracted text for only 4,786.
That gap matters because the unit-scale multiplier ("RMB'000", "Expressed in
thousands of Renminbi", 人民幣千元) lives in the statement table headers, which
only the text preserves -- and without it the panel's HK figures mix thousands
and millions under an identical `unit_text` of "RMB".

`hkex_financials_pipeline.py` cannot do this job: its skip test is
`summary exists AND facts exist` (line ~430), both true for every filing, so a
re-run skips all of them and never notices the missing text.

Of the 21,696 filings needing text, a raw PDF exists for 20,782. The remaining
914 have no source document and can never be scaled -- they stay `unknown`.

Filing-id convention: the raw tree is
`01_raw/hkex_pdfs/{YYYYMMDD}/{filing_id}/ltn*.pdf`, i.e. the filing id is the
PDF's PARENT DIRECTORY, not its filename stem.

Writes only into `02_structured/hkex_financials/text/`; never touches facts,
summaries, or the panel.

Usage:
    python scripts/hk_text_backfill.py --limit 20 --dry-run
    python scripts/hk_text_backfill.py --workers 4
"""

from __future__ import annotations

import argparse
import gzip
import shutil
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

HK = Path("/Volumes/OWC Express 1M2/datasets/markets/hk")
FACTS = HK / "02_structured/hkex_financials/facts"
TEXT = HK / "02_structured/hkex_financials/text"
PDFS = HK / "01_raw/hkex_pdfs"


def ids_with(root: Path, pattern: str, suffix: str) -> Dict[str, str]:
    """filing_id -> dated directory name, for files matching `pattern`."""
    out: Dict[str, str] = {}
    for path in root.rglob(pattern):
        out[path.name.replace(suffix, "")] = path.parent.name
    return out


def pdf_index(root: Path) -> Dict[str, Path]:
    """filing_id -> pdf path. The id is the PDF's parent directory."""
    out: Dict[str, Path] = {}
    for path in root.rglob("*.pdf"):
        out.setdefault(path.parent.name, path)
    return out


def text_target(text_root: Path, filing_date: str, filing_id: str) -> Path:
    return text_root / filing_date / f"{filing_id}.txt.gz"


def extract_one(
    pdf: Path, target: Path, pdftotext: str = "pdftotext", timeout: int = 180
) -> Tuple[bool, str]:
    """PDF -> gzipped text, written atomically. Returns (ok, reason)."""
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp_txt = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as handle:
            tmp_txt = Path(handle.name)
        subprocess.run(
            [pdftotext, "-q", "-enc", "UTF-8", str(pdf), str(tmp_txt)],
            check=True, timeout=timeout,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        if not tmp_txt.exists() or tmp_txt.stat().st_size == 0:
            return False, "empty-output"
        tmp_gz = target.with_suffix(target.suffix + ".tmp")
        with open(tmp_txt, "rb") as src, gzip.open(tmp_gz, "wb") as dst:
            shutil.copyfileobj(src, dst)
        tmp_gz.replace(target)
        return True, "ok"
    except subprocess.TimeoutExpired:
        return False, "timeout"
    except subprocess.CalledProcessError as exc:
        return False, f"pdftotext-rc{exc.returncode}"
    except OSError as exc:
        return False, f"oserror:{type(exc).__name__}"
    finally:
        if tmp_txt is not None and tmp_txt.exists():
            tmp_txt.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--facts-root", type=Path, default=FACTS)
    parser.add_argument("--text-root", type=Path, default=TEXT)
    parser.add_argument("--pdf-root", type=Path, default=PDFS)
    parser.add_argument("--pdftotext", default="pdftotext")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--log-every", type=int, default=250)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    facts = ids_with(args.facts_root, "*/*.jsonl.gz", ".jsonl.gz")
    text = ids_with(args.text_root, "*/*.txt.gz", ".txt.gz")
    pdfs = pdf_index(args.pdf_root)

    missing = {fid: d for fid, d in facts.items() if fid not in text}
    todo = [(fid, d, pdfs[fid]) for fid, d in missing.items() if fid in pdfs]
    no_pdf = [fid for fid in missing if fid not in pdfs]

    print(f"filings with facts : {len(facts)}")
    print(f"filings with text  : {len(text)}")
    print(f"missing text       : {len(missing)}")
    print(f"  recoverable (PDF): {len(todo)}")
    print(f"  no PDF, unknown  : {len(no_pdf)}", flush=True)

    if args.limit:
        todo = todo[: args.limit]
    if args.dry_run:
        for fid, d, pdf in todo[:5]:
            print(f"  would extract {fid} ({d}) <- {pdf}")
        print("(dry run)")
        return 0

    counts: Dict[str, int] = {}
    done = 0
    start = time.time()

    def work(item: Tuple[str, str, Path]) -> str:
        fid, filing_date, pdf = item
        ok, reason = extract_one(
            pdf, text_target(args.text_root, filing_date, fid), args.pdftotext
        )
        return reason

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(work, item) for item in todo]
        for fut in as_completed(futures):
            reason = fut.result()
            counts[reason] = counts.get(reason, 0) + 1
            done += 1
            if done % args.log_every == 0:
                rate = done / max(time.time() - start, 1e-9)
                remaining = (len(todo) - done) / max(rate, 1e-9) / 60
                print(
                    f"  {done}/{len(todo)} {rate:.1f}/s "
                    f"~{remaining:.0f}min left {counts}", flush=True
                )

    print(f"\ndone: {counts}")
    print(f"{counts.get('ok', 0)} filings gained text; {len(no_pdf)} remain unknown")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
