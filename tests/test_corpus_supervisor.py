"""A supervisor must start work when it becomes available -- and otherwise sit still.

The existing annual-financials supervisor restarts every ~80 seconds with
nothing to do, which is the failure mode to avoid: a busy loop that burns I/O
on a saturated volume and fills logs with noise, so real events hide in it.

The rules encoded here:
  - never launch a job whose preconditions are unmet
  - never launch a job that is already running (double-download, doubled load on
    a rate-limited host)
  - never re-launch a job that already completed
  - respect a concurrency cap, because the bottleneck is one external drive
"""

import pytest

from scripts.corpus_supervisor import Job, decide, is_satisfied


def _job(job_id, needs=(), **kw):
    return Job(id=job_id, title=job_id, command=["true"], needs=list(needs), **kw)


# --- preconditions -------------------------------------------------------


def test_a_job_with_no_preconditions_is_ready():
    assert is_satisfied(_job("a"), state={}) is True


def test_a_job_waits_for_a_named_predecessor():
    job = _job("b", needs=["a"])
    assert is_satisfied(job, state={"a": "running"}) is False
    assert is_satisfied(job, state={"a": "done"}) is True


def test_a_failed_predecessor_does_not_satisfy():
    """A failed upstream must block, not release -- otherwise a sweep runs over
    a corpus that was never acquired."""
    assert is_satisfied(_job("b", needs=["a"]), state={"a": "failed"}) is False


def test_all_predecessors_must_be_done():
    job = _job("c", needs=["a", "b"])
    assert is_satisfied(job, state={"a": "done", "b": "running"}) is False
    assert is_satisfied(job, state={"a": "done", "b": "done"}) is True


# --- what decide() launches ---------------------------------------------


def test_ready_jobs_are_launched():
    jobs = [_job("a")]
    assert decide(jobs, state={}, running=set(), cap=2) == ["a"]


def test_a_running_job_is_never_relaunched():
    """Relaunching a live downloader doubles the load on a rate-limited host."""
    jobs = [_job("a")]
    assert decide(jobs, state={"a": "running"}, running={"a"}, cap=2) == []


def test_a_done_job_is_never_relaunched():
    jobs = [_job("a")]
    assert decide(jobs, state={"a": "done"}, running=set(), cap=2) == []


def test_the_concurrency_cap_is_respected():
    """One external drive is the bottleneck; three sweeps at once is slower than
    two, and it is how the volume filled up."""
    jobs = [_job(x) for x in ("a", "b", "c")]
    assert decide(jobs, state={}, running=set(), cap=2) == ["a", "b"]


def test_the_cap_counts_jobs_already_running():
    jobs = [_job(x) for x in ("a", "b", "c")]
    assert decide(jobs, state={"a": "running"}, running={"a"}, cap=2) == ["b"]


def test_nothing_ready_launches_nothing():
    """The quiet path must be genuinely quiet -- this is the busy-restart bug."""
    jobs = [_job("b", needs=["a"])]
    assert decide(jobs, state={"a": "running"}, running={"a"}, cap=4) == []


def test_a_blocked_chain_does_not_deadlock_the_rest():
    jobs = [_job("b", needs=["missing"]), _job("c")]
    assert decide(jobs, state={}, running=set(), cap=2) == ["c"]


# --- completion is an artifact, not a status flag ------------------------


def test_a_dead_pid_with_status_running_does_not_count_as_done():
    """Oslo stage-02 finished at 71.7% yield and wrote 15,972 rows, but its
    tracker entry still read `running` because the sweep has no finish hook.
    The chain stalled behind a stale flag -- the same class as a backfill that
    read `running` for 34 days. A dead pid means UNKNOWN, never success."""
    job = _job("b", needs=["a"])
    assert is_satisfied(job, state={"a": "running"}) is False


def test_an_artifact_check_can_satisfy_a_dependency(tmp_path):
    """Completion is defined on the artifact produced, not on a flag someone
    remembered to set (practices 1.1)."""
    from scripts.corpus_supervisor import artifact_satisfied
    out = tmp_path / "wide.parquet"
    import pandas as pd
    pd.DataFrame({"a": range(12)}).to_parquet(out)
    assert artifact_satisfied(str(out), min_rows=10) is True
    assert artifact_satisfied(str(out), min_rows=99) is False


def test_a_missing_artifact_is_not_satisfied(tmp_path):
    from scripts.corpus_supervisor import artifact_satisfied
    assert artifact_satisfied(str(tmp_path / "absent.parquet"), min_rows=1) is False


def test_produces_lets_a_stalled_but_finished_job_release_the_chain(tmp_path):
    """The live case: tracker says running, pid dead, artifact complete."""
    import pandas as pd
    out = tmp_path / "wide.parquet"
    pd.DataFrame({"a": range(20)}).to_parquet(out)
    upstream = _job("a", produces=str(out), min_rows=10)
    downstream = _job("b", needs=["a"])
    state = {"a": "running"}   # stale flag
    assert is_satisfied(downstream, state, jobs=[upstream, downstream]) is True
