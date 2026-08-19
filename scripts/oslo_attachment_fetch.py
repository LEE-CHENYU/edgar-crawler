#!/usr/bin/env python3
"""Re-acquire Oslo annual reports from announcement attachments.

The backfill downloaded `/v1/newsreader/pdf?messageId=X` for all 8,134 Oslo
announcements. That endpoint returns the announcement COVER SHEET, not the
report: median 7 KB, max 0.1 MB, 100% under 50 KB. Stage 02 duly yielded 2.2%
(181 of 8,139), and no extractor improvement can raise that -- the documents
simply are not filings. The metadata even recorded `numbAttachments`, so the
crawler saw the real documents and passed them by.

  7,690 of 8,134 announcements (94.5%) carry attachments
  12,784 attachment documents in total
  every row is category "ANNUAL FINANCIAL REPORT"

Verified end to end before this script was written (practices 1.6): message
420146 attachment 23798 is a 20-page Norwegian report, 2.5 MB, from which the
Norwegian labels extract 11 metrics including revenue 3,000,347 (thousands NOK)
-- matching the announcement text "driftsinntekter paa 3,0 milliarder kroner".

Two requests per announcement: `message` for the attachment ids, then one
download each. Rate limited to <=1 req/s with a descriptive UA and a contact
address, per the standing rule for exchange APIs.

Usage:
    python scripts/oslo_attachment_fetch.py --limit 20      # sample
    python scripts/oslo_attachment_fetch.py --reports-only  # skip decks
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

API = "https://api3.oslo.oslobors.no/v1/newsreader"
UA = "stock-journal research crawler (fretin13@gmail.com)"
DATASETS = Path("/Volumes/OWC Express 1M2/datasets/MARKET_FILINGS")
METADATA = DATASETS / "metadata" / "MARKET_FILINGS_METADATA.csv"
# Boot volume, NOT OWC: this pulls ~49.6 GB (12,784 attachments averaging 4 MB)
# and OWC has 51 GB free at 98% capacity -- landing it there would fill the disk,
# which is how the 2026-08-17 I/O cascade started. Mirrors the Nordic fold.
OUT_ROOT = Path("/Users/lichenyu/datasets/markets/no/01_raw/oslo_attachments")

# A slide deck carries headline numbers but no statements. Both are worth
# keeping, but the report has to be identifiable so stage 02 can be scoped.
_DECK = re.compile(r"presentasjon|presentation|slides?\b|webcast", re.IGNORECASE)
_REPORT = re.compile(r"rapport|report|regnskap|arsrapport|årsrapport|annual", re.IGNORECASE)


def message_url(message_id) -> str:
    return f"{API}/message?messageId={int(message_id)}"


def attachment_url(message_id, attachment_id) -> str:
    """Both ids are required -- attachmentId alone returns 400 (probed live)."""
    return f"{API}/attachment?messageId={int(message_id)}&attachmentId={int(attachment_id)}"


def parse_attachments(payload: dict) -> List[Tuple[int, str]]:
    """[(attachment_id, filename)] from a message response."""
    try:
        items = payload["data"]["message"].get("attachments") or []
    except (KeyError, TypeError, AttributeError):
        return []
    out = []
    for a in items:
        try:
            out.append((int(a["id"]), str(a.get("name") or "")))
        except (KeyError, TypeError, ValueError):
            continue
    return out


def is_probably_report(filename: str) -> bool:
    name = str(filename or "")
    if not name.lower().endswith(".pdf"):
        return False
    if _DECK.search(name):
        return False
    return bool(_REPORT.search(name))


def oslo_rows(metadata=METADATA) -> List[dict]:
    """Announcements that declare attachments, newest first."""
    csv.field_size_limit(sys.maxsize)
    rows = []
    with open(metadata, newline="", encoding="utf-8") as fin:
        for r in csv.DictReader(fin):
            if r.get("market") != "NO_OSLO_NEWSWEB":
                continue
            meta = json.loads(r.get("raw_metadata") or "{}")
            if not meta.get("numbAttachments"):
                continue
            rows.append({
                "message_id": meta.get("messageId") or meta.get("id"),
                "issuer": meta.get("issuerSign") or meta.get("issuerName") or "UNKNOWN",
                "published": str(meta.get("publishedTime") or "")[:10],
                "n": int(meta.get("numbAttachments") or 0),
            })
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-root", type=Path, default=OUT_ROOT)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--sleep", type=float, default=1.0,
                        help="seconds between requests (<=1 req/s by default)")
    parser.add_argument("--reports-only", action="store_true",
                        help="skip presentations and non-PDF attachments")
    parser.add_argument("--log-every", type=int, default=50)
    args = parser.parse_args()

    import requests

    rows = oslo_rows()
    if args.limit:
        rows = rows[: args.limit]
    print(f"oslo: {len(rows)} announcements with attachments", flush=True)

    session = requests.Session()
    session.headers.update({"User-Agent": UA})
    got = skipped = failed = adopted = 0
    start = time.time()

    for i, row in enumerate(rows, 1):
        mid = row["message_id"]
        if not mid:
            failed += 1
            continue
        try:
            time.sleep(args.sleep)
            resp = session.get(message_url(mid), timeout=60)
            resp.raise_for_status()
            attachments = parse_attachments(resp.json())
        except Exception as exc:
            failed += 1
            print(f"  message {mid} failed: {type(exc).__name__}", flush=True)
            continue

        for aid, name in attachments:
            if args.reports_only and not is_probably_report(name):
                skipped += 1
                continue
            safe = re.sub(r"[^\w.\-]+", "_", name) or f"{aid}.pdf"
            dest = Path(args.out_root) / str(row["published"][:4] or "unknown") / \
                str(row["issuer"]) / f"{mid}_{aid}_{safe}"
            if dest.exists() and dest.stat().st_size > 0:
                adopted += 1
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            try:
                time.sleep(args.sleep)
                with session.get(attachment_url(mid, aid), timeout=180, stream=True) as r:
                    r.raise_for_status()
                    tmp = dest.with_suffix(dest.suffix + ".part")
                    with open(tmp, "wb") as fout:
                        for chunk in r.iter_content(65536):
                            fout.write(chunk)
                    tmp.replace(dest)
                got += 1
            except Exception as exc:
                failed += 1
                print(f"  attachment {mid}/{aid} failed: {type(exc).__name__}", flush=True)

        if i % args.log_every == 0:
            rate = i / max(time.time() - start, 1e-9)
            left = (len(rows) - i) / max(rate, 1e-9) / 3600
            print(f"  {i}/{len(rows)} announcements  downloaded={got} "
                  f"adopted={adopted} skipped={skipped} failed={failed} "
                  f"~{left:.1f}h left", flush=True)

    print(f"\noslo attachments: downloaded {got}, already had {adopted}, "
          f"skipped {skipped}, failed {failed}")
    print(f"-> {args.out_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
