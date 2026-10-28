"""Artifact-level tests for the India BSE residual repair overlay."""

import hashlib
import json

import pyarrow.parquet as pq

from scripts.in_bse_stage02_repair import (
    _write_parquet,
    build_blind_probe_manifest,
    build_unresolved_primary_probe_manifest,
    build_partial_repair_checkpoint,
    build_overlay_outputs,
    build_repair_manifest,
)


def _good_row(filing_id, ticker):
    return {
        "filing_id": filing_id,
        "ticker": ticker,
        "publication_date": "2025-06-01",
        "period_end": "2025-03-31",
        "statement_scope": "standalone",
        "industry_template": "industrial",
        "operating_profit_closure_status": "pass",
        "tax_identity_status": "pass",
        "total_borrowings": 10.0,
        "currency": "INR",
        "unit_scale": 1,
        "screen_eligible": True,
    }


def test_build_repair_manifest_and_validated_overlay(tmp_path):
    baseline = tmp_path / "baseline"
    repair = tmp_path / "repair"
    baseline.mkdir()
    repair.mkdir()
    _write_parquet(
        baseline / "documents.parquet",
        [
            {"filing_id": "a", "source_year": "2025", "status": "extracted", "row_count": 1},
            {"filing_id": "b", "source_year": "2025", "status": "unresolved", "row_count": 0},
        ],
    )
    _write_parquet(baseline / "all_statement_rows.parquet", [_good_row("a", "AAA")])
    _write_parquet(
        repair / "documents.parquet",
        [{"filing_id": "b", "source_year": "2025", "status": "extracted", "row_count": 1}],
    )
    _write_parquet(repair / "all_statement_rows.parquet", [_good_row("b", "BBB")])
    full_manifest = tmp_path / "full.jsonl"
    full_manifest.write_text(
        "".join(
            json.dumps({"filing_id": filing_id, "expected_pdf_bytes": 100}) + "\n"
            for filing_id in ("a", "b")
        )
    )
    residual_manifest = tmp_path / "repair.jsonl"
    audit_path = tmp_path / "selection.json"

    selection = build_repair_manifest(
        baseline, full_manifest, residual_manifest, audit_path
    )

    assert selection["document_count"] == 1
    assert json.loads(residual_manifest.read_text())["filing_id"] == "b"

    output = tmp_path / "output"
    qa = build_overlay_outputs(
        baseline, repair, output, run_id="test-repair-overlay"
    )

    assert qa["normalization_contract_pass"] is True
    assert qa["document_extraction_rate"] == 1.0
    assert pq.read_table(output / "documents.parquet").num_rows == 2
    assert pq.read_table(output / "all_statement_rows.parquet").num_rows == 2
    assert (output / "VALIDATED_COMPLETE.json").exists()
    for line in (output / "manifest.sha256").read_text().splitlines():
        expected, name = line.split("  ", 1)
        observed = hashlib.sha256((output / name).read_bytes()).hexdigest()
        assert observed == expected


def test_partial_checkpoint_materializes_committed_pairs_and_missing_manifest(tmp_path):
    shard_dir = tmp_path / "shards"
    shard_dir.mkdir()
    (shard_dir / "documents-000000.jsonl").write_text(
        "".join(
            json.dumps(record) + "\n"
            for record in (
                {"filing_id": "a", "source_year": "2025", "status": "extracted", "row_count": 1},
                {"filing_id": "b", "source_year": "2025", "status": "unresolved", "row_count": 0},
            )
        )
    )
    (shard_dir / "rows-000000.jsonl").write_text(
        json.dumps(_good_row("a", "AAA")) + "\n"
    )
    source_manifest = tmp_path / "source.jsonl"
    source_manifest.write_text(
        "".join(
            json.dumps({"filing_id": filing_id, "expected_pdf_bytes": 100}) + "\n"
            for filing_id in ("a", "b", "c")
        )
    )
    output = tmp_path / "partial-final"
    missing_manifest = tmp_path / "missing.jsonl"

    audit = build_partial_repair_checkpoint(
        shard_dir,
        source_manifest,
        output,
        missing_manifest,
        run_id="partial-test",
    )

    assert audit["completed_shards"] == 1
    assert audit["completed_documents"] == 2
    assert audit["missing_documents"] == 1
    assert json.loads(missing_manifest.read_text())["filing_id"] == "c"
    assert pq.read_table(output / "documents.parquet").num_rows == 2
    assert pq.read_table(output / "all_statement_rows.parquet").num_rows == 1
    assert (output / "PARTIAL_CHECKPOINT.json").exists()
    assert len((output / "source_shards.sha256").read_text().splitlines()) == 2


