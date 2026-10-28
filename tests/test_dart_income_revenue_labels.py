"""Tests for DART income statement revenue label recognition and note suffix stripping."""

import pytest
from bs4 import BeautifulSoup

from dart_raw_xml_v2 import _metric_for_income, extract_raw_xml_row

TARGET = {
    "filing_id": "20250311000487",
    "corp_code": "01035942",
    "stock_code": "233250",
    "company_name": "테스트회사",
    "filing_date": "2025-03-11",
    "report_nm": "사업보고서 (2024.12)",
}


def _make_xml(income_rows: str, unit_label: str = "원") -> bytes:
    """Build a minimal valid DART audit report XML with standard balance sheet."""
    return f"""<?xml version="1.0" encoding="utf-8"?>
    <DOCUMENT><DOCUMENT-NAME ACODE="00761">연결감사보고서</DOCUMENT-NAME>
    <COMPANY-NAME>테스트회사</COMPANY-NAME>
    <TABLE><TR><TD>연 결 재 무 상 태 표</TD></TR><TR><TD>(단위: {unit_label})</TD></TR></TABLE>
    <TABLE BORDER="1"><THEAD><TR><TH>과목</TH><TH>주석</TH><TH>제 10(당) 기</TH><TH>제 9(전) 기</TH></TR></THEAD><TBODY>
      <TR><TD>유동자산</TD><TD></TD><TD ALIGN="RIGHT">60</TD><TD ALIGN="RIGHT">55</TD></TR>
      <TR><TD>비유동자산</TD><TD></TD><TD ALIGN="RIGHT">40</TD><TD ALIGN="RIGHT">35</TD></TR>
      <TR><TD>자산총계</TD><TD></TD><TD ALIGN="RIGHT">100</TD><TD ALIGN="RIGHT">90</TD></TR>
      <TR><TD>유동부채</TD><TD></TD><TD ALIGN="RIGHT">25</TD><TD ALIGN="RIGHT">20</TD></TR>
      <TR><TD>비유동부채</TD><TD></TD><TD ALIGN="RIGHT">15</TD><TD ALIGN="RIGHT">15</TD></TR>
      <TR><TD>부채총계</TD><TD></TD><TD ALIGN="RIGHT">40</TD><TD ALIGN="RIGHT">35</TD></TR>
      <TR><TD>자본총계</TD><TD></TD><TD ALIGN="RIGHT">60</TD><TD ALIGN="RIGHT">55</TD></TR>
    </TBODY></TABLE>
    <TABLE><TR><TD>연 결 포 괄 손 익 계 산 서</TD></TR><TR><TD>(단위: {unit_label})</TD></TR></TABLE>
    <TABLE BORDER="1"><THEAD><TR><TH>과목</TH><TH>주석</TH><TH>제 10(당) 기</TH><TH>제 9(전) 기</TH></TR></THEAD><TBODY>
      {income_rows}
      <TR><TD>매출원가</TD><TD></TD><TD ALIGN="RIGHT">100</TD><TD ALIGN="RIGHT">90</TD></TR>
      <TR><TD>매출총이익</TD><TD></TD><TD ALIGN="RIGHT">100</TD><TD ALIGN="RIGHT">90</TD></TR>
      <TR><TD>판매비와관리비</TD><TD></TD><TD ALIGN="RIGHT">30</TD><TD ALIGN="RIGHT">25</TD></TR>
      <TR><TD>영업이익</TD><TD></TD><TD ALIGN="RIGHT">70</TD><TD ALIGN="RIGHT">65</TD></TR>
      <TR><TD>법인세비용차감전순이익</TD><TD></TD><TD ALIGN="RIGHT">60</TD><TD ALIGN="RIGHT">55</TD></TR>
      <TR><TD>법인세비용</TD><TD></TD><TD ALIGN="RIGHT">15</TD><TD ALIGN="RIGHT">10</TD></TR>
      <TR><TD>당기순이익</TD><TD></TD><TD ALIGN="RIGHT">45</TD><TD ALIGN="RIGHT">45</TD></TR>
    </TBODY></TABLE>
    </DOCUMENT>""".encode()


def test_observed_four_pilot_revenue_labels_map_to_revenue():
    """Verify the 4 real-source pilot revenue labels map to 'revenue'."""
    # 1) I. 매출 with separate note 25 (e.g. 00195229)
    assert _metric_for_income("I. 매출") == "revenue"
    # 2) Ⅰ. 매출 with separate note 28 (e.g. 01686755)
    assert _metric_for_income("Ⅰ. 매출") == "revenue"
    # 3) 매출액 (주4,20,33) embedded note suffix (e.g. 00842585)
    assert _metric_for_income("매출액 (주4,20,33)") == "revenue"
    # 4) 매출액(주4,23,33) embedded note suffix without space (e.g. 00109310)
    assert _metric_for_income("매출액(주4,23,33)") == "revenue"


