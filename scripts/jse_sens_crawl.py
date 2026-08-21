#!/usr/bin/env python3
"""Crawl JSE SENS announcements and their PDFs.

Discovered 2026-08-21 by watching the JSE client portal call its own SharePoint
WCF service -- the guessed REST paths had all 404'd, and only the page's own
traffic revealed it:

    POST https://clientportal.jse.co.za/_vti_bin/JSE/SENSService.svc/
         GetSensAnnouncementForDates
    Content-Type: application/json
    {"from": "2024-03-01T00:00:00.000Z", "to": "2024-03-07T00:00:00.000Z"}

Every one of 327 announcements in that week carried `pdfPathField` pointing at
https://senspdf.jse.co.za/documents/SENS_{yyyymmdd}_{ref}.pdf, and one was
fetched, opened and parsed end to end before this crawler was written
(practices 1.6): a real "REVIEWED ANNUAL FINANCIAL RESULTS" for Letshego
Holdings (Namibia). No authentication.

SENS carries EVERY announcement type, and dealings-in-securities dominates. At
~7% financial reports (22 of 327 in the sample) filtering is what separates a
~1,100/year corpus of statements from a ~17,000/year pile of notices.

CAVEAT worth carrying: SENS items are short-form summaries -- the sampled PDF was
2 pages and yielded 1 metric. Some are "notice of availability" pointers to a
full annual report hosted elsewhere. This crawler captures SENS itself; following
those pointers is separate work.

Usage:
    python scripts/jse_sens_crawl.py --from 2015-01-01 --to 2026-08-01
    python scripts/jse_sens_crawl.py --from 2024-01-01 --to 2024-03-01 --all-types
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import re
import time
from pathlib import Path
from typing import List, Optional, Tuple

SERVICE = ("https://clientportal.jse.co.za/_vti_bin/JSE/SENSService.svc/"
           "GetSensAnnouncementForDates")
REFERER = "https://clientportal.jse.co.za/communication/sens-announcements"
UA = "stock-journal research crawler (fretin13@gmail.com)"
OUT_ROOT = Path("/Users/lichenyu/datasets/markets/za/01_raw/jse_sens")

# Kept: anything carrying or announcing financial statements.
_KEEP = re.compile(
    r"(annual\s+financial|financial\s+statements|financial\s+results|"
    r"interim\s+results|integrated\s+(annual\s+)?report|annual\s+report|"
    r"summarised\s+consolidated|audited\s+results|reviewed\s+results|"
    r"trading\s+statement)", re.IGNORECASE)
# Rejected even when they match above -- "notice of annual general meeting"
# contains "annual" but carries no statements.
_DROP = re.compile(r"(annual\s+general\s+meeting|\bagm\b|dealings\s+in\s+securities)",
                   re.IGNORECASE)


def is_financial_report(headline: Optional[str]) -> bool:
    text = str(headline or "")
    if not text.strip():
        return False
    if _DROP.search(text):
        return False
    return bool(_KEEP.search(text))


def date_windows(start: dt.date, end: dt.date, days: int = 7
                 ) -> List[Tuple[dt.date, dt.date]]:
    """Contiguous [lo, hi] windows covering start..end with no gaps."""
    out = []
    cur = start
    while cur <= end:
        hi = min(cur + dt.timedelta(days=days - 1), end)
        out.append((cur, hi))
        cur = hi + dt.timedelta(days=1)
    return out


def iso_window(lo: dt.date, hi: dt.date) -> Tuple[str, str]:
    """Millisecond-Z form, matching the payload the page itself sends."""
    return (f"{lo.isoformat()}T00:00:00.000Z", f"{hi.isoformat()}T00:00:00.000Z")


def safe_name(path_or_name: str) -> str:
    return re.sub(r"[^\w.\-]+", "_", str(path_or_name or "").split("/")[-1])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--from", dest="start", default="2015-01-01")
    ap.add_argument("--to", dest="end", default=dt.date.today().isoformat())
    ap.add_argument("--out-root", type=Path, default=OUT_ROOT)
    ap.add_argument("--window-days", type=int, default=7)
    ap.add_argument("--sleep", type=float, default=1.0)
    ap.add_argument("--all-types", action="store_true",
                    help="keep every announcement, not just financial reports")
    ap.add_argument("--index-only", action="store_true",
                    help="write the metadata index without downloading PDFs")
    args = ap.parse_args()

    import requests

    start = dt.date.fromisoformat(args.start)
    end = dt.date.fromisoformat(args.end)
    windows = date_windows(start, end, args.window_days)
    print(f"JSE SENS: {len(windows)} windows of {args.window_days}d "
          f"({start} .. {end})", flush=True)

    session = requests.Session()
    session.headers.update({"User-Agent": UA, "Referer": REFERER,
                            "Content-Type": "application/json;"})
    args.out_root.mkdir(parents=True, exist_ok=True)
    index_path = args.out_root / "sens_index.csv"
    seen = set()
    if index_path.exists():
        with open(index_path, newline="", encoding="utf-8") as fin:
            seen = {r["reference"] for r in csv.DictReader(fin)}

    fields = ["reference", "announcement_id", "date", "headline", "issuers",
              "pdf_url", "local_path"]
    new_index = not index_path.exists()
    fout = open(index_path, "a", newline="", encoding="utf-8")
    writer = csv.DictWriter(fout, fieldnames=fields)
    if new_index:
        writer.writeheader()

    total = kept = downloaded = failed = 0
    for i, (lo, hi) in enumerate(windows, 1):
        a, b = iso_window(lo, hi)
        try:
            time.sleep(args.sleep)
            resp = session.post(SERVICE, data=json.dumps({"from": a, "to": b}),
                                timeout=90)
            resp.raise_for_status()
            rows = resp.json().get("GetSensAnnouncementForDatesResult") or []
        except Exception as exc:
            print(f"  {lo}..{hi}: FAILED {type(exc).__name__}", flush=True)
            failed += 1
            continue
        total += len(rows)
        for row in rows:
            ref = str(row.get("announcementReferenceNumberField") or "").strip()
            headline = row.get("flashHeadlineField")
            if not args.all_types and not is_financial_report(headline):
                continue
            if ref in seen:
                continue
            kept += 1
            seen.add(ref)
            url = row.get("pdfPathField") or ""
            local = ""
            if url and not args.index_only:
                dest = args.out_root / str(lo.year) / safe_name(url)
                dest.parent.mkdir(parents=True, exist_ok=True)
                if dest.exists() and dest.stat().st_size > 0:
                    local = str(dest)
                else:
                    try:
                        time.sleep(args.sleep)
                        with session.get(url, timeout=120, stream=True) as doc:
                            doc.raise_for_status()
                            tmp = dest.with_suffix(dest.suffix + ".part")
                            with open(tmp, "wb") as bf:
                                for chunk in doc.iter_content(65536):
                                    bf.write(chunk)
                            tmp.replace(dest)
                        local = str(dest)
                        downloaded += 1
                    except Exception as exc:
                        failed += 1
                        print(f"    {ref}: download failed {type(exc).__name__}",
                              flush=True)
            writer.writerow({
                "reference": ref,
                "announcement_id": row.get("announcementIdField"),
                "date": lo.isoformat(),
                "headline": str(headline or "")[:300],
                "issuers": json.dumps(row.get("issuersField"))[:300],
                "pdf_url": url, "local_path": local,
            })
        if i % 10 == 0:
            fout.flush()
            print(f"  {i}/{len(windows)} windows | {total:,} announcements seen, "
                  f"{kept:,} kept, {downloaded:,} downloaded, {failed} failed",
                  flush=True)

    fout.close()
    print(f"\nJSE SENS: {total:,} announcements, {kept:,} kept "
          f"({kept/max(total,1)*100:.1f}%), {downloaded:,} downloaded, "
          f"{failed} failed -> {args.out_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
