"""A cursor is only meaningful against the queue that produced it.

Incident 2026-08-18: the TWSE rescue exhausted a 12,497-row queue, leaving
cursor=12484. The next run rebuilt the queue from current metadata -- 11,038
rows, because ~1,459 documents had been acquired -- and the persisted cursor
(12,484) was past the end. The run reported "done" instantly and skipped all
11,038 remaining documents. Silent, and it would have recurred on every restart.

Resetting to 0 costs nothing: acquired rows leave the missing-list, so no work
is repeated.
"""

import pytest

from scripts.rescue_missing_documents import cursor_for_queue


def test_cursor_past_the_end_of_a_rebuilt_queue_resets():
    """The live case."""
    assert cursor_for_queue(saved_cursor=12484, saved_queue_size=12497,
                            current_queue_size=11038) == 0


def test_a_shrunken_queue_resets_even_when_the_cursor_still_fits():
    """5000 into a 12,497-row queue is NOT 5000 into an 11,038-row one -- the
    rows shifted when acquired rows left the list."""
    assert cursor_for_queue(saved_cursor=5000, saved_queue_size=12497,
                            current_queue_size=11038) == 0


def test_an_unchanged_queue_keeps_the_cursor():
    """The whole point of the cursor: resume mid-queue after a cooldown."""
    assert cursor_for_queue(saved_cursor=5000, saved_queue_size=12497,
                            current_queue_size=12497) == 5000


def test_legacy_state_without_a_queue_size_is_trusted_only_if_it_fits():
    assert cursor_for_queue(saved_cursor=5000, saved_queue_size=None,
                            current_queue_size=11038) == 5000
    assert cursor_for_queue(saved_cursor=12484, saved_queue_size=None,
                            current_queue_size=11038) == 0


def test_a_fresh_run_starts_at_zero():
    assert cursor_for_queue(saved_cursor=0, saved_queue_size=None,
                            current_queue_size=11038) == 0


def test_an_empty_queue_yields_zero():
    assert cursor_for_queue(saved_cursor=99, saved_queue_size=99,
                            current_queue_size=0) == 0
