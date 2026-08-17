"""Assemble the panel from every v1 market adapter.

Currency note (found 2026-08-17 full-build review): AU/TW/PH/KR rows carry
currency="USD" with fx_rate=1.0 on this panel's own FX step, but that is NOT
"no conversion needed from a local currency" -- their upstream
*_screening_input.csv builders already convert AUD/TWD/PHP/KRW to USD at
build time, at an FX rate and as-of date this panel never sees or records.
CN/JP/IN_BSE/HK stay in local currency (HK is a genuine three-currency mix:
HKD/CNY/USD, since ~40% of HKEX filers report in RMB) and get FX applied
here, with fx_rate/fx_asof captured per row. Comparing a raw `revenue`
across markets therefore mixes units -- USD for the four upstream-converted
markets, local currency for the rest -- until every row's own currency
column is checked.
"""
from __future__ import annotations

import argparse
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

from panel.fx import fetch_rates, to_usd
from panel.schema import PANEL_COLUMNS
from panel.spine import EXCH_CODES, resolve, surrogate_key
from panel.views import check_invariants

DATA_ROOT = Path("/Volumes/OWC Express 1M2/datasets")
DEFAULT_OUT = Path("/Users/lichenyu/datasets/panel")

MARKET_SOURCES: Dict[str, dict] = {
    # au/tw/ph/kr "currency" is declared as the local currency, but the
    # *_screening_input.csv these adapters read is ALREADY USD-converted by
    # its upstream builder -- verified in the 2026-08-17 full build, every
    # row from these four markets lands with currency="USD". The FX rate and
    # as-of date used for that upstream conversion are not recorded anywhere
    # in this panel; this panel's own fx_rate=1.0 on those rows means
    # "already USD", not "no conversion was needed from a local currency".
    "au": {"kind": "screening", "currency": "AUD",
           "path": DATA_ROOT / "MARKET_FILINGS/derived/ASX_FINANCIALS/au_screening_input.csv"},
    "tw": {"kind": "screening", "currency": "TWD",
           "path": DATA_ROOT / "MARKET_FILINGS/derived/TWSE_FINANCIALS/tw_screening_input.csv"},
    "ph": {"kind": "screening", "currency": "PHP",
           "path": DATA_ROOT / "MARKET_FILINGS/derived/PSE_FINANCIALS/ph_screening_input.csv"},
    "kr": {"kind": "screening", "currency": "KRW",
           "path": DATA_ROOT / "MARKET_FILINGS/derived/DART_FINANCIALS/kr_screening_input.csv"},
    "cn": {"kind": "cn", "currency": "CNY",
           "path": DATA_ROOT / "markets/cn/02_structured"},
    "in_bse": {"kind": "canonical", "currency": "INR",
               "path": DATA_ROOT / "MARKET_FILINGS/derived/IN_BSE_FINANCIALS/canonical_metrics_wide.parquet"},
    "hk": {"kind": "hk", "currency": "HKD",
           "path": DATA_ROOT / "markets/hk/02_structured/hkex_financials/facts"},
    "jp": {"kind": "jp", "currency": "JPY",
           "path": DATA_ROOT / "markets/jp/02_structured/processed/edinet_xbrl/edinet_xbrl.duckdb"},
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def attach_spine(rows: List[dict], resolved: Dict[tuple, dict]) -> List[dict]:
    out = []
    for row in rows:
        key = (row.get("market"), row.get("local_id"))
        entry = resolved.get(key)
        merged = dict(row)
        if entry:
            merged["spine_key"] = entry.get("spine_key")
            merged["figi"] = entry.get("figi")
            merged["resolution_source"] = entry.get("resolution_source")
            if not merged.get("company_name"):
                merged["company_name"] = entry.get("name")
        else:
            merged["spine_key"] = surrogate_key(
                EXCH_CODES.get(key[0], key[0] or ""), key[1] or ""
            )
            merged["figi"] = None
            merged["resolution_source"] = "unresolved"
        out.append(merged)
    return out


def _reporting_basis_for(row: dict) -> str:
    """consolidated / parent / raw-passthrough, per market.

    CN: cn_typrep 'A' -> consolidated, 'B' -> parent, anything else -> the
    raw value so nothing is silently lost.
    JP: has_consolidated_statements True -> consolidated, False -> parent
    (verified live 2026-08-17: stock_code 85950 period 2021-03-31 carries
    both for the same company-period, not an amendment).
    Every other market has a single reporting basis and defaults to
    "consolidated".
    """
    market = row.get("market")
    if market == "cn":
        typrep = row.get("cn_typrep")
        if typrep == "A":
            return "consolidated"
        if typrep == "B":
            return "parent"
        return "consolidated" if typrep in (None, "") else str(typrep)
    if market == "jp":
        flag = row.get("jp_has_consolidated_statements")
        if flag is False:
            return "parent"
        return "consolidated"
    return "consolidated"


def attach_reporting_basis(rows: List[dict]) -> List[dict]:
    out = []
    for row in rows:
        merged = dict(row)
        merged["reporting_basis"] = _reporting_basis_for(row)
        out.append(merged)
    return out


def write_panel(rows: List[dict], out_root) -> Optional[Path]:
    if not rows:
        return None
    df = pd.DataFrame(rows)
    for col in PANEL_COLUMNS:
        if col not in df.columns:
            df[col] = None
    ordered = PANEL_COLUMNS + [c for c in df.columns if c not in PANEL_COLUMNS]
    df = df[ordered]
    out_root = Path(out_root)
    out_root.mkdir(parents=True, exist_ok=True)
    final = out_root / "panel.parquet"
    tmp = out_root / "panel.parquet.tmp"
    df.to_parquet(tmp, index=False)
    os.replace(tmp, final)
    return final


def load_market(market: str, limit: int = 0) -> List[dict]:
    cfg = MARKET_SOURCES[market]
    kind, path, currency = cfg["kind"], cfg["path"], cfg["currency"]
    if kind == "screening":
        from panel.adapters.screening_input import rows_from_screening_csv
        return rows_from_screening_csv(path, market=market)
    if kind == "canonical":
        from panel.adapters.in_bse import rows_from_canonical_metrics
        return rows_from_canonical_metrics(pd.read_parquet(path), market, currency)
    if kind == "cn":
        from panel.adapters.cn import rows_from_cn_frames
        bs = pd.read_parquet(Path(path) / "balance_sheet.parquet")
        inc = pd.read_parquet(Path(path) / "income_statement.parquet")
        cf = pd.read_parquet(Path(path) / "cash_flow_direct.parquet")
        if limit:
            bs, inc, cf = bs.head(limit), inc.head(limit), cf.head(limit)
        return rows_from_cn_frames(bs, inc, cf)
    if kind == "hk":
        from panel.adapters.hk import iter_hk_fact_records, rows_from_hk_facts
        return rows_from_hk_facts(iter_hk_fact_records(path, limit=limit))
    if kind == "jp":
        from panel.adapters.jp import read_jp_wide, rows_from_jp_frame
        return rows_from_jp_frame(read_jp_wide(path, limit=limit))
    raise ValueError(f"unknown source kind: {kind}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--markets", nargs="*", default=sorted(MARKET_SOURCES))
    parser.add_argument("--out-root", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--no-fx", action="store_true")
    args = parser.parse_args()

    rows: List[dict] = []
    for market in args.markets:
        try:
            got = load_market(market, limit=args.limit)
            print(f"{market}: {len(got)} rows", flush=True)
            rows.extend(got)
        except Exception as exc:
            print(f"{market}: FAILED ({type(exc).__name__}: {exc})", flush=True)

    rows = attach_reporting_basis(rows)

    identifiers = sorted({(r["market"], r["local_id"]) for r in rows})
    print(f"resolving {len(identifiers)} identifiers", flush=True)
    resolved = resolve(identifiers)
    rows = attach_spine(rows, resolved)

    if not args.no_fx:
        asof = _utc_now()
        rates = fetch_rates({r.get("currency") for r in rows})
        print(f"fx rates: {rates}", flush=True)
        rows = [to_usd(r, rates, asof) for r in rows]

    path = write_panel(rows, args.out_root)
    print(f"wrote {path} ({len(rows)} rows)", flush=True)

    violations = check_invariants(pd.DataFrame(rows))
    if violations:
        print(f"INVARIANT VIOLATIONS ({len(violations)}):", flush=True)
        for v in violations[:20]:
            print(f"  - {v}", flush=True)
    else:
        print("invariants: clean", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
