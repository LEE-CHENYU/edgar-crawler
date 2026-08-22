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


# --- TWSE ---------------------------------------------------------------


def _twse_tree(tmp_path):
    """{filing_date}/{filing_id}/{fiscal}_{ticker}_{date}F04.pdf -- a fourth
    layout, distinct from in_bse, asx, oslo and nordic."""
    d = tmp_path / "20220602" / "TWSE_REPORT_F_1717_2022_2021_1717_20220623F04"
    d.mkdir(parents=True)
    (d / "2021_1717_20220623F04.pdf").write_bytes(b"%PDF-1.4")
    d2 = tmp_path / "20180521" / "TWSE_REPORT_F_2916_2018_2017_2916_20180608F04"
    d2.mkdir(parents=True)
    (d2 / "2017_2916_20180608F04.pdf").write_bytes(b"%PDF-1.4")
    return tmp_path


def test_twse_targets_read_ticker_and_fiscal_year_from_the_filename(tmp_path):
    from scripts.pdf_market_sweep import build_targets_twse
    targets = build_targets_twse(_twse_tree(tmp_path), None)
    by_ticker = {t["ticker"]: t for t in targets}
    assert set(by_ticker) == {"1717", "2916"}
    assert by_ticker["1717"]["fiscal_year"] == 2021
    assert by_ticker["2916"]["fiscal_year"] == 2017


def test_twse_doc_ids_are_unique(tmp_path):
    from scripts.pdf_market_sweep import build_targets_twse
    targets = build_targets_twse(_twse_tree(tmp_path), None)
    assert len({t["doc_id"] for t in targets}) == len(targets)


def test_twse_targets_filter_by_year(tmp_path):
    from scripts.pdf_market_sweep import build_targets_twse
    targets = build_targets_twse(_twse_tree(tmp_path), {"2017"})
    assert [t["ticker"] for t in targets] == ["2916"]


def test_twse_is_registered_against_its_own_builder():
    """The Oslo incident: a market pointed at another market's builder finds
    zero targets and reports success having swept nothing."""
    from scripts.pdf_market_sweep import MARKETS
    assert MARKETS["twse"]["builder"] == "twse"
    assert MARKETS["twse"]["out_dir"] != MARKETS["in_bse"]["out_dir"]


def test_every_market_has_a_distinct_builder_and_out_dir():
    from scripts.pdf_market_sweep import MARKETS
    builders = [m["builder"] for m in MARKETS.values()]
    assert len(builders) == len(set(builders))


# --- Nordic: country and currency must survive the sweep ----------------


def test_nordic_targets_carry_the_country_from_the_raw_root(tmp_path):
    """Six countries were swept into ONE output directory and the country became
    unrecoverable -- neither the parquet nor sweep_results.jsonl kept the path.
    The country is in the raw root, so capture it at target-build time."""
    from scripts.pdf_market_sweep import build_targets_nordic
    root = tmp_path / "markets" / "dk" / "01_raw" / "nasdaq_nordic_baltic"
    (root / "2019" / "1234").mkdir(parents=True)
    (root / "2019" / "1234" / "rap.pdf").write_bytes(b"%PDF-1.4")
    targets = build_targets_nordic(root, None)
    assert targets[0]["country"] == "dk"


def test_nordic_country_is_none_when_the_path_does_not_name_one(tmp_path):
    """Better an explicit None than a wrong country silently attached."""
    from scripts.pdf_market_sweep import build_targets_nordic
    root = tmp_path / "somewhere" / "else"
    (root / "2019" / "1234").mkdir(parents=True)
    (root / "2019" / "1234" / "rap.pdf").write_bytes(b"%PDF-1.4")
    assert build_targets_nordic(root, None)[0]["country"] is None


def test_consolidate_keeps_country_and_currency(tmp_path):
    """The sweep records country/currency/currency_source per row, but
    consolidate() kept only 5 id columns and silently dropped them -- so the
    parquet lost the provenance the sweep was rewritten to capture. The data
    survived in sweep_results.jsonl, which is the only reason a re-sweep was
    not needed."""
    import json
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location("sw", "scripts/pdf_market_sweep.py")
    sw = importlib.util.module_from_spec(spec); spec.loader.exec_module(spec and sw)
    import asx_financials_extract as ax

    results = tmp_path / "sweep_results.jsonl"
    results.write_text(json.dumps({
        "ticker": "ABC", "filing_date": "2020", "fiscal_year": 2020,
        "doc_id": "d1", "n_metrics": 1, "country": "se", "currency": "EUR",
        "currency_source": "declared", "total_assets": 5.0,
    }) + "\n")
    out = tmp_path / "wide.parquet"
    sw.consolidate(results, out, ax)

    import pandas as pd
    df = pd.read_parquet(out)
    assert df["country"].iloc[0] == "se"
    assert df["currency"].iloc[0] == "EUR"
    assert df["currency_source"].iloc[0] == "declared"


def test_asx_builder_matches_the_real_underscore_filenames(tmp_path):
    """The regex expected TICKER-YYYYMMDD-docid.pdf with HYPHENS; every one of
    the 28,202 files on disk uses UNDERSCORES (GNC_20080122_00804731.pdf). The
    builder returned 0 targets while the sweep reported "consolidated 27,665
    rows" -- success on zero work, the §2.1 defect in a new form. ASX's existing
    stage-02 rows came from the legacy asx_financials_sweep.py, so this path was
    never exercised until the supervisor ran it."""
    from scripts.pdf_market_sweep import build_targets_asx
    d = tmp_path / "20080122" / "GNC"
    d.mkdir(parents=True)
    (d / "GNC_20080122_00804731.pdf").write_bytes(b"%PDF-1.4")
    targets = build_targets_asx(tmp_path, None)
    assert len(targets) == 1, "underscore filenames must match"
    assert targets[0]["ticker"] == "GNC"
    # January filing -> FY2007: Australian fiscal years end 30 June, so a filing
    # before July reports the PRIOR year. My first assertion said 2008; the
    # builder was right and the test was wrong.
    assert targets[0]["fiscal_year"] == 2007


def test_asx_builder_still_accepts_hyphen_filenames(tmp_path):
    """Older exports used hyphens; both shapes must work."""
    from scripts.pdf_market_sweep import build_targets_asx
    d = tmp_path / "20080122" / "GNC"
    d.mkdir(parents=True)
    (d / "GNC-20080122-00804731.pdf").write_bytes(b"%PDF-1.4")
    assert len(build_targets_asx(tmp_path, None)) == 1
