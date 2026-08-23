#!/usr/bin/env python3
"""Extract financials from ESEF iXBRL packages — 27 European markets.

16,002 filings acquired and none processed: the practices doc's longest-standing
open item ("no confirmed parser for the ESEF packages"). They are ZIPs whose
`reports/*.html` is an inline-XBRL document.

These are the BEST-structured documents in the corpus and the opposite of every
PDF market. Each fact carries its own concept, period context, currency unit and
an explicit decimal scale:

    name="ifrs-full:RevenueFromInterest" contextRef="c1" unitRef="EUR" scale="3"

so none of the defects that cost this program days can occur -- no caption
vocabulary (hence no language problem across 27 countries), no scale detection,
no period-end inference, no hardcoded currency.

Four traps that DO exist here, each with a test:

  1. `scale` is a power of ten applied to the WRITTEN value. Ignoring it is a
     1000x error -- the same magnitude as the HK/India unit-scale defect.
  2. `sign="-"` negates a value written as positive.
  3. A filing carries many contexts: current year, prior year, and SEGMENT
     breakdowns carrying xbrldi dimensions. Taking a dimensioned fact reports
     one business line as the whole company.
  4. Numbers appear in both dot-decimal (1,234,567.89) and comma-decimal
     (1.234.567,89) locales. Misreading the European form gives 1.234 -- six
     orders of magnitude out.

Usage:
    python scripts/esef_extract.py --limit 50
    python scripts/esef_extract.py --countries GB FR DE
"""

from __future__ import annotations

import argparse
import re
import sys
import zipfile
from collections import Counter
from pathlib import Path
from typing import Dict, List, Optional

MARKETS_ROOT = Path("/Volumes/OWC Express 1M2/datasets/markets")
OUT_DIR = Path("/Users/lichenyu/datasets/derived/ESEF_FINANCIALS")

# IFRS taxonomy concept -> canonical metric. Only STANDARD concepts are mapped:
# a filer's own extension (htb:ZunahmeAbnahme...) has no shared meaning, and
# mapping one would silently mean something different in every filing.
CONCEPTS: Dict[str, str] = {
    "ifrs-full:Assets": "total_assets",
    "ifrs-full:Liabilities": "total_liabilities",
    "ifrs-full:Equity": "total_equity",
    "ifrs-full:EquityAttributableToOwnersOfParent": "total_equity",
    "ifrs-full:CurrentAssets": "current_assets",
    "ifrs-full:NoncurrentAssets": "non_current_assets",
    "ifrs-full:CurrentLiabilities": "current_liabilities",
    "ifrs-full:NoncurrentLiabilities": "non_current_liabilities",
    "ifrs-full:CashAndCashEquivalents": "cash_and_equivalents",
    "ifrs-full:Inventories": "inventories",
    "ifrs-full:TradeAndOtherCurrentReceivables": "accounts_receivable",
    "ifrs-full:Revenue": "revenue",
    "ifrs-full:RevenueFromContractsWithCustomers": "revenue",
    "ifrs-full:CostOfSales": "cost_of_revenue",
    "ifrs-full:GrossProfit": "gross_profit",
    "ifrs-full:ProfitLossBeforeTax": "profit_before_tax",
    "ifrs-full:IncomeTaxExpenseContinuingOperations": "income_tax_expense",
    "ifrs-full:ProfitLoss": "net_income",
    "ifrs-full:ProfitLossAttributableToOwnersOfParent": "net_income",
    "ifrs-full:BasicEarningsLossPerShare": "basic_eps",
    "ifrs-full:CashFlowsFromUsedInOperatingActivities": "operating_cash_flow",
    "ifrs-full:CashFlowsFromUsedInInvestingActivities": "investing_cash_flow",
    "ifrs-full:CashFlowsFromUsedInFinancingActivities": "financing_cash_flow",
}


