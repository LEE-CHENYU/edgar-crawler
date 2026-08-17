"""HK adapter: tidy long facts -> wide panel rows.

HK stage-02 emits one JSONL.gz record per extracted line item, so this is a
pivot rather than extraction. `net_assets` is deliberately NOT aliased to
`total_equity` -- they are not interchangeable and conflating them would
silently corrupt equity figures.
"""
from __future__ import annotations

import gzip
import json
from pathlib import Path
from typing import Dict, Iterator, List

from panel.schema import METRIC_COLUMNS, is_plausible_fiscal_year, period_type_for, to_number

HK_METRIC_ALIASES: Dict[str, str] = {
    "profit_for_year": "net_income",
    "profit_for_the_year": "net_income",
}

# unit_text on HK fact records is the REPORTING CURRENCY, not a scale factor
# (a live scan of 30k records showed HK$/RMB/US$/Hong Kong dollars/Renminbi/
# HKD/USD/Rmb -- ~40% of HK-listed filings report in RMB, ~7% in USD).
# Keys are matched case-insensitively after stripping whitespace.
HK_CURRENCY_ALIASES: Dict[str, str] = {
    "hk$": "HKD", "hkd": "HKD", "hong kong dollars": "HKD",
    "rmb": "CNY", "renminbi": "CNY",
    "us$": "USD", "usd": "USD",
}
_HK_DEFAULT_CURRENCY = "HKD"


def _resolve_currency(unit_text) -> "tuple[str, str]":
    """Map a raw unit_text to (currency, currency_source).

    Falls back to the exchange default (HKD) when unit_text is missing,
    empty, or unrecognised -- but always records the raw value in
    currency_source so the fallback is auditable rather than silent.
    """
    raw = str(unit_text or "").strip()
    if not raw:
        return _HK_DEFAULT_CURRENCY, "fallback:missing"
    currency = HK_CURRENCY_ALIASES.get(raw.lower())
    if currency is not None:
        return currency, raw
    return _HK_DEFAULT_CURRENCY, f"fallback:unrecognised:{raw}"


def iter_hk_fact_records(facts_root, limit: int = 0) -> Iterator[dict]:
    """Yield JSON records from every *.jsonl.gz file under facts_root.

    Unreadable/corrupt gzip files and individual unparseable lines are
    skipped rather than raising, so one bad filing does not kill the run.
    """
    count = 0
    for path in sorted(Path(facts_root).rglob("*.jsonl.gz")):
        try:
            with gzip.open(path, "rt", encoding="utf-8", errors="replace") as fin:
                for line in fin:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        yield json.loads(line)
                    except ValueError:
                        continue
                    count += 1
                    if limit and count >= limit:
                        return
        except OSError:
            continue


def rows_from_hk_facts(records) -> List[dict]:
    """Pivot tidy long HK fact records into one wide row per (stock_code, fiscal_year).

    `value_index` orders repeated extractions of the same metric within a
    filing; the lowest index is the primary value and wins regardless of
    arrival order.
    """
    grouped: Dict[tuple, dict] = {}
    winning_index: Dict[tuple, int] = {}
    currency_locked: Dict[tuple, bool] = {}
    for rec in records:
        year = rec.get("fiscal_year")
        code = str(rec.get("stock_code") or "").strip()
        if not code or not is_plausible_fiscal_year(year):
            continue
        fiscal_year = int(year)
        key = (code, fiscal_year)
        row = grouped.get(key)
        if row is None:
            period_end = f"{fiscal_year}-12-31"
            currency, currency_source = _resolve_currency(rec.get("unit_text"))
            row = {
                "market": "hk",
                "local_id": code,
                "company_name": rec.get("company_name") or None,
                "currency": currency,
                "currency_source": currency_source,
                "fiscal_year": fiscal_year,
                "period_end": period_end,
                "period_type": period_type_for(period_end, cadence="annual"),
                "source_artifact": "markets/hk/02_structured/hkex_financials/facts",
            }
            grouped[key] = row
            currency_locked[key] = not currency_source.startswith("fallback:")
        elif not currency_locked.get(key):
            # Currency is per (stock_code, fiscal_year) group; records within
            # one filing should agree. If they disagree, the first RECOGNISED
            # unit_text wins and further records never thrash it.
            currency, currency_source = _resolve_currency(rec.get("unit_text"))
            if not currency_source.startswith("fallback:"):
                row["currency"] = currency
                row["currency_source"] = currency_source
                currency_locked[key] = True

        metric = HK_METRIC_ALIASES.get(rec.get("metric"), rec.get("metric"))
        if metric not in METRIC_COLUMNS:
            continue

        value = to_number(rec.get("value"))
        if value is None:
            row.setdefault(metric, None)
            continue

        index = int(rec.get("value_index") or 0)
        metric_key = (key, metric)
        prior_index = winning_index.get(metric_key)
        if prior_index is None or index < prior_index:
            row[metric] = value
            winning_index[metric_key] = index

    return list(grouped.values())
