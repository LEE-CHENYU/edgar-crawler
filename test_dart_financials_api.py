"""TDD tests for the OpenDART fnlttSinglAcntAll structured-financials fetcher.

Covers the two pure cores: amount parsing and IFRS-tag -> canonical-metric
mapping. The live HTTP fetch is thin and exercised separately once the key is set.
"""
import dart_financials_api as api


# ---- parse_amount -------------------------------------------------------

def test_parse_amount_commas():
    assert api.parse_amount("1,234,567") == 1234567.0

def test_parse_amount_negative_parens_and_sign():
    assert api.parse_amount("-1,234") == -1234.0
    assert api.parse_amount("(1,234)") == -1234.0   # accounting negatives

def test_parse_amount_blank_is_none():
    assert api.parse_amount("") is None
    assert api.parse_amount("-") is None
    assert api.parse_amount(None) is None


# ---- parse_response envelope -------------------------------------------

def test_parse_response_ok_returns_rows():
    payload = {"status": "000", "message": "정상", "list": [
        {"sj_div": "BS", "account_id": "ifrs-full_Assets", "account_nm": "자산총계",
         "fs_div": "CFS", "thstrm_amount": "1,000", "frmtrm_amount": "900"},
    ]}
    rows, status = api.parse_response(payload)
    assert status == "000"
    assert len(rows) == 1
    assert rows[0]["account_id"] == "ifrs-full_Assets"

def test_parse_response_no_data_status_013():
    # OpenDART returns 013 when a company/year has no structured financials.
    rows, status = api.parse_response({"status": "013", "message": "조회된 데이타가 없습니다."})
    assert status == "013"
    assert rows == []


# ---- map_to_canonical (the core) ---------------------------------------

def _rows():
    """A minimal consolidated (CFS) statement set spanning BS / IS / CF."""
    def r(sj, aid, nm, amt):
        return {"sj_div": sj, "account_id": aid, "account_nm": nm,
                "fs_div": "CFS", "thstrm_amount": amt, "currency": "KRW"}
    return [
        r("BS", "ifrs-full_Assets", "자산총계", "5,000,000"),
        r("BS", "ifrs-full_Liabilities", "부채총계", "2,000,000"),
        r("BS", "ifrs-full_Equity", "자본총계", "3,000,000"),
        r("BS", "ifrs-full_CashAndCashEquivalents", "현금및현금성자산", "400,000"),
        r("BS", "ifrs-full_Inventories", "재고자산", "150,000"),
        r("IS", "ifrs-full_Revenue", "매출액", "8,000,000"),
        r("IS", "dart_OperatingIncomeLoss", "영업이익", "900,000"),
        r("IS", "ifrs-full_ProfitLoss", "당기순이익", "600,000"),
        r("CF", "ifrs-full_CashFlowsFromUsedInOperatingActivities", "영업활동현금흐름", "700,000"),
    ]

def test_map_to_canonical_by_ifrs_tag():
    m = api.map_to_canonical(_rows())
    assert m["total_assets"] == 5_000_000
    assert m["total_liabilities"] == 2_000_000
    assert m["total_equity"] == 3_000_000
    assert m["revenue"] == 8_000_000
    assert m["operating_profit"] == 900_000        # DART-specific tag
    assert m["net_income"] == 600_000
    assert m["operating_cash_flow"] == 700_000
    assert m["cash_and_equivalents"] == 400_000
    assert m["inventories"] == 150_000

def test_map_falls_back_to_korean_account_name():
    # Non-standard filer: account_id blank, must match on account_nm.
    rows = [{"sj_div": "IS", "account_id": "-표준계정코드 미사용-", "account_nm": "매출액",
             "fs_div": "CFS", "thstrm_amount": "1,111,000"}]
    m = api.map_to_canonical(rows)
    assert m["revenue"] == 1_111_000

def test_map_values_are_note_ref_free():
    """Regression vs the broken text extractor: real values, not tiny note refs."""
    m = api.map_to_canonical(_rows())
    assert m["total_assets"] > 10_000   # never a 주NN note number


# ---- parse_fiscal_year (drives bsns_year for the sweep) ----------------

def test_parse_fiscal_year_standard():
    assert api.parse_fiscal_year("사업보고서 (2025.12)") == 2025
    assert api.parse_fiscal_year("사업보고서 (2019.12)") == 2019

def test_parse_fiscal_year_non_december_end():
    assert api.parse_fiscal_year("사업보고서 (2026.01)") == 2026

def test_parse_fiscal_year_unparseable_is_none():
    assert api.parse_fiscal_year("사업보고서") is None
    assert api.parse_fiscal_year("") is None
