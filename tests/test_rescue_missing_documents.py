"""Tests for the TWSE/DART document rescue driver.

The rescue re-drives document acquisition for metadata rows that discovery
already recorded but whose download failed, leaving `local_path` empty.
"""

import json

import pytest

from scripts.rescue_missing_documents import (
    DAILY_CALL_CAP_DART,
    RescueState,
    build_twse_local_path,
    dart_document_url,
    load_state,
    progress_details,
    quota_exhausted,
    report_progress,
    rescue_dart_row,
    rescue_twse_row,
    save_state,
    select_missing_rows,
    twse_filing_from_row,
)


def _twse_row(filing_id="TWSE_REPORT_F_3372_2008_2007_3372_20080506F04", local_path="",
              stale_pdf_url="https://doc.twse.com.tw/pdf/STALE_20260101_000000.pdf"):
    listing = {
        "kind": "F",
        "company_code": "3372",
        "query_year": "2008",
        "filename": "2007_3372_20080506F04.pdf",
        "upload_datetime": "2008/05/06 00:00:00",
    }
    raw = {"listing": listing, "document_url": "https://doc.twse.com.tw/server-java/t57sb01?step=9"}
    if stale_pdf_url:
        raw["pdf_url"] = stale_pdf_url
    return {
        "market": "TWSE_REPORTS",
        "filing_id": filing_id,
        "filing_date": "2008-04-30",
        "local_path": local_path,
        "raw_metadata": json.dumps(raw, ensure_ascii=False, sort_keys=True),
    }


def _dart_row(rcept_no="20080125000241", local_path=""):
    return {
        "market": "DART",
        "filing_id": rcept_no,
        "filing_date": "2008-01-25",
        "local_path": local_path,
        "raw_metadata": json.dumps({"rcept_no": rcept_no}),
    }


# --- row selection -------------------------------------------------------


def test_select_missing_rows_picks_only_rows_without_local_path():
    rows = [
        _twse_row(filing_id="A", local_path=""),
        _twse_row(filing_id="B", local_path="/some/where/b.pdf"),
        _twse_row(filing_id="C", local_path=""),
    ]
    missing = select_missing_rows(rows, market="TWSE_REPORTS")
    assert [r["filing_id"] for r in missing] == ["A", "C"]


def test_select_missing_rows_filters_by_market():
    rows = [_twse_row(filing_id="A"), _dart_row(rcept_no="B")]
    assert [r["filing_id"] for r in select_missing_rows(rows, market="DART")] == ["B"]


def test_select_missing_rows_treats_whitespace_local_path_as_missing():
    rows = [_twse_row(filing_id="A", local_path="   ")]
    assert len(select_missing_rows(rows, market="TWSE_REPORTS")) == 1


# --- TWSE: the one-shot link contract ------------------------------------


def test_rescue_twse_row_resolves_link_fresh_and_ignores_cached_pdf_url():
    """TWSE /pdf/ links are timestamped one-shots.

    A cached `pdf_url` from a previous run is always dead, so the rescue must
    re-resolve per attempt and download the freshly-resolved URL.
    """
    row = _twse_row(stale_pdf_url="https://doc.twse.com.tw/pdf/STALE_20260101_000000.pdf")
    fresh = "https://doc.twse.com.tw/pdf/2007_3372_20080506F04_20260815_003409.pdf"
    downloaded = []

    def resolver(session, filing, timeout):
        return fresh

    def downloader(session, url, path, headers, timeout):  # noqa: keyword-called
        downloaded.append(url)
        return True

    result = rescue_twse_row(
        row=row,
        raw_dir="/tmp/raw",
        session=None,
        resolver=resolver,
        downloader=downloader,
        timeout=30,
    )

    assert downloaded == [fresh], "must download the freshly resolved URL"
    assert "STALE" not in downloaded[0]
    assert result.local_path.endswith("2007_3372_20080506F04.pdf")
    assert result.ok is True


