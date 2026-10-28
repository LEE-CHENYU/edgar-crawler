"""Tests for DART number formats, specifically prefix negative syntax '(-)' and '( - )'.

Verifies task T6 fix:
- '(-)1,234' and '( - ) 1,234' -> -1234.0
- Preserves existing behavior for '(1,234)', '-1,234', '△1,234', '▲1,234', plain numbers, blanks, and '-' -> 0.0.
- End-to-end XML parsing with paren-minus values.
- Real fixture cut from filing 20140331001933.
- Full zip extraction from /Volumes/OWC Express 1M2/datasets/MARKET_FILINGS/raw/DART/20140331/20140331001933/20140331001933.zip when present.
"""

import base64
import gzip
from pathlib import Path

import pytest

from dart_raw_xml_v2 import _number, extract_raw_xml_row
from dart_raw_xml_v2_job import extract_zip_payload

# Exact cut of document header, balance sheet (tables 6-7), and income statement (tables 8-9)
# from 20140331001933_00761.xml (consolidated audit report).
_CUT_FIXTURE_B64 = (
    "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"
    "nMAAA=="
)

TARGET_20140331001933 = {
    "filing_id": "20140331001933",
    "corp_code": "00255105",
    "stock_code": "",
    "company_name": "에이치엘비네트웍스",
    "report_name": "사업보고서 (2013.12)",
    "period_end": "2013-12-31",
}


def test_dart_number_parser_paren_minus_formats():
    """Verify prefix negative paren syntax '(-)' and '( - )', plus preserved conventions."""
    # Prefix negative format reported in Korean financial tables (T6 target)
    assert _number("(-)1,234") == -1234.0
    assert _number("( - ) 1,234") == -1234.0
    assert _number("( - )1,234") == -1234.0
    assert _number("(-) 1,234") == -1234.0
    assert _number("(-)1,074,561,019") == -1074561019.0
    assert _number("(-)6,137,582,475") == -6137582475.0
    assert _number("( - ) 1,074,561,019") == -1074561019.0

    # Standard negative conventions
    assert _number("(1,234)") == -1234.0
    assert _number("(-1,234)") == -1234.0
    assert _number("-1,234") == -1234.0
    assert _number("−1,234") == -1234.0  # Unicode minus U+2212
    assert _number("△1,234") == -1234.0  # White triangle
    assert _number("▲1,234") == -1234.0  # Black triangle

    # Plain positive numbers and decimals
    assert _number("1,234") == 1234.0
    assert _number("1234") == 1234.0
    assert _number("1,234.56") == 1234.56
    assert _number("-1,234.56") == -1234.56
    assert _number("0") == 0.0
    assert _number("(0)") == 0.0
    assert _number("(-)0") == 0.0

    # Explicit nils preserved as 0.0 (current repo convention)
    assert _number("-") == 0.0
    assert _number("—") == 0.0
    assert _number("–") == 0.0

    # Blanks, None, non-numeric strings
    assert _number("") is None
    assert _number("   ") is None
    assert _number(None) is None
    assert _number("(*1)") is None
    assert _number("-)1234") is None


