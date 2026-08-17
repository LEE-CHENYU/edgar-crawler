import pandas as pd
from panel.views import quarterly_view


def _panel():
    return pd.DataFrame([
        {"spine_key": "B1", "period_end": "2024-03-31", "period_type": "Q", "revenue": 1.0},
        {"spine_key": "B1", "period_end": "2024-12-31", "period_type": "A", "revenue": 4.0},
        {"spine_key": "B1", "period_end": "2024-01-01", "period_type": "OPEN", "revenue": 9.0},
        {"spine_key": "B2", "period_end": "2024-06-30", "period_type": "H", "revenue": 2.0},
    ])


def test_open_rows_are_excluded_from_the_quarterly_view():
    """Opening balances would double-count against the prior close."""
    out = quarterly_view(_panel())
    assert "OPEN" not in set(out["period_type"])
    assert 9.0 not in set(out["revenue"])


def test_view_keeps_q_h_and_a_rows():
    out = quarterly_view(_panel())
    assert set(out["period_type"]) == {"Q", "A", "H"}


def test_view_does_not_forward_fill_by_default():
    out = quarterly_view(_panel())
    assert len(out) == 3


def test_view_is_sorted_by_security_then_period():
    out = quarterly_view(_panel())
    first = out.iloc[0]
    assert first["spine_key"] == "B1" and first["period_end"] == "2024-03-31"


def test_forward_fill_is_opt_in_and_flags_filled_rows():
    out = quarterly_view(_panel(), forward_fill=True)
    assert "is_filled" in out.columns
    assert out["is_filled"].any()


def _panel_with_null_metric():
    # B1 has two REAL filed rows (Q1 and the annual close). The Q1 row has a
    # null total_assets — a legitimately-filed row with an incomplete metric
    # (e.g. JP's permanently-null metrics), not a synthesized grid slot.
    return pd.DataFrame([
        {"spine_key": "B1", "period_end": "2024-03-31", "period_type": "Q",
         "revenue": 1.0, "total_assets": None},
        {"spine_key": "B1", "period_end": "2024-12-31", "period_type": "A",
         "revenue": 4.0, "total_assets": 100.0},
    ])


def test_real_row_with_null_metric_is_not_flagged_as_filled():
    """A filed row with a null field is real data, not something the view invented."""
    out = quarterly_view(_panel_with_null_metric(), forward_fill=True)
    q1 = out[out["period_end"] == "2024-03-31"].iloc[0]
    assert q1["is_filled"] == False  # noqa: E712 — explicit bool check, not truthiness


def test_synthesized_quarter_with_no_real_row_is_flagged_as_filled():
    out = quarterly_view(_panel_with_null_metric(), forward_fill=True)
    synthesized = out[out["period_end"].isin(["2024-06-30", "2024-09-30"])]
    assert len(synthesized) == 2
    assert synthesized["is_filled"].all()


def test_is_filled_count_matches_grid_slots_without_a_real_row():
    """A blanket True or blanket False for is_filled must not be able to pass this."""
    out = quarterly_view(_panel_with_null_metric(), forward_fill=True)
    # Grid for B1 spans 2024-03-31..2024-12-31 quarterly: 4 slots, 2 real rows.
    assert len(out) == 4
    assert out["is_filled"].sum() == 2
    assert (~out["is_filled"]).sum() == 2


def test_open_rows_still_excluded_when_forward_filling():
    out = quarterly_view(_panel(), forward_fill=True)
    assert "OPEN" not in set(out["period_type"])
    assert 9.0 not in set(out["revenue"])
