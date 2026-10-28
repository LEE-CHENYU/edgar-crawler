#!/usr/bin/env python3
"""Resume-safe OpenDART Stage-02 V2 build and validation.

Each company-period is committed as one document JSON plus one scoped-row
JSONL.  A rerun skips only committed pairs, preventing the stale doc-id resume
failure that affected earlier corpus jobs.  Consolidation uses the shared V2
finality/scope contract and writes Parquet only after all available outputs are
read successfully.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Iterable, Optional

from dart_financials_api import fetch_accounts
from dart_financials_sweep import load_key
from dart_stage02_v2 import extract_canonical_row, period_end_from_report_name
from stage02_v2 import (
    CanonicalizationSpec,
    RateGate,
    canonicalize_rows,
    evaluate_contract,
    valid_annual_period_end,
)


DEFAULT_METADATA = Path(
    "/Volumes/OWC Express 1M2/datasets/MARKET_FILINGS/metadata/"
    "MARKET_FILINGS_METADATA.csv"
)
DEFAULT_OUTPUT = Path(
    "/Volumes/OWC Express 1M2/datasets/MARKET_FILINGS/derived/"
    "DART_FINANCIALS_V2"
)


def _clean(value: Any) -> str:
    return str(value or "").strip()


def build_targets(metadata_path: Path, *, require_local: bool = True) -> list[dict[str, Any]]:
    """Latest annual-report filing per corporation and exact fiscal period."""
    selected: dict[tuple[str, str], dict[str, Any]] = {}
    with Path(metadata_path).open(newline="", encoding="utf-8", errors="replace") as handle:
        for record in csv.DictReader(handle):
            if _clean(record.get("market")).upper() != "DART":
                continue
            try:
                raw = json.loads(record.get("raw_metadata") or "{}")
            except json.JSONDecodeError:
                raw = {}
            report_name = _clean(raw.get("report_nm") or record.get("title"))
            period_end = period_end_from_report_name(report_name)
            if period_end is None or "사업보고서" not in report_name:
                continue
            if "제출기한연장" in report_name:
                continue
            corp_code = _clean(raw.get("corp_code"))
            filing_id = _clean(record.get("filing_id") or raw.get("rcept_no"))
            local_path = _clean(record.get("local_path"))
            if not corp_code or not filing_id:
                continue
            if require_local and (not local_path or not Path(local_path).exists()):
                continue
            item = {
                "market": "DART",
                "filing_id": filing_id,
                "filing_date": _clean(record.get("filing_date")),
                "corp_code": corp_code,
                "stock_code": _clean(raw.get("stock_code") or record.get("stock_code")),
                "company_name": _clean(raw.get("corp_name") or record.get("company_name")),
                "report_nm": report_name,
                "period_end": period_end,
                "local_path": local_path,
                "document_url": _clean(record.get("document_url")),
            }
            key = (corp_code, period_end)
            previous = selected.get(key)
            if previous is None or (
                item["filing_date"], item["filing_id"]
            ) > (previous["filing_date"], previous["filing_id"]):
                selected[key] = item
    return sorted(
        selected.values(),
        key=lambda row: (row["period_end"], row["corp_code"], row["filing_id"]),
        reverse=True,
    )


def summarize_run(
    documents: Iterable[dict[str, Any]],
    rows: Iterable[dict[str, Any]],
    *,
    threshold: float = 0.95,
) -> dict[str, Any]:
    documents = list(documents)
    rows = list(rows)
    contract = evaluate_contract(
        documents,
        rows,
        (
            RateGate("document_extraction", "documents", lambda _: True,
                     lambda item: item.get("status") == "extracted", threshold),
            RateGate(
                "operating_profit_closure", "rows",
                lambda item: item.get("operating_profit_closure_status") in {"pass", "fail"},
                lambda item: item.get("operating_profit_closure_status") == "pass",
                threshold,
            ),
            RateGate(
                "tax_identity", "rows",
                lambda item: item.get("tax_identity_status") in {"pass", "fail"},
                lambda item: item.get("tax_identity_status") == "pass",
                threshold,
            ),
            RateGate(
                "balance_identity", "rows",
                lambda item: item.get("balance_identity_status") in {"pass", "fail"},
                lambda item: item.get("balance_identity_status") == "pass",
                threshold,
            ),
            RateGate(
                "industrial_debt_resolution", "rows",
                lambda item: item.get("industry_template") == "industrial",
                lambda item: item.get("debt_reconciliation_status") == "resolved_components",
                threshold,
            ),
            RateGate("period_end_plausibility", "rows", lambda _: True,
                     lambda item: valid_annual_period_end(item.get("period_end")), threshold),
            RateGate("currency_resolution", "rows", lambda _: True,
                     lambda item: item.get("currency") == "KRW", threshold),
        ),
    )
    gates = contract["gates"]
    return {
        "documents_total": len(documents),
        "documents_extracted": sum(item.get("status") == "extracted" for item in documents),
        "statement_rows": len(rows),
        "screen_eligible_rows": sum(bool(item.get("screen_eligible")) for item in rows),
        "document_extraction_rate": gates["document_extraction"]["rate"],
        "operating_profit_closure_rate": gates["operating_profit_closure"]["rate"],
        "tax_identity_rate": gates["tax_identity"]["rate"],
        "balance_identity_rate": gates["balance_identity"]["rate"],
        "industrial_debt_resolution_rate": gates["industrial_debt_resolution"]["rate"],
        "period_end_plausibility_rate": gates["period_end_plausibility"]["rate"],
        "currency_resolution_rate": gates["currency_resolution"]["rate"],
        "contract_gates": gates,
        "normalization_contract_pass": contract["contract_pass"],
    }


def process_target(
    target: dict[str, Any], api_key: str, *, timeout: int = 30, retries: int = 3
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    rows: list[dict[str, Any]] = []
    accounts_by_scope: dict[str, list[dict[str, Any]]] = {}
    statuses: dict[str, str] = {}
    errors: dict[str, str] = {}
    for scope in ("CFS", "OFS"):
        try:
            accounts, status = fetch_accounts(
                target["corp_code"], int(target["period_end"][:4]), api_key,
                fs_div=scope, timeout=timeout, retries=retries,
            )
            statuses[scope] = status
            accounts_by_scope[scope] = accounts
            if status == "020":
                break
            if accounts:
                row = extract_canonical_row(accounts, target, fs_div=scope)
                row["api_status"] = status
                rows.append(row)
        except Exception as exc:
            statuses[scope] = "error"
            errors[scope] = f"{type(exc).__name__}: {exc}"
    document = {
        **target,
        "status": (
            "rate_limited" if "020" in statuses.values()
            else "extracted" if rows
            else "error" if errors
            else "no_structured_data"
        ),
        "scope_statuses": statuses,
        "scope_errors": errors,
        "row_count": len(rows),
    }
    return document, rows, accounts_by_scope


def _atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def write_target_output(
    output_dir: Path,
    document: dict[str, Any],
    rows: Iterable[dict[str, Any]],
    accounts_by_scope: dict[str, list[dict[str, Any]]],
) -> None:
    filing_id = document["filing_id"]
    _atomic_text(
        output_dir / "documents" / f"{filing_id}.json",
        json.dumps(document, ensure_ascii=False, sort_keys=True) + "\n",
    )
    payload = "".join(
        json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows
    )
    _atomic_text(output_dir / "rows" / f"{filing_id}.jsonl", payload)
    _atomic_text(
        output_dir / "accounts" / f"{filing_id}.json",
        json.dumps(accounts_by_scope, ensure_ascii=False, sort_keys=True) + "\n",
    )


def committed(output_dir: Path, filing_id: str) -> bool:
    document_path = output_dir / "documents" / f"{filing_id}.json"
    if not (
        document_path.exists()
        and (output_dir / "rows" / f"{filing_id}.jsonl").exists()
        and (output_dir / "accounts" / f"{filing_id}.json").exists()
    ):
        return False
    try:
        status = json.loads(document_path.read_text(encoding="utf-8")).get("status")
    except (json.JSONDecodeError, OSError):
        return False
    return status in {"extracted", "no_structured_data"}


def load_outputs(output_dir: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    documents = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted((output_dir / "documents").glob("*.json"))
    ]
    rows: list[dict[str, Any]] = []
    for path in sorted((output_dir / "rows").glob("*.jsonl")):
        with path.open(encoding="utf-8") as handle:
            rows.extend(json.loads(line) for line in handle if line.strip())
    return documents, rows


def canonical_rows(rows: Iterable[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    return canonicalize_rows(
        rows,
        CanonicalizationSpec(
            company_field="corp_code",
            period_field="period_end",
            scope_field="statement_scope",
            publication_field="publication_date",
            filing_field="filing_id",
            eligibility_field="screen_eligible",
            consolidated_scopes=("consolidated",),
        ),
    )


def consolidate(output_dir: Path, *, threshold: float = 0.95) -> dict[str, Any]:
    import pandas as pd

    documents, rows = load_outputs(output_dir)
    scoped, preferred = canonical_rows(rows)
    qa = summarize_run(documents, scoped, threshold=threshold)

    def parquet_safe(row: dict[str, Any]) -> dict[str, Any]:
        return {
            key: (
                json.dumps(value, ensure_ascii=False, sort_keys=True)
                if isinstance(value, (dict, list)) else value
            )
            for key, value in row.items()
        }

    for name, records in (
        ("canonical_scoped_rows.parquet", scoped),
        ("preferred_screening_rows.parquet", preferred),
    ):
        path = output_dir / name
        temporary = path.with_suffix(path.suffix + ".tmp")
        pd.DataFrame([parquet_safe(row) for row in records]).to_parquet(
            temporary, index=False
        )
        os.replace(temporary, path)
    _atomic_text(
        output_dir / "qa_summary.json",
        json.dumps(qa, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    return qa


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--metadata", type=Path, default=DEFAULT_METADATA)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--threshold", type=float, default=0.95)
    args = parser.parse_args()

    targets = build_targets(args.metadata)
    pending_all = [
        target for target in targets
        if not committed(args.output_dir, target["filing_id"])
    ]
    committed_count = len(targets) - len(pending_all)
    pending = pending_all
    if args.limit:
        pending = pending[: args.limit]
    print(
        f"targets={len(targets)} committed={committed_count} "
        f"pending_total={len(pending_all)} "
        f"pending_run={len(pending)}",
        flush=True,
    )
    key = load_key()
    totals: Counter[str] = Counter()
    completed_count = 0
    stop_for_limit = False
    worker_count = max(1, args.workers)
    for start in range(0, len(pending), worker_count):
        batch = pending[start:start + worker_count]
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures = {
                executor.submit(
                    process_target, target, key,
                    timeout=args.timeout, retries=args.retries,
                ): target
                for target in batch
            }
            for future in as_completed(futures):
                document, rows, accounts_by_scope = future.result()
                status = document["status"]
                totals[status] += 1
                completed_count += 1
                if status in {"rate_limited", "error"}:
                    _atomic_text(
                        args.output_dir / "attempts" / f"{document['filing_id']}.json",
                        json.dumps(document, ensure_ascii=False, sort_keys=True) + "\n",
                    )
                else:
                    write_target_output(
                        args.output_dir, document, rows, accounts_by_scope
                    )
                print(
                    f"[{completed_count}/{len(pending)}] {document['stock_code']} "
                    f"{document['period_end']} {status} rows={len(rows)}",
                    flush=True,
                )
                if status == "rate_limited":
                    stop_for_limit = True
        if stop_for_limit:
            print(
                "OpenDART daily limit reached; retryable target was not committed. "
                "Stopping the bounded queue for a later resume.",
                flush=True,
            )
            break
    qa = consolidate(args.output_dir, threshold=args.threshold)
    print(json.dumps({"run_statuses": totals, "qa": qa}, ensure_ascii=False, indent=2, default=dict))
    return 0 if qa["normalization_contract_pass"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
