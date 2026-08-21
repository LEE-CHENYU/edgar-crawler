"""JSE SENS crawler.

Discovered 2026-08-21 by watching the client portal call its own SharePoint WCF
service:

    POST /_vti_bin/JSE/SENSService.svc/GetSensAnnouncementForDates
    {"from": ISO, "to": ISO}
    -> [{announcementIdField, announcementReferenceNumberField,
         flashHeadlineField, issuersField, pdfPathField, ...}]

Every one of 327 announcements in the sampled week carried pdfPathField, and one
was downloaded end to end: a real "REVIEWED ANNUAL FINANCIAL RESULTS" PDF. No
auth.

Two things the format demands:
  1. The window is dated, so backfill iterates windows -- like the Oslo crawler.
     A window that is too wide risks truncation, so it must be bounded.
  2. SENS carries EVERY announcement type. Annual reports are ~7% (22 of 327),
     so headline filtering decides whether this is a 1,100/yr corpus or a
     17,000/yr one.
"""

import datetime as dt

import pytest

from scripts.jse_sens_crawl import (
    date_windows, is_financial_report, iso_window, safe_name,
)


# --- windows -------------------------------------------------------------


def test_windows_cover_the_range_without_gaps():
    got = date_windows(dt.date(2024, 1, 1), dt.date(2024, 1, 22), days=7)
    assert got[0][0] == dt.date(2024, 1, 1)
    assert got[-1][1] >= dt.date(2024, 1, 22)
    for (a1, b1), (a2, b2) in zip(got, got[1:]):
        assert a2 == b1 + dt.timedelta(days=1), "gap between windows"


def test_a_single_day_range_yields_one_window():
    got = date_windows(dt.date(2024, 5, 2), dt.date(2024, 5, 2), days=7)
    assert len(got) == 1


def test_iso_window_matches_the_captured_payload_shape():
    """The page sends 2026-08-21T07:00:00.000Z -- millisecond Z form."""
    lo, hi = iso_window(dt.date(2024, 3, 1), dt.date(2024, 3, 7))
    assert lo == "2024-03-01T00:00:00.000Z"
    assert hi == "2024-03-07T00:00:00.000Z"


# --- headline filtering --------------------------------------------------


def test_annual_and_financial_results_are_kept():
    for h in ("Reviewed Annual Financial Results for The Year Ended 31 December",
              "Notice of availability of consolidated annual financial statements",
              "Audited summarised consolidated financial statements",
              "Integrated Annual Report 2023"):
        assert is_financial_report(h) is True, h


def test_routine_announcements_are_excluded():
    """Dealings-in-securities is the single most common SENS type; keeping it
    would turn a 1,100/yr corpus into a 17,000/yr one of no analytical value."""
    for h in ("Dealings in securities by directors and the company secretary",
              "Change of company secretary",
              "Cautionary announcement",
              "Notice of annual general meeting"):
        assert is_financial_report(h) is False, h


def test_a_missing_headline_is_not_kept():
    assert is_financial_report(None) is False
    assert is_financial_report("") is False


def test_agm_notice_is_not_confused_with_an_annual_report():
    """"Notice of annual general meeting" contains 'annual' but carries no
    statements."""
    assert is_financial_report("Notice of Annual General Meeting") is False
    assert is_financial_report("Annual Financial Statements") is True


# --- filenames -----------------------------------------------------------


def test_safe_name_keeps_the_reference_and_strips_separators():
    assert safe_name("SENS_20240304_S486435.pdf") == "SENS_20240304_S486435.pdf"
    assert "/" not in safe_name("a/b/c.pdf")
