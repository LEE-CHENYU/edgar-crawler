"""Operational helpers for the distributed India BSE Stage-02 rebuild."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Iterable, Optional

from in_bse_stage02 import _balance_candidates, extract_document, find_statement_blocks
from stage02_v2 import (
    CanonicalizationSpec,
    RateGate,
    canonicalize_rows,
    evaluate_contract,
    valid_annual_period_end,
)


MONETARY_FIELDS = (
    "revenue_from_operations",
    "other_operating_income",
    "other_income",
    "other_gain_loss",
    "finance_income",
    "total_income",
    "cost_of_materials",
    "purchases_stock_in_trade",
    "inventory_changes",
    "employee_benefits",
    "finance_costs",
    "depreciation_amortization",
    "other_expenses",
    "total_expenses",
    "exceptional_items",
    "profit_before_exceptional_items_and_tax",
    "associate_jv_share",
    "profit_before_tax",
    "profit_before_associate_and_tax",
    "profit_before_regulatory_deferral",
    "profit_after_tax_before_minority_interest",
    "profit_after_tax",
    "profit_for_period",
    "profit_from_continuing_operations",
    "income_tax_expense",
    "current_tax_net",
    "tax_provision_adjustment",
    "minimum_alternative_tax",
    "tax_reserve_transfer",
    "operating_profit_after_da",
    "operating_profit_pbt_bridge",
    "cash_and_equivalents",
    "other_bank_balances",
    "current_investments",
    "total_assets",
    "total_equity",
    "current_borrowings",
    "non_current_borrowings",
    "total_borrowings",
    "current_lease_liabilities",
    "non_current_lease_liabilities",
    "total_lease_liabilities",
    "total_interest_bearing_debt",
)


def pending_shard_indices(shard_dir: Path, shard_count: int) -> list[int]:
    """Return only shard indices without a committed row/document pair."""
    return [
        index
        for index in range(shard_count)
        if not (
            (shard_dir / f"rows-{index:06d}.jsonl").exists()
            and (shard_dir / f"documents-{index:06d}.jsonl").exists()
        )
    ]


def read_jsonl_files(
    paths: Iterable[Path], *, max_workers: int = 64
) -> list[dict[str, Any]]:
    """Read many JSONL shards concurrently while preserving path order."""
    ordered_paths = list(paths)
    if not ordered_paths:
        return []

    def read_one(path: Path) -> list[dict[str, Any]]:
        with path.open(encoding="utf-8") as handle:
            return [json.loads(line) for line in handle if line.strip()]

    worker_count = max(1, min(max_workers, len(ordered_paths)))
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        batches = executor.map(read_one, ordered_paths)
        return [record for batch in batches for record in batch]


def broad_statement_kinds(text: str) -> set[str]:
    """Find title-like balance/P&L signals for OCR page routing."""
    text = re.sub(r"[\x00-\x08\x0b\x0e-\x1f\x7f]", " ", text)
    text = re.sub(r"\bStatoment\b", "Statement", text, flags=re.I)
    text = re.sub(r"\bStatem\s+ent\b", "Statement", text, flags=re.I)
    text = re.sub(r"\bPro\s+fit\b", "Profit", text, flags=re.I)
    text = re.sub(r"\bforthe\b", "for the", text, flags=re.I)
    text = re.sub(r"\bonded\b", "ended", text, flags=re.I)
    compact = " ".join(text.split())
    kinds: set[str] = set()
    if re.search(
        r"\b(?:balance\s+sheet|statement\s+of\s+(?:assets\s+and\s+liabilities|financial\s+position)|"
        r"financial\s+statements?\s+as\s+(?:on|at))\b",
        compact,
        re.I,
    ) or re.search(r"%DODQFH\s+6KHHW", compact) or (
        '#"-"/$&' in compact and "4)&&5" in compact
    ) or "ÞßÔßÒÝÛ ÍØÛÛÌ" in compact:
        kinds.add("balance_sheet")
    if re.search(
        r"\b(?:statement\s+of\s+(?:standalone\s+|consolidated\s+)?profit\s*"
        r"(?:and|or|&)\s*loss|statement\s+of\s+(?:standalone\s+|consolidated\s+)?"
        r"trading\s*(?:and|&)\s*profit\s*(?:and|&)\s*loss|"
        r"profit\s*(?:and|&)\s*loss\s+"
        r"(?:statement|account|for\s+(?:the\s+)?(?:period|year)))\b",
        compact,
        re.I,
    ) or re.search(
        r"6WDWHPHQW\s+(?:RI|2I)\s+3UR\S{0,4}W\s+(?:DQG|RU)\s+/RVV",
        compact,
        re.I,
    ) or (
        '45"5&.&/5' in compact and "130'*5" in compact and "-044" in compact
    ) or "ÍÌßÌÛÓÛÒÌ ÑÚ ÐÎÑÚ×Ì ßÒÜ ÔÑÍÍ" in compact:
        kinds.add("profit_and_loss")
    return kinds


def select_statement_toc_pages(
    pdftotext_text: str, *, page_count: int, maximum_offset: int = 4
) -> list[int]:
    """Route statement pages named in a table of contents through OCR.

    Older BSE annual reports often have readable contents pages but broken font
    maps on the statements themselves.  The printed and physical PDF page
    numbers differ by a small cover-matter offset, so retain the reported page
    and the next few physical pages.  Requiring both balance-sheet and P&L
    entries keeps ordinary prose and statement rows from becoming candidates.
    """
    title_patterns = {
        "balance_sheet": re.compile(
            r"\bbalance\s+sheet\b[^\n\r\d]{0,100}(?P<page>0*[1-9]\d{1,2})\s*$",
            re.I,
        ),
        "profit_and_loss": re.compile(
            r"\bprofit\s*(?:and|or|&)\s*loss(?:\s+(?:statement|account))?\b"
            r"[^\n\r\d]{0,100}(?P<page>0*[1-9]\d{1,2})\s*$",
            re.I,
        ),
    }
    reported: set[int] = set()
    for page_text in pdftotext_text.split("\f"):
        page_refs: dict[str, int] = {}
        for raw_line in page_text.splitlines():
            line = " ".join(raw_line.split())
            for kind, pattern in title_patterns.items():
                match = pattern.search(line)
                if match:
                    page_refs[kind] = int(match.group("page"))
        if {"balance_sheet", "profit_and_loss"} <= page_refs.keys():
            reported.update(page_refs.values())
    return sorted(
        {
            candidate
            for page_number in reported
            for candidate in range(page_number, page_number + maximum_offset + 1)
            if 1 <= candidate <= page_count
        }
    )


def select_low_text_pages(
    pdftotext_text: str,
    *,
    max_pages: int = 160,
    short_page_threshold: int = 400,
    minimum_alpha_words: int = 5,
) -> list[int]:
    """Select short pages and pages whose embedded font map yields gibberish."""
    selected: list[int] = []
    for page_number, page in enumerate(pdftotext_text.split("\f"), 1):
        stripped = page.strip()
        alpha_words = re.findall(r"[A-Za-z]{4,}", stripped)
        if (
            len(stripped) < short_page_threshold
            or len(alpha_words) < minimum_alpha_words
        ):
            selected.append(page_number)
        if len(selected) >= max_pages:
            break
    return selected


_BALANCE_EVIDENCE = (
    r"\btotal\s+assets?\b",
    r"\btotal\s+equity\b",
    r"\bfinancial\s+liabil",
    r"\bborrowings?\b",
    r"\bcurrent\s+assets?\b",
)
_PROFIT_EVIDENCE = (
    r"\brevenue\s+from\s+operations\b",
    r"\btotal\s+income\b",
    r"\bfinance\s+costs?\b",
    r"\btotal\s+expenses?\b",
    r"\b(?:profit|loss).*?\bbefore\s+tax\b",
    r"\bcurrent\s+tax\b",
)


def _ocr_statement_evidence(text: str) -> tuple[set[str], int, int]:
    nonempty_lines = [line.strip() for line in text.splitlines() if line.strip()]
    title_region = " ".join(nonempty_lines[:15])[:600]
    kinds = broad_statement_kinds(title_region)
    if re.search(r"\bbalance\s+sheet\s+abstract\b", text, re.I):
        kinds.clear()
    balance_hits = sum(
        bool(re.search(pattern, text, re.I)) for pattern in _BALANCE_EVIDENCE
    )
    profit_hits = sum(
        bool(re.search(pattern, text, re.I | re.S)) for pattern in _PROFIT_EVIDENCE
    )
    return kinds, balance_hits, profit_hits


def select_ocr_candidate_pages(pages: dict[int, str]) -> list[int]:
    """Narrow low-resolution title hits when any table evidence is readable."""
    broad = [
        page
        for page, text in sorted(pages.items())
        if _ocr_statement_evidence(text)[0]
    ]
    evidenced = []
    for page_number, text in sorted(pages.items()):
        kinds, balance_hits, profit_hits = _ocr_statement_evidence(text)
        if (
            "balance_sheet" in kinds
            and balance_hits >= 1
            or "profit_and_loss" in kinds
            and profit_hits >= 1
        ):
            evidenced.append(page_number)
    return evidenced or broad


def expand_ocr_candidate_pages(
    candidates: Iterable[int], *, page_count: int, radius: int = 2
) -> list[int]:
    """Include nearby pages so a title hit can recover an adjacent statement."""
    return sorted(
        {
            adjacent
            for page_number in candidates
            for adjacent in range(page_number - radius, page_number + radius + 1)
            if 1 <= adjacent <= max(page_count, 1)
        }
    )


def select_ocr_primary_pages(pages: dict[int, str]) -> list[int]:
    """Keep OCR pages with both a statement title and table-level evidence."""
    selected: list[int] = []
    for page_number, text in sorted(pages.items()):
        kinds, balance_hits, profit_hits = _ocr_statement_evidence(text)
        if (
            "balance_sheet" in kinds
            and balance_hits >= 2
            or "profit_and_loss" in kinds
            and profit_hits >= 3
        ):
            selected.append(page_number)
    return selected


def select_ocr_output_pages(
    pages: dict[int, str], primary_pages: list[int], high_pages: list[int]
) -> list[int]:
    """Use narrowed primary pages only when they cover both required statements."""
    covered_kinds = {
        kind
        for page_number in primary_pages
        for kind in _ocr_statement_evidence(pages.get(page_number, ""))[0]
    }
    if {"balance_sheet", "profit_and_loss"} <= covered_kinds:
        high_page_set = set(high_pages)
        continuation_pages = {
            page_number + 1
            for page_number in primary_pages
            if page_number + 1 in high_page_set
        }
        return sorted(set(primary_pages) | continuation_pages)
    return high_pages


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _run(command: list[str], *, cwd: Path, timeout: int = 900) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )


def _ocr_page(
    pdf_path: Path,
    page_number: int,
    *,
    workdir: Path,
    dpi: int,
    psm: int,
    image_format: str,
) -> str:
    stem = f"ocr-{page_number}-{dpi}-{psm}"
    render_command = [
        "pdftoppm",
        "-f",
        str(page_number),
        "-l",
        str(page_number),
        "-singlefile",
        "-r",
        str(dpi),
    ]
    if image_format == "png":
        render_command.append("-gray")
    render_command.extend([f"-{image_format}", pdf_path.name, stem])
    render = _run(render_command, cwd=workdir)
    if render.returncode != 0:
        return ""
    suffix = "jpg" if image_format == "jpeg" else image_format
    image_path = workdir / f"{stem}.{suffix}"
    if not image_path.exists():
        return ""
    ocr = _run(
        ["tesseract", image_path.name, "stdout", "--psm", str(psm)],
        cwd=workdir,
    )
    return ocr.stdout if ocr.returncode == 0 else ""


def _contiguous_page_ranges(page_numbers: Iterable[int]) -> list[tuple[int, int]]:
    pages = sorted({int(page) for page in page_numbers if int(page) >= 1})
    if not pages:
        return []
    ranges: list[tuple[int, int]] = []
    start = previous = pages[0]
    for page in pages[1:]:
        if page != previous + 1:
            ranges.append((start, previous))
            start = page
        previous = page
    ranges.append((start, previous))
    return ranges


def _ocr_pages(
    pdf_path: Path,
    page_numbers: Iterable[int],
    *,
    workdir: Path,
    dpi: int,
    psm: int,
    image_format: str,
) -> dict[int, str]:
    """Render each contiguous range once, then OCR its requested pages."""
    requested = sorted({int(page) for page in page_numbers if int(page) >= 1})
    output = {page: "" for page in requested}
    suffix = "jpg" if image_format == "jpeg" else image_format
    for first, last in _contiguous_page_ranges(requested):
        stem = f"ocr-batch-{first}-{last}-{dpi}-{psm}"
        render_command = [
            "pdftoppm",
            "-f",
            str(first),
            "-l",
            str(last),
            "-r",
            str(dpi),
        ]
        if image_format == "png":
            render_command.append("-gray")
        render_command.extend([f"-{image_format}", pdf_path.name, stem])
        render = _run(render_command, cwd=workdir)
        if render.returncode != 0:
            continue
        rendered: dict[int, Path] = {}
        for image_path in workdir.glob(f"{stem}-*.{suffix}"):
            match = re.search(r"-(\d+)\." + re.escape(suffix) + r"$", image_path.name)
            if match:
                rendered[int(match.group(1))] = image_path
        def recognize(item: tuple[int, Path]) -> tuple[int, str]:
            page_number, image_path = item
            ocr = _run(
                ["tesseract", image_path.name, "stdout", "--psm", str(psm)],
                cwd=workdir,
            )
            return page_number, ocr.stdout if ocr.returncode == 0 else ""

        items = [
            (page_number, rendered[page_number])
            for page_number in range(first, last + 1)
            if page_number in rendered
        ]
        with ThreadPoolExecutor(max_workers=min(2, len(items) or 1)) as executor:
            for page_number, text in executor.map(recognize, items):
                output[page_number] = text
    return output


def _ocr_rows_need_alternate(rows: list[dict[str, Any]]) -> bool:
    """Retry OCR when no usable industrial scope survived the first pass."""
    if not rows:
        return True
    industrial = [row for row in rows if row.get("industry_template") == "industrial"]
    return bool(industrial) and not any(
        row.get("screen_eligible") for row in industrial
    )


def _ocr_row_set_quality(rows: list[dict[str, Any]]) -> tuple[int, int, int, int, int]:
    """Rank OCR alternatives by screening utility before raw row count."""
    industrial = [row for row in rows if row.get("industry_template") == "industrial"]
    resolved_industrial = sum(
        row.get("operating_profit_closure_status") == "pass"
        and row.get("tax_identity_status") == "pass"
        and row.get("total_borrowings") is not None
        for row in industrial
    )
    closed_industrial_gates = sum(
        int(row.get("operating_profit_closure_status") == "pass")
        + int(row.get("tax_identity_status") == "pass")
        + int(row.get("total_borrowings") is not None)
        for row in industrial
    )
    metadata_fields = (
        "period_end",
        "currency",
        "unit_scale",
        "statement_scope",
    )
    return (
        sum(bool(row.get("screen_eligible")) for row in rows),
        resolved_industrial,
        closed_industrial_gates,
        sum(row.get("total_borrowings") is not None for row in rows),
        sum(row.get(field) is not None for row in rows for field in metadata_fields),
    )


def _ocr_gate_rank(status: Any) -> int:
    return {
        "pass": 3,
        "not_applicable": 3,
        "insufficient_components": 2,
        "insufficient_profit_values": 2,
        "insufficient_tax_components": 2,
        "fail": 0,
    }.get(status, 1)


def _ocr_row_quality(row: dict[str, Any]) -> tuple[int, int, int, int, int, int]:
    operating = _ocr_gate_rank(row.get("operating_profit_closure_status"))
    tax = _ocr_gate_rank(row.get("tax_identity_status"))
    debt = 3 * int(
        row.get("industry_template") != "industrial"
        or row.get("total_borrowings") is not None
    )
    metadata = sum(
        row.get(field) is not None
        for field in ("period_end", "currency", "unit_scale", "statement_scope")
    )
    return (
        int(bool(row.get("screen_eligible"))),
        min(operating, tax, debt),
        operating + tax + debt,
        debt,
        metadata,
        sum(row.get(field) is not None for field in MONETARY_FIELDS),
    )


def merge_ocr_rows_by_scope(
    primary_rows: list[dict[str, Any]], alternate_rows: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Select the more balanced OCR row independently per scope and period."""
    primary_by_key = {
        (row.get("statement_scope"), row.get("period_end")): dict(row)
        for row in primary_rows
    }
    alternate_by_key = {
        (row.get("statement_scope"), row.get("period_end")): dict(row)
        for row in alternate_rows
    }
    ordered_keys = list(primary_by_key)
    ordered_keys.extend(key for key in alternate_by_key if key not in primary_by_key)
    output: list[dict[str, Any]] = []
    for key in ordered_keys:
        primary = primary_by_key.get(key)
        alternate = alternate_by_key.get(key)
        if primary is None:
            output.append(dict(alternate))
        elif alternate is None:
            output.append(dict(primary))
        elif _ocr_row_quality(alternate) > _ocr_row_quality(primary):
            output.append(dict(alternate))
        else:
            output.append(dict(primary))
    return output


