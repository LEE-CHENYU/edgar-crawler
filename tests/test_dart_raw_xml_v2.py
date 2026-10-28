"""DART raw audit-report XML fallback for API status 013 filings."""

from bs4 import BeautifulSoup

from dart_raw_xml_v2 import _scale_near, extract_raw_xml_row


def _xml(
    *, include_debt=True, negative_sga=False, operating_loss=False,
    impairment_reversal=False,
):
    debt = """
      <TR><TD>단기차입금</TD><TD>1</TD><TD ALIGN="RIGHT">10</TD><TD ALIGN="RIGHT">9</TD></TR>
      <TR><TD>장기차입금</TD><TD>2</TD><TD ALIGN="RIGHT">20</TD><TD ALIGN="RIGHT">18</TD></TR>
      <TR><TD>리스부채</TD><TD>3</TD><TD ALIGN="RIGHT">5</TD><TD ALIGN="RIGHT">4</TD></TR>
    """ if include_debt else ""
    sga = "110" if operating_loss else "(30)" if negative_sga else "30"
    op_label = "영업손실" if operating_loss else "영업이익"
    op_value = "30" if operating_loss else "57" if impairment_reversal else "50"
    reversal = """
      <TR><TD>매출채권손상차손(환입)</TD><TD></TD><TD ALIGN="RIGHT">(7)</TD><TD ALIGN="RIGHT">2</TD></TR>
    """ if impairment_reversal else ""
    return f"""<?xml version="1.0" encoding="utf-8"?>
    <DOCUMENT><DOCUMENT-NAME ACODE="00761">연결감사보고서</DOCUMENT-NAME>
    <COMPANY-NAME>테스트회사</COMPANY-NAME>
    <TABLE><TR><TD>연 결 재 무 상 태 표</TD></TR><TR><TD>(단위: 천원)</TD></TR></TABLE>
    <TABLE BORDER="1"><THEAD><TR><TH>과목</TH><TH>주석</TH><TH>제 10(당) 기</TH><TH>제 9(전) 기</TH></TR></THEAD><TBODY>
      <TR><TD>유동자산</TD><TD></TD><TD ALIGN="RIGHT">60</TD><TD ALIGN="RIGHT">55</TD></TR>
      <TR><TD>비유동자산</TD><TD></TD><TD ALIGN="RIGHT">40</TD><TD ALIGN="RIGHT">35</TD></TR>
      <TR><TD>자산총계</TD><TD></TD><TD ALIGN="RIGHT">100</TD><TD ALIGN="RIGHT">90</TD></TR>
      <TR><TD>유동부채</TD><TD></TD><TD ALIGN="RIGHT">25</TD><TD ALIGN="RIGHT">20</TD></TR>
      <TR><TD>비유동부채</TD><TD></TD><TD ALIGN="RIGHT">15</TD><TD ALIGN="RIGHT">15</TD></TR>
      {debt}
      <TR><TD>부채총계</TD><TD></TD><TD ALIGN="RIGHT">40</TD><TD ALIGN="RIGHT">35</TD></TR>
      <TR><TD>자본총계</TD><TD></TD><TD ALIGN="RIGHT">60</TD><TD ALIGN="RIGHT">55</TD></TR>
    </TBODY></TABLE>
    <TABLE><TR><TD>연 결 포 괄 손 익 계 산 서</TD></TR><TR><TD>(단위: 천원)</TD></TR></TABLE>
    <TABLE BORDER="1"><THEAD><TR><TH>과목</TH><TH>주석</TH><TH>제 10(당) 기</TH><TH>제 9(전) 기</TH></TR></THEAD><TBODY>
      <TR><TD>매출액</TD><TD>4</TD><TD ALIGN="RIGHT">200</TD><TD ALIGN="RIGHT">180</TD></TR>
      <TR><TD>매출원가</TD><TD>5</TD><TD ALIGN="RIGHT">120</TD><TD ALIGN="RIGHT">110</TD></TR>
      <TR><TD>매출총이익</TD><TD></TD><TD ALIGN="RIGHT">80</TD><TD ALIGN="RIGHT">70</TD></TR>
      <TR><TE ALEVEL="0">판매비와관리비</TE><TD>6</TD><TD ALIGN="RIGHT">{sga}</TD><TD ALIGN="RIGHT">25</TD></TR>
      <TR><TE ALEVEL="1">급여</TE><TD></TD><TD ALIGN="RIGHT">10</TD><TD ALIGN="RIGHT">9</TD></TR>
      {reversal}
      <TR><TD>{op_label}</TD><TD></TD><TD ALIGN="RIGHT">{op_value}</TD><TD ALIGN="RIGHT">45</TD></TR>
      <TR><TD>법인세비용차감전순이익</TD><TD></TD><TD ALIGN="RIGHT">40</TD><TD ALIGN="RIGHT">35</TD></TR>
      <TR><TD>법인세비용</TD><TD>7</TD><TD ALIGN="RIGHT">10</TD><TD ALIGN="RIGHT">8</TD></TR>
      <TR><TD>당기순이익</TD><TD></TD><TD ALIGN="RIGHT">30</TD><TD ALIGN="RIGHT">27</TD></TR>
    </TBODY></TABLE>
    <TABLE><TR><TD>주석</TD></TR><TR><TD>자산총계</TD><TD ALIGN="RIGHT">999999</TD></TR></TABLE>
    </DOCUMENT>""".encode()


