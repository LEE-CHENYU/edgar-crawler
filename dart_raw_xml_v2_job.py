#!/usr/bin/env python3
"""Batch and merge utilities for DART raw audit-report XML rescue.

The raw DART ZIP is an official source package.  Attachments 00761 and 00760
are the consolidated and separate audit reports respectively; their statement
tables are parsed directly, without OCR or an LLM.  The stronger validated row
is the merge base when sources overlap, and independently validated statement
domains can be overlaid without admitting an untested value group.
"""
from __future__ import annotations

import io
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
from typing import Any, Iterable
import zipfile

from dart_raw_xml_v2 import extract_raw_xml_row


SCOPE_MEMBERS = (
    ("standalone", "_00760.xml"),
    ("consolidated", "_00761.xml"),
)


def extract_zip_payload(
    payload: bytes, target: dict[str, Any]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Extract the two exact audit-report attachments from one official ZIP."""
    rows: list[dict[str, Any]] = []
    statuses: dict[str, str] = {}
    errors: dict[str, str] = {}
    members_used: dict[str, str] = {}
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        member_names = archive.namelist()
        for scope, suffix in SCOPE_MEMBERS:
            candidates = sorted(
                name for name in member_names
                if PurePosixPath(name).name.lower().endswith(suffix)
            )
            if not candidates:
                statuses[scope] = "missing"
                continue
            member = candidates[0]
            members_used[scope] = member
            try:
                row = extract_raw_xml_row(
                    archive.read(member), target, scope=scope
                )
                row["raw_zip_member"] = member
                rows.append(row)
                statuses[scope] = "extracted"
            except Exception as exc:
                statuses[scope] = "error"
                errors[scope] = f"{type(exc).__name__}: {exc}"
    document = {
        **target,
        "status": "extracted" if rows else "no_raw_structured_data",
        "extraction_method": "raw_dart_audit_report_xml",
        "scope_statuses": statuses,
        "scope_errors": errors,
        "scope_members": members_used,
        "row_count": len(rows),
    }
    return document, rows


def build_raw_manifest(
    targets: Iterable[dict[str, Any]],
    *,
    raw_root: Path,
    gcs_prefix: str,
) -> list[dict[str, Any]]:
    """Attach the GCS URI created by a recursive upload of ``raw_root``."""
    root = Path(raw_root).resolve()
    prefix = gcs_prefix.rstrip("/")
    records: list[dict[str, Any]] = []
    for target in targets:
        local_path = Path(str(target.get("local_path") or "")).resolve()
        try:
            relative = local_path.relative_to(root)
        except ValueError as exc:
            raise ValueError(
                f"DART raw path is outside configured root: {local_path}"
            ) from exc
        records.append({
            **target,
            "gcs_uri": f"{prefix}/{relative.as_posix()}",
        })
    return records


def pending_shard_indices(shard_dir: Path, shard_count: int) -> list[int]:
    """A shard is resumable only after both rows and documents are committed."""
    return [
        index for index in range(shard_count)
        if not (
            (Path(shard_dir) / f"rows-{index:06d}.jsonl").exists()
            and (Path(shard_dir) / f"documents-{index:06d}.jsonl").exists()
        )
    ]


def merge_rescue_rows(
    api_documents: Iterable[dict[str, Any]],
    api_rows: Iterable[dict[str, Any]],
    raw_documents: Iterable[dict[str, Any]],
    raw_rows: Iterable[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, int]]:
    """Choose the better row, then overlay independently validated domains.

    The API and audit-report XML describe the same filing and scope, but each
    can be stronger for a different statement domain.  Selecting one complete
    row discarded valid tax or debt evidence from the other source.  Domain
    overlays copy the complete identity group only when the alternate source
    independently passes that group's validation; unvalidated values never
    replace a validated group.
    """
    api_documents = list(api_documents)
    api_rows = list(api_rows)
    raw_documents = list(raw_documents)
    raw_rows = list(raw_rows)
    documents = {
        str(item["filing_id"]): dict(item) for item in api_documents
    }
    raw_document_by_id = {
        str(item["filing_id"]): item for item in raw_documents
    }
    rows_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for row in api_rows:
        key = (str(row["filing_id"]), str(row["statement_scope"]))
        rows_by_key[key] = dict(row)

    def row_quality(row: dict[str, Any]) -> tuple[int, int, int, int]:
        statuses = (
            row.get("operating_profit_closure_status"),
            row.get("tax_identity_status"),
            row.get("balance_identity_status"),
        )
        passed = sum(status in {"pass", "not_applicable"} for status in statuses)
        failed = sum(status == "fail" for status in statuses)
        debt_ok = (
            row.get("industry_template") == "financial_services"
            or row.get("debt_reconciliation_status") == "resolved_components"
        )
        coverage = sum(
            row.get(field) is not None for field in (
                "revenue", "operating_profit", "profit_before_tax",
                "income_tax_expense", "net_income", "cash_and_equivalents",
                "total_interest_bearing_debt", "total_assets",
                "total_liabilities", "total_equity",
            )
        )
        return passed + int(debt_ok), -failed, int(bool(row.get("screen_eligible"))), coverage

    raw_rows_added = 0
    raw_rows_replaced_api = 0
    for row in raw_rows:
        key = (str(row["filing_id"]), str(row["statement_scope"]))
        if key not in rows_by_key:
            rows_by_key[key] = dict(row)
            raw_rows_added += 1
        elif row_quality(row) > row_quality(rows_by_key[key]):
            rows_by_key[key] = dict(row)
            raw_rows_replaced_api += 1

    domain_specs = {
        "operating_profit": {
            "status": "operating_profit_closure_status",
            "good": {"pass", "not_applicable"},
            "fields": (
                "gross_profit", "selling_general_administrative_expenses",
                "operating_profit", "operating_profit_pbt_bridge",
                "operating_profit_bridge_components",
                "operating_profit_closure_status",
                "operating_profit_closure_ratio",
            ),
        },
        "tax": {
            "status": "tax_identity_status",
            "good": {"pass"},
            "fields": (
                "profit_before_tax", "income_tax_expense", "net_income",
                "tax_identity_status", "tax_identity_ratio",
                "tax_sign_convention",
            ),
        },
        "balance": {
            "status": "balance_identity_status",
            "good": {"pass"},
            "fields": (
                "total_assets", "total_liabilities", "total_equity",
                "balance_identity_status", "balance_identity_ratio",
            ),
        },
        "debt": {
            "status": "debt_reconciliation_status",
            "good": {"resolved_components"},
            "fields": (
                "current_borrowings", "non_current_borrowings",
                "total_borrowings", "current_lease_liabilities",
                "non_current_lease_liabilities", "total_lease_liabilities",
                "total_interest_bearing_debt", "generic_borrowings",
                "generic_lease_liabilities", "debt_reconciliation_status",
                "debt_component_provenance",
            ),
        },
    }
    numeric_fields = {
        "gross_profit", "selling_general_administrative_expenses",
        "operating_profit", "profit_before_tax", "income_tax_expense",
        "net_income", "total_assets", "total_liabilities", "total_equity",
        "current_borrowings", "non_current_borrowings", "total_borrowings",
        "current_lease_liabilities", "non_current_lease_liabilities",
        "total_lease_liabilities", "total_interest_bearing_debt",
    }
    candidates_by_key: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in [*api_rows, *raw_rows]:
        key = (str(row["filing_id"]), str(row["statement_scope"]))
        candidates_by_key.setdefault(key, []).append(row)
    domain_overlay_counts = {name: 0 for name in domain_specs}
    for key, base in rows_by_key.items():
        overlaid_any = False
        domain_sources: dict[str, str] = {}
        existing_sources = base.get("merge_domain_sources")
        if isinstance(existing_sources, dict):
            domain_sources.update(existing_sources)
        for domain, spec in domain_specs.items():
            status_field = str(spec["status"])
            good_values = set(spec["good"])
            if base.get(status_field) in good_values:
                continue
            alternate = next(
                (
                    row for row in candidates_by_key.get(key, [])
                    if row.get(status_field) in good_values
                    and base.get("period_end") and base.get("currency")
                    and row.get("period_end") == base.get("period_end")
                    and row.get("currency") == base.get("currency")
                ),
                None,
            )
            if alternate is None:
                continue
            for field in spec["fields"]:
                base[field] = alternate.get(field)
                if field in numeric_fields:
                    krw_field = f"{field}_krw"
                    base[krw_field] = alternate.get(
                        krw_field,
                        alternate.get(field)
                        if alternate.get("currency") == "KRW" else None,
                    )
            domain_sources[domain] = str(
                alternate.get("extraction_method") or "dart_api"
            )
            domain_overlay_counts[domain] += 1
            overlaid_any = True
        if domain_sources:
            base["merge_domain_sources"] = domain_sources
        if not overlaid_any:
            continue
        debt_required = (
            base.get("industry_template") != "industrial"
            or base.get("debt_reconciliation_status") == "resolved_components"
        )
        base["screen_eligible"] = bool(
            base.get("filing_id")
            and base.get("ticker")
            and base.get("period_end")
            and base.get("statement_scope") in {"consolidated", "standalone"}
            and base.get("currency") == "KRW"
            and base.get("operating_profit_closure_status")
            in {"pass", "not_applicable"}
            and base.get("tax_identity_status") == "pass"
            and base.get("balance_identity_status") == "pass"
            and debt_required
        )

    # Retain newly extracted liability evidence even when the API row wins
    # overall. Do not mix periods, currencies or already populated float domains.
    float_fields = ("trade_and_other_payables", "trade_and_other_current_payables",
                    "trade_and_other_non_current_payables", "contract_liabilities",
                    "current_contract_liabilities", "non_current_contract_liabilities")
    for key, base in rows_by_key.items():
        if any(base.get(field) is not None for field in float_fields):
            continue
        for alternate in candidates_by_key.get(key, []):
            if (alternate.get("extraction_method") != "raw_dart_audit_report_xml"
                    or alternate.get("balance_identity_status") != "pass"
                    or not base.get("period_end") or not base.get("currency")
                    or alternate.get("period_end") != base.get("period_end")
                    or alternate.get("currency") != base.get("currency")):
                continue
            provenance = alternate.get("field_provenance")
            if not isinstance(provenance, dict):
                continue
            recovered = {field: alternate[field] for field in float_fields
                         if alternate.get(field) is not None and provenance.get(field)}
            if not recovered:
                continue
            base.update(recovered)
            base["float_source_receipt"] = {
                "filing_id": key[0], "statement_scope": key[1],
                "period_end": alternate["period_end"], "currency": alternate["currency"],
                "raw_zip_member": alternate.get("raw_zip_member"),
                "field_provenance": {field: provenance[field] for field in recovered},
                "status": "partial_source_fields_not_complete_float",
            }
            break

    documents_rescued = 0
    for filing_id in sorted(set(documents) | set(raw_document_by_id)):
        raw_document = raw_document_by_id.get(filing_id, {})
        if filing_id not in documents:
            documents[filing_id] = dict(raw_document)
        document = documents[filing_id]
        old_status = document.get("status")
        filing_rows = [
            row for (row_filing_id, _), row in rows_by_key.items()
            if row_filing_id == filing_id
        ]
        if filing_rows:
            document["status"] = "extracted"
        document["row_count"] = len(filing_rows)
        document["raw_rescue_status"] = raw_document.get("status", "not_available")
        document["raw_scope_statuses"] = raw_document.get("scope_statuses", {})
        document["raw_scope_errors"] = raw_document.get("scope_errors", {})
        document["raw_scope_members"] = raw_document.get("scope_members", {})
        if old_status != "extracted" and document.get("status") == "extracted":
            documents_rescued += 1

    merged_rows = [rows_by_key[key] for key in sorted(rows_by_key)]
    merged_documents = [documents[key] for key in sorted(documents)]
    return merged_documents, merged_rows, {
        "api_rows_retained": len(api_rows) - raw_rows_replaced_api,
        "raw_rows_added": raw_rows_added,
        "raw_rows_replaced_api": raw_rows_replaced_api,
        "documents_rescued": documents_rescued,
        "scopes_rescued": raw_rows_added + raw_rows_replaced_api,
        **{
            f"{domain}_domains_overlaid": count
            for domain, count in domain_overlay_counts.items()
        },
    }


def _atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def _parquet_safe(record: dict[str, Any]) -> dict[str, Any]:
    return {
        key: (
            json.dumps(value, ensure_ascii=False, sort_keys=True)
            if isinstance(value, (dict, list)) else value
        )
        for key, value in record.items()
    }


def _write_parquet(path: Path, records: list[dict[str, Any]]) -> int:
    import pandas as pd
    import pyarrow.parquet as pq

    temporary = path.with_suffix(path.suffix + ".tmp")
    safe = [_parquet_safe(record) for record in records]
    frame = pd.DataFrame(safe)
    if frame.empty and "filing_id" not in frame:
        frame = pd.DataFrame({"filing_id": pd.Series(dtype="string")})
    frame.to_parquet(temporary, index=False)
    observed = pq.ParquetFile(temporary).metadata.num_rows
    if observed != len(records):
        raise RuntimeError(f"Parquet row mismatch: {observed} != {len(records)}")
    os.replace(temporary, path)
    return observed


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_merged_candidate(
    api_documents: Iterable[dict[str, Any]],
    api_rows: Iterable[dict[str, Any]],
    raw_documents: Iterable[dict[str, Any]],
    raw_rows: Iterable[dict[str, Any]],
    *,
    output_dir: Path,
    threshold: float = 0.95,
) -> dict[str, Any]:
    """Write an immutable-until-validated API-plus-raw DART candidate."""
    from dart_stage02_v2_job import canonical_rows, summarize_run

    output_dir = Path(output_dir)
    if (output_dir / "VALIDATED_COMPLETE.json").exists():
        raise FileExistsError(
            f"refusing to overwrite validated DART candidate: {output_dir}"
        )
    output_dir.mkdir(parents=True, exist_ok=True)
    raw_documents = list(raw_documents)
    documents, merged_rows, audit = merge_rescue_rows(
        api_documents, api_rows, raw_documents, raw_rows
    )
    scoped, preferred = canonical_rows(merged_rows)
    duplicate_keys = [
        (
            str(row.get("corp_code") or ""),
            str(row.get("period_end") or ""),
            str(row.get("statement_scope") or ""),
        )
        for row in scoped
    ]
    duplicate_count = len(duplicate_keys) - len(set(duplicate_keys))
    qa = summarize_run(documents, scoped, threshold=threshold)
    target_count = len({str(item["filing_id"]) for item in raw_documents})
    qa.update({
        "target_count": target_count,
        "document_count": len(documents),
        "canonical_scoped_row_count": len(scoped),
        "preferred_screening_row_count": len(preferred),
        "duplicate_company_period_scope_count": duplicate_count,
        "merge_audit": audit,
    })
    complete = bool(
        qa["normalization_contract_pass"]
        and target_count > 0
        and len(documents) == target_count
        and duplicate_count == 0
    )

    parquet_counts = {
        "documents.parquet": _write_parquet(
            output_dir / "documents.parquet", documents
        ),
        "canonical_scoped_rows.parquet": _write_parquet(
            output_dir / "canonical_scoped_rows.parquet", scoped
        ),
        "preferred_screening_rows.parquet": _write_parquet(
            output_dir / "preferred_screening_rows.parquet", preferred
        ),
    }
    qa["parquet_row_counts"] = parquet_counts
    _atomic_text(
        output_dir / "qa_summary.json",
        json.dumps(qa, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    _atomic_text(
        output_dir / "merge_audit.json",
        json.dumps(audit, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    targets = [
        output_dir / "canonical_scoped_rows.parquet",
        output_dir / "documents.parquet",
        output_dir / "merge_audit.json",
        output_dir / "preferred_screening_rows.parquet",
        output_dir / "qa_summary.json",
    ]
    _atomic_text(
        output_dir / "manifest.sizes.tsv",
        "".join(f"{path.name}\t{path.stat().st_size}\n" for path in targets),
    )
    _atomic_text(
        output_dir / "manifest.sha256",
        "".join(f"{_sha256(path)}  {path.name}\n" for path in targets),
    )
    marker = {
        "validated": complete,
        "normalization_contract_pass": qa["normalization_contract_pass"],
        "target_count": target_count,
        "document_count": len(documents),
        "canonical_scoped_row_count": len(scoped),
        "duplicate_company_period_scope_count": duplicate_count,
        "manifest": "manifest.sha256",
    }
    marker_name = "VALIDATED_COMPLETE.json" if complete else "VALIDATION_FAILED.json"
    _atomic_text(
        output_dir / marker_name,
        json.dumps(marker, indent=2, sort_keys=True) + "\n",
    )
    return qa
