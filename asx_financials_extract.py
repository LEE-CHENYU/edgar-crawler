"""ASX annual-report financial extractor (pdftotext + regex, first pass).

Australian filings are PDF-only (AASB ≈ IFRS, English). This mirrors the HK
approach: pdftotext -layout -> text -> label-matched line extraction of the
primary statements. First-pass — imperfect on noisy layouts; a targeted rescue
can follow (as HK did). Values captured as shown; $'000/$m unit normalisation
is a downstream adapter concern.
"""
from __future__ import annotations
import re
import subprocess
from pathlib import Path
from typing import Optional

CANONICAL_METRICS = [
    "revenue", "cost_of_revenue", "gross_profit", "profit_before_tax",
    "income_tax_expense", "net_income", "basic_eps",
    "cash_and_equivalents", "accounts_receivable", "inventories",
    "current_assets", "non_current_assets", "total_assets",
    "current_liabilities", "non_current_liabilities", "total_liabilities",
    "total_equity", "operating_cash_flow", "investing_cash_flow", "financing_cash_flow",
]

# Ordered literal label prefixes (line.startswith, lowercased). Specific first.
_LABELS: list[tuple[str, list[str]]] = [
    ("cost_of_revenue", ["cost of sales", "cost of goods sold"]),
    ("gross_profit", ["gross profit"]),
    ("profit_before_tax", ["profit before income tax", "profit before tax",
                            "profit/(loss) before income tax", "loss before income tax"]),
    ("income_tax_expense", ["income tax expense", "income tax benefit"]),
    ("net_income", ["profit for the year", "profit for the period", "loss for the year",
                     "profit/(loss) for the year", "profit after income tax", "net profit"]),
    ("basic_eps", ["basic earnings per share", "basic loss per share"]),
    ("revenue", ["revenue from contracts", "total revenue", "sales revenue", "revenue"]),
    ("cash_and_equivalents", ["cash and cash equivalents"]),
    ("accounts_receivable", ["trade and other receivables", "trade receivables"]),
    ("inventories", ["inventories"]),
    ("current_assets", ["total current assets"]),
    ("non_current_assets", ["total non-current assets", "total non current assets"]),
    ("total_assets", ["total assets"]),
    ("current_liabilities", ["total current liabilities"]),
    ("non_current_liabilities", ["total non-current liabilities", "total non current liabilities"]),
    ("total_liabilities", ["total liabilities"]),
    ("total_equity", ["total equity", "net assets", "total shareholders' equity",
                       "total shareholders equity"]),
]
# Cash-flow lines vary in the middle ("from"/"used in"/"provided by") -> match by pair.
_CF = [
    ("operating_cash_flow", "operating activities"),
    ("investing_cash_flow", "investing activities"),
    ("financing_cash_flow", "financing activities"),
]

_NOTE_REF = re.compile(r"\(?\s*notes?\s+[\d,\s&]+\)?", re.IGNORECASE)
_NUM = re.compile(r"\(?-?\$?\s?[\d,]+(?:\.\d+)?\)?")


def parse_amount(s: Optional[str]) -> Optional[float]:
    if s is None:
        return None
    s = s.strip()
    if s in ("", "-"):
        return None
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()").replace(",", "").replace("$", "").strip()
    if s.startswith("-"):
        neg = True
        s = s[1:]
    if not s:
        return None
    try:
        v = float(s)
    except ValueError:
        return None
    return -v if neg else v


def _value_number(text: str) -> Optional[float]:
    """Current-year value from a statement line. Layout is
    [label] [Note?] [CurrentYr] [PriorYr] — so the current-year figure is the
    second-to-last number (note is first, prior-year is last). Falls back to the
    lone number for single-column lines. Ignores 'note N' references."""
    cleaned = _NOTE_REF.sub(" ", text)
    nums = [parse_amount(m.group(0)) for m in _NUM.finditer(cleaned)]
    nums = [n for n in nums if n is not None]
    if not nums:
        return None
    return nums[-2] if len(nums) >= 2 else nums[-1]


def _classify(low: str) -> Optional[str]:
    if low.startswith("net cash"):
        for metric, tail in _CF:
            if tail in low:
                return metric
    for metric, prefixes in _LABELS:
        for p in prefixes:
            if low.startswith(p):
                return metric
    return None


def extract_metrics(text: str) -> dict:
    """pdftotext output -> {canonical_metric: value}. First match wins per metric
    (primary statements precede the notes)."""
    out: dict[str, float] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        metric = _classify(line.lower())
        if metric is None or metric in out:
            continue
        num = _value_number(line)
        if num is not None:
            out[metric] = num
    return out


def pdf_to_text(pdf_path: str, timeout: int = 120) -> str:
    """pdftotext -layout to stdout. Empty string on failure (log-and-skip)."""
    try:
        r = subprocess.run(["pdftotext", "-layout", str(pdf_path), "-"],
                           capture_output=True, text=True, timeout=timeout)
        return r.stdout if r.returncode == 0 else ""
    except (subprocess.SubprocessError, OSError):
        return ""


def extract_pdf(pdf_path: str) -> dict:
    return extract_metrics(pdf_to_text(pdf_path))
