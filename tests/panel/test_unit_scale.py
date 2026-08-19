"""Attach the recovered unit scale to panel rows.

Stage 02 captured `unit_text` (the reporting CURRENCY) but never the
multiplier, so a figure's magnitude depended on a caption nobody recorded.
Tencent's total_assets appears as 56,804,365 (thousands) and 17,506 (millions),
both labelled RMB; Indian filings mix lakh (10^5) and crore (10^7), a 100x
spread WITHIN one market that correlates with company size.

Two rules this module exists to enforce:

  1. Values are never pre-multiplied. unit_scale is recorded for the consumer,
     exactly as panel/adapters/hk.py already promises.
  2. An unrecovered scale is NOT 1. Defaulting to 1 is what made the original
     defect invisible -- a figure in lakh silently read as units. Unknown stays
     unknown and is labelled as such.
"""

from panel.unit_scale import attach_unit_scale, load_scale_table


def _table():
    return {"d1": (1000.0, "anchored:10votes"), "d2": (10_000_000.0, "document-head")}


def test_a_known_scale_is_attached_with_its_provenance():
    rows = [{"source_doc_id": "d1", "total_assets": 5.0}]
    attach_unit_scale(rows, _table())
    assert rows[0]["unit_scale"] == 1000.0
    assert rows[0]["unit_scale_source"] == "detected:anchored"


def test_a_document_head_match_is_labelled_as_weaker_evidence():
    rows = [{"source_doc_id": "d2"}]
    attach_unit_scale(rows, _table())
    assert rows[0]["unit_scale_source"] == "detected:document-head"


def test_values_are_never_pre_multiplied():
    """The multiplier is metadata for the consumer, not an edit to the data."""
    rows = [{"source_doc_id": "d1", "total_assets": 5.0, "revenue": 7.0}]
    attach_unit_scale(rows, _table())
    assert rows[0]["total_assets"] == 5.0
    assert rows[0]["revenue"] == 7.0


def test_an_unrecovered_scale_is_unknown_not_one():
    """Defaulting to 1 is the original defect: a lakh figure read as units."""
    rows = [{"source_doc_id": "nope", "total_assets": 5.0}]
    attach_unit_scale(rows, _table())
    assert rows[0]["unit_scale"] is None
    assert rows[0]["unit_scale_source"] == "unknown"


def test_rows_without_a_doc_id_are_unknown_not_crashes():
    rows = [{"total_assets": 5.0}, {"source_doc_id": None}]
    attach_unit_scale(rows, _table())
    assert all(r["unit_scale_source"] == "unknown" for r in rows)


def test_attach_reports_how_many_matched():
    rows = [{"source_doc_id": "d1"}, {"source_doc_id": "nope"}]
    stats = attach_unit_scale(rows, _table())
    assert stats["matched"] == 1 and stats["unknown"] == 1


# --- loading the on-disk tables -----------------------------------------


def test_load_handles_the_hk_table_shape(tmp_path):
    import pandas as pd
    p = tmp_path / "hk.parquet"
    pd.DataFrame([{"filing_id": "1562416", "unit_scale": 1000.0,
                   "evidence": "anchored:10votes"}]).to_parquet(p, index=False)
    assert load_scale_table(p)["1562416"] == (1000.0, "anchored:10votes")


def test_load_handles_the_india_table_shape(tmp_path):
    import pandas as pd
    p = tmp_path / "in.parquet"
    pd.DataFrame([{"doc_stem": "5330890311", "unit_scale": 100000.0,
                   "evidence": "anchored:29votes"}]).to_parquet(p, index=False)
    assert load_scale_table(p)["5330890311"] == (100000.0, "anchored:29votes")


def test_load_drops_rows_with_no_recovered_scale(tmp_path):
    """A null scale must not enter the table as a match."""
    import pandas as pd
    p = tmp_path / "x.parquet"
    pd.DataFrame([{"doc_stem": "a", "unit_scale": None, "evidence": "none"},
                  {"doc_stem": "b", "unit_scale": 1000.0, "evidence": "anchored:2votes"}]
                 ).to_parquet(p, index=False)
    table = load_scale_table(p)
    assert "a" not in table and "b" in table


def test_load_a_missing_file_is_empty_not_an_error(tmp_path):
    assert load_scale_table(tmp_path / "absent.parquet") == {}


# --- key shapes that actually occur in the corpus ------------------------


def test_a_doc_id_prefixed_with_ticker_and_year_still_matches():
    """IN_BSE doc_ids come in two shapes: bare "5330890310" and prefixed
    "532955_2020_65588532955". The scale table is keyed on the PDF stem, so
    27,000 rows silently missed until the prefix was stripped."""
    rows = [{"source_doc_id": "532955_2020_65588532955",
             "local_id": "532955", "fiscal_year": 2020}]
    attach_unit_scale(rows, {"65588532955": (100000.0, "anchored:5votes")})
    assert rows[0]["unit_scale"] == 100000.0