_BALANCE_SUPPLEMENT_FIELDS = (
    "cash_and_equivalents",
    "other_bank_balances",
    "current_investments",
    "total_assets",
    "total_equity",
    "current_borrowings",
    "non_current_borrowings",
    "total_borrowings",
    "current_lease_liabilities",
    "non_current_lease_liabilities",
    "total_lease_liabilities",
    "total_interest_bearing_debt",
    "debt_reconciliation_status",
)


def _screen_eligible(row: dict[str, Any]) -> bool:
    return all(
        (
            row.get("statement_scope") not in {None, "unknown"},
            row.get("industry_template") == "industrial",
            row.get("period_end") is not None,
            row.get("currency") is not None,
            row.get("currency_source") != "market_jurisdiction_default",
            row.get("unit_scale") is not None,
            row.get("operating_profit_closure_status") == "pass",
            row.get("tax_identity_status") == "pass",
            row.get("total_borrowings") is not None,
        )
    )


def supplement_rows_with_balance_text(
    rows: list[dict[str, Any]], text: str, *, source: str
) -> list[dict[str, Any]]:
    """Fill missing balance fields from an independently OCRed layout.

    Evidence must match the exact statement period.  An unknown-scope balance
    is usable only when that period has one output row, and a displayed zero is
    accepted only when the balance parser independently classified it as a
    true zero.  This prevents a missing OCR cell or lone dash from becoming
    fabricated debt evidence.
    """
    output = [dict(row) for row in rows]
    if not output or not text:
        return output
    lines = text.splitlines()
    candidates = _balance_candidates(find_statement_blocks(text), lines)
    rows_per_period: dict[Any, int] = defaultdict(int)
    for row in output:
        rows_per_period[row.get("period_end")] += 1

    for row in output:
        if row.get("total_borrowings") is not None:
            continue
        period = row.get("period_end")
        scope = row.get("statement_scope")
        compatible: list[dict[str, Any]] = []
        for candidate in candidates:
            values = candidate["values"]
            debt = values.get("total_borrowings")
            valid_debt = (
                debt is not None
                and (
                    debt > 0
                    or values.get("debt_reconciliation_status") == "true_zero"
                )
            )
            candidate_scope = candidate["block"].scope
            scope_matches = candidate_scope == scope or (
                candidate_scope == "unknown" and rows_per_period[period] == 1
            )
            if candidate["period"] == period and scope_matches and valid_debt:
                compatible.append(candidate)
        if not compatible:
            continue

        def rank(candidate: dict[str, Any]) -> tuple[int, int, int, int]:
            values = candidate["values"]
            return (
                int(candidate["block"].scope == scope),
                int(values.get("total_assets") is not None),
                int(values.get("total_equity") is not None),
                sum(
                    values.get(field) is not None
                    for field in ("current_borrowings", "non_current_borrowings")
                ),
            )

        best_rank = max(rank(candidate) for candidate in compatible)
        finalists = [candidate for candidate in compatible if rank(candidate) == best_rank]
        if len({candidate["values"]["total_borrowings"] for candidate in finalists}) > 1:
            continue
        winner = min(finalists, key=lambda candidate: candidate["block"].start)
        values = winner["values"]
        for field in _BALANCE_SUPPLEMENT_FIELDS:
            if row.get(field) is None and values.get(field) is not None:
                row[field] = values[field]
        row["total_borrowings_null_reason"] = (
            "true_zero"
            if row.get("debt_reconciliation_status") == "true_zero"
            else None
        )
        row["balance_supplement_source"] = source
        if row.get("source_balance_heading") is None:
            row["source_balance_heading"] = winner["block"].heading
        row["screen_eligible"] = _screen_eligible(row)
    return output


