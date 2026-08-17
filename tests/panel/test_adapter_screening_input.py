from panel.adapters.screening_input import (
    SCREENING_COLUMN_MAP, rows_from_screening_csv,
)

HEADER = ("Ticker,Symbol,Company Name,Exchange,Sector,Industry Group,Stock Style,"
          "Currency,bsns_year,Total Assets,Total Liabilities,Total Equity,"
          "Current Assets,Current Liabilities,Cash (Balance Sheet),Revenue,"
          "Operating Profit,Net Income\n")

def _write(tmp_path, body):
    p = tmp_path / "s.csv"
    p.write_text(HEADER + body)
    return p

def test_column_map_targets_canonical_metric_names():
    assert SCREENING_COLUMN_MAP["Total Assets"] == "total_assets"
    assert SCREENING_COLUMN_MAP["Cash (Balance Sheet)"] == "cash_and_equivalents"
    assert SCREENING_COLUMN_MAP["Revenue"] == "revenue"
    assert SCREENING_COLUMN_MAP["Net Income"] == "net_income"

def test_rows_carry_identity_and_metrics(tmp_path):
    p = _write(tmp_path, "BHP,BHP,BHP GROUP,ASX,Materials,,,USD,2024,100,40,60,50,20,10,200,30,25\n")
    rows = rows_from_screening_csv(p, market="au")
    assert len(rows) == 1
    r = rows[0]
    assert r["market"] == "au" and r["local_id"] == "BHP"
    assert r["company_name"] == "BHP GROUP"
    assert r["currency"] == "USD"
    assert r["fiscal_year"] == 2024
    assert r["period_end"] == "2024-12-31"
    assert r["period_type"] == "A"
    assert r["total_assets"] == 100.0
    assert r["cash_and_equivalents"] == 10.0
    assert r["net_income"] == 25.0

def test_implausible_fiscal_year_is_dropped(tmp_path):
    """PSE emitted bsns_year=430 on 2 rows."""
    p = _write(tmp_path, "FS,FS,X,PSE,,,,PHP,430,1,1,1,1,1,1,1,1,1\n")
    assert rows_from_screening_csv(p, market="ph") == []

def test_row_without_ticker_is_dropped(tmp_path):
    p = _write(tmp_path, ",,,PSE,,,,PHP,2024,1,1,1,1,1,1,1,1,1\n")
    assert rows_from_screening_csv(p, market="ph") == []

def test_blank_metric_becomes_none_not_zero(tmp_path):
    p = _write(tmp_path, "AAA,AAA,X,ASX,,,,AUD,2024,,40,60,50,20,10,200,30,25\n")
    assert rows_from_screening_csv(p, market="au")[0]["total_assets"] is None

def test_unparseable_metric_becomes_none(tmp_path):
    p = _write(tmp_path, "AAA,AAA,X,ASX,,,,AUD,2024,n/a,40,60,50,20,10,200,30,25\n")
    assert rows_from_screening_csv(p, market="au")[0]["total_assets"] is None

def test_source_artifact_records_provenance(tmp_path):
    p = _write(tmp_path, "AAA,AAA,X,ASX,,,,AUD,2024,1,1,1,1,1,1,1,1,1\n")
    assert str(p) in rows_from_screening_csv(p, market="au")[0]["source_artifact"]


# --- FIX 4: drops are counted, never silent ---

def test_screening_drop_reasons_are_counted(tmp_path):
    from collections import Counter

    path = tmp_path / "x_screening_input.csv"
    path.write_text(
        "Ticker,bsns_year,Revenue\n"
        ",2024,1\n"
        "OK,430,2\n"
        "OK,2024,3\n"
    )
    drops = Counter()
    rows = rows_from_screening_csv(path, market="au", drops=drops)
    assert len(rows) == 1
    assert drops["missing_ticker"] == 1
    assert drops["implausible_fiscal_year"] == 1
