"""Statement-aware PSE EDGE Stage-02 V2 source and HTML-summary parsing.

PSE's stable issuer identity is ``company_id``.  The downloaded annual-report
surface contains both substantive PDF filings and EDGE-generated HTML summary
forms, so filename extensions and guessed capitalized tokens are not accepted
as identity or format evidence.  HTML forms provide useful reported summary
facts, but they do not prove interest-bearing debt and therefore remain
screen-ineligible until a substantive statement/notes source resolves debt.
"""
from __future__ import annotations

from collections import defaultdict
import csv
from datetime import datetime
import json
from pathlib import Path
import re
from typing import Any, Iterable, Optional

from bs4 import BeautifulSoup


_AMENDMENT = re.compile(r"(?:amend(?:ment|ed)?)[-\s]*(\d+)?", re.I)


def classify_payload(payload: bytes) -> str:
    """Route by content magic, never by a potentially misleading suffix."""
    head = payload[:4096].lstrip(b"\xef\xbb\xbf\x00\t\r\n ").lower()
    if head.startswith(b"%pdf"):
        return "pdf"
    if b"<html" in head or b"<!doctype" in head or b"<body" in head:
        return "html"
    return "unknown"


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _amendment_sequence(title: Any) -> int:
    match = _AMENDMENT.search(_clean(title))
    if not match:
        return 0
    return int(match.group(1) or 1)


def build_targets(metadata_path: Path) -> list[dict[str, Any]]:
    """Build a PSE manifest retaining issuer, amendment and transport facts."""
    targets: list[dict[str, Any]] = []
    with Path(metadata_path).open(
        newline="", encoding="utf-8", errors="replace"
    ) as handle:
        for record in csv.DictReader(handle):
            if _clean(record.get("market")) != "PSE_EDGE":
                continue
            try:
                raw = json.loads(record.get("raw_metadata") or "{}")
            except json.JSONDecodeError:
                raw = {}
            local_path = Path(_clean(record.get("local_path")))
            if not local_path.is_file():
                continue
            company_id = _clean(raw.get("company_id"))
            filing_id = _clean(record.get("filing_id") or raw.get("edge_no"))
            if not company_id or not filing_id:
                continue
            title = _clean(raw.get("title") or record.get("title"))
            targets.append({
                "market": "PSE_EDGE",
                "filing_id": filing_id,
                "filing_date": _clean(record.get("filing_date")),
                "company_id": company_id,
                "company_name": _clean(
                    raw.get("company_name") or record.get("company_name")
                ),
                "title": title,
                "form_number": _clean(
                    raw.get("form_number") or record.get("category")
                ),
                "report_number": _clean(raw.get("report_number")),
                "download_file_id": _clean(raw.get("download_file_id")),
                "download_file_label": _clean(raw.get("download_file_label")),
                "document_url": _clean(record.get("document_url")),
                "local_path": str(local_path),
                "source_size": local_path.stat().st_size,
                "source_kind": classify_payload(local_path.read_bytes()[:4096]),
                "amendment_sequence": _amendment_sequence(title),
            })
    return sorted(
        targets,
        key=lambda row: (
            row["company_id"], row["filing_date"],
            row["amendment_sequence"], row["filing_id"],
        ),
    )


def _number(value: Any) -> Optional[float]:
    text = _clean(value).replace("\u2212", "-")
    if not text:
        return None
    if text in {"-", "—", "–"}:
        return 0.0
    negative = text.startswith("(") and text.endswith(")")
    text = text.strip("()").replace(",", "").replace("PHP", "").strip()
    try:
        value = float(text)
    except ValueError:
        return None
    return -value if negative else value


