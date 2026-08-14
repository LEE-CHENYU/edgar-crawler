import csv
import json
import zipfile
from pathlib import Path

from dart_financials_pipeline import (
    build_manifest,
    extract_facts_from_text,
    process_filing,
    read_dart_zip_text,
)


def test_build_manifest_keeps_existing_dart_local_paths(tmp_path):
    zip_path = tmp_path / "doc.zip"
    zip_path.write_bytes(b"PK\x05\x06" + b"\x00" * 18)
    rows = [
        {
            "market": "DART",
            "filing_id": "20240306000534",
            "filing_date": "2024-03-06",
            "company_name": "효성화학",
            "stock_code": "298000",
            "title": "사업보고서 (2023.12)",
            "category": "Y",
            "document_url": "https://opendart.fss.or.kr/api/document.xml?rcept_no=20240306000534",
            "local_path": str(zip_path),
            "source_url": "https://opendart.fss.or.kr/api/list.json",
            "raw_metadata": '{"corp_code": "01316236"}',
        },
        {
            "market": "DART",
            "filing_id": "missing",
            "local_path": str(tmp_path / "missing.zip"),
        },
        {
            "market": "HKEX",
            "filing_id": "hk",
            "local_path": str(zip_path),
        },
    ]

    manifest = build_manifest(rows)

    assert [row["filing_id"] for row in manifest] == ["20240306000534"]
    assert manifest[0]["market"] == "DART"


def test_read_dart_zip_text_decodes_xml_members(tmp_path):
    zip_path = tmp_path / "dart.zip"
    xml = (
        '<?xml version="1.0" encoding="utf-8"?>'
        "<DOCUMENT><DOCUMENT-NAME>사업보고서</DOCUMENT-NAME>"
        "<SECTION-1><TITLE>재무제표</TITLE><P>매출액 1,234</P></SECTION-1>"
        "</DOCUMENT>"
    )
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("doc.xml", xml.encode("utf-8"))

    result = read_dart_zip_text(zip_path)

    assert result.member_count == 1
    assert "사업보고서" in result.text
    assert "매출액 1,234" in result.text


def test_read_dart_zip_text_does_not_duplicate_nested_text(tmp_path):
    zip_path = tmp_path / "dart.zip"
    xml = (
        '<?xml version="1.0" encoding="utf-8"?>'
        "<DOCUMENT><A><B>매출액 1,234</B></A></DOCUMENT>"
    )
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("doc.xml", xml.encode("utf-8"))

    result = read_dart_zip_text(zip_path)

    assert result.text.count("매출액 1,234") == 1


def test_extract_facts_from_korean_financial_lines():
    text = "\n".join(
        [
            "제 12 기 2024 제 11 기 2023",
            "매출액 1,234,567 1,111,111",
            "영업이익 (12,345) 23,456",
            "자산총계 9,999,999 8,888,888",
            "영업활동으로 인한 현금흐름 345,678 234,567",
        ]
    )

    facts, summary = extract_facts_from_text(text, {"filing_id": "r1", "stock_code": "005930"})

    metrics = [fact["metric"] for fact in facts]
    assert metrics == [
        "revenue",
        "revenue",
        "operating_profit",
        "operating_profit",
        "total_assets",
        "total_assets",
        "operating_cash_flow",
        "operating_cash_flow",
    ]
    assert facts[2]["numeric_value"] == -12345
    assert summary["metrics"]["revenue"] == 2


def test_process_filing_writes_text_facts_summary_and_event(tmp_path):
    zip_path = tmp_path / "dart.zip"
    xml = (
        '<?xml version="1.0" encoding="utf-8"?>'
        "<DOCUMENT><DOCUMENT-NAME>사업보고서</DOCUMENT-NAME>"
        "<SECTION-1><TITLE>재무제표</TITLE>"
        "<P>제 12 기 2024 제 11 기 2023</P>"
        "<P>매출액 1,234,567 1,111,111</P>"
        "</SECTION-1></DOCUMENT>"
    )
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("doc.xml", xml.encode("utf-8"))
    row = {
        "filing_id": "20240306000534",
        "filing_date": "2024-03-06",
        "company_name": "효성화학",
        "stock_code": "298000",
        "title": "사업보고서 (2023.12)",
        "category": "Y",
        "document_url": "",
        "local_path": str(zip_path),
        "raw_metadata": "{}",
    }

    event = process_filing(row, tmp_path / "out", gzip_text=True, gzip_facts=True)

    assert event["status"] == "ok"
    assert event["fact_count"] == 2
    assert Path(event["summary_path"]).exists()
    assert Path(event["facts_path"]).exists()
    assert Path(event["text_path"]).exists()
    summary = json.loads(Path(event["summary_path"]).read_text(encoding="utf-8"))
    assert summary["text_chars"] > 0
    assert summary["metrics"]["revenue"] == 2
