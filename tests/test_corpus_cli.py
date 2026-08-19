

def test_stage02_finds_a_nested_parquet(tmp_path, monkeypatch):
    """DART's stage-02 output is in three_statement_parquet/, not facts/, so the
    CLI reported 1,045 rows against a real 9,193 -- understating a finished
    market as barely started."""
    import scripts.corpus as corpus
    d = tmp_path / "DART_FINANCIALS" / "three_statement_parquet"
    d.mkdir(parents=True)
    import pandas as pd
    pd.DataFrame({"ticker": ["A"] * 7}).to_parquet(d / "canonical_metrics_wide.parquet")
    monkeypatch.setattr(corpus, "DERIVED", tmp_path)
    state = corpus.stage02_state("DART_FINANCIALS")
    assert state["present"] is True
    assert state["rows"] == 7


def test_stage02_reports_a_market_living_outside_derived(tmp_path, monkeypatch):
    """HK's stage 02 is under markets/hk/02_structured; reporting 'none' for it
    reads as a missing stage rather than a different path."""
    import scripts.corpus as corpus
    monkeypatch.setattr(corpus, "DERIVED", tmp_path)
    state = corpus.stage02_state(None)
    assert state["present"] is False
    assert "no stage-02 path known" in state["note"]
