"""Tests for DART income statement revenue component recovery.

When no revenue total row maps, and component rows matching an explicit component
pattern (numbered/prefixed 제품|상품|용역|공사|기타 + 매출(액)) appear contiguously
directly before the cost-of-sales row in the selected income table, set:
  revenue = sum(components)
ONLY IF:
  sum - |cost_of_revenue| == gross_profit within max(1, 1e-6 * sum).

Record provenance listing every component label and flag revenue_from_components=True.
Never use components when a total row exists.
"""

from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from dart_raw_xml_v2 import (
    _is_revenue_component,
    extract_raw_xml_row,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "dart_revenue_components"

TARGET_INTOPS = {
    "filing_id": "20100331000349",
    "corp_code": "00259837",
    "stock_code": "049070",
    "company_name": "인탑스",
    "filing_date": "2010-03-31",
    "report_nm": "사업보고서 (2009.12)",
}

TARGET_SUNGWOO = {
    "filing_id": "20100331001278",
    "corp_code": "00216717",
    "stock_code": "015750",
    "company_name": "성우하이텍",
    "filing_date": "2010-03-31",
    "report_nm": "사업보고서 (2009.12)",
}


def test_component_regex_patterns():
    """Verify that _is_revenue_component matches component rows and rejects totals/costs/GP."""
    valid_components = [
        "1. 제품매출액",
        "2. 상품매출액",
        "3. 용역매출액",
        "1상품매출",
        "4공사매출액",
        "제품매출액",
        "상품매출액",
        "기타매출액",
        "1. 제품매출",
        "1. 상 품 매 출",
        "(1) 제품매출액",
        "가. 공사매출액",
        "기타매출",
        "1.제품매출액(주석 18)",
        "3. 용역매출액<주석 2>",
    ]
    for label in valid_components:
        assert _is_revenue_component(label), f"Expected '{label}' to match component pattern"

    invalid_components = [
        "매출액",
        "Ⅰ. 매출액",
        "수익(매출액)",
        "매출원가",
        "1. 제품매출원가",
        "상품매출원가",
        "제품매출총이익",
        "영업이익",
        "기타수익",
        "금융수익",
        "당기순이익",
    ]
    for label in invalid_components:
        assert not _is_revenue_component(label), f"Expected '{label}' NOT to match component pattern"


def test_real_fixture_20100331000349_components_recovered():
    """Real filing 20100331000349 has unmapped total 'l. 매출액(주석23)' (lowercase 'l').

    Directly before 'll. 매출원가(주석23)' are contiguous component rows:
    - 제품매출액: 203,596,435,041
    - 상품매출액: 26,906,147,091
    - 기타매출액: 5,976,627,847
    Sum = 236,479,209,979
    Cost of sales = 203,538,855,715
    Gross profit = 32,940,354,264
    Identity 236,479,209,979 - 203,538,855,715 == 32,940,354,264 holds.
    """
    payload = (FIXTURES_DIR / "20100331000349_components.xml").read_bytes()
    row = extract_raw_xml_row(payload, TARGET_INTOPS, scope="standalone")

    assert row["revenue"] == 236_479_209_979.0
    assert row["revenue_from_components"] is True
    assert row["cost_of_revenue"] == 203_538_855_715.0
    assert row["gross_profit"] == 32_940_354_264.0
    assert row["operating_profit"] == 18_379_415_238.0

    # Provenance check
    prov = row["field_provenance"]["revenue"]
    assert len(prov) == 3
    assert [p["label"] for p in prov] == ["제품매출액", "상품매출액", "기타매출액"]
    for p in prov:
        assert p["revenue_from_components"] is True
        assert p["source_unit_scale"] == 1.0


def test_numbered_components_recovered_when_no_total():
    """Numbered components '1. 제품매출액', '2. 상품매출액', '3. 용역매출액', '4. 공사매출액'

    when total row is absent.
    """
    payload = (FIXTURES_DIR / "20100331001278_no_total.xml").read_bytes()
    row = extract_raw_xml_row(payload, TARGET_SUNGWOO, scope="standalone")

    expected_sum = (
        204_546_352_307.0 + 27_820_536_729.0 + 622_203_523.0 + 325_500_590.0
    )
    assert expected_sum == 233_314_593_149.0
    assert row["revenue"] == 233_314_593_149.0
    assert row["revenue_from_components"] is True
    assert row["cost_of_revenue"] == 181_450_138_972.0
    assert row["gross_profit"] == 51_864_454_177.0

    prov = row["field_provenance"]["revenue"]
    assert len(prov) == 4
    assert [p["label"] for p in prov] == [
        "1. 제품매출액",
        "2. 상품매출액",
        "3. 용역매출액",
        "4. 공사매출액",
    ]
    for p in prov:
        assert p["revenue_from_components"] is True


def test_total_row_exists_takes_precedence_over_components():
    """When a total row 'Ⅰ. 매출액(주석20)' exists, components are never used."""
    payload = (FIXTURES_DIR / "20100331001278_with_total.xml").read_bytes()
    row = extract_raw_xml_row(payload, TARGET_SUNGWOO, scope="standalone")

    assert row["revenue"] == 233_314_593_149.0
    assert row.get("revenue_from_components") is False
    prov = row["field_provenance"]["revenue"]
    assert len(prov) == 1
    assert prov[0]["label"] == "Ⅰ. 매출액(주석20)"
    assert not prov[0].get("revenue_from_components", False)


def test_identity_mismatch_leaves_revenue_none():
    """If sum(components) - |cost_of_revenue| != gross_profit, revenue must be None."""
    payload = (FIXTURES_DIR / "identity_mismatch.xml").read_bytes()
    row = extract_raw_xml_row(payload, TARGET_SUNGWOO, scope="standalone")

    assert row.get("revenue") is None
    assert row.get("revenue_from_components") is False
    assert "revenue" not in row["field_provenance"]


def test_missing_cogs_leaves_revenue_none():
    """If cost_of_revenue is absent, components cannot be validated, revenue is None."""
    payload = (FIXTURES_DIR / "missing_cogs.xml").read_bytes()
    row = extract_raw_xml_row(payload, TARGET_SUNGWOO, scope="standalone")

    assert row.get("revenue") is None
    assert row.get("revenue_from_components") is False


def test_missing_gp_leaves_revenue_none():
    """If gross_profit is absent, components cannot be validated, revenue is None."""
    payload = (FIXTURES_DIR / "missing_gp.xml").read_bytes()
    row = extract_raw_xml_row(payload, TARGET_SUNGWOO, scope="standalone")

    assert row.get("revenue") is None
    assert row.get("revenue_from_components") is False


def test_non_contiguous_component_rows_leaves_revenue_none():
    """If component rows are not contiguously directly before COGS, identity fails/revenue is None."""
    payload = (FIXTURES_DIR / "non_contiguous.xml").read_bytes()
    row = extract_raw_xml_row(payload, TARGET_SUNGWOO, scope="standalone")

    assert row.get("revenue") is None
    assert row.get("revenue_from_components") is False