# NEGATIVE RESULT, kept as a note: mapping ifrs-full:EquityAndLiabilities to
# total_assets via the balance-sheet identity was tried and REMOVED. It never
# fired once in a 60-filing sample (those concepts appear only in dimensioned or
# prior-period contexts), so it bought nothing -- and because facts are read in
# document order it could have let the financing side shadow a directly tagged
# ifrs-full:Assets. total_assets coverage is 36/60; the gap is filers who tag
# only the financing side, and closing it needs context-aware selection rather
# than a concept alias.


def canonical_metric(concept: Optional[str]) -> Optional[str]:
    return CONCEPTS.get(str(concept or "").strip())


def apply_scale(value: float, scale: Optional[str], sign: Optional[str]) -> float:
    """value * 10**scale, negated when sign is '-'."""
    out = float(value)
    if scale not in (None, ""):
        try:
            out *= 10 ** int(scale)
        except (TypeError, ValueError):
            pass          # a nonsense scale must not silently multiply
    if str(sign or "").strip() == "-":
        out = -out
    return out


_NBSP = " "


def parse_ixbrl_number(text: Optional[str]) -> Optional[float]:
    """Locale-tolerant number parse.

    European filings write 1.234.567,89 while others write 1,234,567.89.
    Reading the European form as dot-decimal yields 1.234 -- six orders of
    magnitude out, and silent.
    """
    raw = str(text or "").replace(_NBSP, " ").strip()
    raw = re.sub(r"[^\d,.\- ]", "", raw)
    if not raw or raw in {"-", "."} or not re.search(r"\d", raw):
        return None
    raw = raw.replace(" ", "")
    if "," in raw and "." in raw:
        # whichever separator appears LAST is the decimal point
        dec = "," if raw.rfind(",") > raw.rfind(".") else "."
        thou = "." if dec == "," else ","
        raw = raw.replace(thou, "").replace(dec, ".")
    elif "," in raw:
        # a lone comma is a decimal point only when it is not a thousands group
        raw = raw.replace(",", "." if re.search(r",\d{1,2}$", raw) else "")
    try:
        return float(raw)
    except ValueError:
        return None


def context_is_consolidated(context_xml: str) -> bool:
    """True when the context carries NO dimensional members.

    A dimensioned context is a segment, a business line or a restated scenario.
    Reporting one as the entity's total is the ESEF equivalent of taking a
    comparative column for the current year.
    """
    return "explicitMember" not in str(context_xml or "") and \
           "typedMember" not in str(context_xml or "")


_FACT = re.compile(r"<ix:nonFraction([^>]*)>(.*?)</ix:nonFraction>", re.S | re.I)
_ATTR = re.compile(r'([\w:-]+)\s*=\s*"([^"]*)"')
_CONTEXT = re.compile(r"<xbrli:context[^>]*id=\"([^\"]+)\"(.*?)</xbrli:context>", re.S | re.I)
_INSTANT = re.compile(r"<xbrli:instant>([^<]+)</xbrli:instant>", re.I)
_END = re.compile(r"<xbrli:endDate>([^<]+)</xbrli:endDate>", re.I)
_UNIT = re.compile(r"<xbrli:unit[^>]*id=\"([^\"]+)\"(.*?)</xbrli:unit>", re.S | re.I)
_MEASURE = re.compile(r"<xbrli:measure>([^<]+)</xbrli:measure>", re.I)
_TAG = re.compile(r"<[^>]+>")


