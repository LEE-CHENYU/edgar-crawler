"""A few bad rows must not cost a whole market.

The adapter raised ValueError on any duplicate (ticker, doc_id). The intent is
right -- duplicate identity means two rows claim the same filing -- but the
response was disproportionate: the live IN_BSE parquet contains exactly ONE
such pair out of 67,993 rows (document 5238460310 filed under both FY2009 and
FY2010, same value), and it blocked the entire market from building. The panel
on disk carries 16 Indian rows as a result.

The guard must still fire on systemic identity collapse, which is what it was
written for. So: drop the offending rows and count them by reason, and raise
only when duplicates exceed a share of the market that no data-entry artifact
could explain.
"""

from collections import Counter

import pandas as pd
import pytest

from panel.adapters.in_bse import DUPLICATE_IDENTITY_LIMIT, rows_from_canonical_metrics


def _frame(pairs):
    return pd.DataFrame([
        {"ticker": t, "doc_id": d, "fiscal_year": fy, "revenue": 1.0}
        for t, d, fy in pairs
    ])


def test_a_single_duplicate_does_not_kill_the_market():
    """The live IN_BSE case: one duplicated pair among many good rows."""
    pairs = [("A", f"d{i}", 2020) for i in range(50)]
    pairs += [("B", "dup", 2009), ("B", "dup", 2010)]
    drops = Counter()
    rows = rows_from_canonical_metrics(_frame(pairs), market="in_bse",
                                       currency="INR", drops=drops)
    assert len(rows) == 50
    assert drops["duplicate_identity"] == 2


def test_dropped_duplicates_are_counted_not_silent():
    """Spec Sec 6: never silently skipped."""
    pairs = [("A", f"d{i}", 2020) for i in range(50)]
    pairs += [("B", "dup", 2009), ("B", "dup", 2010)]
    drops = Counter()
    rows_from_canonical_metrics(_frame(pairs), market="in_bse",
                                currency="INR", drops=drops)
    assert drops["duplicate_identity"] == 2


def test_systemic_identity_collapse_still_raises():
    """If most rows share an identity, the upstream extraction is broken and a
    silent 90% drop would be worse than a failure."""
    pairs = [("A", "same", 2000 + i) for i in range(40)]
    pairs += [("B", f"d{i}", 2020) for i in range(5)]
    with pytest.raises(ValueError, match="duplicate"):
        rows_from_canonical_metrics(_frame(pairs), market="in_bse",
                                    currency="INR", drops=Counter())


def test_the_limit_is_a_named_constant_not_a_magic_number():
    assert 0 < DUPLICATE_IDENTITY_LIMIT < 1


def test_clean_frames_are_untouched():
    pairs = [("A", f"d{i}", 2020) for i in range(10)]
    drops = Counter()
    rows = rows_from_canonical_metrics(_frame(pairs), market="au",
                                       currency="AUD", drops=drops)
    assert len(rows) == 10
    assert drops["duplicate_identity"] == 0


def test_stapled_tickers_sharing_one_doc_id_are_still_legitimate():
    """One ASX filing can cover several co-listed tickers -- doc_id repeats but
    (ticker, doc_id) does not. That must not be treated as a duplicate."""
    pairs = [("HDN", "02969892", 2024), ("HCW", "02969892", 2024),
             ("DGT", "02969892", 2024)]
    drops = Counter()
    rows = rows_from_canonical_metrics(_frame(pairs), market="au",
                                       currency="AUD", drops=drops)
    assert len(rows) == 3
    assert drops["duplicate_identity"] == 0
