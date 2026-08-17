"""HK adapter: tidy long facts -> wide panel rows.

HK stage-02 emits one JSONL.gz record per extracted line item, so this is a
pivot rather than extraction. `net_assets` is deliberately NOT aliased to
`total_equity` -- they are not interchangeable and conflating them would
silently corrupt equity figures.
"""
from __future__ import annotations

import gzip
import json
from collections import Counter
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

# Scale words that may ride along in unit_text. HK stage-02 DOES emit a
# numeric `unit_scale` field per record (live scan: 175,064 records at 1 and 53
# at 1000000, all 53 with unit_text='HK$Million'), but it only captures the
# scale when the currency string itself carries the word. The far more common
# case -- a source table headed "RMB'000" or "RMB million" with a bare 'RMB'
# unit_text -- is NOT captured anywhere, which is why the same company can
# appear 1000x apart between filings:
#   Tencent 00700 total_assets, unit_text='RMB' in both:
#     filing 1617463 FY2011 -> 56,804,365  (thousands)
#     filing 1875861 FY2009 ->     17,506  (millions)
# unit_scale is therefore RECORDED, never applied: multiplying would fabricate
# precision the corpus does not have, while a visible scale column lets a
# consumer decide and lets the ambiguity be audited.
_SCALE_WORDS = (
    ("billion", 1_000_000_000),
    ("bn", 1_000_000_000),
    ("million", 1_000_000),
    ("thousand", 1_000),
    ("'000", 1_000),
    ("’000", 1_000),
)
DEFAULT_UNIT_SCALE = 1


def scale_from_unit_text(unit_text) -> "int | None":
    """Scale multiplier implied by a unit_text, or None when it declares none."""
    low = str(unit_text or "").strip().lower()
    if not low:
        return None
    for word, scale in _SCALE_WORDS:
        if word in low:
            return scale
    return None


def _resolve_unit_scale(record: dict) -> "int | None":
    """Scale for one fact record: the source's own unit_scale, else unit_text."""
    raw = record.get("unit_scale")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = None
    if value and value > 1:
        return value
    return scale_from_unit_text(record.get("unit_text"))


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


def rows_from_hk_facts(records, drops: "Counter | None" = None) -> List[dict]:
    """Pivot tidy long HK fact records into one wide row per
    (filing_id, stock_code, fiscal_year).

    filing_id is part of the group key, and that is load-bearing. Grouping by
    (stock_code, fiscal_year) across the WHOLE corpus -- what this did until
    2026-08-17 -- merged the comparative-year column of a later annual report
    into the original filing's row, so figures 1000x apart (different
    presentation scales, see _SCALE_WORDS) were arbitrated by `value_index`,
    i.e. effectively by directory iteration order. Live proof: Tencent 00700
    total_assets, unit_text='RMB' in both, filing 1617463 FY2011 = 56,804,365
    vs filing 1875861 FY2009 = 17,506. Keeping filing_id in the key makes each
    row internally consistent with one document, and source_doc_id makes it
    traceable upstream.

    `value_index` still arbitrates repeated extractions of the same metric
    WITHIN one filing (the same filing emits raw_label 'Total assets' several
    times: group, segment, company-level); the lowest index wins regardless of
    arrival order. That contest is now scoped to a single document rather than
    to the whole corpus.
    """
    if drops is None:
        drops = Counter()
    grouped: Dict[tuple, dict] = {}
    winning_index: Dict[tuple, int] = {}
    currency_locked: Dict[tuple, bool] = {}
    for rec in records:
        year = rec.get("fiscal_year")
        code = str(rec.get("stock_code") or "").strip()
        filing_id = str(rec.get("filing_id") or "").strip() or None
        if not code:
            drops["missing_stock_code"] += 1
            continue
        if not is_plausible_fiscal_year(year):
            drops["implausible_fiscal_year"] += 1
            continue
        if filing_id is None:
            drops["missing_filing_id"] += 1
            continue
        fiscal_year = int(year)
        key = (filing_id, code, fiscal_year)
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
                "unit_scale": _resolve_unit_scale(rec) or DEFAULT_UNIT_SCALE,
                "source_doc_id": filing_id,
                "source_artifact": "markets/hk/02_structured/hkex_financials/facts",
            }
            grouped[key] = row
            currency_locked[key] = not currency_source.startswith("fallback:")
        else:
            # A filing can declare a scale on some lines only (e.g. a
            # "HK$Million" summary table alongside bare-'HK$' statements). Take
            # the first DECLARED scale rather than letting the first record's
            # default of 1 stand, and count the disagreement.
            scale = _resolve_unit_scale(rec)
            if scale and scale != row.get("unit_scale"):
                if row.get("unit_scale", DEFAULT_UNIT_SCALE) == DEFAULT_UNIT_SCALE:
                    row["unit_scale"] = scale
                else:
                    drops["unit_scale_conflict_within_filing"] += 1
            if not currency_locked.get(key):
                # Currency is per group; records within one filing should
                # agree. If they disagree, the first RECOGNISED unit_text wins
                # and further records never thrash it.
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