def test_standard_and_variant_revenue_labels_map_to_revenue():
    """Check short label 매출, standard labels, and observed note suffix patterns."""
    assert _metric_for_income("매출") == "revenue"
    assert _metric_for_income("매출액") == "revenue"
    assert _metric_for_income("영업수익") == "revenue"
    assert _metric_for_income("수익(매출액)") == "revenue"
    assert _metric_for_income("수익") == "revenue"

    # Note variations on 매출 / 매출액 / 영업수익 / 수익
    assert _metric_for_income("매출 (주25)") == "revenue"
    assert _metric_for_income("Ⅰ. 매출 (주28)") == "revenue"
    assert _metric_for_income("매출액 (주4)") == "revenue"
    assert _metric_for_income("매출액 (주석4,20,33)") == "revenue"
    assert _metric_for_income("매출액(주석28)") == "revenue"
    assert _metric_for_income("영업수익 (주1,2)") == "revenue"
    assert _metric_for_income("수익 (주10)") == "revenue"
    assert _metric_for_income("수익(매출액)(주1)") == "revenue"


def test_cost_of_revenue_and_gross_profit_preserved():
    """Preserve rejection of 매출원가 and 매출총이익 as revenue."""
    assert _metric_for_income("매출원가") == "cost_of_revenue"
    assert _metric_for_income("매출원가 (주21,26,33)") == "cost_of_revenue"
    assert _metric_for_income("매출원가(주24,29,33)") == "cost_of_revenue"
    assert _metric_for_income("영업원가") == "cost_of_revenue"
    assert _metric_for_income("영업원가 (주5)") == "cost_of_revenue"

    assert _metric_for_income("매출총이익") == "gross_profit"
    assert _metric_for_income("매출총이익 (주4)") == "gross_profit"
    assert _metric_for_income("매출총손실") == "gross_profit"


def test_growth_rates_rejected():
    """Growth rates containing 매출 must not map to revenue."""
    assert _metric_for_income("매출액증가율") is None
    assert _metric_for_income("매출증가율") is None
    assert _metric_for_income("매출성장률") is None
    assert _metric_for_income("매출액성장률") is None


def test_segment_phrases_rejected():
    """Disaggregated or segment phrases must not map to primary revenue."""
    assert _metric_for_income("제품매출") is None
    assert _metric_for_income("상품매출") is None
    assert _metric_for_income("용역매출") is None
    assert _metric_for_income("국내매출") is None
    assert _metric_for_income("해외매출") is None
    assert _metric_for_income("부문매출") is None
    assert _metric_for_income("사업부문매출") is None
    assert _metric_for_income("기타매출") is None


def test_deferred_revenue_rejected():
    """Deferred revenue or contract liability phrases must not map to revenue."""
    assert _metric_for_income("선수수익") is None
    assert _metric_for_income("이연수익") is None
    assert _metric_for_income("계약부채") is None


def test_invalid_note_payloads_and_footnote_substrings_rejected():
    """Non-numeric payloads, malformed note syntax, and footnote phrases are rejected."""
    # Non-numeric text in note parentheses
    assert _metric_for_income("매출액(주석참조)") is None
    assert _metric_for_income("매출(주석참조)") is None
    assert _metric_for_income("매출액 (주석: 세부사항 참조)") is None
    assert _metric_for_income("매출액(단위:천원)") is None
    assert _metric_for_income("매출(상세내역은주석참조)") is None

    # Empty or alpha note payloads
    assert _metric_for_income("매출액(주)") is None
    assert _metric_for_income("매출액(주석)") is None
    assert _metric_for_income("매출액(주abc)") is None

    # Malformed commas
    assert _metric_for_income("매출액(주1,)") is None
    assert _metric_for_income("매출액(주,1)") is None
    assert _metric_for_income("매출액(주1,,2)") is None

    # Semantic non-note parentheses
    assert _metric_for_income("매출(손실)") is None
    assert _metric_for_income("매출(할인)") is None
    assert _metric_for_income("매출(수출)") is None

    # Other income statement lines or adjustments
    assert _metric_for_income("매출액비고") is None
    assert _metric_for_income("매출채권") is None
    assert _metric_for_income("매출할인") is None
    assert _metric_for_income("매출에누리") is None
    assert _metric_for_income("매출환입") is None
    assert _metric_for_income("기타영업수익") is None
    assert _metric_for_income("금융수익") is None
    assert _metric_for_income("지분법손익") is None