def supplement_rows_with_statement_metadata(
    rows: list[dict[str, Any]],
    donor_rows: list[dict[str, Any]],
    *,
    source: str,
) -> list[dict[str, Any]]:
    """Carry independently decoded header metadata across matching OCR rows.

    Only an exact scope/period match is eligible, and all matching donors must
    agree on a non-null value.  Monetary statement fields are deliberately not
    copied here.
    """
    output = [dict(row) for row in rows]
    for row in output:
        compatible = [
            donor
            for donor in donor_rows
            if donor.get("statement_scope") == row.get("statement_scope")
            and donor.get("period_end") == row.get("period_end")
        ]
        changed = False
        unit_scales = {
            donor.get("unit_scale")
            for donor in compatible
            if donor.get("unit_scale") is not None
        }
        if row.get("unit_scale") is None and len(unit_scales) == 1:
            row["unit_scale"] = unit_scales.pop()
            unit_sources = {
                donor.get("unit_scale_source")
                for donor in compatible
                if donor.get("unit_scale") == row["unit_scale"]
                and donor.get("unit_scale_source") not in {None, "unknown"}
            }
            if len(unit_sources) == 1:
                row["unit_scale_source"] = unit_sources.pop()
            changed = True

        donor_currencies = {
            donor.get("currency")
            for donor in compatible
            if donor.get("currency") is not None
            and donor.get("currency_source") != "market_jurisdiction_default"
        }
        if (
            len(donor_currencies) == 1
            and (
                row.get("currency") is None
                or (
                    row.get("currency") in donor_currencies
                    and row.get("currency_source") == "market_jurisdiction_default"
                )
            )
        ):
            row["currency"] = donor_currencies.pop()
            currency_sources = {
                donor.get("currency_source")
                for donor in compatible
                if donor.get("currency") == row["currency"]
                and donor.get("currency_source")
                not in {None, "market_jurisdiction_default"}
            }
            if len(currency_sources) == 1:
                row["currency_source"] = currency_sources.pop()
            changed = True

        if changed:
            row["statement_metadata_supplement_source"] = source
            row["screen_eligible"] = _screen_eligible(row)
    return output


