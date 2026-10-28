#!/usr/bin/env python3
"""Statement-table fallback for OpenDART API status 013 annual filings.

DART's raw document ZIPs include audit-report XML attachments (00760 separate,
00761 consolidated).  Their primary statement tables are machine-readable,
so this fallback parses the current-year column directly and keeps exact
scope, unit and label provenance.  It does not OCR rendered pages.
"""
from __future__ import annotations

import codecs
import json
import re
import warnings
from typing import Any, Optional

from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning

from dart_stage02_v2 import period_end_from_report_name
from stage02_v2 import valid_annual_period_end


_FINANCIAL_NAME_RE = re.compile(
    r"(?:은행|보험|증권|금융|캐피탈|카드|저축|리츠|스팩|"
    r"investment|bank|insurance|\bspac\b)", re.I,
)
_ROMAN_PREFIX = re.compile(r"^[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩIVXivx]+[.)·ㆍ\s]*")
_UNIT = re.compile(r"단위\s*[:：]?\s*(원|천원|백만원|십억원)")
_SCALE = {"원": 1.0, "천원": 1_000.0, "백만원": 1_000_000.0,
          "십억원": 1_000_000_000.0}


def _label(value: Any) -> str:
    text = re.sub(r"\s+", "", str(value or ""))
    text = _ROMAN_PREFIX.sub("", text)
    return re.sub(r"[·ㆍ.:：]", "", text)


