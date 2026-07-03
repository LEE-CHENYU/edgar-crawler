"""TWSE (Taiwan) financial extractor — Traditional-Chinese IFRS-TW statements.

Taiwanese annual reports (年報) are PDF, Traditional Chinese. pdftotext extracts
the text cleanly and the standard IFRS-TW account labels are present, so this
reuses the ASX number machinery (parse_amount + second-to-last-column selection)
with Chinese labels. First-pass quality (like ASX/PSE).
"""
from __future__ import annotations
from typing import Optional

from asx_financials_extract import (
    CANONICAL_METRICS, parse_amount, _value_number, pdf_to_text,
)

# Ordered Chinese label prefixes (line.startswith after strip). Specific first.
_LABELS: list[tuple[str, list[str]]] = [
    ("cost_of_revenue", ["營業成本"]),
    ("gross_profit", ["營業毛利"]),
    ("operating_profit", ["營業利益"]),
    ("profit_before_tax", ["稅前淨利", "繼續營業單位稅前淨利", "稅前純益", "稅前純益（純損）"]),
    ("income_tax_expense", ["所得稅費用", "所得稅利益"]),
    ("net_income", ["本期淨利", "本期純益", "本期稅後淨利", "淨利（淨損）", "本期淨利（淨損）"]),
    ("basic_eps", ["基本每股盈餘"]),
    ("revenue", ["營業收入", "收入合計"]),
    ("cash_and_equivalents", ["現金及約當現金"]),
    ("accounts_receivable", ["應收帳款", "應收票據及帳款", "應收款項"]),
    ("inventories", ["存貨"]),
    ("current_assets", ["流動資產合計", "流動資產總計"]),
    ("non_current_assets", ["非流動資產合計", "非流動資產總計"]),
    ("total_assets", ["資產總計", "資產總額"]),
    ("current_liabilities", ["流動負債合計", "流動負債總計"]),
    ("non_current_liabilities", ["非流動負債合計", "非流動負債總計"]),
    ("total_liabilities", ["負債總計", "負債總額"]),
    ("total_equity", ["權益總計", "權益總額", "股東權益總計", "歸屬於母公司業主之權益"]),
]
# Cash-flow net lines: contain the activity keyword AND 淨現金 (the net-flow total).
_CF = [
    ("operating_cash_flow", "營業活動"),
    ("investing_cash_flow", "投資活動"),
    ("financing_cash_flow", "籌資活動"),
]


def _classify(line: str) -> Optional[str]:
    if "淨現金" in line:
        for metric, kw in _CF:
            if kw in line:
                return metric
    for metric, labels in _LABELS:
        for lb in labels:
            if line.startswith(lb):
                return metric
    return None


def extract_metrics(text: str) -> dict:
    """pdftotext output -> {canonical_metric: value}, first match wins."""
    out: dict[str, float] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        metric = _classify(line)
        if metric is None or metric in out:
            continue
        num = _value_number(line)
        if num is not None:
            out[metric] = num
    return out


def extract_pdf(pdf_path: str) -> dict:
    return extract_metrics(pdf_to_text(pdf_path))
