#!/usr/bin/env python3
"""Detect the unit-scale multiplier per filing, for any PDF market.

The PDF extractor never captured units -- its docstring concedes it ("Values
captured as shown") -- so AU, TW, PH and IN all carry HK's defect: figures whose
magnitude depends on a caption nobody recorded.

India is the worst affected. A 30-report full-text sample recovered a scale for
87%, split lakh 19 / crore 3 / million 3 / thousand 1. Lakh (10^5) dominates for
small and mid caps while crore (10^7) appears for large ones, so filings in the
same market differ by 100x. Reliance's `total_assets` of 706,802 is crore, not
units -- a 10^7 error if taken at face value.

Writes a lookup table only. It never modifies an adapter or the panel; applying
the table is a separate, reviewed change.

Usage:
    python scripts/market_scale_detect.py --market in_bse --limit 50
    python scripts/market_scale_detect.py --market in_bse --workers 3
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, List, Optional, Tuple

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

# Load the sibling by PATH, not by name. `import scripts.market_conventions`
# binds to /Users/lichenyu/leviathan/scripts instead: an easy-install.pth puts
# that directory on sys.path and its scripts/ is a regular package, which Python
# resolves ahead of this repo's namespace package. Path-explicit loading is
# immune to whatever else claims the name `scripts`.
import importlib.util  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "_market_conventions", Path(__file__).resolve().parent / "market_conventions.py"
)
_conv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_conv)
detect_scale = _conv.detect_scale

DATASETS = Path("/Volumes/OWC Express 1M2/datasets/MARKET_FILINGS")
OUT_ROOT = Path("/Users/lichenyu/datasets/panel")

# market -> (raw root, how to read the identifier from the path)
MARKETS: Dict[str, Dict[str, str]] = {
    "in_bse": {
        "raw_root": str(DATASETS / "raw" / "IN_BSE_ANNUAL_REPORTS"),
        "layout": "scrip_year",   # {scrip}/{year}/{file}.pdf
    },
    "asx": {
        "raw_root": str(DATASETS / "raw" / "ASX"),
        "layout": "flat",         # identifier is in the filename
    },
    "nordic": {
        "raw_root": "/Users/lichenyu/datasets/markets/se/01_raw/nasdaq_nordic_baltic",
        "layout": "year_company",  # {year}/{company}/{file}.pdf
    },
}


# Consecutive unreadable files that mean "the filesystem is gone", not "these
# PDFs are corrupt". 4 of 30 sampled Indian reports are legitimately unreadable,
# but they are scattered; 25 in a row is an outage.
UNREADABLE_ABORT_LIMIT = 25


def storage_action(consecutive_unreadable: int, limit: int) -> str:
    """"continue" or "abort" -- the disk equivalent of the HTTP breaker.

    An outage recorded as a per-file result is worse than a crash: it writes
    rows that falsely claim "no scale found", which nothing downstream can
    distinguish from a filing that genuinely has no scale word.
    """
    if limit <= 0:
        return "continue"
    return "abort" if consecutive_unreadable >= limit else "continue"


def load_done(out: Path) -> set:
    """PDF paths already processed SUCCESSFULLY, for resume.

    Rows marked unreadable are deliberately excluded: during an outage every
    file is unreadable, and a resume must retry those rather than inherit the
    outage's verdict. A checkpoint truncated by the same outage reads as empty
    rather than blocking the restart.
    """
    try:
        import pandas as pd
        df = pd.read_parquet(out)
    except Exception:
        return set()
    if "pdf" not in df.columns:
        return set()
    if "evidence" in df.columns:
        df = df[df["evidence"].astype(str) != "unreadable"]
    return set(df["pdf"].astype(str))


def keep_done_rows(out: Path) -> List[dict]:
    """Previously completed rows, minus any recorded during an outage."""
    try:
        import pandas as pd
        df = pd.read_parquet(out)
    except Exception:
        return []
    if "evidence" in df.columns:
        df = df[df["evidence"].astype(str) != "unreadable"]
    return df.to_dict("records")


def write_table(rows: List[dict], out: Path) -> None:
    """Atomic write, so a checkpoint interrupted mid-write cannot destroy the
    previous one."""
    import pandas as pd

    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(out.suffix + ".tmp")
    pd.DataFrame(rows).to_parquet(tmp, index=False)
    tmp.replace(out)


def identify(pdf: Path, raw_root: Path, layout: str) -> Tuple[str, str]:
    """(entity, period_label) from the path, per the market's layout."""
    try:
        parts = pdf.relative_to(raw_root).parts
    except ValueError:
        return pdf.stem, ""
    if layout == "scrip_year" and len(parts) >= 3:
        return parts[0], parts[1]
    if layout == "year_company" and len(parts) >= 3:
        return parts[1], parts[0]
    return pdf.stem, ""