def _number(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = (
        str(value)
        .strip()
        .replace("−", "-")
        .replace("△", "-")
        .replace("▲", "-")
        .replace("（", "(")
        .replace("）", ")")
    )
    if not text:
        return None
    # A dash in a reported current-period statement cell is an explicit nil,
    # not missing data.  Returning ``None`` made ``_row_value`` skip to the
    # next right-aligned cell, which is commonly the prior-year amount.
    if text in {"-", "—", "–"}:
        return 0.0
    negative = False
    prefix_match = re.match(r"^\(\s*-\s*\)\s*", text)
    if prefix_match:
        negative = True
        text = text[prefix_match.end():]
    elif text.startswith("(") and text.endswith(")"):
        negative = True
        text = text[1:-1]
    text = text.strip("()").replace(",", "").replace(" ", "")
    if not re.fullmatch(r"[-+]?\d+(?:\.\d+)?", text):
        return None
    number = float(text)
    if negative:
        return -abs(number) if number != 0.0 else 0.0
    return number


def _metric_for_balance(label: str) -> Optional[str]:
    compact = _label(label)
    # Preserve explicitly named aggregates and maturity components separately.
    # An unqualified Korean caption does not prove a current-only balance.
    liability_label = re.sub(r"^\d+[)]*", "", compact)
    liability_label = re.sub(r"\(주석[^)]*\)$", "", liability_label)
    float_labels = {
        "매입채무및기타채무": "trade_and_other_payables",
        "유동매입채무및기타채무": "trade_and_other_current_payables",
        "비유동매입채무및기타채무": "trade_and_other_non_current_payables",
        "계약부채": "contract_liabilities",
        "유동계약부채": "current_contract_liabilities",
        "비유동계약부채": "non_current_contract_liabilities",
    }
    if liability_label in float_labels:
        return float_labels[liability_label]
    if "현금및현금성자산" in compact:
        return "cash_and_equivalents"
    if "비유동자산" in compact and "총계" not in compact:
        return "non_current_assets"
    if "유동자산" in compact and "비유동" not in compact and "총계" not in compact:
        return "current_assets"
    if "자산총계" in compact:
        return "total_assets"
    if "비유동부채" in compact and "총계" not in compact:
        return "non_current_liabilities"
    if "유동부채" in compact and "비유동" not in compact and "총계" not in compact:
        return "current_liabilities"
    if "부채총계" in compact:
        return "total_liabilities"
    if "부채" in compact and "자본" in compact and ("총계" in compact or "합계" in compact):
        return "total_liabilities_and_equity"
    if "자본총계" in compact:
        return "total_equity"
    if any(term in compact for term in (
        "단기차입금", "유동차입금", "유동성장기차입금", "유동성사채",
        "단기차입채무", "유동차입채무",
    )):
        return "current_borrowings"
    if (
        any(term in compact for term in ("장기차입금", "장기차입채무"))
        and "유동성" not in compact
    ):
        return "non_current_borrowings"
    # Conversion-right consideration is equity, despite containing the word bond.
    if "전환사채권대가" in compact:
        return "equity_conversion_component"
    if "상환할증금" in compact:
        return "bond_redemption_premium"
    if "전환권조정" in compact:
        return "bond_conversion_adjustment"
    if "사채" in compact and "유동성" not in compact:
        return "non_current_borrowings"
    if "차입금" in compact or "차입채무" in compact:
        return "generic_borrowings"
    if "리스부채" in compact:
        return (
            "non_current_lease_liabilities" if "비유동" in compact
            else "current_lease_liabilities"
        )
    return None


def _metric_for_income(label: str) -> Optional[str]:
    compact = _label(label)
    if "법인세비용차감전" in compact:
        return "profit_before_tax"
    if (
        compact.startswith(("법인세비용", "법인세수익", "법인세이익"))
        and not any(term in compact for term in (
            "차감전", "차감후", "기타포괄", "법인세효과", "조정",
        ))
    ):
        return "income_tax_expense"
    if (
        (
            "당기순이익" in compact
            or "당기순손익" in compact
            or "당기순손실" in compact
        )
        and not any(term in compact for term in (
            "귀속", "지배기업", "비지배", "계속영업", "중단영업",
            "관계기업", "지분", "증가", "감소", "배분",
        ))
    ):
        return "net_income"
    if "영업이익" in compact or "영업손실" in compact:
        return "operating_profit"
    if "매출총이익" in compact or "매출총손실" in compact:
        return "gross_profit"
    if "판매비와관리비" in compact or compact == "영업비용":
        return "selling_general_administrative_expenses"
    if "매출원가" in compact or "영업원가" in compact:
        return "cost_of_revenue"
    revenue_label = re.sub(
        r"(?:\(주(?:석)?(?:\d+(?:[,\s과와및~-]\d+)+,?|\d+)\)|"
        r"<주(?:석)?(?:\d+(?:[,\s과와및~-]\d+)+,?|\d+)>)$",
        "",
        compact,
    )
    if revenue_label in {"매출", "매출액", "영업수익", "수익(매출액)", "수익"}:
        return "revenue"
    return None


_REVENUE_COMPONENT_RE = re.compile(
    r"^(?:\(?[0-9a-zA-ZⅠ-ⅩivxIVX가나다라마바사아자차카타파하]+\)?[.)·ㆍ-]*)?"
    r"(?:제품|상품|용역|공사|기타)\s*매출(?:액)?$"
)


def _is_revenue_component(label: str) -> bool:
    cleaned = re.sub(r"\s+", "", str(label or ""))
    cleaned = re.sub(
        r"(?:\(주(?:석)?(?:\d+(?:[,\s과와및~-]\d+)+,?|\d+)\)|"
        r"<주(?:석)?(?:\d+(?:[,\s과와및~-]\d+)+,?|\d+)>)$",
        "",
        cleaned,
    )
    return bool(_REVENUE_COMPONENT_RE.fullmatch(cleaned))


def _extract_revenue_components(
    income_table,
    income_scale: float,
    gross_profit: Optional[float],
    cost_of_revenue: Optional[float],
) -> tuple[Optional[float], list[dict[str, Any]]]:
    if gross_profit is None or cost_of_revenue is None:
        return None, []
    current_column = _current_column(income_table)
    table_rows = []
    for row_idx, tr in enumerate(income_table.find_all("tr")):
        cells = tr.find_all(["th", "td", "te", "tu"], recursive=False)
        if len(cells) < 2:
            continue
        label = cells[0].get_text(" ", strip=True)
        val = _row_value(cells, current_column)
        table_rows.append({
            "label": label,
            "value": val,
            "row_index": row_idx,
        })
    cogs_idx = next(
        (i for i, r in enumerate(table_rows) if _metric_for_income(r["label"]) == "cost_of_revenue"),
        None,
    )
    if cogs_idx is None:
        return None, []
    comp_rows = []
    for i in range(cogs_idx - 1, -1, -1):
        r = table_rows[i]
        if _is_revenue_component(r["label"]) and r["value"] is not None:
            comp_rows.append(r)
        else:
            break
    if not comp_rows:
        return None, []
    comp_rows.reverse()
    sum_raw = sum(r["value"] for r in comp_rows)
    sum_scaled = sum_raw * income_scale
    cost_abs = abs(cost_of_revenue)
    diff = abs((sum_scaled - cost_abs) - gross_profit)
    tol = max(1.0, 1e-6 * abs(sum_scaled))
    if diff <= tol:
        return sum_scaled, comp_rows
    return None, []


_DEBT_NOTE_MOVEMENT_TERMS = (
    "증가", "감소", "상환", "발행", "지급", "이자", "현금흐름",
    "공정가치", "위험", "만기", "약정", "담보", "조정", "상계", "회계정책",
    "차입금의차입", "신규차입",
)


def _metric_for_debt_note(label: str) -> Optional[str]:
    """Map closing-balance debt note rows, excluding movement disclosures."""
    compact = _label(label)
    if any(term in compact for term in _DEBT_NOTE_MOVEMENT_TERMS):
        return None
    if "리스부채" in compact:
        if "비유동" in compact:
            return "non_current_lease_liabilities"
        if "유동" in compact:
            return "current_lease_liabilities"
        return "generic_lease_liabilities"
    if any(term in compact for term in (
        "단기차입금", "유동성장기차입금", "유동성사채",
        "단기차입채무", "유동차입채무",
    )):
        return "current_borrowings"
    if (
        any(term in compact for term in ("장기차입금", "장기차입채무"))
        and "유동성" not in compact
    ):
        return "non_current_borrowings"
    if "사채" in compact and "유동성" not in compact:
        return "non_current_borrowings"
    if "차입금" in compact or "차입채무" in compact:
        return "generic_borrowings"
    return None


def _current_column(table) -> Optional[tuple[int, int]]:
    rows = table.find_all("tr")[:5]
    for row in rows:
        cells = row.find_all(["th", "td", "te", "tu"], recursive=False)
        column = 0
        for cell in cells:
            span = max(1, int(cell.get("colspan") or 1))
            if column > 0:
                text = cell.get_text(" ", strip=True)
                if "(당)" in text or "당기" in text or "당 기" in text:
                    return column, column + span
            column += span

    for row in rows:
        cells = row.find_all(["th", "td", "te", "tu"], recursive=False)
        column = 0
        term_cells = []
        for cell in cells:
            span = max(1, int(cell.get("colspan") or 1))
            if column > 0:
                text = cell.get_text(" ", strip=True)
                match = re.search(r"제\s*(\d+)\s*기", text)
                if match:
                    term_cells.append((int(match.group(1)), column, column + span))
            column += span
        if len(term_cells) >= 2:
            term_cells.sort(key=lambda item: item[0], reverse=True)
            return term_cells[0][1], term_cells[0][2]

    return None


def _row_value(cells, current_column: Optional[tuple[int, int]]) -> Optional[float]:
    if current_column is not None:
        # Older statements put detail and subtotal columns under one year.
        # Search only that header span, never a different year's numeric cell.
        start, end = current_column
        column = 0
        values = []
        for cell in cells:
            span = max(1, int(cell.get("colspan") or 1))
            if start <= column and column + span <= end:
                value = _number(cell.get_text(" ", strip=True))
                if value is not None:
                    values.append(value)
            column += span
        return values[-1] if values else None
    right_aligned = [
        cell for cell in cells[1:]
        if str(cell.get("align") or "").lower() == "right"
    ]
    for cell in right_aligned:
        value = _number(cell.get_text(" ", strip=True))
        if value is not None:
            return value
    return None


def _table_records(table, mapper):
    current_column = _current_column(table)
    records = []
    for row_index, row in enumerate(table.find_all("tr")):
        cells = row.find_all(["th", "td", "te", "tu"], recursive=False)
        if len(cells) < 2:
            continue
        label = cells[0].get_text(" ", strip=True)
        metric = mapper(label)
        value = _row_value(cells, current_column)
        if metric is not None and value is not None:
            records.append({
                "metric": metric, "label": label, "value": value,
                "row_index": row_index,
            })
    return records


def _best_table(tables, mapper, required: set[str]):
    candidates = []
    for index, table in enumerate(tables):
        records = _table_records(table, mapper)
        metrics = {record["metric"] for record in records}
        score = (len(metrics & required), len(metrics), -index)
        candidates.append((score, index, table, records))
    winner = max(candidates, default=None, key=lambda item: item[0])
    if winner is None or winner[0][0] < max(2, len(required) - 1):
        return None
    return winner[1:]


def _scale_near(tables, table_index: int) -> tuple[Optional[float], Optional[str]]:
    for index in range(table_index, max(-1, table_index - 4), -1):
        text = tables[index].get_text(" ", strip=True)
        match = _UNIT.search(re.sub(r"\s+", "", text))
        if match:
            unit = match.group(1)
            return _SCALE[unit], f"reported:{unit}"
    # Note units are commonly emitted as a P/SPAN immediately before the
    # table rather than inside a preceding one.  Search backwards in document
    # order so a nearby note-specific unit wins over the primary-statement
    # unit much farther up the report.
    for element in tables[table_index].find_all_previous(
        ["p", "div", "span", "table"], limit=80
    ):
        match = _UNIT.search(re.sub(r"\s+", "", element.get_text(" ", strip=True)))
        if match:
            unit = match.group(1)
            return _SCALE[unit], f"reported:{unit}"
    return None, None


def _debt_note_components(
    tables, *, balance_table_index: int
) -> tuple[dict[str, float], dict[str, list[dict[str, Any]]]]:
    """Best exact closing-balance debt evidence from machine-readable notes.

    The same balance may be repeated in maturity, fair-value and accounting
    policy tables.  We therefore take the largest exact closing aggregate per
    debt bucket rather than summing across tables.  Movement rows are rejected
    by ``_metric_for_debt_note``.
    """
    best: dict[str, tuple[float, list[dict[str, Any]]]] = {}
    for table_index, table in enumerate(tables):
        if table_index == balance_table_index:
            continue
        records = _table_records(table, _metric_for_debt_note)
        if not records:
            continue
        scale, scale_source = _scale_near(tables, table_index)
        if scale is None:
            continue
        grouped: dict[str, list[dict[str, Any]]] = {}
        for record in records:
            grouped.setdefault(record["metric"], []).append(record)
        for metric, items in grouped.items():
            value = sum(abs(float(item["value"]) * scale) for item in items)
            evidence = [{
                "label": item["label"],
                "table_index": table_index,
                "source_unit_scale": scale,
                "source_unit_scale_source": scale_source,
                "source": "debt_note_closing_balance",
            } for item in items]
            previous = best.get(metric)
            if previous is None or value > previous[0]:
                best[metric] = (value, evidence)
    return (
        {metric: value for metric, (value, _) in best.items()},
        {metric: evidence for metric, (_, evidence) in best.items()},
    )


def _identity(actual, derived):
    if actual is None or derived is None:
        return "insufficient_components", None
    ratio = abs(actual - derived) / max(abs(actual), abs(derived), 1.0)
    return ("pass" if ratio <= 0.01 else "fail"), ratio


def _tax_identity(pbt, tax, net):
    if pbt is None or tax is None or net is None:
        return "insufficient_components", None, None
    candidates = {"subtract": pbt - tax, "add": pbt + tax}
    convention, derived = min(
        candidates.items(), key=lambda item: abs(item[1] - net)
        / max(abs(item[1]), abs(net), 1.0)
    )
    status, ratio = _identity(net, derived)
    return status, ratio, convention


def _sum_present(*values):
    present = [value for value in values if value is not None]
    return sum(present) if present else None


def _semantic_statement_sign(metric: str, label: str, value: float) -> float:
    compact = _label(label)
    unambiguous_loss = "손실" in compact and "이익" not in compact
    if metric in {
        "gross_profit", "operating_profit", "profit_before_tax", "net_income"
    } and unambiguous_loss and value > 0:
        return -value
    return value


def _operating_bridge(table, scale: float, gross: float, operating: float):
    current_column = _current_column(table)
    rows = []
    for row in table.find_all("tr"):
        cells = row.find_all(["th", "td", "te", "tu"], recursive=False)
        if len(cells) < 2:
            continue
        label = cells[0].get_text(" ", strip=True)
        value = _row_value(cells, current_column)
        level = cells[0].get("alevel")
        rows.append((label, value, str(level) if level is not None else None))
    gross_index = next((i for i, (label, _, _) in enumerate(rows)
                        if _metric_for_income(label) == "gross_profit"), None)
    op_index = next((i for i, (label, _, _) in enumerate(rows)
                     if _metric_for_income(label) == "operating_profit"), None)
    if gross_index is None or op_index is None or op_index <= gross_index:
        return None, []
    effects = []
    between = rows[gross_index + 1:op_index]
    # DART FINANCE tables often place an ALEVEL=0 SGA total immediately
    # before its ALEVEL=1 account breakdown.  Summing both counts the same
    # expense twice.  Regular audit-report tables omit ALEVEL and are already
    # flat, so those rows remain eligible.
    has_levelled_total = any(level == "0" for _, _, level in between)
    for label, raw_value, level in between:
        if raw_value is None:
            continue
        if has_levelled_total and level not in {None, "0"}:
            continue
        compact = _label(label)
        value = raw_value * scale
        is_income = (
            any(term in compact for term in ("수익", "이익"))
            and not any(term in compact for term in ("비용", "손실", "차손"))
        )
        mapped = _metric_for_income(label)
        if mapped == "selling_general_administrative_expenses":
            # A total SGA line is an expense even where the filing displays it
            # parenthesized; normalize both presentation conventions.
            effect = -abs(value)
        else:
            # A negative impairment-loss row is a reversal and is therefore
            # added back when the expense is subtracted.
            effect = value if is_income else -value
        effects.append({"label": label, "value": value, "effect": effect})
    return gross + sum(item["effect"] for item in effects), effects


_BOMS: tuple[tuple[bytes, str], ...] = (
    (codecs.BOM_UTF8, "utf-8-sig"),
    (codecs.BOM_UTF32_LE, "utf-32-le"),
    (codecs.BOM_UTF32_BE, "utf-32-be"),
    (codecs.BOM_UTF16_LE, "utf-16-le"),
    (codecs.BOM_UTF16_BE, "utf-16-be"),
)
_XML_DECL_RE = re.compile(
    rb'<\?xml[^>]+encoding\s*=\s*["\']([a-zA-Z0-9_\-.]+)["\']',
    re.IGNORECASE,
)
_HTML_META_RE = re.compile(
    rb'<\s*meta[^>]+charset\s*=\s*["\']?([a-zA-Z0-9_\-.]+)',
    re.IGNORECASE,
)


def _decode_payload(payload: bytes) -> tuple[str | bytes, str]:
    if not isinstance(payload, bytes):
        return payload, "str"

    # 1. Byte-order mark (BOM)
    for bom, bom_codec in _BOMS:
        if payload.startswith(bom):
            try:
                return payload.decode(bom_codec, errors="strict"), bom_codec
            except UnicodeDecodeError:
                break

    # 1 (cont). Declared XML/HTML encoding in first ~2 KB
    head = payload[:2048]
    match = _XML_DECL_RE.search(head) or _HTML_META_RE.search(head)
    if match:
        declared = match.group(1).decode("ascii", errors="ignore").strip().lower()
        try:
            codecs.lookup(declared)
            return payload.decode(declared, errors="strict"), declared
        except (LookupError, UnicodeDecodeError):
            pass

    # 2. Strict utf-8
    try:
        return payload.decode("utf-8", errors="strict"), "utf-8"
    except UnicodeDecodeError:
        pass

    # 3. Strict cp949 (superset of euc-kr)
    try:
        return payload.decode("cp949", errors="strict"), "cp949"
    except UnicodeDecodeError:
        pass

    # 4. Fallback to existing behavior (pass bytes unchanged)
    return payload, "fallback"


_decode_xml_payload = _decode_payload


def extract_raw_xml_row(
    payload: bytes,
    target: dict[str, Any],
    *,
    scope: str,
) -> dict[str, Any]:
    # DART's custom XML schema intentionally embeds HTML-like TABLE markup;
    # html.parser is more tolerant of historical malformed documents than an
    # XML parser while preserving the table tree we need.
    content, _ = _decode_payload(payload)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", XMLParsedAsHTMLWarning)
        soup = BeautifulSoup(content, "html.parser")
    tables = soup.find_all("table")
    balance = _best_table(
        tables, _metric_for_balance,
        {"total_assets", "total_liabilities", "total_equity"},
    )
    income = _best_table(
        tables, _metric_for_income,
        {"operating_profit", "profit_before_tax", "net_income"},
    )
    if balance is None or income is None:
        raise ValueError("primary balance or income statement table not found")
    balance_index, _balance_table, balance_records = balance
    income_index, income_table, income_records = income
    balance_scale, balance_scale_source = _scale_near(tables, balance_index)
    income_scale, income_scale_source = _scale_near(tables, income_index)
    if balance_scale is None or income_scale is None:
        raise ValueError("statement unit scale not found")

    row: dict[str, Any] = {
        "market": "DART",
        "filing_id": str(target.get("filing_id") or ""),
        "corp_code": target.get("corp_code"),
        "ticker": str(target.get("stock_code") or ""),
        "company_name": target.get("company_name"),
        "publication_date": target.get("filing_date"),
        "report_name": target.get("report_nm"),
        "period_end": period_end_from_report_name(target.get("report_nm")),
        "statement_scope": scope,
        "fs_div": "CFS" if scope == "consolidated" else "OFS",
        "currency": "KRW",
        "currency_resolution_status": "reported:korean-audit-report",
        "unit_scale": 1.0,
        "unit_scale_source": "normalized:raw-dart-xml",
        "source_unit_scale": balance_scale,
        "source_unit_scale_source": balance_scale_source,
        "income_source_unit_scale": income_scale,
        "income_source_unit_scale_source": income_scale_source,
        "extraction_method": "raw_dart_audit_report_xml",
    }
    provenance = {}
    debt_components: dict[str, list[float]] = {}
    for record in balance_records:
        metric = record["metric"]
        value = record["value"] * balance_scale
        if metric in {
            "current_borrowings", "non_current_borrowings",
            "current_lease_liabilities", "non_current_lease_liabilities",
            "generic_borrowings", "generic_lease_liabilities",
        }:
            debt_components.setdefault(metric, []).append(value)
        elif metric not in row:
            row[metric] = value
        provenance.setdefault(metric, []).append({
            "label": record["label"], "table_index": balance_index,
            "source_unit_scale": balance_scale,
        })
    for record in income_records:
        metric = record["metric"]
        if metric not in row:
            value = record["value"] * income_scale
            row[metric] = _semantic_statement_sign(
                metric, record["label"], value
            )
        provenance.setdefault(metric, []).append({
            "label": record["label"], "table_index": income_index,
            "source_unit_scale": income_scale,
        })

    has_total_revenue = any(record["metric"] == "revenue" for record in income_records)
    if "revenue" not in row and not has_total_revenue:
        comp_revenue, comp_records = _extract_revenue_components(
            income_table,
            income_scale,
            row.get("gross_profit"),
            row.get("cost_of_revenue"),
        )
        if comp_revenue is not None:
            row["revenue"] = comp_revenue
            row["revenue_from_components"] = True
            provenance["revenue"] = [{
                "label": record["label"],
                "table_index": income_index,
                "source_unit_scale": income_scale,
                "revenue_from_components": True,
            } for record in comp_records]
        else:
            row["revenue_from_components"] = False
    else:
        row["revenue_from_components"] = False
    for metric, values in debt_components.items():
        row[metric] = sum(values)

    face_debt_present = any(
        row.get(metric) is not None for metric in (
            "current_borrowings", "non_current_borrowings",
            "current_lease_liabilities", "non_current_lease_liabilities",
            "generic_borrowings", "generic_lease_liabilities",
        )
    )
    if not face_debt_present:
        note_debt, note_provenance = _debt_note_components(
            tables, balance_table_index=balance_index
        )
        for metric, value in note_debt.items():
            row[metric] = value
            provenance.setdefault(metric, []).extend(note_provenance[metric])

    split_borrowings = _sum_present(
        row.get("current_borrowings"), row.get("non_current_borrowings")
    )
    generic_borrowings = row.get("generic_borrowings")
    row["total_borrowings"] = (
        max(split_borrowings, generic_borrowings)
        if split_borrowings is not None and generic_borrowings is not None
        else split_borrowings if split_borrowings is not None
        else generic_borrowings
    )
    split_leases = _sum_present(
        row.get("current_lease_liabilities"),
        row.get("non_current_lease_liabilities"),
    )
    generic_leases = row.get("generic_lease_liabilities")
    row["total_lease_liabilities"] = (
        max(split_leases, generic_leases)
        if split_leases is not None and generic_leases is not None
        else split_leases if split_leases is not None
        else generic_leases
    )
    row["total_interest_bearing_debt"] = _sum_present(
        row.get("total_borrowings"), row.get("total_lease_liabilities")
    )
    row["debt_reconciliation_status"] = (
        "resolved_components" if row["total_interest_bearing_debt"] is not None
        else "unresolved_not_reported"
    )

    # Preserve face/carrying adjustments for source review. Their presence
    # does not prove which adjacent bond total already includes them.
    if any(row.get(k) not in (None, 0) for k in
           ("bond_redemption_premium", "bond_conversion_adjustment")):
        row["debt_reconciliation_status"] = "unresolved_bond_carrying_adjustments"

    financial = bool(_FINANCIAL_NAME_RE.search(str(row.get("company_name") or "")))
    row["industry_template"] = "financial_services" if financial else "industrial"
    row["industry_template_source"] = "company_name_heuristic"
    if financial:
        row["operating_profit_pbt_bridge"] = None
        row["operating_profit_closure_status"] = "not_applicable"
        row["operating_profit_closure_ratio"] = None
    else:
        bridge, components = _operating_bridge(
            income_table, income_scale, row.get("gross_profit"),
            row.get("operating_profit"),
        ) if row.get("gross_profit") is not None else (None, [])
        op_status, op_ratio = _identity(row.get("operating_profit"), bridge)
        simple_bridge = (
            row["gross_profit"]
            - abs(row["selling_general_administrative_expenses"])
            if row.get("gross_profit") is not None
            and row.get("selling_general_administrative_expenses") is not None
            else None
        )
        simple_status, simple_ratio = _identity(
            row.get("operating_profit"), simple_bridge
        )
        if op_status != "pass" and simple_status == "pass":
            bridge, op_status, op_ratio = (
                simple_bridge, simple_status, simple_ratio
            )
            components = [{
                "label": "gross_profit_minus_sga",
                "value": row["selling_general_administrative_expenses"],
                "effect": -abs(row["selling_general_administrative_expenses"]),
            }]
        row["operating_profit_pbt_bridge"] = bridge
        row["operating_profit_bridge_components"] = components
        row["operating_profit_closure_status"] = op_status
        row["operating_profit_closure_ratio"] = op_ratio

    balance_bridge = (
        row["total_liabilities"] + row["total_equity"]
        if row.get("total_liabilities") is not None
        and row.get("total_equity") is not None else None
    )
    balance_status, balance_ratio = _identity(
        row.get("total_assets"), balance_bridge
    )
    tax_status, tax_ratio, tax_convention = _tax_identity(
        row.get("profit_before_tax"), row.get("income_tax_expense"),
        row.get("net_income"),
    )
    row.update({
        "balance_identity_status": balance_status,
        "balance_identity_ratio": balance_ratio,
        "tax_identity_status": tax_status,
        "tax_identity_ratio": tax_ratio,
        "tax_sign_convention": tax_convention,
        "field_provenance": provenance,
        "field_provenance_json": json.dumps(
            provenance, ensure_ascii=False, sort_keys=True
        ),
    })
    debt_required = financial or row["debt_reconciliation_status"] == "resolved_components"
    row["screen_eligible"] = bool(
        row["filing_id"] and row["ticker"]
        and valid_annual_period_end(row["period_end"])
        and row["statement_scope"] in {"consolidated", "standalone"}
        and row["operating_profit_closure_status"] in {"pass", "not_applicable"}
        and row["tax_identity_status"] == "pass"
        and row["balance_identity_status"] == "pass"
        and debt_required
    )
    return row
