import io
import json
import zipfile
from pathlib import Path

import dart_raw_xml_v2_job as job
import pyarrow.parquet as pq
import pytest


def _zip_bytes(members):
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as archive:
        for name, contents in members.items():
            archive.writestr(name, contents)
    return payload.getvalue()


def test_extract_zip_payload_routes_audit_attachments_by_exact_scope(monkeypatch):
    calls = []

    def fake_extract(payload, target, *, scope):
        calls.append((payload, scope))
        return {
            "filing_id": target["filing_id"],
            "statement_scope": scope,
            "extraction_method": "raw_dart_audit_report_xml",
        }

    monkeypatch.setattr(job, "extract_raw_xml_row", fake_extract)
    payload = _zip_bytes({
        "20250000000001.xml": b"main",
        "20250000000001_00760.xml": b"parent",
        "20250000000001_00761.xml": b"group",
        "notes.txt": b"ignore",
    })

    document, rows = job.extract_zip_payload(
        payload, {"filing_id": "20250000000001"}
    )

    assert calls == [(b"parent", "standalone"), (b"group", "consolidated")]
    assert [row["statement_scope"] for row in rows] == [
        "standalone", "consolidated",
    ]
    assert document["status"] == "extracted"
    assert document["scope_statuses"] == {
        "standalone": "extracted", "consolidated": "extracted",
    }


def test_extract_zip_payload_records_missing_and_parse_errors(monkeypatch):
    def fail_extract(payload, target, *, scope):
        raise ValueError("bad statement")

    monkeypatch.setattr(job, "extract_raw_xml_row", fail_extract)
    payload = _zip_bytes({"20250000000001_00760.xml": b"parent"})

    document, rows = job.extract_zip_payload(
        payload, {"filing_id": "20250000000001"}
    )

    assert rows == []
    assert document["status"] == "no_raw_structured_data"
    assert document["scope_statuses"] == {
        "standalone": "error", "consolidated": "missing",
    }
    assert "ValueError: bad statement" in document["scope_errors"]["standalone"]


def test_build_raw_manifest_maps_local_paths_to_run_specific_gcs_prefix(tmp_path):
    raw_root = tmp_path / "DART"
    local_path = raw_root / "20250311" / "20250311000487.zip"
    target = {"filing_id": "20250311000487", "local_path": str(local_path)}

    records = job.build_raw_manifest(
        [target], raw_root=raw_root,
        gcs_prefix="gs://bucket/dart/run/raw",
    )

    assert records == [{
        **target,
        "gcs_uri": "gs://bucket/dart/run/raw/20250311/20250311000487.zip",
    }]


def test_pending_shard_requires_both_committed_artifacts(tmp_path):
    (tmp_path / "rows-000000.jsonl").write_text("")
    (tmp_path / "documents-000000.jsonl").write_text("")
    (tmp_path / "rows-000001.jsonl").write_text("")

    assert job.pending_shard_indices(tmp_path, 3) == [1, 2]


def test_merge_rescue_rows_fills_only_api_missing_scopes():
    api_documents = [
        {"filing_id": "A", "status": "no_structured_data", "row_count": 0},
        {"filing_id": "B", "status": "extracted", "row_count": 1},
    ]
    api_rows = [
        {"filing_id": "B", "statement_scope": "consolidated", "source": "api"},
    ]
    raw_documents = [
        {"filing_id": "A", "status": "extracted",
         "scope_statuses": {"consolidated": "extracted", "standalone": "extracted"}},
        {"filing_id": "B", "status": "extracted",
         "scope_statuses": {"consolidated": "extracted", "standalone": "extracted"}},
    ]
    raw_rows = [
        {"filing_id": "A", "statement_scope": "consolidated", "source": "raw"},
        {"filing_id": "A", "statement_scope": "standalone", "source": "raw"},
        {"filing_id": "B", "statement_scope": "consolidated", "source": "raw"},
        {"filing_id": "B", "statement_scope": "standalone", "source": "raw"},
    ]

    documents, rows, audit = job.merge_rescue_rows(
        api_documents, api_rows, raw_documents, raw_rows
    )

    assert [(row["filing_id"], row["statement_scope"], row["source"])
            for row in rows] == [
        ("A", "consolidated", "raw"),
        ("A", "standalone", "raw"),
        ("B", "consolidated", "api"),
        ("B", "standalone", "raw"),
    ]
    by_id = {document["filing_id"]: document for document in documents}
    assert by_id["A"]["status"] == "extracted"
    assert by_id["A"]["row_count"] == 2
    assert by_id["B"]["row_count"] == 2
    assert audit == {
        "api_rows_retained": 1,
        "raw_rows_added": 3,
        "raw_rows_replaced_api": 0,
        "documents_rescued": 1,
        "scopes_rescued": 3,
        "operating_profit_domains_overlaid": 0,
        "tax_domains_overlaid": 0,
        "balance_domains_overlaid": 0,
        "debt_domains_overlaid": 0,
    }


