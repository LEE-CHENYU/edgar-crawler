"""Two ways a defect crept back in after being fixed upstream.

1. write_panel applied PANEL_COLUMN_DEFAULTS with fillna, turning the
   deliberate unit_scale=None ("we could not recover this filing's scale") into
   1. The 2026-08-18 panel shipped 10,446 Indian and 3,389 HK rows claiming
   scale 1 -- i.e. lakh figures presented as units, the exact error the recovery
   was built to remove. panel/unit_scale.py's own tests passed throughout,
   because the damage happened two layers downstream.

2. HK period_end is fabricated as {fiscal_year}-12-31 -- the HK facts carry only
   filing_date and fiscal_year, no period end -- yet the panel labelled it
   "reported". Filing months say December is wrong for ~20% of the market:
   HKEX allows 4 months, and 424 July + 151 June filings imply 31 March year
   ends, misdated by a quarter.
"""

import pandas as pd

from panel.schema import PANEL_COLUMN_DEFAULTS


def test_unit_scale_is_not_in_the_defaults():
    """An unrecoverable scale must stay null all the way to the parquet."""
    assert "unit_scale" not in PANEL_COLUMN_DEFAULTS


def test_write_panel_keeps_unknown_scale_null(tmp_path):
    from panel.build import write_panel
    rows = [{"market": "in_bse", "local_id": "X", "period_end": "2024-03-31",
             "unit_scale": None, "unit_scale_source": "unknown"},
            {"market": "in_bse", "local_id": "Y", "period_end": "2024-03-31",
             "unit_scale": 100000.0, "unit_scale_source": "detected:anchored"}]
    path = write_panel(rows, tmp_path)
    df = pd.read_parquet(path)
    assert df.unit_scale.isna().sum() == 1
    assert set(df.unit_scale.dropna()) == {100000.0}


def test_hk_period_end_is_labelled_assumed_not_reported():
    """HK carries no period end; calling the fabricated date "reported" is the
    claim that hid this for months."""
    from panel.adapters.hk import rows_from_hk_facts
    recs = [{"stock_code": "00700", "filing_id": "f1", "fiscal_year": "2023",
             "metric": "total_assets", "value": "100", "unit_text": "RMB",
             "filing_date": "2024-04-02"}]
    rows = rows_from_hk_facts(recs)
    assert rows[0]["period_end"] == "2023-12-31"
    assert rows[0]["period_end_source"] == "assumed:calendar-year-end"
