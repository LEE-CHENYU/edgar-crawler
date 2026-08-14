"""Tests for the durable resume ledger.

The ledger exists because every other surface lied at least once: the queue
reported complete=true at 16% coverage, and the Nordic tracker entry showed
"running" for 34 days after its process died.
"""

from scripts.corpus_ledger import (
    is_stale,
    next_actions,
    process_alive,
    relevant_jobs,
    render_markdown,
)


def _job(**kw):
    base = {
        "id": "j", "title": "t", "status": "running",
        "updated_at": "2026-08-14T17:00:00+00:00", "tags": ["corpus"],
        "pid": None, "progress": {"current": 1, "total": 2},
    }
    base.update(kw)
    return base


def test_job_running_but_silent_for_days_is_stale():
    """The Nordic entry showed running for 34 days after the process died."""
    assert is_stale(_job(updated_at="2026-07-11T00:00:00+00:00")) is True


def test_recently_updated_running_job_is_not_stale():
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    assert is_stale(_job(updated_at=now)) is False


def test_finished_job_is_never_stale():
    assert is_stale(_job(status="done", updated_at="2020-01-01T00:00:00+00:00")) is False


def test_missing_timestamp_does_not_crash():
    assert is_stale(_job(updated_at=None)) is False


def test_process_alive_reports_false_for_dead_pid():
    assert process_alive(999999) is False


def test_process_alive_reports_none_without_pid():
    assert process_alive(None) is None


def test_relevant_jobs_filters_out_unrelated_work():
    jobs = [
        _job(id="rescue_twse", tags=["corpus", "rescue"]),
        _job(id="aeread_panel_probe", tags=["research"], title="AERead panel"),
    ]
    assert [j["id"] for j in relevant_jobs(jobs)] == ["rescue_twse"]


def test_next_actions_flags_dead_job_claiming_to_run():
    jobs = [_job(id="nordic", pid=999999, updated_at="2026-07-11T00:00:00+00:00")]
    actions = next_actions([], jobs)
    assert any("STALE" in a and "nordic" in a for a in actions)


def test_next_actions_lists_markets_with_gaps():
    coverage = [{"market": "TWSE_REPORTS", "rows": 100, "acquired": 16,
                 "missing": 84, "pct": 16.0}]
    actions = next_actions(coverage, [])
    assert any("TWSE_REPORTS" in a and "84" in a for a in actions)


def test_next_actions_is_explicit_when_nothing_to_do():
    assert next_actions([], []) == ["No gaps detected; nothing queued."]


def test_markdown_leads_with_next_actions():
    ledger = {
        "generated_at": "2026-08-14T17:00:00+00:00",
        "data_root": "/d",
        "coverage": [{"market": "M", "rows": 10, "acquired": 4, "missing": 6, "pct": 40.0}],
        "jobs": [],
        "next_actions": ["do the thing"],
        "totals": {"rows": 10, "acquired": 4, "missing": 6},
    }
    out = render_markdown(ledger)
    assert out.index("Next actions") < out.index("Coverage by market")
    assert "do the thing" in out
    assert "6" in out