def test_dart_xml_end_to_end_with_paren_minus_income_statement():
    """Verify end-to-end XML extraction when income statement rows use '(-)' prefix."""
    xml_content = """<?xml version="1.0" encoding="utf-8"?>
    <DOCUMENT><DOCUMENT-NAME ACODE="00761">연결감사보고서</DOCUMENT-NAME>
    <COMPANY-NAME>테스트회사</COMPANY-NAME>
    <TABLE><TR><TD>연 결 재 무 상 태 표</TD></TR><TR><TD>(단위: 원)</TD></TR></TABLE>
    <TABLE BORDER="1"><THEAD><TR><TH>과목</TH><TH>주석</TH><TH>제 17(당) 기</TH><TH>제 16(전) 기</TH></TR></THEAD><TBODY>
      <TR><TD>유동자산</TD><TD></TD><TD ALIGN="RIGHT">10,000</TD><TD ALIGN="RIGHT">8,000</TD></TR>
      <TR><TD>비유동자산</TD><TD></TD><TD ALIGN="RIGHT">20,000</TD><TD ALIGN="RIGHT">18,000</TD></TR>
      <TR><TD>자산총계</TD><TD></TD><TD ALIGN="RIGHT">30,000</TD><TD ALIGN="RIGHT">26,000</TD></TR>
      <TR><TD>유동부채</TD><TD></TD><TD ALIGN="RIGHT">10,000</TD><TD ALIGN="RIGHT">8,000</TD></TR>
      <TR><TD>비유동부채</TD><TD></TD><TD ALIGN="RIGHT">5,000</TD><TD ALIGN="RIGHT">6,000</TD></TR>
      <TR><TD>부채총계</TD><TD></TD><TD ALIGN="RIGHT">15,000</TD><TD ALIGN="RIGHT">14,000</TD></TR>
      <TR><TD>자본총계</TD><TD></TD><TD ALIGN="RIGHT">15,000</TD><TD ALIGN="RIGHT">12,000</TD></TR>
    </TBODY></TABLE>
    <TABLE><TR><TD>연 결 포 괄 손 익 계 산 서</TD></TR><TR><TD>(단위: 원)</TD></TR></TABLE>
    <TABLE BORDER="1"><THEAD><TR><TH>과목</TH><TH>주석</TH><TH>제 17(당) 기</TH><TH>제 16(전) 기</TH></TR></THEAD><TBODY>
      <TR><TD>I. 매출액</TD><TD></TD><TD ALIGN="RIGHT">7,000</TD><TD ALIGN="RIGHT">10,000</TD></TR>
      <TR><TD>II. 매출원가</TD><TD></TD><TD ALIGN="RIGHT">8,000</TD><TD ALIGN="RIGHT">9,000</TD></TR>
      <TR><TD>III. 매출총손실</TD><TD></TD><TD ALIGN="RIGHT">(-)1,000</TD><TD ALIGN="RIGHT">1,000</TD></TR>
      <TR><TD>IV. 판매비와관리비</TD><TD></TD><TD ALIGN="RIGHT">5,000</TD><TD ALIGN="RIGHT">4,000</TD></TR>
      <TR><TD>V. 영업손실</TD><TD></TD><TD ALIGN="RIGHT">(-)6,000</TD><TD ALIGN="RIGHT">(-)3,000</TD></TR>
      <TR><TD>법인세비용</TD><TD></TD><TD ALIGN="RIGHT">-</TD><TD ALIGN="RIGHT">500</TD></TR>
      <TR><TD>당기순손실</TD><TD></TD><TD ALIGN="RIGHT">(-)9,000</TD><TD ALIGN="RIGHT">(-)3,500</TD></TR>
    </TBODY></TABLE>
    </DOCUMENT>""".encode("utf-8")

    target = {
        "filing_id": "99999999999999",
        "corp_code": "00000000",
        "stock_code": "",
        "company_name": "테스트회사",
        "report_name": "사업보고서 (2013.12)",
        "period_end": "2013-12-31",
    }
    row = extract_raw_xml_row(xml_content, target, scope="consolidated")
    assert row["revenue"] == 7000.0
    assert row["cost_of_revenue"] == 8000.0
    assert row["gross_profit"] == -1000.0
    assert row["operating_profit"] == -6000.0
    assert row["net_income"] == -9000.0
    assert row["balance_identity_status"] == "pass"
    assert row["operating_profit_closure_status"] == "pass"


def test_real_fixture_cut_from_20140331001933():
    """Verify that the real table cut from 20140331001933_00761.xml extracts correctly."""
    cut_payload = gzip.decompress(base64.b64decode(_CUT_FIXTURE_B64))
    row = extract_raw_xml_row(cut_payload, TARGET_20140331001933, scope="consolidated")

    assert row["revenue"] == 7_167_832_122.0
    assert row["cost_of_revenue"] == 8_242_393_141.0
    assert row["gross_profit"] == -1_074_561_019.0
    assert row["operating_profit"] == -6_137_582_475.0
    assert row["income_tax_expense"] == 0.0
    assert row["net_income"] == -9_381_352_908.0
    assert row["balance_identity_status"] == "pass"
    assert row["operating_profit_closure_status"] == "pass"
    assert row["operating_profit_closure_ratio"] == 0.0


def test_real_zip_20140331001933_consolidated_and_standalone():
    """Verify that the official zip archive extracts both scopes without error."""
    zip_path = Path("/Volumes/OWC Express 1M2/datasets/MARKET_FILINGS/raw/DART/20140331/20140331001933/20140331001933.zip")
    if not zip_path.exists():
        pytest.skip(f"Archive not mounted at {zip_path}")

    doc, rows = extract_zip_payload(zip_path.read_bytes(), TARGET_20140331001933)
    assert doc["scope_statuses"]["consolidated"] == "extracted"
    assert doc["scope_statuses"]["standalone"] == "extracted"
    assert doc["scope_errors"] == {}
    assert len(rows) == 2

    by_scope = {r["statement_scope"]: r for r in rows}
    c_row = by_scope["consolidated"]
    assert c_row["gross_profit"] == -1_074_561_019.0
    assert c_row["operating_profit"] == -6_137_582_475.0
    assert c_row["net_income"] == -9_381_352_908.0

    s_row = by_scope["standalone"]
    assert s_row["operating_profit"] == -5_584_539_566.0
    assert s_row["net_income"] == -8_455_941_819.0