def test_xml_end_to_end_extracts_revenue_with_roman_prefix_and_separate_note():
    """End-to-end XML extraction: 'I. 매출' with separate note column and proper current/prior alignment."""
    # Real pilot case 1: 00195229 (4,268,743,012,835 KRW current, 4,006,627,455,519 KRW prior)
    income_rows = """
      <TR><TD>I. 매출</TD><TD>25</TD><TD ALIGN="RIGHT">4,268,743,012,835</TD><TD ALIGN="RIGHT">4,006,627,455,519</TD></TR>
    """
    doc = _make_xml(income_rows, unit_label="원")
    row = extract_raw_xml_row(doc, TARGET, scope="consolidated")

    assert row["revenue"] == 4_268_743_012_835.0
    assert row["revenue"] != 4_006_627_455_519.0


def test_xml_end_to_end_extracts_revenue_with_unicode_roman_prefix():
    """End-to-end XML extraction: 'Ⅰ. 매출' with separate note column (01686755)."""
    income_rows = """
      <TR><TD>Ⅰ. 매출</TD><TD>28</TD><TD ALIGN="RIGHT">382,168,522,104</TD><TD ALIGN="RIGHT">380,029,515,142</TD></TR>
    """
    doc = _make_xml(income_rows, unit_label="원")
    row = extract_raw_xml_row(doc, TARGET, scope="consolidated")

    assert row["revenue"] == 382_168_522_104.0
    assert row["revenue"] != 380_029_515_142.0


def test_xml_end_to_end_extracts_revenue_with_embedded_note_suffix():
    """End-to-end XML extraction: '매출액 (주4,20,33)' embedded note suffix (00842585)."""
    # 3-column table: 과목, 제 10(당) 기, 제 9(전) 기 (note is embedded in caption)
    income_rows = """
      <TR><TD>매출액 (주4,20,33)</TD><TD ALIGN="RIGHT">17,200,064,065</TD><TD ALIGN="RIGHT">13,443,862,679</TD></TR>
    """
    xml_content = f"""<?xml version="1.0" encoding="utf-8"?>
    <DOCUMENT><DOCUMENT-NAME ACODE="00761">연결감사보고서</DOCUMENT-NAME>
    <COMPANY-NAME>테스트회사</COMPANY-NAME>
    <TABLE><TR><TD>연 결 재 무 상 태 표</TD></TR><TR><TD>(단위: 원)</TD></TR></TABLE>
    <TABLE BORDER="1"><THEAD><TR><TH>과목</TH><TH>제 10(당) 기</TH><TH>제 9(전) 기</TH></TR></THEAD><TBODY>
      <TR><TD>자산총계</TD><TD ALIGN="RIGHT">100</TD><TD ALIGN="RIGHT">90</TD></TR>
      <TR><TD>부채총계</TD><TD ALIGN="RIGHT">40</TD><TD ALIGN="RIGHT">35</TD></TR>
      <TR><TD>자본총계</TD><TD ALIGN="RIGHT">60</TD><TD ALIGN="RIGHT">55</TD></TR>
    </TBODY></TABLE>
    <TABLE><TR><TD>연 결 포 괄 손 익 계 산 서</TD></TR><TR><TD>(단위: 원)</TD></TR></TABLE>
    <TABLE BORDER="1"><THEAD><TR><TH>과목</TH><TH>제 10(당) 기</TH><TH>제 9(전) 기</TH></TR></THEAD><TBODY>
      {income_rows}
      <TR><TD>매출원가 (주21,26,33)</TD><TD ALIGN="RIGHT">9,241,655,140</TD><TD ALIGN="RIGHT">7,473,943,487</TD></TR>
      <TR><TD>매출총이익</TD><TD ALIGN="RIGHT">7,958,408,925</TD><TD ALIGN="RIGHT">5,969,919,192</TD></TR>
      <TR><TD>영업이익</TD><TD ALIGN="RIGHT">385,146,720</TD><TD ALIGN="RIGHT">(2,100,774,254)</TD></TR>
      <TR><TD>법인세비용차감전순이익</TD><TD ALIGN="RIGHT">1,261,070,486</TD><TD ALIGN="RIGHT">(1,466,776,619)</TD></TR>
      <TR><TD>당기순이익</TD><TD ALIGN="RIGHT">1,285,339,884</TD><TD ALIGN="RIGHT">(1,435,198,081)</TD></TR>
    </TBODY></TABLE>
    </DOCUMENT>""".encode()
    row = extract_raw_xml_row(xml_content, TARGET, scope="consolidated")

    assert row["revenue"] == 17_200_064_065.0
    assert row["revenue"] != 13_443_862_679.0


