"""Tests for deterministic, fail-safe payload encoding detection in DART raw-XML extraction."""
import codecs
from typing import Any

import pytest

from dart_raw_xml_v2 import _decode_payload, extract_raw_xml_row


XML_BODY_TEMPLATE = """{header}
<DOCUMENT><DOCUMENT-NAME ACODE="00761">연결감사보고서</DOCUMENT-NAME>
<COMPANY-NAME>테스트회사</COMPANY-NAME>
<TABLE><TR><TD>연 결 재 무 상 태 표</TD></TR><TR><TD>(단위: 천원)</TD></TR></TABLE>
<TABLE BORDER="1"><THEAD><TR><TH>과목</TH><TH>주석</TH><TH>제 10(당) 기</TH><TH>제 9(전) 기</TH></TR></THEAD><TBODY>
  <TR><TD>유동자산</TD><TD></TD><TD ALIGN="RIGHT">60</TD><TD ALIGN="RIGHT">55</TD></TR>
  <TR><TD>비유동자산</TD><TD></TD><TD ALIGN="RIGHT">40</TD><TD ALIGN="RIGHT">35</TD></TR>
  <TR><TD>자산총계</TD><TD></TD><TD ALIGN="RIGHT">100</TD><TD ALIGN="RIGHT">90</TD></TR>
  <TR><TD>유동부채</TD><TD></TD><TD ALIGN="RIGHT">25</TD><TD ALIGN="RIGHT">20</TD></TR>
  <TR><TD>비유동부채</TD><TD></TD><TD ALIGN="RIGHT">15</TD><TD ALIGN="RIGHT">15</TD></TR>
  <TR><TD>부채총계</TD><TD></TD><TD ALIGN="RIGHT">40</TD><TD ALIGN="RIGHT">35</TD></TR>
  <TR><TD>자본총계</TD><TD></TD><TD ALIGN="RIGHT">60</TD><TD ALIGN="RIGHT">55</TD></TR>
</TBODY></TABLE>
<TABLE><TR><TD>연 결 포 괄 손 익 계 산 서</TD></TR><TR><TD>(단위: 천원)</TD></TR></TABLE>
<TABLE BORDER="1"><THEAD><TR><TH>과목</TH><TH>주석</TH><TH>제 10(당) 기</TH><TH>제 9(전) 기</TH></TR></THEAD><TBODY>
  <TR><TD>매출액</TD><TD>4</TD><TD ALIGN="RIGHT">200</TD><TD ALIGN="RIGHT">180</TD></TR>
  <TR><TD>매출원가</TD><TD>5</TD><TD ALIGN="RIGHT">120</TD><TD ALIGN="RIGHT">110</TD></TR>
  <TR><TD>매출총이익</TD><TD></TD><TD ALIGN="RIGHT">80</TD><TD ALIGN="RIGHT">70</TD></TR>
  <TR><TD>판매비와관리비</TD><TD>6</TD><TD ALIGN="RIGHT">30</TD><TD ALIGN="RIGHT">25</TD></TR>
  <TR><TD>영업이익</TD><TD></TD><TD ALIGN="RIGHT">50</TD><TD ALIGN="RIGHT">45</TD></TR>
  <TR><TD>법인세비용차감전순이익</TD><TD></TD><TD ALIGN="RIGHT">40</TD><TD ALIGN="RIGHT">35</TD></TR>
  <TR><TD>법인세비용</TD><TD>7</TD><TD ALIGN="RIGHT">10</TD><TD ALIGN="RIGHT">8</TD></TR>
  <TR><TD>당기순이익</TD><TD></TD><TD ALIGN="RIGHT">30</TD><TD ALIGN="RIGHT">27</TD></TR>
</TBODY></TABLE>
{extra}
</DOCUMENT>"""

TARGET: dict[str, Any] = {
    "filing_id": "20250311000487",
    "corp_code": "01035942",
    "stock_code": "233250",
    "company_name": "테스트회사",
    "filing_date": "2025-03-11",
    "report_nm": "사업보고서 (2024.12)",
}


# --- Unit tests for _decode_payload helper ---


def test_decode_payload_bom_utf8():
    text = "<?xml version='1.0'?><root>매출액 영업이익</root>"
    payload = codecs.BOM_UTF8 + text.encode("utf-8")
    decoded, codec = _decode_payload(payload)
    assert codec == "utf-8-sig"
    assert "매출액" in decoded
    assert "영업이익" in decoded


def test_decode_payload_declared_euckr():
    text = '<?xml version="1.0" encoding="euc-kr"?><root>매출액 영업이익</root>'
    payload = text.encode("euc-kr")
    decoded, codec = _decode_payload(payload)
    assert codec == "euc-kr"
    assert "매출액" in decoded
    assert "영업이익" in decoded


def test_decode_payload_declared_euckr_with_cp949_extension():
    # 똠 is in CP949 but not in standard 2350 EUC-KR syllables.
    text = '<?xml version="1.0" encoding="euc-kr"?><root>매출액 영업이익 똠</root>'
    payload = text.encode("cp949")
    decoded, codec = _decode_payload(payload)
    # Strict euc-kr fails, falls through to cp949
    assert codec == "cp949"
    assert "매출액" in decoded
    assert "영업이익" in decoded
    assert "똠" in decoded