def pdf_text(pdf: Path, timeout: int = 180) -> str:
    try:
        done = subprocess.run(
            ["pdftotext", "-q", "-enc", "UTF-8", str(pdf), "-"],
            capture_output=True, timeout=timeout,
        )
        return done.stdout.decode("utf-8", "replace")
    except (subprocess.TimeoutExpired, OSError):
        return ""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--market", choices=sorted(MARKETS), required=True)
    parser.add_argument("--raw-root", default=None)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--log-every", type=int, default=250)
    parser.add_argument("--checkpoint-every", type=int, default=2000,
                        help="write the parquet every N filings so a crash costs "
                             "minutes, not hours")
    parser.add_argument("--unreadable-abort", type=int, default=UNREADABLE_ABORT_LIMIT,
                        help="abort after this many CONSECUTIVE unreadable files "
                             "(0 disables); a vanished volume is not a result")
    parser.add_argument("--resume", action="store_true",
                        help="skip filings already recorded in the output parquet")
    args = parser.parse_args()

    cfg = MARKETS[args.market]
    raw_root = Path(args.raw_root or cfg["raw_root"])
    out = args.out or (OUT_ROOT / f"{args.market}_unit_scale.parquet")

    pdfs: List[Path] = []
    for path in raw_root.rglob("*.pdf"):
        pdfs.append(path)
        if args.limit and len(pdfs) >= args.limit:
            break
    print(f"{args.market}: {len(pdfs)} PDFs under {raw_root}", flush=True)

    rows: List[dict] = []
    if args.resume:
        done = load_done(out)
        if done:
            before = len(pdfs)
            pdfs = [p for p in pdfs if str(p) not in done]
            # Carry the completed rows forward so the next checkpoint holds
            # the whole table, not just this session's slice.
            rows = keep_done_rows(out)
            print(f"  resume: {before - len(pdfs)} already done, "
                  f"{len(pdfs)} to go", flush=True)

    tally: Counter = Counter()
    evidence: Counter = Counter()
    consecutive_unreadable = 0
    start = time.time()

    def work(pdf: Path) -> Optional[dict]:
        text = pdf_text(pdf)
        if not text:
            return {"pdf": str(pdf), "unit_scale": None, "evidence": "unreadable"}
        scale, how = detect_scale(text)
        entity, period = identify(pdf, raw_root, cfg["layout"])
        return {
            "market": args.market, "entity": entity, "period_label": period,
            "doc_stem": pdf.stem, "unit_scale": scale, "evidence": how,
            "pdf": str(pdf),
        }

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(work, p) for p in pdfs]
        for i, fut in enumerate(as_completed(futures), 1):
            row = fut.result()
            if row is None:
                continue
            rows.append(row)
            tally[row["unit_scale"]] += 1
            evidence[str(row["evidence"]).split(":")[0]] += 1

            if row.get("evidence") == "unreadable":
                consecutive_unreadable += 1
            else:
                consecutive_unreadable = 0
            if storage_action(consecutive_unreadable, args.unreadable_abort) == "abort":
                write_table(rows, out)
                print(f"\nABORT after {consecutive_unreadable} consecutive unreadable "
                      f"files -- the volume is gone, not the PDFs. Checkpointed "
                      f"{len(rows)} rows to {out}. Re-run with --resume once the "
                      f"volume is back.", flush=True)
                for f in futures:
                    f.cancel()
                pool.shutdown(wait=False, cancel_futures=True)
                return 2

            if args.checkpoint_every and i % args.checkpoint_every == 0:
                write_table(rows, out)
            if i % args.log_every == 0:
                rate = i / max(time.time() - start, 1e-9)
                left = (len(pdfs) - i) / max(rate, 1e-9) / 60
                got = sum(v for k, v in tally.items() if k)
                print(
                    f"  {i}/{len(pdfs)} {rate:.1f}/s ~{left:.0f}min left "
                    f"recovered={got}/{i} ({got/max(i,1)*100:.0f}%)", flush=True
                )

    # Report from the WHOLE table, not the session tally. On a --resume run the
    # tally counts only this session's filings while len(rows) spans every
    # session, so mixing them understates recovery -- the denominator error from
    # practices 1.2, which has already produced two false numbers in this
    # program ("17,801 resolved", "99.7% scale recovery").
    table_tally = Counter(r.get("unit_scale") for r in rows)
    table_evidence = Counter(str(r.get("evidence")).split(":")[0] for r in rows)
    got = sum(v for k, v in table_tally.items() if k)
    print(f"\n{args.market}: {len(rows)} filings in table "
          f"({len(tally.values()) and sum(tally.values())} scanned this session)")
    print(f"  scale recovered : {got} of {len(rows)} "
          f"({got / max(len(rows),1) * 100:.1f}%)")
    print(f"  distribution    : {dict(table_tally)}")
    print(f"  evidence        : {dict(table_evidence)}")

    write_table(rows, out)
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
