"""Rescue for markets whose metadata already carries a direct document URL.

HKEX (915 missing) and ASX (537 missing) were blocked only because the driver
supported TWSE and DART alone -- 1,452 documents behind one resolver each.

These two are simpler than TWSE, and the difference matters: a TWSE link is a
timestamped one-shot that is dead by the time a rescue runs, so it must be
RE-RESOLVED every attempt. HKEX and ASX rows already carry a stable
`document_url`:

    HKEX  https://www1.hkexnews.hk/listedco/listconews/sehk/2013/0206/ltn20130206404.pdf
    ASX   https://www.asx.com.au/asx/v2/statistics/displayAnnouncement.do?display=pdf&idsId=00884327

so the rescue is: use the recorded URL, write to the market's layout, adopt
anything already on disk.
"""

from pathlib import Path

import pytest

from scripts.rescue_missing_documents import (
    RescueResult, build_direct_local_path, rescue_direct_url_row,
)


def _row(market="HKEX", **over):
    row = {
        "market": market,
        "filing_id": "1585042",
        "stock_code": "00626",
        "filing_date": "2013-02-06",
        "document_url": "https://www1.hkexnews.hk/listedco/listconews/sehk/2013/0206/ltn20130206404.pdf",
    }
    row.update(over)
    return row


# --- path layout ---------------------------------------------------------


def test_hkex_path_mirrors_the_existing_corpus_layout(tmp_path):
    """HKEX raw is {yyyymmdd}/{filing_id}/{name}.pdf -- 25,658 documents already
    sit in that shape and a rescue must not invent a second one."""
    got = build_direct_local_path(str(tmp_path), _row())
    assert got.endswith("20130206/1585042/ltn20130206404.pdf"), got


def test_asx_path_uses_date_and_ticker(tmp_path):
    """ASX raw is {yyyymmdd}/{TICKER}/{file}.pdf."""
    row = _row(market="ASX", filing_id="AMN_20080926_00884327", stock_code="AMN",
               filing_date="2008-09-26",
               document_url="https://www.asx.com.au/asx/v2/statistics/displayAnnouncement.do?display=pdf&idsId=00884327")
    got = build_direct_local_path(str(tmp_path), row)
    assert "/20080926/AMN/" in got, got
    assert got.endswith(".pdf"), got


def test_a_url_without_a_filename_still_produces_a_pdf_name(tmp_path):
    """The ASX URL is a query-string endpoint with no filename in the path."""
    row = _row(market="ASX", filing_id="AMN_20080926_00884327", stock_code="AMN",
               document_url="https://www.asx.com.au/x.do?display=pdf&idsId=00884327")
    assert build_direct_local_path(str(tmp_path), row).endswith(".pdf")


# --- the rescue itself ---------------------------------------------------


def test_a_row_without_a_document_url_fails_cleanly(tmp_path):
    calls = []
    res = rescue_direct_url_row(_row(document_url=""), str(tmp_path), session=None,
                                downloader=lambda **kw: calls.append(kw) or True,
                                timeout=30)
    assert res.ok is False and "document_url" in res.error
    assert calls == [], "must not attempt a download without a URL"


def test_an_already_present_file_is_adopted_not_refetched(tmp_path):
    dest = Path(build_direct_local_path(str(tmp_path), _row()))
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(b"%PDF-1.4 already here")
    calls = []
    res = rescue_direct_url_row(_row(), str(tmp_path), session=None,
                                downloader=lambda **kw: calls.append(kw) or True,
                                timeout=30)
    assert res.ok is True and res.skipped is True
    assert calls == [], "adopt-from-disk must not re-download"


def test_the_downloader_is_called_with_keyword_arguments(tmp_path):
    """try_download_binary's 5th POSITIONAL parameter is `params`, not
    `timeout` -- passing positionally sent a timeout into params and crashed
    inside requests. Keyword-only is the guard."""
    seen = {}

    def downloader(**kwargs):
        seen.update(kwargs)
        Path(kwargs["path"]).parent.mkdir(parents=True, exist_ok=True)
        Path(kwargs["path"]).write_bytes(b"%PDF-1.4")
        return True

    res = rescue_direct_url_row(_row(), str(tmp_path), session="S",
                                downloader=downloader, timeout=42)
    assert res.ok is True and res.skipped is False
    assert set(seen) >= {"session", "url", "path", "timeout"}
    assert seen["timeout"] == 42
    assert seen["url"].endswith("ltn20130206404.pdf")


def test_a_failed_download_reports_not_raises(tmp_path):
    res = rescue_direct_url_row(_row(), str(tmp_path), session="S",
                                downloader=lambda **kw: False, timeout=30)
    assert res.ok is False and res.error


def test_the_new_markets_are_selectable_from_the_cli():
    """915 HKEX + 537 ASX documents were blocked solely because the driver's
    --market choices did not include them."""
    from scripts.rescue_missing_documents import DIRECT_URL_MARKETS, SUPPORTED_MARKETS
    for m in ("HKEX", "ASX", "EDGAR_IE", "EDGAR_DE"):
        assert m in SUPPORTED_MARKETS, m
        assert m in DIRECT_URL_MARKETS, m
    # the originals must survive
    assert "TWSE_REPORTS" in SUPPORTED_MARKETS and "DART" in SUPPORTED_MARKETS
