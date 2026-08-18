"""The scale detector must not turn a storage outage into data.

Incident 2026-08-17: the OWC drive disconnected 3.5 hours into a 67,994-file
run. Every subsequent PDF was unreadable, and the detector recorded each one as
a normal result with unit_scale=None -- `recovered` froze at 19,642 while the
processed count climbed past 26,000. Left alone it would have written a table
in which ~70% of rows falsely claimed "no scale found", indistinguishable
downstream from a filing that genuinely has no scale word.

Two separate defects:
  1. No abort. A vanished filesystem is not a per-file result; consecutive
     unreadable files mean the storage is gone, exactly as consecutive HTTP
     failures mean a host is limiting (see rescue_missing_documents).
  2. No checkpoint. Results accumulated in memory and were written only at the
     end, so 3.5 hours of work vanished with the process.
"""

import pandas as pd
import pytest

from scripts.market_scale_detect import load_done, storage_action


# --- abort on a vanished filesystem -------------------------------------


def test_isolated_unreadable_files_do_not_abort():
    """A single corrupt PDF is a normal result, not an outage."""
    assert storage_action(consecutive_unreadable=1, limit=25) == "continue"
    assert storage_action(consecutive_unreadable=24, limit=25) == "continue"


def test_consecutive_unreadable_files_abort_the_run():
    assert storage_action(consecutive_unreadable=25, limit=25) == "abort"
    assert storage_action(consecutive_unreadable=999, limit=25) == "abort"


def test_a_disabled_limit_never_aborts():
    assert storage_action(consecutive_unreadable=10_000, limit=0) == "continue"


def test_the_counter_is_consecutive_not_cumulative():
    """Scattered unreadable files across a long run must not accumulate into a
    false abort -- 4 of 30 sampled Indian reports are legitimately unreadable."""
    assert storage_action(consecutive_unreadable=0, limit=25) == "continue"


# --- resume from a checkpoint -------------------------------------------


def test_load_done_returns_already_processed_paths(tmp_path):
    out = tmp_path / "scale.parquet"
    pd.DataFrame([{"pdf": "/a/1.pdf", "unit_scale": 100000},
                  {"pdf": "/a/2.pdf", "unit_scale": None}]).to_parquet(out, index=False)
    assert load_done(out) == {"/a/1.pdf", "/a/2.pdf"}


def test_load_done_on_a_missing_file_is_empty_not_an_error(tmp_path):
    assert load_done(tmp_path / "nope.parquet") == set()


def test_load_done_survives_a_corrupt_checkpoint(tmp_path):
    """A checkpoint truncated by the same outage must not block the restart."""
    bad = tmp_path / "bad.parquet"
    bad.write_bytes(b"not a parquet file")
    assert load_done(bad) == set()


def test_unreadable_rows_are_not_treated_as_done(tmp_path):
    """A file recorded as unreadable during an outage must be RETRIED on
    resume, not skipped as already processed."""
    out = tmp_path / "scale.parquet"
    pd.DataFrame([
        {"pdf": "/a/1.pdf", "unit_scale": 100000, "evidence": "anchored:3votes"},
        {"pdf": "/a/2.pdf", "unit_scale": None, "evidence": "unreadable"},
    ]).to_parquet(out, index=False)
    assert load_done(out) == {"/a/1.pdf"}