TARGET = {
    "filing_id": "20250311000487",
    "corp_code": "01035942",
    "stock_code": "233250",
    "company_name": "테스트회사",
    "filing_date": "2025-03-11",
    "report_nm": "사업보고서 (2024.12)",
}


def test_raw_xml_maps_current_column_scale_scope_and_identities():
    row = extract_raw_xml_row(_xml(), TARGET, scope="consolidated")

    assert row["ticker"] == "233250"
    assert row["period_end"] == "2024-12-31"
    assert row["statement_scope"] == "consolidated"
    assert row["currency"] == "KRW"
    assert row["unit_scale"] == 1.0
    assert row["source_unit_scale"] == 1000.0
    assert row["revenue"] == 200_000.0
    assert row["total_assets"] == 100_000.0
    assert row["operating_profit"] == 50_000.0
    assert row["total_borrowings"] == 30_000.0
    assert row["total_lease_liabilities"] == 5_000.0
    assert row["total_interest_bearing_debt"] == 35_000.0
    assert row["balance_identity_status"] == "pass"
    assert row["tax_identity_status"] == "pass"
    assert row["operating_profit_closure_status"] == "pass"
    assert row["screen_eligible"] is True


def test_absent_debt_is_unresolved_not_zero():
    row = extract_raw_xml_row(_xml(include_debt=False), TARGET, scope="consolidated")

    assert row["total_interest_bearing_debt"] is None
    assert row["debt_reconciliation_status"] == "unresolved_not_reported"
    assert row["screen_eligible"] is False


def test_parenthesized_operating_expense_keeps_its_reported_sign():
    row = extract_raw_xml_row(
        _xml(negative_sga=True), TARGET, scope="consolidated"
    )

    assert row["operating_profit_pbt_bridge"] == 50_000.0
    assert row["operating_profit_closure_status"] == "pass"


def test_loss_label_negates_a_positive_displayed_magnitude():
    row = extract_raw_xml_row(
        _xml(operating_loss=True), TARGET, scope="consolidated"
    )

    assert row["operating_profit"] == -30_000.0
    assert row["operating_profit_pbt_bridge"] == -30_000.0
    assert row["operating_profit_closure_status"] == "pass"


