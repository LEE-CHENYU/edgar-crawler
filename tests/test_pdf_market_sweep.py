"""Tests for the generic PDF-market stage-02 sweep.

asx_financials_sweep hardcodes its output dir and derives identifiers from a
filename regex. IN_BSE (67,994 docs, the largest unprocessed corpus) carries
its identifiers in the path instead: {scrip_code}/{year}/{file}.pdf. This
generalises target discovery per market while reusing the market-agnostic
extractor.
"""

import pytest

from scripts.pdf_market_sweep import (
    MARKETS,
    build_targets_asx,
    build_targets_in_bse,
    fiscal_year_from_bse_name,
    targets_for,
)


def _touch(root, rel):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"%PDF-1.4 stub")
    return p


# --- IN_BSE: identifiers come from the path ------------------------------


def test_in_bse_targets_read_scrip_and_year_from_path(tmp_path):
    _touch(tmp_path, "530145/2013/5301450313.pdf")
    targets = build_targets_in_bse(tmp_path, years=None)
    assert len(targets) == 1
    t = targets[0]
    assert t["ticker"] == "530145"
    assert t["fiscal_year"] == 2013
    assert t["doc_id"] == "5301450313"
    assert t["pdf_path"].endswith("530145/2013/5301450313.pdf")


def test_in_bse_targets_filter_by_year(tmp_path):
    _touch(tmp_path, "530145/2013/a.pdf")
    _touch(tmp_path, "530145/2018/b.pdf")
    got = build_targets_in_bse(tmp_path, years={"2018"})
    assert [t["fiscal_year"] for t in got] == [2018]


def test_in_bse_skips_paths_without_a_year_directory(tmp_path):
    """A stray PDF at the corpus root must not become a bogus target."""
    _touch(tmp_path, "loose.pdf")
    _touch(tmp_path, "530145/notayear/c.pdf")
    assert build_targets_in_bse(tmp_path, years=None) == []


def test_in_bse_doc_ids_are_unique_across_years(tmp_path):
    """doc_id is the resume key, so collisions would silently skip work."""
    _touch(tmp_path, "505052/2013/5050520313.pdf")
    _touch(tmp_path, "505052/2014/5050520314.pdf")
    ids = [t["doc_id"] for t in build_targets_in_bse(tmp_path, years=None)]
    assert len(ids) == len(set(ids)) == 2


def test_fiscal_year_from_bse_name_prefers_the_directory():
    """The filename encodes a date too; the directory is authoritative."""
    assert fiscal_year_from_bse_name("5301450313.pdf", "2013") == 2013
    assert fiscal_year_from_bse_name("garbage.pdf", "2019") == 2019


# --- ASX behaviour must not regress -------------------------------------


def test_asx_targets_still_parse_from_filename(tmp_path):
    _touch(tmp_path, "BHP/BHP-20240101-12345.pdf")
    targets = build_targets_asx(tmp_path, years=None)
    assert len(targets) == 1
    assert targets[0]["ticker"] == "BHP"
    assert targets[0]["filing_date"] == "20240101"


# --- registry -----------------------------------------------------------


def test_market_registry_covers_in_bse_with_its_own_out_dir():
    assert "in_bse" in MARKETS
    cfg = MARKETS["in_bse"]
    assert "IN_BSE" in cfg["raw_root"]
    assert "IN_BSE" in cfg["out_dir"]


def test_targets_for_dispatches_by_market(tmp_path):
    _touch(tmp_path, "530145/2013/5301450313.pdf")
    got = targets_for("in_bse", tmp_path, years=None)
    assert got[0]["ticker"] == "530145"


def test_targets_for_rejects_unknown_market(tmp_path):
    with pytest.raises(KeyError):
        targets_for("atlantis", tmp_path, years=None)


# --- Nordic and Oslo have their own path shapes ---------------------------


def test_nordic_targets_read_year_and_company_from_path(tmp_path):
    """Nordic layout is {year}/{company_id}/{file}.pdf — NOT {scrip}/{year}/."""
    from scripts.pdf_market_sweep import build_targets_nordic

    _touch(tmp_path, "2013/542683/Cryptzone_Bokslutskommunike_2012.pdf")
    targets = build_targets_nordic(tmp_path, years=None)
    assert len(targets) == 1
    t = targets[0]
    assert t["ticker"] == "542683"
    assert t["fiscal_year"] == 2013
    assert t["doc_id"] == "542683_2013_Cryptzone_Bokslutskommunike_2012"


def test_nordic_targets_filter_by_year(tmp_path):
    from scripts.pdf_market_sweep import build_targets_nordic

    _touch(tmp_path, "2013/1/a.pdf")
    _touch(tmp_path, "2019/1/b.pdf")
    got = build_targets_nordic(tmp_path, years={"2019"})
    assert [t["fiscal_year"] for t in got] == [2019]


def test_nordic_skips_paths_without_a_year_first(tmp_path):
    from scripts.pdf_market_sweep import build_targets_nordic

    _touch(tmp_path, "notayear/1/a.pdf")
    _touch(tmp_path, "loose.pdf")
    assert build_targets_nordic(tmp_path, years=None) == []


def test_oslo_targets_read_year_month_and_id(tmp_path):
    """Oslo layout is {year}/{month}/{id}.pdf — the id is the FILE stem."""
    from scripts.pdf_market_sweep import build_targets_oslo

    _touch(tmp_path, "2022/03/557638.pdf")
    targets = build_targets_oslo(tmp_path, years=None)
    assert len(targets) == 1
    t = targets[0]
    assert t["ticker"] == "557638"
    assert t["fiscal_year"] == 2022
    assert t["filing_date"] == "2022-03"


def test_oslo_doc_ids_unique_across_months(tmp_path):
    from scripts.pdf_market_sweep import build_targets_oslo

    _touch(tmp_path, "2022/03/1.pdf")
    _touch(tmp_path, "2022/04/1.pdf")
    ids = [t["doc_id"] for t in build_targets_oslo(tmp_path, years=None)]
    assert len(ids) == len(set(ids)) == 2


def test_registry_routes_each_market_to_its_own_builder():
    """The in_bse builder finds 0 targets in Nordic/Oslo trees — a registration
    pointing them at it would silently sweep nothing."""
    from scripts.pdf_market_sweep import MARKETS

    assert MARKETS["no_oslo"]["builder"] == "oslo"
    assert MARKETS["nordic"]["builder"] == "nordic"
    assert MARKETS["in_bse"]["builder"] == "in_bse"