def test_blind_probe_manifest_is_stratified_unique_and_excludes_prior_ids(tmp_path):
    overlay = tmp_path / "overlay"
    overlay.mkdir()
    documents = [
        {"filing_id": "u1", "status": "unresolved"},
        {"filing_id": "u2", "status": "unresolved"},
        {"filing_id": "t1", "status": "extracted"},
        {"filing_id": "t2", "status": "extracted"},
        {"filing_id": "o1", "status": "extracted"},
        {"filing_id": "d1", "status": "extracted"},
        {"filing_id": "excluded", "status": "unresolved"},
    ]
    rows = [
        {"filing_id": "t1", "tax_identity_status": "fail", "industry_template": "financial_services"},
        {"filing_id": "t2", "tax_identity_status": "fail", "industry_template": "industrial"},
        {
            "filing_id": "o1",
            "tax_identity_status": "pass",
            "industry_template": "industrial",
            "operating_profit_closure_status": "fail",
            "total_borrowings": 1.0,
        },
        {
            "filing_id": "d1",
            "tax_identity_status": "pass",
            "industry_template": "industrial",
            "operating_profit_closure_status": "pass",
            "total_borrowings": None,
        },
        {"filing_id": "excluded", "tax_identity_status": "fail", "industry_template": "industrial"},
    ]
    _write_parquet(overlay / "documents.parquet", documents)
    _write_parquet(overlay / "all_statement_rows.parquet", rows)
    full_manifest = tmp_path / "full.jsonl"
    full_manifest.write_text(
        "".join(
            json.dumps({"filing_id": filing_id}) + "\n"
            for filing_id in reversed([document["filing_id"] for document in documents])
        )
    )
    exclusion = tmp_path / "excluded.jsonl"
    exclusion.write_text(json.dumps({"filing_id": "excluded"}) + "\n")
    output = tmp_path / "probe.jsonl"
    audit_path = tmp_path / "probe.json"

    audit = build_blind_probe_manifest(
        overlay,
        full_manifest,
        [exclusion],
        output,
        audit_path,
        quotas={"unresolved": 1, "tax": 1, "operating_profit": 1, "debt": 1},
        seed="blind-test",
    )

    selected = [json.loads(line)["filing_id"] for line in output.read_text().splitlines()]
    assert len(selected) == 4
    assert len(set(selected)) == 4
    assert "excluded" not in selected
    assert audit["selected_by_category"] == {
        "debt": 1,
        "operating_profit": 1,
        "tax": 1,
        "unresolved": 1,
    }


def test_unresolved_primary_probe_requires_detected_primary_statement_pages(tmp_path):
    documents = tmp_path / "documents.parquet"
    _write_parquet(
        documents,
        [
            {
                "filing_id": "primary-a",
                "status": "unresolved",
                "ocr_primary_statement_pages": [10, 11],
            },
            {
                "filing_id": "primary-b",
                "status": "unresolved",
                "ocr_primary_statement_pages": [20],
            },
            {
                "filing_id": "no-primary",
                "status": "unresolved",
                "ocr_primary_statement_pages": [],
            },
            {
                "filing_id": "already-extracted",
                "status": "extracted",
                "ocr_primary_statement_pages": [30],
            },
        ],
    )
    full_manifest = tmp_path / "full.jsonl"
    full_manifest.write_text(
        "".join(
            json.dumps({"filing_id": filing_id}) + "\n"
            for filing_id in (
                "primary-a",
                "primary-b",
                "no-primary",
                "already-extracted",
            )
        )
    )
    exclusion = tmp_path / "exclude.jsonl"
    exclusion.write_text(json.dumps({"filing_id": "primary-b"}) + "\n")
    output = tmp_path / "probe.jsonl"
    audit_path = tmp_path / "probe.json"

    audit = build_unresolved_primary_probe_manifest(
        documents,
        full_manifest,
        [exclusion],
        output,
        audit_path,
        sample_size=1,
        seed="primary-test",
    )

    assert json.loads(output.read_text())["filing_id"] == "primary-a"
    assert audit["candidate_documents"] == 2
    assert audit["eligible_documents"] == 1
    assert audit["selected_documents"] == 1
