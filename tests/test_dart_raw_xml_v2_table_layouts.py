"""Tests for Korean DART statement table layouts with ordinal period headers."""
from pathlib import Path
from bs4 import BeautifulSoup

from dart_raw_xml_v2 import extract_raw_xml_row, _current_column, _table_records, _metric_for_income, _metric_for_balance

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "dart_table_layouts"


def test_spanned_ordinal_terms_layout_extracts_correct_column_and_row():
    """Filing 20180330001947: Header uses '제 18 기' spanning 2 columns, '제 17 기' spanning 2 columns."""
    fixture_path = FIXTURES_DIR / "20180330001947_spanned_terms.xml"
    payload = fixture_path.read_bytes()
    target = {
        "filing_id": "20180330001947",
        "corp_code": "00787162",
        "stock_code": "106080",
        "company_name": "하이소닉",
        "filing_date": "2018-03-30",
        "report_nm": "사업보고서 (2017.12)",
    }

    # Verify column span detection directly on the tables
    soup = BeautifulSoup(payload, "html.parser")
    tables = soup.find_all("table")
    # Table 1 is balance, Table 3 is income
    bal_table = tables[1]
    inc_table = tables[3]

    assert _current_column(bal_table) == (1, 3)
    assert _current_column(inc_table) == (1, 3)

    row = extract_raw_xml_row(payload, target, scope="standalone")
    assert row["ticker"] == "106080"
    assert row["revenue"] == 150_596_909_219.0
    assert row["operating_profit"] == 6_647_435_976.0
    assert row["net_income"] == 6_647_008_688.0
    assert row["total_assets"] == 134_251_673_996.0
    assert row["total_liabilities"] == 90_314_239_481.0
    assert row["total_equity"] == 43_937_434_515.0
    assert row["balance_identity_status"] == "pass"


def test_single_column_ordinal_terms_layout_extracts_correctly():
    """Filing 20130329001921: Header uses '구분', '제26기', '제25기'."""
    fixture_path = FIXTURES_DIR / "20130329001921_single_terms.xml"
    payload = fixture_path.read_bytes()
    target = {
        "filing_id": "20130329001921",
        "corp_code": "00163346",
        "stock_code": "023590",
        "company_name": "다우기술",
        "filing_date": "2013-03-29",
        "report_nm": "사업보고서 (2012.12)",
    }

    soup = BeautifulSoup(payload, "html.parser")
    tables = soup.find_all("table")
    bal_table = tables[1]
    inc_table = tables[3]

    assert _current_column(bal_table) == (1, 3)
    assert _current_column(inc_table) == (1, 2)

    row = extract_raw_xml_row(payload, target, scope="standalone")
    assert row["ticker"] == "023590"
    assert row["revenue"] == 309_642_334_452.0
    assert row["operating_profit"] == 27_671_468_356.0
    assert row["net_income"] == 20_933_343_305.0
    assert row["total_assets"] == 219_424_831_559.0
    assert row["total_liabilities"] == 150_832_935_088.0
    assert row["total_equity"] == 68_591_896_471.0
    assert row["balance_identity_status"] == "pass"


def test_note_column_ordinal_terms_layout_extracts_correctly():
    """Filing 20140331002494: Header uses '과 목', '주석', '제34기', '제33기'."""
    fixture_path = FIXTURES_DIR / "20140331002494_note_col_terms.xml"
    payload = fixture_path.read_bytes()
    target = {
        "filing_id": "20140331002494",
        "corp_code": "00142778",
        "stock_code": "069620",
        "company_name": "대웅제약",
        "filing_date": "2014-03-31",
        "report_nm": "사업보고서 (2013.12)",
    }

    soup = BeautifulSoup(payload, "html.parser")
    tables = soup.find_all("table")
    bal_table = tables[1]
    inc_table = tables[3]

    assert _current_column(bal_table) == (2, 4)
    assert _current_column(inc_table) == (2, 4)

    row = extract_raw_xml_row(payload, target, scope="consolidated")
    assert row["ticker"] == "069620"
    assert row["revenue"] == 681_887_317_248.0
    assert row["operating_profit"] == 48_573_720_298.0
    assert row["net_income"] == 35_924_287_817.0
    assert row["total_assets"] == 880_892_350_093.0
    assert row["total_liabilities"] == 386_580_419_942.0
    assert row["total_equity"] == 494_311_930_151.0
    assert row["balance_identity_status"] == "pass"
