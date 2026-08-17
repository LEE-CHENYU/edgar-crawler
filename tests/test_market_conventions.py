"""Tests for per-market reporting conventions.

Both defect classes here were live in the pipeline: AU period ends were
fabricated as 12-31 against real 30 June year ends, IN as 12-31 against 31
March, and no market captured a unit scale at all.
"""

import pytest

from scripts.market_conventions import (
    FISCAL_YEAR_END,
    detect_scale,
    is_fabricated,
    period_end_for,
    scale_in_window,
)


# --- fiscal year ends ---------------------------------------------------


def test_australia_ends_in_june_not_december():
    """AU filings cluster Sep/Aug/Oct, i.e. 30 June year ends."""
    assert period_end_for("au", 2024) == "2024-06-30"


def test_india_ends_in_march_not_december():
    """Indian statutory fiscal year ends 31 March."""
    assert period_end_for("in_bse", 2024) == "2024-03-31"


def test_calendar_year_markets_keep_december():
    for market in ("tw", "ph", "kr"):
        assert period_end_for(market, 2024) == f"2024-12-31", market


def test_markets_with_real_period_end_refuse_to_fabricate():
    """cn/jp/hk carry a real period end; fabricating one misdates rows."""
    for market in ("cn", "jp", "hk"):
        assert period_end_for(market, 2024) is None, market
        assert is_fabricated(market) is False, market


def test_fabricated_markets_are_flagged_as_such():
    for market in ("au", "in_bse", "tw", "ph", "kr"):
        assert is_fabricated(market) is True, market


def test_unknown_market_does_not_silently_fabricate():
    assert period_end_for("atlantis", 2024) is None


def test_every_configured_market_is_either_a_date_or_an_explicit_none():
    for market, convention in FISCAL_YEAR_END.items():
        assert convention is None or (
            1 <= convention[0] <= 12 and 1 <= convention[1] <= 31
        ), market


# --- unit scale ---------------------------------------------------------


def test_indian_scales_are_recognised():
    """lakh 10^5 and crore 10^7 have no Western equivalent."""
    assert scale_in_window("(Rs. in crores)") == 10_000_000
    assert scale_in_window("(Rs. in lakhs)") == 100_000
    assert scale_in_window("Amount in Lacs") == 100_000


def test_crore_beats_lakh_when_it_appears_first():
    assert scale_in_window("in crore; prior year in lakh") == 10_000_000


def test_western_scales_still_work():
    assert scale_in_window("HK$'000") == 1_000
    assert scale_in_window("in millions of RMB") == 1_000_000
    assert scale_in_window("expressed in thousands") == 1_000
    assert scale_in_window("RMB billion") == 1_000_000_000


def test_cjk_scales_are_recognised():
    assert scale_in_window("人民幣千元") == 1_000
    assert scale_in_window("單位：百萬元") == 1_000_000
    assert scale_in_window("百万円") == 1_000_000


def test_no_scale_word_returns_none():
    assert scale_in_window("total assets and liabilities") is None
    assert scale_in_window("") is None


def test_detect_scale_votes_across_statement_anchors():
    """A report mentioning several scales must be decided by the caption nearest
    each statement, not by the first word in the document."""
    text = (
        "annual report discussing revenue in crore terms generally. "
        + "Balance Sheet (Rs. in lakhs) " + "x" * 50
        + "Statement of Profit and Loss (Rs. in lakhs) " + "y" * 50
        + "Income Statement (Rs. in lakhs)"
    )
    scale, evidence = detect_scale(text)
    assert scale == 100_000
    assert "anchored" in evidence


def test_detect_scale_falls_back_to_document_head():
    scale, evidence = detect_scale("(All figures in Rs. crore) no statements here")
    assert scale == 10_000_000
    assert evidence == "document-head"


def test_detect_scale_reports_none_rather_than_guessing():
    scale, evidence = detect_scale("no financial statements in this document")
    assert scale is None
    assert evidence == "none"


def test_detect_scale_records_the_vote_split_for_auditing():
    text = ("Balance Sheet (in crore) " + "x" * 2000
            + "Income Statement (in lakh) " + "y" * 20)
    scale, evidence = detect_scale(text)
    assert "votes" in evidence
    assert scale in (10_000_000, 100_000)
