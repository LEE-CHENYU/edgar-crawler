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