def parse_package(path: Path) -> Optional[dict]:
    """One row of canonical metrics from an ESEF zip, or None."""
    try:
        with zipfile.ZipFile(path) as zf:
            names = [n for n in zf.namelist()
                     if n.lower().endswith((".html", ".xhtml"))
                     and "report" in n.lower()]
            if not names:
                names = [n for n in zf.namelist()
                         if n.lower().endswith((".html", ".xhtml"))]
            if not names:
                return None
            doc = zf.read(max(names, key=lambda n: zf.getinfo(n).file_size))
    except (zipfile.BadZipFile, OSError, KeyError):
        return None
    data = doc.decode("utf-8", "replace")

    contexts = {}
    for cid, body in _CONTEXT.findall(data):
        end = _INSTANT.search(body) or _END.search(body)
        contexts[cid] = {"end": (end.group(1)[:10] if end else None),
                         "consolidated": context_is_consolidated(body)}
    units = {}
    for uid, body in _UNIT.findall(data):
        m = _MEASURE.search(body)
        if m:
            units[uid] = m.group(1).split(":")[-1].upper()

    # The filing's own period end is the latest end date across contexts.
    ends = [c["end"] for c in contexts.values() if c["end"]]
    period_end = max(ends) if ends else None

    row: Dict[str, object] = {"period_end": period_end, "n_metrics": 0}
    currencies: Counter = Counter()
    for attrs_raw, inner in _FACT.findall(data):
        attrs = dict(_ATTR.findall(attrs_raw))
        metric = canonical_metric(attrs.get("name"))
        if metric is None or metric in row:
            continue
        ctx = contexts.get(attrs.get("contextRef", ""), {})
        if not ctx.get("consolidated"):
            continue
        if period_end and ctx.get("end") != period_end:
            continue          # prior-year comparative
        value = parse_ixbrl_number(_TAG.sub("", inner))
        if value is None:
            continue
        row[metric] = apply_scale(value, attrs.get("scale"), attrs.get("sign"))
        row["n_metrics"] = int(row["n_metrics"]) + 1
        unit = units.get(attrs.get("unitRef", ""))
        if unit:
            currencies[unit] += 1
    row["currency"] = currencies.most_common(1)[0][0] if currencies else None
    row["currency_source"] = "declared" if currencies else "none"
    return row


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--countries", nargs="*", default=None)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--out-dir", type=Path, default=OUT_DIR)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--log-every", type=int, default=200)
    args = ap.parse_args()

    import pandas as pd
    from concurrent.futures import ThreadPoolExecutor, as_completed

    zips: List[Path] = []
    for country_dir in sorted(MARKETS_ROOT.iterdir()):
        if not country_dir.is_dir():
            continue
        code = country_dir.name.upper()
        if args.countries and code not in {c.upper() for c in args.countries}:
            continue
        base = country_dir / "01_raw" / "europe_annual_reports" / "xbrl_org"
        if not base.exists():
            continue
        for z in base.rglob("*.zip"):
            zips.append(z)
            if args.limit and len(zips) >= args.limit:
                break
        if args.limit and len(zips) >= args.limit:
            break
    print(f"ESEF: {len(zips):,} packages", flush=True)

    rows: List[dict] = []
    tally: Counter = Counter()

    def work(z: Path) -> Optional[dict]:
        row = parse_package(z)
        if row is None:
            return None
        parts = z.parts
        try:
            country = parts[parts.index("xbrl_org") + 1]
        except (ValueError, IndexError):
            country = "??"
        row.update({"market": f"esef_{country.lower()}", "country": country.lower(),
                    "lei": z.parent.parent.parent.name, "source_path": str(z)})
        return row

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(work, z) for z in zips]
        for i, fut in enumerate(as_completed(futures), 1):
            row = fut.result()
            if row is None:
                tally["unreadable"] += 1
            else:
                rows.append(row)
                tally["ok" if row["n_metrics"] else "no_metrics"] += 1
            if i % args.log_every == 0:
                print(f"  {i:,}/{len(zips):,} {dict(tally)}", flush=True)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    out = args.out_dir / "canonical_metrics_wide.parquet"
    df = pd.DataFrame(rows)
    df.to_parquet(out, index=False)
    print(f"\nESEF: {dict(tally)}")
    if len(df):
        print(f"  markets: {df.market.nunique()} | currency: "
              f"{df.currency.value_counts().head(4).to_dict()}")
        print(f"  median metrics/filing: {df.n_metrics.median():.0f}")
    print(f"wrote {out} ({len(df):,} rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
