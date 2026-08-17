"""Tests for folding the orphaned Nordic corpus into the canonical layout.

A wrong path rewrite would point 5,142 metadata rows at files that do not
exist, so the mapping is tested before 13 GB is moved.
"""

from scripts.fold_nordic_into_canonical import (
    canonical_dir_for,
    rewrite_local_path,
)


ORPHAN = (
    "/Users/lichenyu/datasets/MARKET_FILINGS/raw/NASDAQ_NORDIC_BALTIC_NEWS"
    "/SE/2024/1234567.pdf"
)


def test_rewrite_maps_country_into_canonical_tree():
    out = rewrite_local_path(ORPHAN)
    assert out == (
        "/Users/lichenyu/datasets/markets/se/01_raw/nasdaq_nordic_baltic"
        "/2024/1234567.pdf"
    )


def test_rewrite_preserves_nested_remainder():
    src = (
        "/Users/lichenyu/datasets/MARKET_FILINGS/raw/NASDAQ_NORDIC_BALTIC_NEWS"
        "/LV/2019/07/deep/file.pdf"
    )
    assert rewrite_local_path(src).endswith("/lv/01_raw/nasdaq_nordic_baltic/2019/07/deep/file.pdf")


def test_rewrite_handles_every_expected_country():
    for cc in ("SE", "DK", "FI", "IS", "LT", "LV", "EE"):
        src = f"/x/raw/NASDAQ_NORDIC_BALTIC_NEWS/{cc}/f.pdf"
        assert f"/{cc.lower()}/01_raw/" in rewrite_local_path(src)


def test_rewrite_leaves_foreign_paths_alone():
    """Rows for other markets must not be touched."""
    assert rewrite_local_path("/Volumes/OWC/.../raw/DART/20080125/x.zip") is None
    assert rewrite_local_path("") is None
    assert rewrite_local_path("/raw/NASDAQ_NORDIC_BALTIC_NEWS/") is None


def test_rewrite_rejects_unknown_country_dir():
    """An unexpected directory should not be silently relocated."""
    assert rewrite_local_path("/x/raw/NASDAQ_NORDIC_BALTIC_NEWS/ZZ/f.pdf") is None


def test_canonical_dir_is_lowercase_market_code():
    assert canonical_dir_for("SE").as_posix().endswith("markets/se/01_raw/nasdaq_nordic_baltic")