def test_decode_payload_declared_wrong_utf8_body_cp949():
    # Header says utf-8, but body is actually Korean encoded in CP949/EUC-KR
    text = '<?xml version="1.0" encoding="utf-8"?><root>매출액 영업이익</root>'
    payload = text.encode("cp949")
    decoded, codec = _decode_payload(payload)
    # Declared utf-8 fails strict decode, falls through to cp949
    assert codec == "cp949"
    assert "매출액" in decoded
    assert "영업이익" in decoded


def test_decode_payload_html_meta_charset():
    text = '<meta http-equiv="Content-Type" content="text/html; charset=euc-kr"><p>매출액 영업이익</p>'
    payload = text.encode("euc-kr")
    decoded, codec = _decode_payload(payload)
    assert codec == "euc-kr"
    assert "매출액" in decoded


def test_decode_payload_undeclared_utf8():
    text = "<root>매출액 영업이익</root>"
    payload = text.encode("utf-8")
    decoded, codec = _decode_payload(payload)
    assert codec == "utf-8"
    assert "매출액" in decoded


def test_decode_payload_undeclared_cp949():
    text = "<root>매출액 영업이익</root>"
    payload = text.encode("cp949")
    decoded, codec = _decode_payload(payload)
    assert codec == "cp949"
    assert "매출액" in decoded


def test_decode_payload_undecodable_fallback():
    # UTF-16-LE without BOM has null bytes throughout; fails strict utf-8 and cp949
    text = "<root>매출액 영업이익</root>"
    payload = text.encode("utf-16-le")
    decoded, codec = _decode_payload(payload)
    assert codec == "fallback"
    assert decoded == payload


# --- End-to-end extraction tests for each branch asserting Korean labels ---


def test_extract_row_bom_utf8():
    header = '<?xml version="1.0" encoding="utf-8"?>'
    payload = codecs.BOM_UTF8 + XML_BODY_TEMPLATE.format(header=header, extra="").encode("utf-8")
    row = extract_raw_xml_row(payload, TARGET, scope="consolidated")
    assert row["revenue"] == 200_000.0
    assert row["operating_profit"] == 50_000.0
    assert row["total_assets"] == 100_000.0
    assert row["net_income"] == 30_000.0


def test_extract_row_declared_euckr():
    header = '<?xml version="1.0" encoding="euc-kr"?>'
    payload = XML_BODY_TEMPLATE.format(header=header, extra="").encode("euc-kr")
    row = extract_raw_xml_row(payload, TARGET, scope="consolidated")
    assert row["revenue"] == 200_000.0
    assert row["operating_profit"] == 50_000.0
    assert row["total_assets"] == 100_000.0


def test_extract_row_declared_html_meta_euckr():
    header = '<meta http-equiv="Content-Type" content="text/html; charset=euc-kr">'
    payload = XML_BODY_TEMPLATE.format(header=header, extra="").encode("euc-kr")
    row = extract_raw_xml_row(payload, TARGET, scope="consolidated")
    assert row["revenue"] == 200_000.0
    assert row["operating_profit"] == 50_000.0
    assert row["total_assets"] == 100_000.0


def test_extract_row_declared_wrong_utf8_is_cp949():
    header = '<?xml version="1.0" encoding="utf-8"?>'
    # Body is encoded in cp949, conflicting with declared utf-8
    payload = XML_BODY_TEMPLATE.format(header=header, extra="").encode("cp949")
    row = extract_raw_xml_row(payload, TARGET, scope="consolidated")
    assert row["revenue"] == 200_000.0
    assert row["operating_profit"] == 50_000.0
    assert row["total_assets"] == 100_000.0


def test_extract_row_declared_euckr_with_cp949_chars():
    header = '<?xml version="1.0" encoding="euc-kr"?>'
    extra = "<COMMENT>똠</COMMENT>"
    payload = XML_BODY_TEMPLATE.format(header=header, extra=extra).encode("cp949")
    row = extract_raw_xml_row(payload, TARGET, scope="consolidated")
    assert row["revenue"] == 200_000.0
    assert row["operating_profit"] == 50_000.0
    assert row["total_assets"] == 100_000.0


def test_extract_row_undeclared_utf8():
    payload = XML_BODY_TEMPLATE.format(header="", extra="").encode("utf-8")
    row = extract_raw_xml_row(payload, TARGET, scope="consolidated")
    assert row["revenue"] == 200_000.0
    assert row["operating_profit"] == 50_000.0
    assert row["total_assets"] == 100_000.0


def test_extract_row_undeclared_cp949():
    payload = XML_BODY_TEMPLATE.format(header="", extra="").encode("cp949")
    row = extract_raw_xml_row(payload, TARGET, scope="consolidated")
    assert row["revenue"] == 200_000.0
    assert row["operating_profit"] == 50_000.0
    assert row["total_assets"] == 100_000.0


def test_extract_row_undecodable_bytes_fallback():
    # A payload with an incomplete multibyte sequence (e.g. trailing lead byte b"\x81")
    # fails strict utf-8 and strict cp949 decode, triggering the fallback path where
    # raw bytes are passed to BeautifulSoup. BeautifulSoup's UnicodeDammit / chardet
    # detects CP949 and uses character replacement, successfully extracting tables.
    base = XML_BODY_TEMPLATE.format(header="", extra="").encode("euc-kr")
    payload = base + b"\x81"
    row = extract_raw_xml_row(payload, TARGET, scope="consolidated")
    assert row["revenue"] == 200_000.0
    assert row["operating_profit"] == 50_000.0
    assert row["total_assets"] == 100_000.0
