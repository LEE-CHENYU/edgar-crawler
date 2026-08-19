"""Attach recovered unit scales to panel rows.

Stage 02 captured `unit_text` -- the reporting CURRENCY -- but never the
multiplier that the statement caption carries ("RMB'000", "Rs. in lakhs",
人民幣千元). The magnitude of every figure therefore depended on something
nobody recorded. Tencent's total_assets appears as 56,804,365 for FY2011
(thousands) and 17,506 for FY2009 (millions), both labelled RMB.

The recovery ran per market and wrote lookup tables:

    hk_unit_scale.parquet      25,565 rows, 98.6% recovered
    in_bse_unit_scale.parquet  67,994 rows, 84.5% recovered

Two invariants this module keeps:

1. **Values are never pre-multiplied.** The scale is metadata a consumer
   applies, matching the promise in panel/adapters/hk.py. Multiplying here
   would bake an irreversible guess into the panel.
2. **An unrecovered scale is `None`, never 1.** Defaulting to 1 is precisely
   what hid the original defect -- a figure in lakh read as units, off by
   10^5. Unknown stays unknown and is labelled, so a consumer can exclude
   those rows from level comparisons rather than silently trusting them.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional, Tuple

# The tables name their key column differently: HK by filing_id, the PDF
# markets by the document stem. Both correspond to the panel's source_doc_id.
KEY_COLUMNS = ("filing_id", "doc_stem", "source_doc_id", "doc_id")


def load_scale_table(path) -> Dict[str, Tuple[float, str]]:
    """{doc_id: (multiplier, evidence)} from a *_unit_scale.parquet.

    Rows with no recovered scale are dropped rather than loaded as a match --
    a null in the table would otherwise read as "scale known to be nothing".
    """
    try:
        import pandas as pd
        df = pd.read_parquet(Path(path))
    except Exception:
        return {}
    key = next((c for c in KEY_COLUMNS if c in df.columns), None)
    if key is None or "unit_scale" not in df.columns:
        return {}
    df = df[df["unit_scale"].notna()]
    out: Dict[str, Tuple[float, str]] = {}
    for rec in df.to_dict("records"):
        out[str(rec[key])] = (float(rec["unit_scale"]),
                              str(rec.get("evidence") or ""))

    # Trailing-segment aliases. The ASX table is keyed by PDF stem
    # ("SM1_20190709_02121819") while the panel's source_doc_id is the bare
    # document id ("02121819") -- zero direct overlap across 27,665 rows. An
    # alias is registered only when exactly one entry claims it: a collision
    # would attach one filing's scale to another filing's figures, and a real
    # key always wins over an alias.
    candidates: Dict[str, list] = {}
    for full in out:
        if "_" in full:
            candidates.setdefault(full.rsplit("_", 1)[1], []).append(full)
    for alias, owners in candidates.items():
        if len(owners) == 1 and alias not in out:
            out[alias] = out[owners[0]]
    return out


def load_scale_fallback(path) -> Dict[Tuple[str, str], Tuple[float, str]]:
    """{(entity, period_label): (multiplier, evidence)} for rows whose document
    id cannot be matched.

    IN_BSE doc_ids come in two shapes -- bare "5330890310" and prefixed
    "532955_2020_65588532955" -- and the scale table is keyed on the PDF stem,
    so a document-level join alone reached only 47.9% of rows against a table
    covering 84.5%. Company-year is the coarser key that closes the gap.

    A company-year whose documents disagree about the scale is OMITTED: the key
    cannot arbitrate between them, and guessing here would reintroduce exactly
    the silent-magnitude-error this whole exercise removes.
    """
    try:
        import pandas as pd
        df = pd.read_parquet(Path(path))
    except Exception:
        return {}
    need = {"entity", "period_label", "unit_scale"}
    if not need.issubset(df.columns):
        return {}
    df = df[df["unit_scale"].notna()]
    grouped: Dict[Tuple[str, str], set] = {}
    evidence: Dict[Tuple[str, str], str] = {}
    for rec in df.to_dict("records"):
        key = (str(rec["entity"]), str(rec["period_label"]))
        grouped.setdefault(key, set()).add(float(rec["unit_scale"]))
        evidence.setdefault(key, str(rec.get("evidence") or ""))
    return {k: (next(iter(v)), evidence[k])
            for k, v in grouped.items() if len(v) == 1}


def _source_label(evidence: str) -> str:
    """Provenance strong enough for a consumer to weigh.

    "anchored" means the caption sat beside a statement heading;
    "document-head" means it was only found in the opening pages, which is
    weaker -- an Indian report can mention crore in narrative and report its
    statements in lakh.
    """
    kind = str(evidence).split(":")[0]
    return f"detected:{kind}" if kind else "detected:unknown-evidence"


def _lookup(row: dict, table: Dict[str, Tuple[float, str]],
            fallback: Optional[Dict[Tuple[str, str], Tuple[float, str]]]):
    """Document-level match first, then company-year.

    The prefix strip uses the row's OWN ticker and year rather than splitting
    on "_", because stems legitimately contain underscores
    ("75040531225_23_09_22") and a split would mangle them.
    """
    doc_id = row.get("source_doc_id")
    if doc_id is not None:
        key = str(doc_id)
        if key in table:
            return table[key]
        prefix = f"{row.get('local_id')}_{row.get('fiscal_year')}_"
        if key.startswith(prefix) and key[len(prefix):] in table:
            return table[key[len(prefix):]]
    if fallback:
        return fallback.get((str(row.get("local_id")),
                             str(row.get("fiscal_year"))))
    return None


def attach_unit_scale(rows: List[dict],
                      table: Dict[str, Tuple[float, str]],
                      fallback: Optional[Dict[Tuple[str, str],
                                              Tuple[float, str]]] = None,
                      ) -> Dict[str, int]:
    """Set unit_scale / unit_scale_source on each row. Returns match counts."""
    matched = unknown = 0
    for row in rows:
        hit = _lookup(row, table, fallback)
        if hit is None:
            row["unit_scale"] = None
            row["unit_scale_source"] = "unknown"
            unknown += 1
            continue
        scale, evidence = hit
        row["unit_scale"] = scale
        row["unit_scale_source"] = _source_label(evidence)
        matched += 1
    return {"matched": matched, "unknown": unknown}
