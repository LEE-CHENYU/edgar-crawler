import pandas as pd
from panel.build import MARKET_SOURCES, attach_reporting_basis, attach_spine, write_panel


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


# --- reporting_basis (Fix round 1: 308,321 CN duplicate-key groups were the
# consolidated-vs-parent Typrep A/B pair for the same company-period; 2 JP
# duplicates turned out to be the same pattern via has_consolidated_statements,
# not an amendment) ---

def test_attach_reporting_basis_maps_cn_typrep_a_to_consolidated():
    rows = [{"market": "cn", "cn_typrep": "A"}]
    assert attach_reporting_basis(rows)[0]["reporting_basis"] == "consolidated"


def test_attach_reporting_basis_maps_cn_typrep_b_to_parent():
    rows = [{"market": "cn", "cn_typrep": "B"}]
    assert attach_reporting_basis(rows)[0]["reporting_basis"] == "parent"


def test_attach_reporting_basis_keeps_unknown_cn_typrep_raw_so_nothing_is_lost():
    rows = [{"market": "cn", "cn_typrep": "C"}]
    assert attach_reporting_basis(rows)[0]["reporting_basis"] == "C"


def test_attach_reporting_basis_does_not_drop_cn_typrep():
    rows = [{"market": "cn", "cn_typrep": "A"}]
    assert attach_reporting_basis(rows)[0]["cn_typrep"] == "A"


def test_attach_reporting_basis_maps_jp_consolidated_flag():
    rows = [
        {"market": "jp", "jp_has_consolidated_statements": True},
        {"market": "jp", "jp_has_consolidated_statements": False},
    ]
    out = attach_reporting_basis(rows)
    assert out[0]["reporting_basis"] == "consolidated"
    assert out[1]["reporting_basis"] == "parent"


def test_attach_reporting_basis_defaults_single_basis_markets_to_consolidated():
    rows = [{"market": "au", "local_id": "BHP"}]
    assert attach_reporting_basis(rows)[0]["reporting_basis"] == "consolidated"