def test_mixed_profit_loss_labels_preserve_the_displayed_sign():
    payload = (
        _xml()
        .replace("매출총이익".encode(), "매출총이익(손실)".encode())
        .replace(
            "법인세비용차감전순이익".encode(),
            "법인세비용차감전순이익(손실)".encode(),
        )
        .replace("당기순이익".encode(), "당기순이익(손실)".encode())
    )

    row = extract_raw_xml_row(payload, TARGET, scope="consolidated")

    assert row["gross_profit"] == 80_000.0
    assert row["profit_before_tax"] == 40_000.0
    assert row["net_income"] == 30_000.0
    assert row["operating_profit_closure_status"] == "pass"
    assert row["tax_identity_status"] == "pass"


def test_unambiguous_net_loss_label_negates_displayed_magnitude():
    payload = _xml().replace("당기순이익".encode(), "당기순손실".encode())

    row = extract_raw_xml_row(payload, TARGET, scope="consolidated")

    assert row["net_income"] == -30_000.0


def test_oci_tax_and_attribution_rows_do_not_shadow_primary_income_lines():
    payload = _xml().replace(
        b"<TR><TD>\xeb\xb2\x95\xec\x9d\xb8\xec\x84\xb8\xeb\xb9\x84\xec\x9a\xa9</TD>",
        (
            "<TR><TD>법인세비용차감후기타포괄손익</TD><TD></TD>"
            "<TD ALIGN=\"RIGHT\">999</TD><TD ALIGN=\"RIGHT\">999</TD></TR>"
            "<TR><TD>비지배지분에 귀속되는 당기순이익(손실)</TD><TD></TD>"
            "<TD ALIGN=\"RIGHT\">888</TD><TD ALIGN=\"RIGHT\">888</TD></TR>"
            "<TR><TD>법인세비용</TD>"
        ).encode(),
    )

    row = extract_raw_xml_row(payload, TARGET, scope="consolidated")

    assert row["income_tax_expense"] == 10_000.0
    assert row["net_income"] == 30_000.0
    assert row["tax_identity_status"] == "pass"


def test_current_period_dash_is_zero_and_does_not_fall_back_to_prior_tax():
    payload = (
        _xml()
        .replace(
            b'<TR><TD>\xeb\xb2\x95\xec\x9d\xb8\xec\x84\xb8\xeb\xb9\x84\xec\x9a\xa9</TD><TD>7</TD><TD ALIGN="RIGHT">10</TD><TD ALIGN="RIGHT">8</TD></TR>',
            b'<TR><TD>\xeb\xb2\x95\xec\x9d\xb8\xec\x84\xb8\xeb\xb9\x84\xec\x9a\xa9</TD><TD>7</TD><TD ALIGN="RIGHT">-</TD><TD ALIGN="RIGHT">8</TD></TR>',
        )
        .replace(
            b'<TR><TD>\xeb\x8b\xb9\xea\xb8\xb0\xec\x88\x9c\xec\x9d\xb4\xec\x9d\xb5</TD><TD></TD><TD ALIGN="RIGHT">30</TD><TD ALIGN="RIGHT">27</TD></TR>',
            b'<TR><TD>\xeb\x8b\xb9\xea\xb8\xb0\xec\x88\x9c\xec\x9d\xb4\xec\x9d\xb5</TD><TD></TD><TD ALIGN="RIGHT">40</TD><TD ALIGN="RIGHT">27</TD></TR>',
        )
    )

    row = extract_raw_xml_row(payload, TARGET, scope="consolidated")

    assert row["income_tax_expense"] == 0.0
    assert row["net_income"] == row["profit_before_tax"]
    assert row["tax_identity_status"] == "pass"


