"""Views over the panel, plus invariant checks."""
from __future__ import annotations

from typing import List

import pandas as pd

from panel.schema import is_plausible_fiscal_year, period_type_for

# Relative tolerance for the balance sheet identity check
# (total_liabilities + total_equity ~= total_assets). Figures span from tiny
# caps to trillions, so a fixed absolute epsilon is useless — 1% of
# total_assets scales with the row.
_BALANCE_IDENTITY_TOLERANCE = 0.01


def quarterly_view(df: pd.DataFrame, forward_fill: bool = False) -> pd.DataFrame:
    """Reporting-period view with opening balances removed.

    OPEN rows are restated opening balances and would double-count against the
    prior close, so they never appear here. Forward-filling is opt-in and
    flagged, never the default.
    """
    out = df[df["period_type"] != "OPEN"].copy()
    out = out.sort_values(["spine_key", "period_end"]).reset_index(drop=True)
    if not forward_fill:
        return out
    if out.empty:
        out["is_filled"] = pd.Series([], dtype=bool)
        return out

    quarter_month_days = ("03-31", "06-30", "09-30", "12-31")
    filled_groups = []
    for spine_key, group in out.groupby("spine_key", sort=False):
        group = group.sort_values("period_end")
        lo, hi = group["period_end"].min(), group["period_end"].max()
        years = range(int(lo[:4]), int(hi[:4]) + 1)
        grid_ends = sorted(
            f"{y}-{md}" for y in years for md in quarter_month_days
            if lo <= f"{y}-{md}" <= hi
        )
        grid = pd.DataFrame({"spine_key": spine_key, "period_end": grid_ends})
        merged = grid.merge(
            group, on=["spine_key", "period_end"], how="left", indicator=True
        )
        # is_filled means "this grid slot had no matching real row" — NOT
        # "the matched real row has some null field". A real, filed row can
        # legitimately have null metrics (e.g. JP's six permanently-null
        # metrics) without being synthetic; conflating the two would mislabel
        # real data as fabricated, which is the wrong direction for a
        # research panel.
        merged["is_filled"] = merged["_merge"] == "left_only"
        merged = merged.drop(columns=["_merge"])
        merged = merged.sort_values("period_end").ffill()
        filled_groups.append(merged)
    return pd.concat(filled_groups, ignore_index=True) if filled_groups else out


def _check_balance_identity(row) -> str | None:
    """Flag total_liabilities + total_equity != total_assets (1% relative tolerance).

    Task 6 found real HK rows where total_equity exactly equals total_assets
    while total_liabilities is separately non-zero — upstream stage-02
    mislabeling. This flags the row; it never corrects it. Only evaluated
    when all three values are present and total_assets is non-zero — a row
    missing one of them is incomplete data, not a wrong value (JP legitimately
    has six permanently-null metrics).
    """
    ta_raw = row.get("total_assets")
    tl_raw = row.get("total_liabilities")
    te_raw = row.get("total_equity")
    if ta_raw is None or pd.isna(ta_raw):
        return None
    if tl_raw is None or pd.isna(tl_raw):
        return None
    if te_raw is None or pd.isna(te_raw):
        return None
    try:
        total_assets = float(ta_raw)
        total_liabilities = float(tl_raw)
        total_equity = float(te_raw)
    except (TypeError, ValueError):
        return None
    if total_assets == 0:
        return None
    diff = abs(total_liabilities + total_equity - total_assets)
    tolerance = abs(total_assets) * _BALANCE_IDENTITY_TOLERANCE
    if diff <= tolerance:
        return None
    return (
        f"balance sheet identity violated for spine_key={row.get('spine_key')!r} "
        f"period_end={row.get('period_end')!r}: "
        f"total_liabilities={total_liabilities} + total_equity={total_equity} "
        f"!= total_assets={total_assets}"
    )


def check_invariants(df: pd.DataFrame) -> List[str]:
    violations: List[str] = []
    if df.empty:
        return violations

    keys = ["spine_key", "period_end", "period_type"]
    if all(k in df.columns for k in keys):
        dupes = df.duplicated(subset=keys).sum()
        if dupes:
            violations.append(f"{dupes} duplicate (spine_key, period_end, period_type) rows")

    usd_cols = [c for c in df.columns if c.endswith("_usd")]

    for _, row in df.iterrows():
        pe, pt = row.get("period_end"), row.get("period_type")
        if pe and pt:
            cadence = "quarterly" if pt in ("Q", "OPEN") else (
                "semiannual" if pt == "H" else "annual"
            )
            try:
                expected = period_type_for(pe, cadence=cadence)
            except ValueError:
                violations.append(f"unusable period_end {pe!r}")
                continue
            if expected != pt:
                violations.append(f"period_type {pt} inconsistent with period_end {pe}")
        if not is_plausible_fiscal_year(row.get("fiscal_year")):
            violations.append(f"implausible fiscal_year {row.get('fiscal_year')!r}")
        if usd_cols and any(
            row.get(c) is not None and not pd.isna(row.get(c)) for c in usd_cols
        ):
            if row.get("fx_rate") is None or pd.isna(row.get("fx_rate")):
                violations.append("USD value present without fx_rate")

        balance_violation = _check_balance_identity(row)
        if balance_violation:
            violations.append(balance_violation)

    return violations