def test_merge_prefers_validated_raw_scope_over_incomplete_api_scope():
    api = {
        "filing_id": "A", "statement_scope": "consolidated",
        "industry_template": "industrial", "screen_eligible": False,
        "operating_profit_closure_status": "fail",
        "tax_identity_status": "fail", "balance_identity_status": "pass",
        "debt_reconciliation_status": "unresolved_not_reported",
        "extraction_method": "dart_api",
    }
    raw = {
        **api,
        "screen_eligible": True,
        "operating_profit_closure_status": "pass",
        "tax_identity_status": "pass",
        "debt_reconciliation_status": "resolved_components",
        "extraction_method": "raw_dart_audit_report_xml",
    }

    _, rows, audit = job.merge_rescue_rows(
        [{"filing_id": "A", "status": "extracted"}], [api],
        [{"filing_id": "A", "status": "extracted"}], [raw],
    )

    assert rows == [raw]
    assert audit["api_rows_retained"] == 0
    assert audit["raw_rows_replaced_api"] == 1


def test_merge_overlays_independently_validated_domain_from_other_source():
    api = {
        "filing_id": "A", "statement_scope": "consolidated",
        "ticker": "100000", "period_end": "2024-12-31", "currency": "KRW",
        "industry_template": "industrial", "screen_eligible": False,
        "operating_profit_closure_status": "fail",
        "tax_identity_status": "pass", "tax_identity_ratio": 0.0,
        "tax_sign_convention": "subtract", "profit_before_tax": 100.0,
        "income_tax_expense": 20.0, "net_income": 80.0,
        "balance_identity_status": "pass",
        "debt_reconciliation_status": "unresolved_not_reported",
        "extraction_method": "dart_api",
    }
    raw = {
        **api,
        "screen_eligible": False,
        "operating_profit_closure_status": "pass",
        "tax_identity_status": "fail", "tax_identity_ratio": 0.5,
        "profit_before_tax": 100.0, "income_tax_expense": 20.0,
        "net_income": 30.0,
        "debt_reconciliation_status": "resolved_components",
        "total_interest_bearing_debt": 10.0,
        "extraction_method": "raw_dart_audit_report_xml",
    }

    _, rows, audit = job.merge_rescue_rows(
        [{"filing_id": "A", "status": "extracted"}], [api],
        [{"filing_id": "A", "status": "extracted"}], [raw],
    )

    assert rows[0]["extraction_method"] == "raw_dart_audit_report_xml"
    assert rows[0]["tax_identity_status"] == "pass"
    assert rows[0]["net_income"] == 80.0
    assert rows[0]["merge_domain_sources"]["tax"] == "dart_api"
    assert audit["tax_domains_overlaid"] == 1