def test_debt_note_recovers_closing_components_when_face_has_no_debt():
    note = """
    <P>차입금 및 리스부채</P><P>(단위: 천원)</P>
    <TABLE BORDER="1"><THEAD><TR><TH>구분</TH><TH>제 10(당) 기</TH><TH>제 9(전) 기</TH></TR></THEAD><TBODY>
      <TR><TD>단기차입금</TD><TD ALIGN="RIGHT">10</TD><TD ALIGN="RIGHT">9</TD></TR>
      <TR><TD>장기차입금</TD><TD ALIGN="RIGHT">20</TD><TD ALIGN="RIGHT">18</TD></TR>
      <TR><TD>리스부채(유동)</TD><TD ALIGN="RIGHT">5</TD><TD ALIGN="RIGHT">4</TD></TR>
      <TR><TD>리스부채(비유동)</TD><TD ALIGN="RIGHT">6</TD><TD ALIGN="RIGHT">5</TD></TR>
    </TBODY></TABLE>
    """.encode()
    payload = _xml(include_debt=False).replace(b"</DOCUMENT>", note + b"</DOCUMENT>")

    row = extract_raw_xml_row(payload, TARGET, scope="consolidated")

    assert row["current_borrowings"] == 10_000.0
    assert row["non_current_borrowings"] == 20_000.0
    assert row["total_borrowings"] == 30_000.0
    assert row["total_lease_liabilities"] == 11_000.0
    assert row["total_interest_bearing_debt"] == 41_000.0
    assert row["debt_reconciliation_status"] == "resolved_components"


def test_explicit_zero_total_borrowings_is_resolved_without_prior_period_fallback():
    note = """
    <P>차입금</P><P>(단위: 천원)</P>
    <TABLE BORDER="1"><THEAD><TR><TH>구분</TH><TH>제 10(당) 기</TH><TH>제 9(전) 기</TH></TR></THEAD><TBODY>
      <TR><TD>총차입금</TD><TD ALIGN="RIGHT">-</TD><TD ALIGN="RIGHT">9</TD></TR>
    </TBODY></TABLE>
    """.encode()
    payload = _xml(include_debt=False).replace(b"</DOCUMENT>", note + b"</DOCUMENT>")

    row = extract_raw_xml_row(payload, TARGET, scope="consolidated")

    assert row["total_borrowings"] == 0.0
    assert row["total_interest_bearing_debt"] == 0.0
    assert row["debt_reconciliation_status"] == "resolved_components"


def test_borrowing_obligation_synonym_is_classified_as_borrowings():
    debt = """
      <TR><TD>단기차입채무</TD><TD>1</TD><TD ALIGN="RIGHT">7</TD><TD ALIGN="RIGHT">6</TD></TR>
    """.encode()
    payload = _xml(include_debt=False).replace(
        b"<TR><TD>\xeb\xb6\x80\xec\xb1\x84\xec\xb4\x9d\xea\xb3\x84</TD>",
        debt + b"<TR><TD>\xeb\xb6\x80\xec\xb1\x84\xec\xb4\x9d\xea\xb3\x84</TD>",
    )

    row = extract_raw_xml_row(payload, TARGET, scope="consolidated")

    assert row["total_borrowings"] == 7_000.0
    assert row["debt_reconciliation_status"] == "resolved_components"


def test_note_scale_can_come_from_a_preceding_paragraph():
    soup = BeautifulSoup(
        "<P>(단위: 백만원)</P>" + "<TABLE><TR><TD>note</TD></TR></TABLE>" * 6,
        "html.parser",
    )
    tables = soup.find_all("table")

    assert _scale_near(tables, 5) == (1_000_000.0, "reported:백만원")


def test_negative_impairment_reversal_is_added_back_in_bridge():
    row = extract_raw_xml_row(
        _xml(impairment_reversal=True), TARGET, scope="consolidated"
    )

    assert row["operating_profit_pbt_bridge"] == 57_000.0
    assert row["operating_profit_closure_status"] == "pass"


