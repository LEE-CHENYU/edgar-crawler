import pandas as pd
from panel.build import MARKET_SOURCES, attach_spine, write_panel


def test_market_sources_cover_v1_adapters_and_exclude_us():
    assert set(MARKET_SOURCES) == {"au", "tw", "ph", "kr", "cn", "in_bse", "hk", "jp"}
    assert "us" not in MARKET_SOURCES


def test_attach_spine_sets_spine_key_from_resolution():
    rows = [{"market": "au", "local_id": "BHP", "period_end": "2024-12-31"}]
    resolved = {("au", "BHP"): {"spine_key": "BBG000D0D358", "figi": "BBG000D0D358",
                                "resolution_source": "openfigi", "name": "BHP GROUP LTD"}}
    out = attach_spine(rows, resolved)
    assert out[0]["spine_key"] == "BBG000D0D358"
    assert out[0]["figi"] == "BBG000D0D358"


def test_attach_spine_keeps_rows_whose_identifier_is_unresolved():
    """Dropping unresolved identifiers would silently shrink the panel."""
    rows = [{"market": "au", "local_id": "OBSCURE", "period_end": "2024-12-31"}]
    out = attach_spine(rows, {})
    assert len(out) == 1
    assert out[0]["spine_key"] == "AU:OBSCURE"
    assert out[0]["resolution_source"] == "unresolved"


def test_attach_spine_prefers_adapter_company_name_over_figi_name():
    rows = [{"market": "au", "local_id": "BHP", "company_name": "BHP GROUP"}]
    resolved = {("au", "BHP"): {"spine_key": "B1", "name": "BHP GROUP LTD",
                                "resolution_source": "openfigi"}}
    assert attach_spine(rows, resolved)[0]["company_name"] == "BHP GROUP"


def test_write_panel_creates_parquet_with_all_schema_columns(tmp_path):
    rows = [{"spine_key": "B1", "market": "au", "local_id": "BHP",
             "period_end": "2024-12-31", "period_type": "A", "fiscal_year": 2024,
             "currency": "AUD", "revenue": 1.0}]
    path = write_panel(rows, tmp_path)
    df = pd.read_parquet(path)
    assert len(df) == 1
    for col in ("spine_key", "period_type", "total_assets", "fx_rate"):
        assert col in df.columns


def test_write_panel_is_atomic_leaving_no_tmp_file(tmp_path):
    rows = [{"spine_key": "B1", "market": "au", "local_id": "X",
             "period_end": "2024-12-31", "period_type": "A", "fiscal_year": 2024}]
    write_panel(rows, tmp_path)
    assert list(tmp_path.rglob("*.tmp")) == []


def test_write_panel_on_empty_rows_returns_none(tmp_path):
    assert write_panel([], tmp_path) is None