def _period_end(value: Any) -> Optional[str]:
    text = _clean(value)
    for pattern in ("%b %d, %Y", "%B %d, %Y", "%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, pattern).date().isoformat()
        except ValueError:
            continue
    return None


def normalize_currency_unit(
    value: Any,
) -> tuple[Optional[str], float, str]:
    """Normalize EDGE's free-text currency field without losing its scale."""
    raw = re.sub(r"\s+", " ", _clean(value)).upper()
    if "USD" in raw or "US DOLLAR" in raw:
        currency = "USD"
    elif "PHP" in raw or "PESO" in raw:
        currency = "PHP"
    elif re.search(r"\bC\$|\bCAD\b|CANADIAN", raw):
        currency = "CAD"
    else:
        currency = None
    if re.search(r"(?:\b000(?:'S|S)?\b|THOUSAND)", raw):
        scale = 1_000.0
    elif re.search(r"\bMILLION|\bC\$M\b|\bC[S$]M\b(?=\s+EXCEPT)", raw):
        scale = 1_000_000.0
    elif re.search(r"\bBILLION", raw):
        scale = 1_000_000_000.0
    else:
        scale = 1.0
    return currency, scale, f"reported:{raw or 'missing'}"


def _row_map(table) -> tuple[dict[str, float], dict[str, str]]:
    values: dict[str, float] = {}
    labels: dict[str, str] = {}
    if table is None:
        return values, labels
    for tr in table.find_all("tr"):
        cells = tr.find_all(["th", "td"], recursive=False)
        if len(cells) < 2:
            continue
        label = re.sub(r"\s+", " ", cells[0].get_text(" ", strip=True)).strip()
        value = _number(cells[1].get_text(" ", strip=True))
        if label and value is not None:
            key = label.casefold()
            values.setdefault(key, value)
            labels.setdefault(key, label)
    return values, labels


def _lookup(values: dict[str, float], *labels: str) -> Optional[float]:
    for label in labels:
        value = values.get(label.casefold())
        if value is not None:
            return value
    return None


def _identity(actual: Optional[float], derived: Optional[float]) -> tuple[str, Optional[float]]:
    if actual is None or derived is None:
        return "insufficient_components", None
    ratio = abs(actual - derived) / max(abs(actual), abs(derived), 1.0)
    return ("pass" if ratio <= 0.01 else "fail"), ratio


def _tax_identity(
    pbt: Optional[float], tax: Optional[float], net: Optional[float]
) -> tuple[str, Optional[float], Optional[str]]:
    if pbt is None or tax is None or net is None:
        return "insufficient_components", None, None
    candidates = {"subtract": pbt - tax, "add": pbt + tax}
    convention, derived = min(
        candidates.items(), key=lambda item: abs(item[1] - net)
    )
    status, ratio = _identity(net, derived)
    return status, ratio, convention


def extract_html_summary(payload: bytes, target: dict[str, Any]) -> dict[str, Any]:
    """Extract the exact current-year values reported in an EDGE HTML form."""
    soup = BeautifulSoup(payload, "html.parser")
    period_end = None
    currency_text = None
    for tr in soup.find_all("tr"):
        cells = tr.find_all(["th", "td"], recursive=False)
        if len(cells) < 2:
            continue
        label = re.sub(r"\s+", " ", cells[0].get_text(" ", strip=True)).casefold()
        value = cells[1].get_text(" ", strip=True)
        if label == "for the fiscal year ended":
            period_end = _period_end(value)
        elif label == "currency":
            currency_text = value

    currency, source_scale, source_scale_source = normalize_currency_unit(
        currency_text
    )

    balance, _ = _row_map(soup.find("table", id="BS"))
    income, _ = _row_map(soup.find("table", id="IS"))
    current_assets = _lookup(balance, "Current Assets")
    total_assets = _lookup(balance, "Total Assets")
    current_liabilities = _lookup(balance, "Current Liabilities")
    total_liabilities = _lookup(balance, "Total Liabilities")
    total_equity = _lookup(
        balance, "Stockholders' Equity", "Total Stockholders' Equity"
    )
    revenue = _lookup(income, "Gross Revenue")
    gross_expense = _lookup(income, "Gross Expense")
    operating_profit = (
        revenue - gross_expense
        if revenue is not None and gross_expense is not None else None
    )
    pbt = _lookup(income, "Income/(Loss) Before Tax", "Income Before Tax")
    tax = _lookup(income, "Income Tax Expense", "Income Tax Benefit")
    net = _lookup(
        income, "Net Income/(Loss) After Tax", "Net Income After Tax"
    )
    scaled = {
        name: value * source_scale if value is not None else None
        for name, value in {
            "current_assets": current_assets,
            "total_assets": total_assets,
            "current_liabilities": current_liabilities,
            "total_liabilities": total_liabilities,
            "total_equity": total_equity,
            "revenue": revenue,
            "gross_expense": gross_expense,
            "operating_profit": operating_profit,
            "profit_before_tax": pbt,
            "income_tax_expense": tax,
            "net_income": net,
        }.items()
    }
    current_assets = scaled["current_assets"]
    total_assets = scaled["total_assets"]
    current_liabilities = scaled["current_liabilities"]
    total_liabilities = scaled["total_liabilities"]
    total_equity = scaled["total_equity"]
    revenue = scaled["revenue"]
    gross_expense = scaled["gross_expense"]
    operating_profit = scaled["operating_profit"]
    pbt = scaled["profit_before_tax"]
    tax = scaled["income_tax_expense"]
    net = scaled["net_income"]
    balance_status, balance_ratio = _identity(
        total_assets,
        total_liabilities + total_equity
        if total_liabilities is not None and total_equity is not None else None,
    )
    tax_status, tax_ratio, tax_convention = _tax_identity(pbt, tax, net)
    return {
        **target,
        "market": "PSE_EDGE",
        "source_kind": "html",
        "extraction_method": "pse_edge_html_summary",
        "statement_scope": "unknown",
        "reporting_basis": "unknown",
        "period_end": period_end,
        "currency": currency,
        "unit_scale": 1.0,
        "unit_scale_source": "normalized:pse-edge-html-summary",
        "source_unit_scale": source_scale,
        "source_unit_scale_source": source_scale_source,
        "current_assets": current_assets,
        "non_current_assets": (
            total_assets - current_assets
            if total_assets is not None and current_assets is not None else None
        ),
        "total_assets": total_assets,
        "current_liabilities": current_liabilities,
        "non_current_liabilities": (
            total_liabilities - current_liabilities
            if total_liabilities is not None and current_liabilities is not None
            else None
        ),
        "total_liabilities": total_liabilities,
        "total_equity": total_equity,
        "revenue": revenue,
        "gross_expense": gross_expense,
        "operating_profit": operating_profit,
        "operating_profit_derivation": (
            "gross_revenue_minus_gross_expense"
            if operating_profit is not None else None
        ),
        "operating_profit_closure_status": "derived_not_independently_reported",
        "profit_before_tax": pbt,
        "income_tax_expense": tax,
        "net_income": net,
        "tax_identity_status": tax_status,
        "tax_identity_ratio": tax_ratio,
        "tax_sign_convention": tax_convention,
        "balance_identity_status": balance_status,
        "balance_identity_ratio": balance_ratio,
        "total_interest_bearing_debt": None,
        "debt_reconciliation_status": "unresolved_not_reported",
        "screen_eligible": False,
        "field_provenance": {
            "balance": "PSE EDGE HTML table#BS current-year column",
            "income": "PSE EDGE HTML table#IS current-year column",
        },
    }


_AMOUNT_TOKEN = re.compile(r"(?<![A-Za-z])(?:[P₱$]\s*)?(\([^)]*\)|-?\d[\d,]*(?:\.\d+)?|[-—–])")


def _amounts(line: str) -> list[float]:
    values: list[float] = []
    for match in _AMOUNT_TOKEN.finditer(line):
        token = match.group(1).strip()
        if token in {"-", "—", "–"}:
            values.append(0.0)
            continue
        negative = token.startswith("(") and token.endswith(")")
        token = token.strip("()").replace(",", "")
        try:
            value = float(token)
        except ValueError:
            continue
        values.append(-value if negative else value)
    return values


def _current_amount(
    section: str, patterns: Iterable[str], year_count: int
) -> Optional[float]:
    for line in section.splitlines():
        if not any(re.search(pattern, line, re.I) for pattern in patterns):
            continue
        values = _amounts(line)
        if len(values) >= year_count:
            return values[-year_count]
    return None


def _all_current_amounts(
    section: str, patterns: Iterable[str], year_count: int
) -> list[float]:
    values: list[float] = []
    for line in section.splitlines():
        if not any(re.search(pattern, line, re.I) for pattern in patterns):
            continue
        amounts = _amounts(line)
        if len(amounts) >= year_count:
            values.append(amounts[-year_count])
    return values


def _statement_section(
    text: str, start_patterns: Iterable[str], stop_patterns: Iterable[str],
    extra_headers: Iterable[str] = (),
) -> str:
    start_patterns = tuple(start_patterns)
    stop_patterns = tuple(stop_patterns)
    starts = [
        match
        for pattern in start_patterns
        for match in re.finditer(pattern, text, re.I)
    ]
    if not starts:
        return ""
    all_headers = start_patterns + stop_patterns + (
        r"Consolidated Statements? of Financial Position",
        r"Consolidated Balance Sheets?",
        r"Consolidated Statements? of (?:Total )?Comprehensive Income",
        r"Consolidated Statements? of Income",
        r"Consolidated Statements? of Operations",
        r"Consolidated Statements? of Changes in Equity",
        r"Consolidated Statements? of Cash Flows",
    ) + tuple(extra_headers)

    candidates: list[tuple[int, int, str]] = []
    for start in starts:
        remainder = text[start.start():]
        after_header = text[start.end():]
        endings = [
            start.end() - start.start() + match.start()
            for pattern in all_headers
            for match in re.finditer(pattern, after_header, re.I)
        ]
        end = min(endings) if endings else min(len(remainder), 20_000)
        section = remainder[:end]
        numeric_lines = sum(
            len(_amounts(line)) >= 2 for line in section.splitlines()
        )
        anchors = len(
            re.findall(
                r"cash and cash equivalents|total assets|total liabilities|"
                r"operating (?:profit|income)|profit before|income tax|net income",
                section,
                re.I,
            )
        )
        candidates.append((numeric_lines + 3 * anchors, -start.start(), section))
    return max(candidates, key=lambda row: (row[0], row[1]))[2]


def _statement_year_count(section: str, default: int) -> int:
    header = "\n".join(section.splitlines()[:12])
    years = re.findall(r"\b(?:19|20)\d{2}\b", header)
    return max(default, min(len(set(years)), 3))


def _pdf_period_end(text: str) -> Optional[str]:
    patterns = (
        r"(?:as at|as of|ended|period ended)\s+([A-Z][a-z]+\s+\d{1,2},\s+20\d{2})",
        r"(?:as at|as of|ended|period ended)\s+(20\d{2}-\d{2}-\d{2})",
        r"(?m)^\s*([A-Z][a-z]+\s+\d{1,2},\s+20\d{2})(?:\s+and\b|\s*$)",
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            return _period_end(match.group(1))
    # T33: column header printed as "December 31" on one line and the year
    # columns ("2025   2024") on the next; the first year is the current-year
    # column, the same convention every amount extractor here uses.
    split = re.search(
        r"(?im)^\s*(?:(?:for\s+)?the\s+)?(?:years?\s+|periods?\s+)?"
        r"(?:ended\s+|as\s+(?:at|of)\s+)?([A-Z][a-z]+)\s+(\d{1,2})\s*,?\s*\n"
        r"\s*(20\d{2})\b",
        text,
    )
    if split:
        return _period_end(f"{split.group(1).title()} {int(split.group(2))}, {split.group(3)}")
    return None


def _pdf_currency_scale(text: str) -> tuple[Optional[str], float, str]:
    match = re.search(
        r"\((?:(?:All\s+)?amounts\s+)?(?:are\s+)?in\s+([^\n)]+)\)",
        text,
        re.I,
    )
    raw = match.group(1) if match else ""
    currency, scale, source = normalize_currency_unit(raw)
    return currency, scale, source


_PRESENTATION_PHP = re.compile(
    r"(?:(?:presented|expressed|reported)\s+in\s+(?:the\s+)?(?:philippine\s+)?(?:pesos?|php)\b"
    r"|presentation\s+currency[^.]{0,80}?(?:philippine\s+)?pesos?\b)",
    re.I,
)
_PRESENTATION_USD = re.compile(
    r"(?:(?:presented|expressed|reported)\s+in\s+(?:the\s+)?(?:united\s+states|u\.?s\.?)\s+dollars?\b"
    r"|presentation\s+currency[^.]{0,80}?(?:united\s+states|u\.?s\.?)\s+dollars?\b)",
    re.I,
)


def _pdf_policy_currency(text: str) -> Optional[str]:
    """Presentation currency stated in the accounting policy note.

    Only explicit presentation phrases count (a subsidiary's functional
    currency is not evidence); conflicting phrases leave the currency open.
    """
    flat = re.sub(r"\s+", " ", text)
    php = bool(_PRESENTATION_PHP.search(flat))
    usd = bool(_PRESENTATION_USD.search(flat))
    if php == usd:
        return None
    return "PHP" if php else "USD"


def _short_term_loans_zero(text: str, year_count: int) -> bool:
    section_match = re.search(
        r"Short[- ]term loans(?P<body>.{0,1800}?)(?:Long[- ]term loans|\f|$)",
        text,
        re.I | re.S,
    )
    if not section_match:
        return False
    value = _current_amount(
        section_match.group("body"), (r"^\s*Ending balance\b",), year_count
    )
    return value == 0.0


_END = r"\s*[.:]?\s*$"
_STANDALONE_BS = (r"(?m)^\s*Statements? of Financial (?:Position|Condition)" + _END,
                  r"(?m)^\s*Balance Sheets?" + _END)
_STANDALONE_IS = (r"(?m)^\s*Statements? of (?:Total )?Comprehensive Income(?: \(Loss\))?" + _END,
                  r"(?m)^\s*Statements? of Income" + _END,
                  r"(?m)^\s*Statements? of Operations" + _END)
_STANDALONE_END = (r"(?m)^\s*Statements? of Changes in (?:Equity|Stockholders'? Equity)" + _END,
                   r"(?m)^\s*Statements? of Cash Flows" + _END)
_STANDALONE_ALL = _STANDALONE_BS + _STANDALONE_IS + _STANDALONE_END
_CONSOLIDATED_ANY = re.compile(
    r"consolidated\s+(?:statements?\s+of\s+(?:financial\s+(?:position|condition)|"
    r"(?:total\s+)?comprehensive\s+income|income|operations)|balance\s+sheets?)", re.I)


def _has_consolidated_heading(text: str) -> bool:
    # any mention (heading or prose) means a consolidated set exists or is referenced: fail closed
    return bool(_CONSOLIDATED_ANY.search(re.sub(r"\s+", " ", text)))


def extract_pdf_statement_text(
    text: str,
    target: dict[str, Any],
    *,
    source_sha256: Optional[str] = None,
) -> dict[str, Any]:
    """Extract a fail-closed PSE row from primary statement text.

    This parser deliberately targets statement labels and note closure.  It
    does not promote narrative phrase hits and it never fills an absent debt
    bucket with zero.
    """
    balance = _statement_section(
        text,
        (
            r"(?m)^\s*Consolidated Statements? of Financial Position\s*$",
            r"(?m)^\s*Consolidated Balance Sheets?\s*$",
        ),
        (
            r"(?m)^\s*Consolidated Statements? of (?:Total )?Comprehensive Income\s*$",
            r"(?m)^\s*Consolidated Statements? of Income\s*$",
            r"(?m)^\s*Consolidated Statements? of Operations\s*$",
        ),
    )
    income = _statement_section(
        text,
        (
            r"(?m)^\s*Consolidated Statements? of (?:Total )?Comprehensive Income\s*$",
            r"(?m)^\s*Consolidated Statements? of Income\s*$",
            r"(?m)^\s*Consolidated Statements? of Operations\s*$",
        ),
        (
            r"(?m)^\s*Consolidated Statements? of Changes in Equity\s*$",
            r"(?m)^\s*Consolidated Statements? of Cash Flows\s*$",
        ),
    )
    statement_scope = "consolidated"
    if not (balance or income) and not _has_consolidated_heading(text):
        # T33c: filer has no consolidated set -> accept exact, unprefixed standalone headings.
        balance = _statement_section(text, _STANDALONE_BS, _STANDALONE_IS, _STANDALONE_ALL)
        income = _statement_section(text, _STANDALONE_IS, _STANDALONE_END, _STANDALONE_ALL)
        if balance or income:
            statement_scope = "standalone"
    balance_years = _statement_year_count(balance, 2)
    income_years = _statement_year_count(income, 2)
    # A filing/notice date is not a financial-statement period. Preserve
    # unsupported documents for coverage accounting without inventing scope.
    document_classification = "primary_statement_text" if balance or income else "no_primary_statements_found"
    if not (balance or income) and re.search(r"SEC\s+FORM\s+17[- ]L\b", text, re.I):
        document_classification = "filing_delay_notice_without_statements"
    period_end = _pdf_period_end(balance or income)
    currency, source_scale, scale_source = _pdf_currency_scale(
        (balance + "\n" + income) or text
    )
    currency_source = "statement_header" if currency else None
    # A policy-note currency is accepted only beside an explicit statement unit
    # header: a missing header means the scale is unproven (whole units assumed).
    if (currency is None and document_classification == "primary_statement_text"
            and scale_source != "reported:missing"):
        currency = _pdf_policy_currency(text)
        currency_source = "document_accounting_policy" if currency else None

    def amount(section: str, patterns: Iterable[str], years: int) -> Optional[float]:
        value = _current_amount(section, patterns, years)
        return value * source_scale if value is not None else None

    # Scope captions to their primary-statement maturity section. Notes or
    # another maturity bucket must not win by document order.
    float_components = {}
    for bucket, start in (("current", r"(?m)^\s*Current liabilities\s*$"),
                          ("non_current", r"(?m)^\s*Non[- ]current liabilities\s*$")):
        section = _statement_section(balance, (start,),
            (r"(?m)^\s*(?:Non[- ]current liabilities|Current liabilities|Equity)\s*$",))
        for stem, pattern in (("trade_and_other", r"^\s*Trade and other payables\b"),
                              ("contract", r"^\s*Contract liabilities\b")):
            value = amount(section, (pattern,), balance_years)
            if value is not None:
                field = (f"trade_and_other_{bucket}_payables" if stem == "trade_and_other"
                         else f"{bucket}_contract_liabilities")
                float_components[field] = value

    cash = amount(balance, (r"^\s*Cash and cash equivalents\b",), balance_years)
    total_assets = amount(balance, (r"^\s*Total assets\b",), balance_years)
    total_liabilities = amount(
        balance, (r"^\s*Total liabilities\s*$", r"^\s*Total liabilities\s+[-(P₱$\d]"),
        balance_years,
    )
    total_equity = amount(balance, (r"^\s*Total equity\b",), balance_years)
    total_liabilities_equity = amount(
        balance, (r"^\s*Total liabilities and equity\b",), balance_years
    )
    nci = amount(
        balance, (r"^\s*Non[- ]controlling interests\b",), balance_years
    )
    operating_profit = amount(
        income, (r"^\s*Operating (?:profit|income)\b",), income_years
    )
    pbt = amount(
        income,
        (r"^\s*(?:Profit|Income) before income tax(?:es)?\b", r"^\s*Profit before tax\b"),
        income_years,
    )
    tax = amount(
        income,
        (
            r"^\s*Income tax (?:expense|benefit)\b",
            r"^\s*Tax (?:expense|benefit)\b",
            r"^\s*Provision for income tax\b",
        ),
        income_years,
    )
    net = amount(
        income,
        (
            r"^\s*Net income(?: for the year)?\b",
            r"^\s*Net profit(?: for the year)?\b",
        ),
        income_years,
    )

    loan_current = amount(
        balance,
        (
            r"^\s*Long[- ]term loans payable\b",
            r"^\s*Current portion of (?:long[- ]term )?(?:loans|borrowings)\b",
        ),
        balance_years,
    )
    lease_current = amount(
        balance,
        (r"^\s*Lease liabilities\b(?!,?\s+net of current portion)",),
        balance_years,
    )
    lease_noncurrent = amount(
        balance,
        (r"^\s*Lease liabilities,?\s+net of current portion\b",),
        balance_years,
    )
    short_term_zero = _short_term_loans_zero(text, income_years)
    debt_parts = (loan_current, lease_current, lease_noncurrent)
    if all(value is not None for value in debt_parts) and short_term_zero:
        debt = sum(float(value) for value in debt_parts)
        debt_status = "resolved_statement_components_plus_note_zero"
    else:
        recognized_loans = _all_current_amounts(
            balance,
            (
                r"^\s*Interest[- ]bearing loans?\b",
                r"^\s*(?:Short|Long)[- ]term (?:loans?|borrowings?)\b",
                r"^\s*Loans? payable\b",
                r"^\s*Notes? payable\b",
                r"^\s*Bank loans?\b",
                r"^\s*Borrowings?\b",
                r"^\s*Current portion of (?:long[- ]term )?(?:loans?|borrowings?)\b",
            ),
            balance_years,
        )
        recognized_leases = _all_current_amounts(
            balance,
            (r"^\s*Lease liabilities\b",),
            balance_years,
        )
        complete_liability_sections = bool(
            re.search(r"^\s*Current liabilities\s*$", balance, re.I | re.M)
            and re.search(
                r"^\s*Non[- ]current liabilities\s*$",
                balance,
                re.I | re.M,
            )
        )
        if (
            recognized_loans
            and len(recognized_leases) == 2
            and complete_liability_sections
        ):
            debt = (
                sum(recognized_loans) + sum(recognized_leases)
            ) * source_scale
            debt_status = "resolved_primary_statement_recognized_components"
        else:
            debt = None
            debt_status = "unresolved_borrowings"

    balance_derived = (
        total_liabilities + total_equity
        if total_liabilities is not None and total_equity is not None else None
    )
    balance_status, balance_ratio = _identity(total_assets, balance_derived)
    second_balance_status, second_balance_ratio = _identity(
        total_assets, total_liabilities_equity
    )
    if balance_status != "pass" and second_balance_status == "pass":
        balance_status, balance_ratio = second_balance_status, second_balance_ratio
    tax_status, tax_ratio, tax_convention = _tax_identity(pbt, tax, net)
    screen_eligible = bool(
        period_end
        and currency
        and cash is not None
        and debt is not None
        and operating_profit is not None
        and pbt is not None
        and tax is not None
        and tax_status == "pass"
        and balance_status == "pass"
    )
    return {
        **target,
        "market": "PSE_EDGE",
        "source_kind": "pdf",
        "extraction_method": "pse_primary_statement_text_v2",
        "document_classification": document_classification,
        "statement_scope": statement_scope,
        "reporting_basis": statement_scope,
        "period_end": period_end,
        "publication_date": target.get("filing_date"),
        "currency": currency,
        "currency_source": currency_source,
        "unit_scale": 1.0,
        "unit_scale_source": "normalized:pse-pdf-primary-statements",
        "source_unit_scale": source_scale,
        "source_unit_scale_source": scale_source,
        "cash_and_equivalents": cash,
        **float_components,
        "total_assets": total_assets,
        "total_liabilities": total_liabilities,
        "total_equity": total_equity,
        "non_controlling_interest": nci,
        "operating_profit": operating_profit,
        "operating_profit_closure_status": (
            "pass" if operating_profit is not None else "missing_direct_subtotal"
        ),
        "profit_before_tax": pbt,
        "income_tax_expense": tax,
        "net_income": net,
        "tax_identity_status": tax_status,
        "tax_identity_ratio": tax_ratio,
        "tax_sign_convention": tax_convention,
        "balance_identity_status": balance_status,
        "balance_identity_ratio": balance_ratio,
        "total_interest_bearing_debt": debt,
        "debt_reconciliation_status": debt_status,
        "short_term_loans_zero_evidence": short_term_zero,
        "source_sha256": source_sha256,
        "screen_eligible": screen_eligible,
        "field_provenance": {
            "balance": "primary consolidated statement current-year column",
            "income": "primary consolidated statement current-year column",
            "debt": "primary liabilities plus loans-note ending balance",
        },
    }


def finalize_pse_rows(
    rows: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Select the latest reported amendment per stable company and period."""
    finalized = [dict(row) for row in rows]
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in finalized:
        if not bool(row.get("screen_eligible")):
            row["finality_status"] = "source_contract_failed"
            continue
        company_id = _clean(row.get("company_id"))
        period_end = _clean(row.get("period_end"))
        if not company_id or not period_end:
            row["screen_eligible"] = False
            row["finality_status"] = "issuer_period_missing"
            continue
        groups[(company_id, period_end)].append(row)

    for candidates in groups.values():
        selected = max(
            candidates,
            key=lambda row: (
                _clean(row.get("filing_date")),
                int(row.get("amendment_sequence") or 0),
                _clean(row.get("filing_id")),
            ),
        )
        for row in candidates:
            if row is selected:
                row["finality_status"] = "latest_amendment_for_company_period"
                row["screen_eligible"] = True
            else:
                row["finality_status"] = "superseded_company_period"
                row["screen_eligible"] = False
    return finalized