def test_dart_retains_payables_and_contract_liabilities_with_current_column_scale():
    extra = '''<TR><TD>매입채무및기타채무</TD><TD></TD><TD ALIGN="RIGHT">12</TD><TD ALIGN="RIGHT">10</TD></TR>
    <TR><TD>유동계약부채</TD><TD></TD><TD ALIGN="RIGHT">7</TD><TD ALIGN="RIGHT">6</TD></TR>
    <TR><TD>비유동계약부채</TD><TD></TD><TD ALIGN="RIGHT">2</TD><TD ALIGN="RIGHT">1</TD></TR>'''.encode()
    doc = _xml().replace('<TR><TD>부채총계</TD>'.encode(), extra+'<TR><TD>부채총계</TD>'.encode())
    row = extract_raw_xml_row(doc, TARGET, scope='consolidated')
    assert row['trade_and_other_payables'] == 12000
    assert row['current_contract_liabilities'] == 7000
    assert row['non_current_contract_liabilities'] == 2000


def test_liabilities_and_equity_total_is_not_equity():
    extra = '<TR><TD>부 채 와 자 본 총 계</TD><TD></TD><TD ALIGN="RIGHT">100</TD><TD ALIGN="RIGHT">90</TD></TR>'
    text = _xml().decode().replace('<TR><TD>자본총계</TD>', extra + '<TR><TD>자본총계</TD>')
    row = extract_raw_xml_row(text.encode(), TARGET, scope='consolidated')
    assert row['total_equity'] == 60000.
    assert row['total_liabilities_and_equity'] == 100000.
    assert row['balance_identity_status'] == 'pass'
    assert all('부 채 와 자 본' not in x['label'] for x in row['field_provenance']['total_equity'])


def test_conversion_right_equity_component_is_not_bond_debt():
    extra = '<TR><TD>3. 전환사채권대가</TD><TD></TD><TD ALIGN="RIGHT">23</TD><TD ALIGN="RIGHT">20</TD></TR>'
    text = _xml().decode().replace('<TR><TD>자본총계</TD>',extra+'<TR><TD>자본총계</TD>')
    row=extract_raw_xml_row(text.encode(),TARGET,scope='consolidated')
    assert row['non_current_borrowings'] == 20000.
    assert row['equity_conversion_component'] == 23000.


def test_known_current_column_blank_never_uses_prior_year_value():
    payload=_xml().replace(b'ALIGN="RIGHT">10</TD><TD ALIGN="RIGHT">9',
                           b'ALIGN="RIGHT"></TD><TD ALIGN="RIGHT">9',1)
    row=extract_raw_xml_row(payload,TARGET,scope='consolidated')
    assert row.get('current_borrowings') is None
    assert row['total_borrowings'] == 20000.


def test_bond_carrying_adjustments_preserved_without_false_resolution():
    extra = '<TR><TD>상환할증금</TD><TD></TD><TD ALIGN="RIGHT">3</TD><TD ALIGN="RIGHT">2</TD></TR><TR><TD>전환권조정</TD><TD></TD><TD ALIGN="RIGHT">(4)</TD><TD ALIGN="RIGHT">(3)</TD></TR>'
    payload=_xml().decode().replace('<TR><TD>부채총계</TD>',extra+'<TR><TD>부채총계</TD>')
    row=extract_raw_xml_row(payload.encode(),TARGET,scope='consolidated')
    assert row['bond_redemption_premium']==3000.
    assert row['bond_conversion_adjustment']==-4000.
    assert row['debt_reconciliation_status']=='unresolved_bond_carrying_adjustments'
    assert row['screen_eligible'] is False


def test_current_year_spanned_columns_allow_subtotal_but_never_prior_year():
    from dart_raw_xml_v2 import _table_records, _metric_for_balance
    table=BeautifulSoup('<table><tr><th>label</th><th colspan="2">제 18(당) 기</th><th>prior</th></tr><tr><td>자산총계</td><td></td><td align="right">100</td><td align="right">90</td></tr><tr><td>단기차입금</td><td></td><td></td><td align="right">9</td></tr></table>','html.parser').table
    records=_table_records(table,_metric_for_balance)
    assert [(r['metric'],r['value']) for r in records]==[('total_assets',100.)]
