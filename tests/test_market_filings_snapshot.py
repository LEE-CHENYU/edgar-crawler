"""Tests for MARKET_FILINGS status reporting.

The queue defines "complete" on discovery progress, so a market can report
complete=true while most of its documents were never downloaded (TWSE sat at
16% for four weeks). The status surface must show acquisition coverage, not
just row counts.
"""

import csv

from scripts.market_filings_snapshot import (
    coverage_rows,
    metadata_counts,
    render_console,
    render_markdown,
)


METADATA_FIELDS = [
    "market", "filing_id", "filing_date", "company_name", "stock_code",
    "title", "category", "source_url", "document_url", "local_path",
    "downloaded_at", "raw_metadata",
]


def _write_metadata(path, rows):
    with open(path, "w", newline="", encoding="utf-8") as fout:
        writer = csv.DictWriter(fout, fieldnames=METADATA_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in METADATA_FIELDS})


def _row(market, local_path=""):
    return {"market": market, "filing_id": "x", "local_path": local_path}


def test_metadata_counts_reports_acquired_documents(tmp_path):
    path = tmp_path / "meta.csv"
    _write_metadata(path, [
        _row("TWSE_REPORTS", "/raw/a.pdf"),
        _row("TWSE_REPORTS", ""),
        _row("TWSE_REPORTS", ""),
        _row("ASX", "/raw/b.pdf"),
    ])
    counts = metadata_counts(path)
    assert counts["total_rows"] == 4
    assert counts["total_acquired"] == 2
    assert counts["acquired_by_market"]["TWSE_REPORTS"] == 1
    assert counts["acquired_by_market"]["ASX"] == 1


def test_metadata_counts_treats_whitespace_local_path_as_not_acquired(tmp_path):
    path = tmp_path / "meta.csv"
    _write_metadata(path, [_row("ASX", "   ")])
    assert metadata_counts(path)["total_acquired"] == 0


def test_coverage_rows_sorts_worst_gap_first():
    counts = {
        "by_market": {"ASX": 100, "TWSE_REPORTS": 1000, "PSE_EDGE": 10},
        "acquired_by_market": {"ASX": 98, "TWSE_REPORTS": 160, "PSE_EDGE": 10},
    }
    rows = coverage_rows(counts)
    assert rows[0]["market"] == "TWSE_REPORTS", "largest missing count first"
    assert rows[0]["missing"] == 840
    assert round(rows[0]["pct"], 1) == 16.0
    assert rows[-1]["market"] == "PSE_EDGE"
    assert rows[-1]["missing"] == 0


def test_coverage_rows_handles_market_with_no_acquired_entry():
    counts = {"by_market": {"NEW": 5}, "acquired_by_market": {}}
    rows = coverage_rows(counts)
    assert rows[0]["missing"] == 5
    assert rows[0]["pct"] == 0.0


def test_render_markdown_includes_coverage_section_with_gap():
    snapshot = {
        "captured_at": "2026-08-14T00:00:00Z",
        "host": "h", "data_root": "/d", "jobs": [],
        "counts": {
            "metadata": {
                "total_rows": 1100, "total_acquired": 260,
                "by_market": {"TWSE_REPORTS": 1000, "ASX": 100},
                "acquired_by_market": {"TWSE_REPORTS": 160, "ASX": 100},
            },
            "raw_files": {"total_files": 260, "by_market": {}},
        },
        "active_screens": [], "notes": [],
    }
    out = render_markdown(snapshot)
    assert "Document Coverage" in out
    assert "16.0%" in out
    assert "840" in out, "missing count must be visible"


def test_render_console_warns_when_a_market_is_incomplete():
    snapshot = {
        "captured_at": "2026-08-14T00:00:00Z",
        "jobs": [],
        "counts": {
            "metadata": {
                "total_rows": 1100, "total_acquired": 260,
                "by_market": {"TWSE_REPORTS": 1000, "ASX": 100},
                "acquired_by_market": {"TWSE_REPORTS": 160, "ASX": 100},
            },
            "raw_files": {"total_files": 260},
        },
    }
    out = render_console(snapshot)
    assert "TWSE_REPORTS" in out
    assert "840" in out


def test_render_console_stays_quiet_when_everything_acquired():
    snapshot = {
        "captured_at": "2026-08-14T00:00:00Z",
        "jobs": [],
        "counts": {
            "metadata": {
                "total_rows": 100, "total_acquired": 100,
                "by_market": {"ASX": 100},
                "acquired_by_market": {"ASX": 100},
            },
            "raw_files": {"total_files": 100},
        },
    }
    out = render_console(snapshot)
    assert "missing documents" not in out