def test_xml_end_to_end_extracts_revenue_with_compact_embedded_note_suffix():
    """End-to-end XML extraction: '매출액(주4,23,33)' without space (00109310)."""
    income_rows = """
      <TR><TD>매출액(주4,23,33)</TD><TD></TD><TD ALIGN="RIGHT">257,057,008,425</TD><TD ALIGN="RIGHT">281,237,511,057</TD></TR>
    """
    doc = _make_xml(income_rows, unit_label="원")
    row = extract_raw_xml_row(doc, TARGET, scope="consolidated")

    assert row["revenue"] == 257_057_008_425.0
    assert row["revenue"] != 281_237_511_057.0


def test_observed_followup_note_suffix_labels_map_to_revenue():
    """Verify follow-up real-source DART revenue labels with angle brackets, Korean conjunctions, ranges, and trailing commas."""
    # 6 real labels from still_missing_labels.json
    assert _metric_for_income("매출액<주석25,31>") == "revenue"
    assert _metric_for_income("매출액<주석21,22>") == "revenue"
    assert _metric_for_income("매출액<주석17,22>") == "revenue"
    assert _metric_for_income("매출액(주석17과23)") == "revenue"
    assert _metric_for_income("매출액(주석15,24와25)") == "revenue"
    assert _metric_for_income("매출액(주석2,5,6,34,)") == "revenue"

    # Variations with roman prefixes, whitespace, single notes in angle brackets, and additional separators (및, ~, -)
    assert _metric_for_income("I. 매출액<주석 25,31>") == "revenue"
    assert _metric_for_income("Ⅰ.매출액<주석 21,22>") == "revenue"
    assert _metric_for_income("I. 매출액<주석 17,22>") == "revenue"
    assert _metric_for_income("Ⅰ.매출액(주석17과23)") == "revenue"
    assert _metric_for_income("Ⅰ.매출액(주석15,24와25)") == "revenue"
    assert _metric_for_income("Ⅰ. 매출액(주석2,5,6,34,)") == "revenue"
    assert _metric_for_income("I. 매출액(주석 19와 28)") == "revenue"
    assert _metric_for_income("Ⅰ. 매출액<주석11,25,27,28>") == "revenue"
    assert _metric_for_income("매출액 <주석 7>") == "revenue"
    assert _metric_for_income("매출액(주석1~3)") == "revenue"
    assert _metric_for_income("매출액(주석1-3)") == "revenue"
    assert _metric_for_income("매출액(주석1및2)") == "revenue"
    assert _metric_for_income("영업수익<주석5>") == "revenue"
    assert _metric_for_income("수익<주석10>") == "revenue"


def test_followup_negative_cases_and_order_preservation():
    """Ensure non-revenue labels, non-note suffixes, and evaluation order are strictly preserved."""
    assert _metric_for_income("매출액및지분법손익") is None
    assert _metric_for_income("Ⅰ. 매출액 및 지분법손익") is None
    assert _metric_for_income("1제품매출액") is None
    assert _metric_for_income("1. 제품매출액") is None
    assert _metric_for_income("2상품매출액") is None
    assert _metric_for_income("2. 상품매출액") is None
    assert _metric_for_income("3용역매출액") is None
    assert _metric_for_income("3. 용역매출액") is None
    assert _metric_for_income("1상품매출") is None
    assert _metric_for_income("1. 상 품 매 출") is None
    assert _metric_for_income("2기타매출액") is None
    assert _metric_for_income("2. 기타매출액") is None
    assert _metric_for_income("4공사매출액") is None
    assert _metric_for_income("1. 하역및장비영업수익") is None

    # Non-note suffixes
    assert _metric_for_income("매출액(외화)") is None
    assert _metric_for_income("매출액<연결>") is None
    assert _metric_for_income("매출액(기타)") is None
    assert _metric_for_income("매출액<별도>") is None

    # Cost of revenue and gross profit order preservation
    assert _metric_for_income("매출원가(주석5)") == "cost_of_revenue"
    assert _metric_for_income("매출원가<주석5>") == "cost_of_revenue"
    assert _metric_for_income("영업원가(주석12)") == "cost_of_revenue"
    assert _metric_for_income("매출총이익(주석3)") == "gross_profit"
    assert _metric_for_income("매출총이익<주석3>") == "gross_profit"