def test_write_merged_candidate_validates_and_hashes_complete_corpus(tmp_path):
    raw_document = {
        "filing_id": "A", "status": "extracted", "row_count": 1,
        "scope_statuses": {"consolidated": "extracted"},
    }
    raw_row = {
        "filing_id": "A", "corp_code": "001", "ticker": "100000",
        "period_end": "2024-12-31", "publication_date": "2025-03-01",
        "statement_scope": "consolidated", "currency": "KRW",
        "industry_template": "industrial", "screen_eligible": True,
        "operating_profit_closure_status": "pass",
        "tax_identity_status": "pass", "balance_identity_status": "pass",
        "debt_reconciliation_status": "resolved_components",
    }

    result = job.write_merged_candidate(
        api_documents=[{"filing_id": "A", "status": "no_structured_data",
                        "row_count": 0}],
        api_rows=[], raw_documents=[raw_document], raw_rows=[raw_row],
        output_dir=tmp_path,
    )

    assert result["normalization_contract_pass"] is True
    assert json.loads((tmp_path / "VALIDATED_COMPLETE.json").read_text())[
        "document_count"
    ] == 1
    assert pq.ParquetFile(tmp_path / "documents.parquet").metadata.num_rows == 1
    assert pq.ParquetFile(
        tmp_path / "canonical_scoped_rows.parquet"
    ).metadata.num_rows == 1
    manifest_names = {
        line.split("  ", 1)[1]
        for line in (tmp_path / "manifest.sha256").read_text().splitlines()
    }
    assert manifest_names == {
        "canonical_scoped_rows.parquet", "documents.parquet",
        "merge_audit.json", "preferred_screening_rows.parquet", "qa_summary.json",
    }


def test_write_merged_candidate_refuses_to_overwrite_validated_result(tmp_path):
    (tmp_path / "VALIDATED_COMPLETE.json").write_text("{}\n")

    with pytest.raises(FileExistsError, match="validated"):
        job.write_merged_candidate([], [], [], [], output_dir=tmp_path)


@pytest.mark.parametrize('currency,period,expected', [('KRW','2024-12-31',True),('USD','2024-12-31',False),('KRW','2023-12-31',False)])
def test_merge_retains_recovered_float_only_for_same_period_and_currency(currency,period,expected):
    api = dict(filing_id='A',statement_scope='consolidated',period_end='2024-12-31',currency='KRW',
               operating_profit_closure_status='pass',tax_identity_status='pass',balance_identity_status='pass',
               debt_reconciliation_status='resolved_components',screen_eligible=True,extraction_method='dart_api')
    raw = {**api,'currency':currency,'period_end':period,'extraction_method':'raw_dart_audit_report_xml',
           'trade_and_other_payables':12000.,'current_contract_liabilities':7000.,
           'field_provenance':{'trade_and_other_payables':[{'label':'매입채무및기타채무','table_index':1}]},
           'raw_zip_member':'A_00761.xml'}
    _,rows,_ = job.merge_rescue_rows([], [api], [], [raw])
    assert ('trade_and_other_payables' in rows[0]) == expected
    if expected:
        assert rows[0]['trade_and_other_payables'] == 12000.
        assert rows[0]['float_source_receipt']['raw_zip_member']=='A_00761.xml'
        assert rows[0]['extraction_method']=='dart_api'


@pytest.mark.parametrize('currency,period', [('USD','2024-12-31'),('KRW','2023-12-31'),(None,'2024-12-31')])
def test_domain_overlay_requires_matching_known_currency_and_period(currency,period):
    api=dict(filing_id='A',statement_scope='consolidated',period_end='2024-12-31',currency='KRW',
             operating_profit_closure_status='pass',tax_identity_status='pass',balance_identity_status='pass',
             debt_reconciliation_status='unresolved_not_reported',extraction_method='dart_api',industry_template='industrial')
    raw={**api,'currency':currency,'period_end':period,'operating_profit_closure_status':'fail',
         'tax_identity_status':'fail','debt_reconciliation_status':'resolved_components',
         'total_interest_bearing_debt':99.,'extraction_method':'raw_dart_audit_report_xml'}
    _,rows,audit=job.merge_rescue_rows([], [api], [], [raw])
    assert rows[0]['debt_reconciliation_status']=='unresolved_not_reported'
    assert rows[0].get('total_interest_bearing_debt') is None
    assert audit['debt_domains_overlaid']==0
