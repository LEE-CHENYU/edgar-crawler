#!/usr/bin/env python3
"""Recover the unit-scale multiplier that HK stage-02 discarded.

The panel's HK rows carry `unit_text` (the reporting CURRENCY, e.g. "RMB") but
no scale. The multiplier lives in the statement table headers -- "RMB'000",
"Expressed in thousands of Renminbi", 人民幣千元 -- which stage-02 never
captured. The consequence is visible in the built panel: Tencent (00700)
`total_assets` appears as 56,804,365 for FY2011 (thousands) and 17,506 for
FY2009 (millions), both labelled RMB. Cross-market medians of `total_assets_usd`
differ by 10^5 between HK and CN as a result.

Two stages, cheapest first, no LLM:

  Stage 1  regex the source text for an explicit scale declaration
  Stage 2  self-calibrate against each company's own multi-year series, where a
           ~1000x step between adjacent years is a scale error rather than growth

Whatever both stages leave ambiguous is the residual -- the only population an
LLM pass would need to cover.

This script only WRITES A LOOKUP TABLE. It does not modify the panel or any
adapter; consuming the table is a separate, reviewed change.

Usage:
    python scripts/hk_scale_recovery.py --limit 500        # sample
    python scripts/hk_scale_recovery.py                    # full corpus
"""

from __future__ import annotations

import argparse
import gzip
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

HK_ROOT = Path(
    "/Volumes/OWC Express 1M2/datasets/markets/hk/02_structured/hkex_financials"
)
DEFAULT_OUT = Path("/Users/lichenyu/datasets/panel/hk_unit_scale.parquet")

# Scale declarations, most specific first. Order matters: "HK$'000" must win
# before a bare "000" pattern, and 百萬 (million) before 萬 (ten-thousand).
SCALE_PATTERNS: List[Tuple[str, int]] = [
    (r"in\s+billions?\s+of", 1_000_000_000),
    (r"\bbillions?\b", 1_000_000_000),
    (r"十\s*億", 1_000_000_000),
    (r"in\s+millions?\s+of", 1_000_000),
    (r"[A-Z$¥€£]{1,4}\s*(?:'|’)?\s*million", 1_000_000),
    (r"\bmillions?\b", 1_000_000),
    (r"百\s*萬", 1_000_000),
    (r"in\s+thousands?\s+of", 1_000),
    (r"(?:'|’)\s*000\b", 1_000),
    (r"\bthousands?\b", 1_000),
    (r"千\s*元", 1_000),
]
_COMPILED = [(re.compile(p, re.IGNORECASE), mult) for p, mult in SCALE_PATTERNS]

# Anchors that mark the start of a primary statement, so the scan window sits
# where the scale caption actually appears rather than anywhere in the document.
STATEMENT_ANCHORS = re.compile(
    r"(consolidated\s+(?:statement|balance\s+sheet)|balance\s+sheet|"
    r"statement\s+of\s+financial\s+position|income\s+statement|"
    r"綜合(?:財務狀況表|損益表|全面收益表)|資產負債表)",
    re.IGNORECASE,
)
WINDOW = 1200  # characters after an anchor to search for a scale caption


def read_text(path: Path) -> str:
    opener = gzip.open if path.suffix == ".gz" else open
    try:
        with opener(path, "rt", encoding="utf-8", errors="replace") as fin:
            return fin.read()
    except OSError:
        return ""


def scale_from_window(window: str) -> Optional[int]:
    """First matching scale in a text window, or None."""
    best: Optional[Tuple[int, int]] = None  # (position, multiplier)
    for pattern, mult in _COMPILED:
        m = pattern.search(window)
        if m and (best is None or m.start() < best[0]):
            best = (m.start(), mult)
    return best[1] if best else None


def detect_scale(text: str) -> Tuple[Optional[int], str]:
    """Scale for a filing, plus the evidence used.

    Prefers a caption near a statement anchor; falls back to the document's
    dominant declaration when no anchor is found.
    """
    votes: Counter = Counter()
    for anchor in STATEMENT_ANCHORS.finditer(text):
        window = text[anchor.end() : anchor.end() + WINDOW]
        scale = scale_from_window(window)
        if scale:
            votes[scale] += 1
    if votes:
        top, count = votes.most_common(1)[0]
        return top, f"anchored:{count}votes:{dict(votes)}"

    scale = scale_from_window(text[:20000])
    if scale:
        return scale, "document-head"
    return None, "none"


def iter_filings(root: Path, limit: int = 0) -> Iterable[Tuple[str, Path]]:
    count = 0
    for path in sorted(root.rglob("*.txt.gz")):
        yield path.stem.replace(".txt", ""), path
        count += 1
        if limit and count >= limit:
            return


# --- Stage 2: self-calibration ------------------------------------------


def calibrate_series(
    series: List[Tuple[int, float]], tolerance: float = 100.0
) -> Dict[int, int]:
    """Infer per-year scale corrections from magnitude discontinuities.

    `series` is [(fiscal_year, value)] for one company and metric. A jump of
    ~1000x between adjacent years is a scale change, not growth: real balance
    sheets do not grow or shrink a thousandfold year on year. Returns
    {fiscal_year: relative_multiplier} where a value of 1000 means "this year's
    figure is 1000x smaller than its neighbours and is therefore in millions
    while they are in thousands".
    """
    fixes: Dict[int, int] = {}
    ordered = sorted(series)
    for (y0, v0), (y1, v1) in zip(ordered, ordered[1:]):
        if not v0 or not v1 or v0 <= 0 or v1 <= 0:
            continue
        ratio = v1 / v0
        if ratio >= tolerance:
            fixes[y0] = 1000  # earlier year was in larger units
        elif ratio <= 1.0 / tolerance:
            fixes[y1] = 1000  # later year was in larger units
    return fixes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hk-root", type=Path, default=HK_ROOT)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--report-only", action="store_true")
    args = parser.parse_args()

    text_root = args.hk_root / "text"
    print(f"scanning {text_root}", flush=True)

    rows = []
    evidence = Counter()
    scanned = 0
    for filing_id, path in iter_filings(text_root, limit=args.limit):
        text = read_text(path)
        if not text:
            evidence["unreadable"] += 1
            continue
        scale, how = detect_scale(text)
        evidence[how.split(":")[0]] += 1
        rows.append(
            {
                "filing_id": filing_id,
                "unit_scale": scale,
                "evidence": how,
                "source_path": str(path),
            }
        )
        scanned += 1
        if scanned % 2000 == 0:
            print(f"  {scanned} filings scanned", flush=True)

    total = len(rows)
    found = sum(1 for r in rows if r["unit_scale"])
    print(f"\nStage 1 (regex over source text)")
    print(f"  filings scanned : {total}")
    print(f"  scale recovered : {found} ({found / max(total,1) * 100:.1f}%)")
    print(f"  residual        : {total - found}")
    print(f"  evidence kinds  : {dict(evidence)}")
    dist = Counter(r["unit_scale"] for r in rows if r["unit_scale"])
    print(f"  scale distribution: {dict(dist)}")

    if args.report_only:
        return 0

    import pandas as pd

    df = pd.DataFrame(rows)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    tmp = args.out.with_suffix(args.out.suffix + ".tmp")
    df.to_parquet(tmp, index=False)
    tmp.replace(args.out)
    print(f"\nwrote {args.out} ({len(df)} rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
