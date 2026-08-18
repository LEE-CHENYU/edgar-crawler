"""The extractor must read Nordic statements, not only English ones.

Incident 2026-08-17: Nordic stage 02 reported "consolidated 3584 rows"; 822
carried any metric. Swedish `arsredovisning` yielded 1,093 zero against 22
extracted -- a 98% failure rate -- while English "annual report" yielded 384
against 212. Cause: `_LABELS` was written for the Australian corpus and reused
verbatim on a majority-Swedish one. An arsredovisning contains the statements
by definition, so "no financials in these documents" was never the explanation.

Norwegian (Oslo, 8,134 PDFs) and Danish/Finnish (1,558) sit behind the same
extractor, so the vocabulary covers all four languages before those sweeps run.
"""

import pytest

from asx_financials_extract import extract_metrics


def _metric(lines, name):
    return extract_metrics("\n".join(lines)).get(name)


# --- Swedish ------------------------------------------------------------


def test_swedish_balance_sheet_lines():
    assert _metric(["Summa tillgangar 1 234"], "total_assets") == 1234.0
    assert _metric(["Summa eget kapital 500"], "total_equity") == 500.0
    assert _metric(["Summa skulder 734"], "total_liabilities") == 734.0


def test_swedish_income_statement_lines():
    assert _metric(["Nettoomsattning 9 100"], "revenue") == 9100.0
    assert _metric(["Arets resultat -250"], "net_income") == -250.0
    assert _metric(["Resultat fore skatt 300"], "profit_before_tax") == 300.0


def test_swedish_cash_line():
    assert _metric(["Likvida medel 42"], "cash_and_equivalents") == 42.0


# --- Norwegian (Oslo) ---------------------------------------------------


def test_norwegian_lines():
    assert _metric(["Sum eiendeler 2 000"], "total_assets") == 2000.0
    assert _metric(["Sum egenkapital 800"], "total_equity") == 800.0
    assert _metric(["Driftsinntekter 5 000"], "revenue") == 5000.0
    assert _metric(["Arsresultat 120"], "net_income") == 120.0


# --- Danish / Finnish ---------------------------------------------------


def test_danish_lines():
    assert _metric(["Aktiver i alt 3 000"], "total_assets") == 3000.0
    assert _metric(["Egenkapital i alt 900"], "total_equity") == 900.0


def test_finnish_lines():
    assert _metric(["Vastaavaa yhteensa 4 000"], "total_assets") == 4000.0
    assert _metric(["Liikevaihto 7 500"], "revenue") == 7500.0


# --- English must not regress -------------------------------------------


def test_english_labels_still_win():
    assert _metric(["Total assets 111"], "total_assets") == 111.0
    assert _metric(["Revenue 222"], "revenue") == 222.0
    assert _metric(["Total equity 333"], "total_equity") == 333.0


def test_a_more_specific_label_is_not_shadowed_by_a_shorter_one():
    """"Summa eget kapital" (equity) must not be captured by a prefix rule for
    "Summa" alone, the way "revenue" nearly swallows "revenue from contracts"."""
    assert _metric(["Summa eget kapital 500"], "total_equity") == 500.0
    assert _metric(["Summa eget kapital 500"], "total_assets") is None


# --- Nordic number formatting -------------------------------------------


def test_space_separated_thousands_are_one_number():
    """Nordic statements write 1 234 567, not 1,234,567. Splitting on the space
    would read the first group as the value -- 1 instead of 1,234,567."""
    assert _metric(["Summa tillgangar 1 234 567"], "total_assets") == 1234567.0


def test_decimal_comma_is_a_decimal_not_a_thousands_separator():
    assert _metric(["Resultat per aktie 1,50"], "basic_eps") == 1.50


def test_english_comma_thousands_still_parse():
    assert _metric(["Total assets 1,234,567"], "total_assets") == 1234567.0


def test_negative_in_parentheses_still_parses():
    assert _metric(["Arets resultat (250)"], "net_income") == -250.0