def _ocr_fallback(
    pdf_path: Path,
    pdftotext_text: str,
    *,
    company_name: Optional[str],
    workdir: Path,
    base_rows: Optional[list[dict[str, Any]]] = None,
    max_low_text_pages: int = 160,
    alternate_ocr_budget_seconds: float = 2700.0,
    include_debug_text: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    ocr_started = time.monotonic()
    pages = pdftotext_text.split("\f")
    low_text_pages = select_low_text_pages(
        pdftotext_text,
        max_pages=max_low_text_pages,
    )
    low_text_pages = sorted(
        set(low_text_pages)
        | set(select_statement_toc_pages(pdftotext_text, page_count=len(pages)))
    )
    page_kinds = {
        index: broad_statement_kinds(page)
        for index, page in enumerate(pages, 1)
        if broad_statement_kinds(page)
    }
    low_ocr = _ocr_pages(
        pdf_path,
        low_text_pages,
        workdir=workdir,
        dpi=140,
        psm=6,
        image_format="jpeg",
    )
    for page_number, text in low_ocr.items():
        kinds = broad_statement_kinds(text)
        if kinds:
            page_kinds[page_number] = kinds

    broad_candidates = {
        page_number
        for page_number, kinds in page_kinds.items()
        if kinds & {"balance_sheet", "profit_and_loss"}
    }
    candidate_text = {
        page_number: low_ocr.get(page_number, pages[page_number - 1])
        for page_number in broad_candidates
    }
    candidates = set(select_ocr_candidate_pages(candidate_text))
    high_pages = expand_ocr_candidate_pages(candidates, page_count=len(pages))
    high_ocr = _ocr_pages(
        pdf_path,
        high_pages,
        workdir=workdir,
        dpi=300,
        psm=3,
        image_format="png",
    )
    primary_pages = select_ocr_primary_pages(high_ocr)
    selected_pages = select_ocr_output_pages(high_ocr, primary_pages, high_pages)
    combined = "\n\f\n".join(
        high_ocr[page] for page in selected_pages if high_ocr[page]
    )
    rows = extract_document(combined, company_name=company_name) if combined else []
    psm3_combined = combined
    psm6_combined = ""
    selected_psm = 3
    psm6_attempted = False
    rowwise_merged = False
    alternate_needed = bool(combined and _ocr_rows_need_alternate(rows))
    elapsed_before_alternate = max(0.0, time.monotonic() - ocr_started)
    psm6_skipped_budget = bool(
        alternate_needed
        and elapsed_before_alternate >= alternate_ocr_budget_seconds
    )
    if alternate_needed and not psm6_skipped_budget:
        psm6_attempted = True
        psm6_ocr = _ocr_pages(
            pdf_path,
            selected_pages,
            workdir=workdir,
            dpi=300,
            psm=6,
            image_format="png",
        )
        psm6_combined = "\n\f\n".join(
            psm6_ocr[page] for page in selected_pages if psm6_ocr[page]
        )
        psm6_rows = (
            extract_document(psm6_combined, company_name=company_name)
            if psm6_combined
            else []
        )
        psm3_rows = rows
        rows = supplement_rows_with_statement_metadata(
            psm3_rows,
            psm6_rows,
            source="ocr_psm6",
        )
        psm6_rows = supplement_rows_with_statement_metadata(
            psm6_rows,
            psm3_rows,
            source="ocr_psm3",
        )
        rows = supplement_rows_with_balance_text(
            rows,
            psm6_combined,
            source="ocr_psm6",
        )
        psm6_rows = supplement_rows_with_balance_text(
            psm6_rows,
            psm3_combined,
            source="ocr_psm3",
        )
        primary_rows = rows
        rows = merge_ocr_rows_by_scope(primary_rows, psm6_rows)
        rowwise_merged = rows != primary_rows and rows != psm6_rows
        if rows == psm6_rows:
            combined = psm6_combined
            selected_psm = 6
    ocr_supplemented_pdftotext = False
    if base_rows:
        balance_text_options: list[tuple[int, str]] = []
        for psm, balance_text in (
            (selected_psm, combined),
            (3, psm3_combined),
            (6, psm6_combined),
        ):
            if balance_text and balance_text not in {
                prior_text for _, prior_text in balance_text_options
            }:
                balance_text_options.append((psm, balance_text))
        supplemented_options = [
            (
                psm,
                supplement_rows_with_balance_text(
                    base_rows,
                    balance_text,
                    source=f"ocr_psm{psm}",
                ),
            )
            for psm, balance_text in balance_text_options
        ]
        if supplemented_options:
            best_quality = max(
                _ocr_row_set_quality(option_rows)
                for _, option_rows in supplemented_options
            )
            best_options = [
                (psm, option_rows)
                for psm, option_rows in supplemented_options
                if _ocr_row_set_quality(option_rows) == best_quality
            ]
            debt_signatures = {
                tuple(
                    sorted(
                        (
                            row.get("statement_scope"),
                            row.get("period_end"),
                            row.get("total_borrowings"),
                        )
                        for row in option_rows
                        if row.get("total_borrowings") is not None
                    )
                )
                for _, option_rows in best_options
            }
            # Equal-quality OCR layouts that disagree on debt are ambiguous;
            # retain the independent text-layer rows rather than choose a PSM
            # variant by iteration order.
            if len(debt_signatures) == 1:
                _, supplemented_base_rows = best_options[0]
                if (
                    _ocr_row_set_quality(supplemented_base_rows)
                    > _ocr_row_set_quality(rows)
                ):
                    rows = supplemented_base_rows
                    ocr_supplemented_pdftotext = True
    metadata = {
        "low_text_page_count": len(low_text_pages),
        "ocr_broad_candidate_pages": sorted(broad_candidates),
        "ocr_candidate_pages": sorted(candidates),
        "ocr_high_resolution_pages": high_pages,
        "ocr_primary_statement_pages": primary_pages,
        "ocr_selected_pages": selected_pages,
        "ocr_selected_psm": selected_psm,
        "ocr_psm6_attempted": psm6_attempted,
        "ocr_rowwise_merged": rowwise_merged,
        "ocr_psm6_skipped_budget": psm6_skipped_budget,
        "ocr_supplemented_pdftotext": ocr_supplemented_pdftotext,
        "ocr_elapsed_before_psm6_seconds": round(elapsed_before_alternate, 3),
        "ocr_page_limit_reached": len(low_text_pages) >= max_low_text_pages,
    }
    if include_debug_text:
        metadata["ocr_selected_text"] = combined
        metadata["ocr_psm3_text"] = psm3_combined
        metadata["ocr_psm6_text"] = psm6_combined
    return rows, metadata


def extract_pdf(
    pdf_path: Path,
    *,
    company_name: Optional[str] = None,
    workdir: Optional[Path] = None,
    include_debug_text: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Run layout text extraction, with targeted two-pass OCR fallback."""
    workdir = workdir or pdf_path.parent
    text_path = workdir / "document.txt"
    result = _run(
        ["pdftotext", "-layout", pdf_path.name, text_path.name],
        cwd=workdir,
    )
    text = text_path.read_text(errors="replace") if text_path.exists() else ""
    rows = extract_document(text, company_name=company_name)
    metadata: dict[str, Any] = {
        "pdftotext_returncode": result.returncode,
        "pdftotext_stderr": result.stderr[-2000:],
        "text_character_count": len(text),
        "extraction_method": "pdftotext",
    }
    if include_debug_text:
        metadata["pdftotext_text"] = text
    if rows and not _ocr_rows_need_alternate(rows):
        return rows, metadata
    text_rows = rows
    ocr_rows, ocr_metadata = _ocr_fallback(
        pdf_path,
        text,
        company_name=company_name,
        workdir=workdir,
        base_rows=text_rows,
        include_debug_text=include_debug_text,
    )
    metadata.update(ocr_metadata)
    if _ocr_row_set_quality(ocr_rows) > _ocr_row_set_quality(text_rows):
        rows = ocr_rows
        metadata["ocr_replaced_pdftotext"] = bool(text_rows)
        metadata["extraction_method"] = (
            f"pdftotext_with_ocr_balance_psm{metadata.get('ocr_selected_psm', 3)}"
            if metadata.get("ocr_supplemented_pdftotext")
            else f"ocr_300dpi_psm{metadata.get('ocr_selected_psm', 3)}"
        )
    elif text_rows:
        rows = text_rows
        metadata["ocr_replaced_pdftotext"] = False
        metadata["extraction_method"] = "pdftotext"
    else:
        rows = ocr_rows
        metadata["ocr_replaced_pdftotext"] = False
        metadata["extraction_method"] = (
            f"ocr_300dpi_psm{metadata.get('ocr_selected_psm', 3)}"
            if rows
            else "unresolved"
        )
    return rows, metadata


def download_pdf(
    urls: Iterable[str],
    destination: Path,
    *,
    max_attempts: int = 4,
    initial_backoff_seconds: float = 2.0,
) -> dict[str, Any]:
    """Download the first healthy BSE PDF candidate with bounded retries."""
    import requests

    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; IN-BSE-Stage02/2.0)",
        "Referer": "https://www.bseindia.com/",
        "Accept": "application/pdf,*/*;q=0.8",
    }
    if max_attempts < 1:
        raise ValueError("max_attempts must be positive")
    unique_urls = list(dict.fromkeys(value for value in urls if value))
    errors: list[str] = []
    for attempt in range(1, max_attempts + 1):
        for url in unique_urls:
            try:
                with requests.get(
                    url, headers=headers, timeout=(20, 180), stream=True
                ) as response:
                    response.raise_for_status()
                    with destination.open("wb") as handle:
                        for chunk in response.iter_content(1024 * 1024):
                            if chunk:
                                handle.write(chunk)
                with destination.open("rb") as handle:
                    header = handle.read(4)
                if destination.stat().st_size < 1024 or header != b"%PDF":
                    errors.append(f"attempt {attempt} {url}: not a PDF")
                    continue
                return {
                    "download_url": url,
                    "download_attempts": attempt,
                    "pdf_bytes": destination.stat().st_size,
                    "pdf_sha256": sha256_file(destination),
                }
            except Exception as exc:  # network/server failures are document-level QA
                errors.append(
                    f"attempt {attempt} {url}: {type(exc).__name__}: {exc}"
                )
        if attempt < max_attempts:
            time.sleep(initial_backoff_seconds * 2 ** (attempt - 1))
    raise RuntimeError("; ".join(errors) or "no document URL")


def download_gcs_pdf(
    client: Any,
    gcs_uri: str,
    destination: Path,
    *,
    expected_bytes: Optional[int] = None,
) -> dict[str, Any]:
    """Download one private GCS object and validate its PDF identity."""
    if not gcs_uri.startswith("gs://"):
        raise ValueError(f"not a GCS URI: {gcs_uri}")
    bucket_name, separator, object_name = gcs_uri[5:].partition("/")
    if not separator or not bucket_name or not object_name:
        raise ValueError(f"incomplete GCS URI: {gcs_uri}")
    client.bucket(bucket_name).blob(object_name).download_to_filename(destination)
    observed_bytes = destination.stat().st_size
    with destination.open("rb") as handle:
        header = handle.read(4)
    if observed_bytes < 1024 or header != b"%PDF":
        raise RuntimeError(f"GCS object is not a healthy PDF: {gcs_uri}")
    if expected_bytes is not None and observed_bytes != int(expected_bytes):
        raise RuntimeError(
            f"GCS PDF size mismatch for {gcs_uri}: "
            f"{observed_bytes} != {expected_bytes}"
        )
    return {
        "download_url": gcs_uri,
        "download_attempts": 1,
        "source_transport": "gcs",
        "pdf_bytes": observed_bytes,
        "pdf_sha256": sha256_file(destination),
    }


def _bse_legacy_quarter_end_url_variants(urls: Iterable[str]) -> list[str]:
    """Add deterministic quarter-end filename alternatives for legacy BSE PDFs."""
    expanded: list[str] = []
    pattern = re.compile(
        r"^(?P<prefix>https?://[^?]+/AnnualReport/(?P<ticker>\d+)/)"
        r"(?P=ticker)(?P<month>03|06|09|12)(?P<year>\d{2})\.pdf"
        r"(?P<suffix>(?:\?.*)?)$",
        re.I,
    )
    for url in urls:
        if not url or url in expanded:
            continue
        expanded.append(url)
        match = pattern.match(url)
        if not match:
            if "/AttachLive/" in url:
                alt = url.replace("/AttachLive/", "/AttachHis/")
                if alt not in expanded:
                    expanded.append(alt)
            elif "/AttachHis/" in url:
                alt = url.replace("/AttachHis/", "/AttachLive/")
                if alt not in expanded:
                    expanded.append(alt)
            continue
        for month in ("03", "06", "09", "12"):
            if month == match.group("month"):
                continue
            variant = (
                f"{match.group('prefix')}{match.group('ticker')}"
                f"{month}{match.group('year')}.pdf{match.group('suffix')}"
            )
            if variant not in expanded:
                expanded.append(variant)
    return expanded


def download_manifest_pdf(
    record: dict[str, Any],
    destination: Path,
    *,
    gcs_client: Any = None,
) -> dict[str, Any]:
    """Download a manifest PDF, falling back to its authoritative BSE URL.

    The staged GCS corpus is preferred for stable bulk throughput.  A small
    number of historical objects contain an HTML/error payload, while the
    source manifest still carries a healthy BSE URL.  Preserve the GCS error
    in provenance and use the source only after staged validation fails.
    """
    gcs_uri = record.get("gcs_uri")
    candidate_urls = _bse_legacy_quarter_end_url_variants(
        record.get("candidate_urls") or []
    )
    if not gcs_uri:
        result = download_pdf(candidate_urls, destination)
        result["source_transport"] = "bse_http"
        return result
    if gcs_client is None:
        raise ValueError("gcs_client is required for a manifest GCS URI")
    try:
        return download_gcs_pdf(
            gcs_client,
            gcs_uri,
            destination,
            expected_bytes=record.get("expected_pdf_bytes"),
        )
    except Exception as gcs_error:
        if not candidate_urls:
            raise
        try:
            result = download_pdf(candidate_urls, destination)
        except Exception as source_error:
            raise RuntimeError(
                f"GCS download failed ({type(gcs_error).__name__}: {gcs_error}); "
                f"BSE fallback failed ({type(source_error).__name__}: {source_error})"
            ) from source_error
        result["source_transport"] = "bse_http_fallback"
        result["gcs_fallback_error"] = (
            f"{type(gcs_error).__name__}: {gcs_error}"
        )
        return result


def enrich_rows(
    rows: Iterable[dict[str, Any]],
    manifest_record: dict[str, Any],
    document_metadata: dict[str, Any],
) -> list[dict[str, Any]]:
    enriched: list[dict[str, Any]] = []
    identity = {
        key: manifest_record.get(key)
        for key in (
            "filing_id",
            "ticker",
            "symbol",
            "isin",
            "company_name",
            "listing_status",
            "listing_segment",
            "publication_date",
            "source_year",
            "document_url",
            "gcs_uri",
        )
    }
    provenance = {
        key: document_metadata.get(key)
        for key in (
            "download_url",
            "source_transport",
            "pdf_bytes",
            "pdf_sha256",
            "extraction_method",
            "text_character_count",
        )
    }
    for row in rows:
        item = {**identity, **row, **provenance}
        scale = item.get("unit_scale")
        for field in MONETARY_FIELDS:
            value = item.get(field)
            item[f"{field}_inr"] = value * scale if value is not None and scale else None
        enriched.append(item)
    return enriched


def repair_candidate_filing_ids(
    documents: Iterable[dict[str, Any]], rows: Iterable[dict[str, Any]]
) -> set[str]:
    """Return filings whose current output can affect a Stage-02 QA gate."""
    candidates = {
        str(document["filing_id"])
        for document in documents
        if document.get("status") != "extracted"
    }
    for row in rows:
        if (
            row.get("operating_profit_closure_status") == "fail"
            or row.get("tax_identity_status") == "fail"
            or (
                row.get("industry_template") == "industrial"
                and row.get("total_borrowings") is None
            )
        ):
            candidates.add(str(row["filing_id"]))
    return candidates


def _overlay_status_rank(status: Any, *, not_applicable: bool = False) -> int:
    if not_applicable and status == "not_applicable":
        return 3
    return {
        "pass": 3,
        "insufficient_components": 2,
        "insufficient_profit_values": 2,
        "insufficient_tax_components": 2,
        "fail": 0,
    }.get(status, 1)


def _overlay_quality_dimensions(row: dict[str, Any]) -> tuple[int, ...]:
    template = row.get("industry_template")
    debt_resolved = int(
        template != "industrial" or row.get("total_borrowings") is not None
    )
    return (
        int(bool(row.get("screen_eligible"))),
        _overlay_status_rank(
            row.get("operating_profit_closure_status"), not_applicable=True
        ),
        _overlay_status_rank(row.get("tax_identity_status")),
        debt_resolved,
        int(row.get("period_end") is not None),
        int(row.get("currency") is not None),
        int(row.get("unit_scale") is not None),
    )


def _repair_row_improves(
    baseline: dict[str, Any], repair: dict[str, Any]
) -> bool:
    # Template changes alter the debt denominator and require a separate
    # classification review; the statement parser repair is not authorized to
    # make that trade implicitly.
    if baseline.get("industry_template") != repair.get("industry_template"):
        return False
    old = _overlay_quality_dimensions(baseline)
    new = _overlay_quality_dimensions(repair)
    if all(new_value >= old_value for old_value, new_value in zip(old, new)):
        if any(new_value > old_value for old_value, new_value in zip(old, new)):
            return True
        old_components = sum(baseline.get(field) is not None for field in MONETARY_FIELDS)
        new_components = sum(repair.get(field) is not None for field in MONETARY_FIELDS)
        return new_components > old_components
    return False


def merge_repair_overlay(
    baseline_documents: Iterable[dict[str, Any]],
    baseline_rows: Iterable[dict[str, Any]],
    repair_documents: Iterable[dict[str, Any]],
    repair_rows: Iterable[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, int]]:
    """Overlay only per-scope repairs that improve every contract dimension."""
    baseline_documents = [dict(document) for document in baseline_documents]
    repair_document_by_id = {
        str(document["filing_id"]): dict(document) for document in repair_documents
    }
    row_by_key = {
        (str(row["filing_id"]), str(row.get("statement_scope"))): dict(row)
        for row in baseline_rows
    }
    changed_filings: set[str] = set()
    added = 0
    replaced = 0
    rejected = 0
    for repair in repair_rows:
        repair = dict(repair)
        key = (str(repair["filing_id"]), str(repair.get("statement_scope")))
        baseline = row_by_key.get(key)
        if baseline is None:
            row_by_key[key] = repair
            changed_filings.add(key[0])
            added += 1
        elif _repair_row_improves(baseline, repair):
            row_by_key[key] = repair
            changed_filings.add(key[0])
            replaced += 1
        else:
            rejected += 1

    merged_rows = [row_by_key[key] for key in sorted(row_by_key)]
    row_counts: dict[str, int] = defaultdict(int)
    for row in merged_rows:
        row_counts[str(row["filing_id"])] += 1

    merged_documents: list[dict[str, Any]] = []
    baseline_ids: set[str] = set()
    for baseline in baseline_documents:
        filing_id = str(baseline["filing_id"])
        baseline_ids.add(filing_id)
        selected = (
            dict(repair_document_by_id[filing_id])
            if filing_id in changed_filings and filing_id in repair_document_by_id
            else dict(baseline)
        )
        selected["row_count"] = row_counts.get(filing_id, 0)
        if selected["row_count"]:
            selected["status"] = "extracted"
        merged_documents.append(selected)
    for filing_id in sorted(repair_document_by_id.keys() - baseline_ids):
        selected = dict(repair_document_by_id[filing_id])
        selected["row_count"] = row_counts.get(filing_id, 0)
        merged_documents.append(selected)

    audit = {
        "added_scope_rows": added,
        "replaced_scope_rows": replaced,
        "rejected_repair_scope_rows": rejected,
        "changed_filings": len(changed_filings),
    }
    return merged_documents, merged_rows, audit


def canonical_rows(
    rows: Iterable[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Resolve filing finality, then choose an eligible scope for screening."""
    return canonicalize_rows(
        rows,
        CanonicalizationSpec(
            company_field="ticker",
            period_field="period_end",
            scope_field="statement_scope",
            publication_field="publication_date",
            filing_field="filing_id",
            eligibility_field="screen_eligible",
            consolidated_scopes=("consolidated",),
        ),
    )


def reduced_run_is_valid(
    qa: dict[str, Any], preferred_rows: Iterable[dict[str, Any]]
) -> bool:
    """A reduced run needs both passing QA and a usable screening output."""
    preferred_rows = list(preferred_rows)
    return bool(qa.get("normalization_contract_pass")) and bool(preferred_rows) and all(
        row.get("screen_eligible") for row in preferred_rows
    )


def summarize_run(
    documents: Iterable[dict[str, Any]], rows: Iterable[dict[str, Any]]
) -> dict[str, Any]:
    documents = list(documents)
    rows = list(rows)
    extracted_docs = [document for document in documents if document.get("status") == "extracted"]
    industrial = [row for row in rows if row.get("industry_template") == "industrial"]
    op_testable = [
        row
        for row in industrial
        if row.get("operating_profit_closure_status") in {"pass", "fail"}
    ]
    tax_testable = [
        row for row in rows if row.get("tax_identity_status") in {"pass", "fail"}
    ]

    contract = evaluate_contract(
        documents,
        rows,
        (
            RateGate("document_extraction", "documents", lambda _: True,
                     lambda row: row.get("status") == "extracted"),
            RateGate(
                "operating_profit_closure", "rows",
                lambda row: row.get("industry_template") == "industrial"
                and row.get("operating_profit_closure_status") in {"pass", "fail"},
                lambda row: row.get("operating_profit_closure_status") == "pass",
            ),
            RateGate(
                "tax_identity", "rows",
                lambda row: row.get("tax_identity_status") in {"pass", "fail"},
                lambda row: row.get("tax_identity_status") == "pass",
            ),
            RateGate(
                "industrial_debt_resolution", "rows",
                lambda row: row.get("industry_template") == "industrial",
                lambda row: row.get("total_borrowings") is not None,
            ),
            RateGate("period_end_resolution", "rows", lambda _: True,
                     lambda row: row.get("period_end") is not None),
            RateGate("period_end_plausibility", "rows", lambda _: True,
                     lambda row: valid_annual_period_end(row.get("period_end"))),
            RateGate("currency_resolution", "rows", lambda _: True,
                     lambda row: row.get("currency") is not None),
        ),
    )
    gates = contract["gates"]
    return {
        "documents_total": len(documents),
        "documents_extracted": len(extracted_docs),
        "document_extraction_rate": gates["document_extraction"]["rate"],
        "ocr_document_count": sum(
            str(document.get("extraction_method") or "").startswith("ocr")
            for document in documents
        ),
        "statement_rows": len(rows),
        "industrial_rows": len(industrial),
        "financial_service_rows": len(rows) - len(industrial),
        "operating_profit_testable_rows": len(op_testable),
        "operating_profit_closure_rate": gates["operating_profit_closure"]["rate"],
        "tax_testable_rows": len(tax_testable),
        "tax_identity_rate": gates["tax_identity"]["rate"],
        "industrial_debt_resolution_rate": gates["industrial_debt_resolution"]["rate"],
        "period_end_resolution_rate": gates["period_end_resolution"]["rate"],
        "period_end_plausibility_rate": gates["period_end_plausibility"]["rate"],
        "currency_resolution_rate": gates["currency_resolution"]["rate"],
        "unresolved_unit_rows": sum(row.get("unit_scale") is None for row in rows),
        "screen_eligible_rows": sum(bool(row.get("screen_eligible")) for row in rows),
        "contract_gates": gates,
        "normalization_contract_pass": contract["contract_pass"],
    }


def write_jsonl(path: Path, records: Iterable[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