def test_prefix_stripping_uses_the_rows_own_identity_not_a_split():
    """Stems contain underscores ("75040531225_23_09_22"), so splitting on "_"
    would mangle them. Only the row's own ticker_year prefix is removed."""
    rows = [{"source_doc_id": "531225_2022_75040531225_23_09_22",
             "local_id": "531225", "fiscal_year": 2022}]
    attach_unit_scale(rows, {"75040531225_23_09_22": (10_000_000.0, "anchored:9votes")})
    assert rows[0]["unit_scale"] == 10_000_000.0


def test_entity_period_fallback_matches_when_no_doc_id_does():
    rows = [{"source_doc_id": "unmatchable", "local_id": "500325",
             "fiscal_year": 2019}]
    attach_unit_scale(rows, {}, fallback={("500325", "2019"): (10_000_000.0, "anchored:4votes")})
    assert rows[0]["unit_scale"] == 10_000_000.0
    assert rows[0]["unit_scale_source"] == "detected:anchored"


def test_the_doc_id_match_wins_over_the_fallback():
    """A document-level match is more specific than a company-year one."""
    rows = [{"source_doc_id": "d1", "local_id": "X", "fiscal_year": 2020}]
    attach_unit_scale(rows, {"d1": (1000.0, "anchored:3votes")},
                      fallback={("X", "2020"): (10_000_000.0, "document-head")})
    assert rows[0]["unit_scale"] == 1000.0


def test_load_fallback_skips_company_years_with_conflicting_scales(tmp_path):
    """If one company-year has two documents claiming different scales, the
    company-year key cannot arbitrate -- it must not guess."""
    import pandas as pd
    from panel.unit_scale import load_scale_fallback
    p = tmp_path / "x.parquet"
    pd.DataFrame([
        {"entity": "A", "period_label": "2020", "doc_stem": "d1",
         "unit_scale": 1000.0, "evidence": "anchored:2votes"},
        {"entity": "A", "period_label": "2020", "doc_stem": "d2",
         "unit_scale": 10_000_000.0, "evidence": "anchored:2votes"},
        {"entity": "B", "period_label": "2020", "doc_stem": "d3",
         "unit_scale": 100000.0, "evidence": "anchored:2votes"},
    ]).to_parquet(p, index=False)
    fb = load_scale_fallback(p)
    assert ("A", "2020") not in fb
    assert fb[("B", "2020")][0] == 100000.0


# --- ASX: table keyed by filename, panel keyed by the bare document id ----


def test_a_trailing_document_id_segment_is_registered_as_an_alias(tmp_path):
    """ASX scale-table stems are "SM1_20190709_02121819" while the panel's
    source_doc_id is "02121819" -- zero direct overlap across 27,665 rows."""
    import pandas as pd
    p = tmp_path / "asx.parquet"
    pd.DataFrame([{"doc_stem": "SM1_20190709_02121819", "unit_scale": 1000.0,
                   "evidence": "anchored:4votes"}]).to_parquet(p, index=False)
    table = load_scale_table(p)
    assert table["02121819"] == (1000.0, "anchored:4votes")
    assert table["SM1_20190709_02121819"] == (1000.0, "anchored:4votes")


def test_an_ambiguous_alias_is_not_registered(tmp_path):
    """Two filings whose trailing segment collides cannot be told apart, so
    neither may claim the alias -- guessing would attach one filing's scale to
    another's figures."""
    import pandas as pd
    p = tmp_path / "asx.parquet"
    pd.DataFrame([
        {"doc_stem": "AAA_20190709_999", "unit_scale": 1000.0, "evidence": "anchored:2votes"},
        {"doc_stem": "BBB_20200101_999", "unit_scale": 1_000_000.0, "evidence": "anchored:2votes"},
    ]).to_parquet(p, index=False)
    table = load_scale_table(p)
    assert "999" not in table
    assert table["AAA_20190709_999"][0] == 1000.0


def test_an_alias_never_overwrites_a_real_key(tmp_path):
    import pandas as pd
    p = tmp_path / "x.parquet"
    pd.DataFrame([
        {"doc_stem": "123", "unit_scale": 1000.0, "evidence": "anchored:2votes"},
        {"doc_stem": "XYZ_20200101_123", "unit_scale": 1_000_000.0, "evidence": "anchored:2votes"},
    ]).to_parquet(p, index=False)
    assert load_scale_table(p)["123"][0] == 1000.0