def test_downloader_is_called_with_keyword_arguments():
    """try_download_binary's 5th positional parameter is `params`, not `timeout`.

    Calling it positionally sends the timeout int as query params and raises
    deep inside requests. The rescue must pass keywords.
    """
    row = _twse_row()
    captured = {}

    def downloader(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return True

    rescue_twse_row(
        row=row, raw_dir="/tmp/raw", session=None,
        resolver=lambda s, f, t: "https://doc.twse.com.tw/pdf/fresh.pdf",
        downloader=downloader, timeout=30,
    )

    assert captured["args"] == (), "downloader must be called with keywords only"
    assert set(captured["kwargs"]) == {"session", "url", "path", "headers", "timeout"}
    assert captured["kwargs"]["timeout"] == 30


def test_dart_downloader_is_called_with_keyword_arguments():
    captured = {}

    def downloader(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return True

    rescue_dart_row(
        row=_dart_row(), raw_dir="/tmp/raw", session=None, api_key="K",
        downloader=downloader, timeout=45,
    )
    assert captured["args"] == ()
    assert set(captured["kwargs"]) == {"session", "url", "path", "headers", "timeout"}


def test_rescue_twse_row_skips_network_when_file_already_on_disk(tmp_path):
    """Restart must not re-download documents already acquired.

    Metadata is checkpointed infrequently (the CSV is 237 MB and is rewritten
    whole), so a crash can leave files on disk whose rows still show an empty
    local_path. Those must be adopted, not re-fetched.
    """
    row = _twse_row()
    raw_dir = str(tmp_path)
    existing = build_twse_local_path(raw_dir, row)
    import os
    os.makedirs(os.path.dirname(existing), exist_ok=True)
    with open(existing, "wb") as fout:
        fout.write(b"%PDF-1.4 already here")

    def resolver(session, filing, timeout):  # pragma: no cover
        raise AssertionError("must not resolve when file already exists")

    def downloader(**kwargs):  # pragma: no cover
        raise AssertionError("must not download when file already exists")

    result = rescue_twse_row(
        row=row, raw_dir=raw_dir, session=None,
        resolver=resolver, downloader=downloader, timeout=30,
    )
    assert result.ok is True
    assert result.skipped is True
    assert result.local_path == existing


def test_rescue_twse_row_ignores_zero_byte_leftover(tmp_path):
    """A 0-byte file is a failed write, not an acquired document."""
    row = _twse_row()
    raw_dir = str(tmp_path)
    stub = build_twse_local_path(raw_dir, row)
    import os
    os.makedirs(os.path.dirname(stub), exist_ok=True)
    open(stub, "wb").close()

    called = []

    def resolver(session, filing, timeout):
        called.append("resolved")
        return "https://doc.twse.com.tw/pdf/fresh.pdf"

    def downloader(**kwargs):
        return True

    result = rescue_twse_row(
        row=row, raw_dir=raw_dir, session=None,
        resolver=resolver, downloader=downloader, timeout=30,
    )
    assert called == ["resolved"], "0-byte leftover must not count as acquired"
    assert result.skipped is False


def test_rescue_twse_row_records_failure_without_local_path():
    row = _twse_row()

    def resolver(session, filing, timeout):
        return "https://doc.twse.com.tw/pdf/fresh.pdf"

    def downloader(session, url, path, headers, timeout):  # noqa: keyword-called
        return False

    result = rescue_twse_row(
        row=row, raw_dir="/tmp/raw", session=None,
        resolver=resolver, downloader=downloader, timeout=30,
    )
    assert result.ok is False
    assert result.local_path == ""
    assert result.error


def test_rescue_twse_row_handles_unresolvable_link():
    row = _twse_row()

    def resolver(session, filing, timeout):
        return ""

    def downloader(session, url, path, headers, timeout):  # noqa: keyword-called  # pragma: no cover
        raise AssertionError("must not download when link is unresolved")

    result = rescue_twse_row(
        row=row, raw_dir="/tmp/raw", session=None,
        resolver=resolver, downloader=downloader, timeout=30,
    )
    assert result.ok is False
    assert "missing generated PDF URL" in result.error


def test_twse_filing_from_row_reconstructs_listing_payload():
    filing = twse_filing_from_row(_twse_row())
    assert filing["company_code"] == "3372"
    assert filing["filename"] == "2007_3372_20080506F04.pdf"
    assert filing["kind"] == "F"


def test_build_twse_local_path_matches_original_download_convention():
    row = _twse_row()
    path = build_twse_local_path("/raw", row)
    assert "/raw/TWSE_REPORTS/20080430/" in path
    assert path.endswith("2007_3372_20080506F04.pdf")


# --- DART ----------------------------------------------------------------


def test_dart_document_url_carries_key_and_receipt():
    url = dart_document_url("20080125000241", api_key="KEY123")
    assert "rcept_no=20080125000241" in url
    assert "crtfc_key=KEY123" in url


def test_rescue_dart_row_writes_zip_and_counts_one_call():
    row = _dart_row()
    calls = []

    def downloader(session, url, path, headers, timeout):  # noqa: keyword-called
        calls.append(url)
        return True

    result = rescue_dart_row(
        row=row, raw_dir="/tmp/raw", session=None, api_key="KEY123",
        downloader=downloader, timeout=30,
    )
    assert result.ok is True
    assert result.calls_used == 1
    assert result.local_path.endswith(".zip")


def test_quota_exhausted_respects_dart_daily_cap():
    assert quota_exhausted(DAILY_CALL_CAP_DART - 1, DAILY_CALL_CAP_DART) is False
    assert quota_exhausted(DAILY_CALL_CAP_DART, DAILY_CALL_CAP_DART) is True


# --- state / resume ------------------------------------------------------


# --- job tracker reporting -----------------------------------------------


def test_progress_details_report_real_acquisition_numbers():
    """The tracker must show documents acquired, not rows scanned."""
    state = RescueState(
        market="TWSE_REPORTS", cursor=500, recovered=470, failed=30, calls_used=0
    )
    details = progress_details(state, total=21929, skipped=12)
    blob = " ".join(details)
    assert "470" in blob and "recovered" in blob
    assert "30" in blob and "failed" in blob
    assert "12" in blob and "disk" in blob
    assert "21929" in blob


def test_report_progress_never_raises_when_tracker_unavailable():
    """Tracker failure must not take the rescue down."""

    def exploding_update(**kwargs):
        raise RuntimeError("tracker gone")

    state = RescueState(market="DART", cursor=1, recovered=1)
    # Must not raise.
    report_progress(
        job_id="x", state=state, total=10, skipped=0, updater=exploding_update
    )


def test_report_progress_passes_current_as_documents_acquired():
    captured = {}

    def updater(**kwargs):
        captured.update(kwargs)

    state = RescueState(market="DART", cursor=100, recovered=90, failed=10)
    report_progress(
        job_id="rescue_dart", state=state, total=4750, skipped=0, updater=updater
    )
    assert captured["current"] == 90, "progress is documents acquired, not rows scanned"
    assert captured["total"] == 4750


def test_state_round_trips_and_resumes_cursor(tmp_path):
    path = tmp_path / "state.json"
    save_state(path, RescueState(market="TWSE_REPORTS", cursor=1234, recovered=7, failed=2))
    restored = load_state(path, market="TWSE_REPORTS")
    assert restored.cursor == 1234
    assert restored.recovered == 7
    assert restored.failed == 2


def test_load_state_defaults_to_zero_cursor_when_absent(tmp_path):
    state = load_state(tmp_path / "missing.json", market="DART")
    assert state.cursor == 0
    assert state.recovered == 0
