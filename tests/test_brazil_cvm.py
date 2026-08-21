"""Brazil CVM ingest: structured regulator data, not PDFs.

dados.cvm.gov.br publishes DFP (annual) as yearly ZIPs of CSV covering
2010-2026. Unlike every PDF corpus in this program it ships the four fields
whose absence caused most of the defects: MOEDA (currency), ESCALA_MOEDA (unit
scale), DT_FIM_EXERC (period end), and separate con/ind files (reporting basis).

Three traps the format sets, each with a test:

  1. ORDEM_EXERC is ÚLTIMO or PENÚLTIMO -- every file carries the CURRENT and
     PRIOR year. Taking both is the HK comparative-column defect, which merged
     values 1000x apart. Here it is labelled, so it must be filtered.
  2. Column ORDER differs between statements: DRE and DFC carry DT_INI_EXERC,
     the balance sheets do not. Parsing by position silently mismaps.
  3. Account CODES are not stable across sectors -- 2.03 is "Patrimônio
     Líquido" for an industrial and "Passivos Financeiros ao Custo Amortizado"
     for a bank. Matching must lead with the description.
"""

import pytest

from scripts.brazil_cvm_ingest import (
    metric_for, parse_scale, is_current_period, reporting_basis_for,
)


# --- ORDEM_EXERC ---------------------------------------------------------


def test_only_the_current_period_is_kept():
    assert is_current_period("ÚLTIMO") is True
    assert is_current_period("PENÚLTIMO") is False


def test_accent_stripped_forms_still_match():
    """Latin-1 round-trips have burned this corpus before."""
    assert is_current_period("ULTIMO") is True
    assert is_current_period("PENULTIMO") is False


# --- ESCALA_MOEDA --------------------------------------------------------


def test_scale_is_read_not_assumed():
    assert parse_scale("MIL") == 1000
    assert parse_scale("UNIDADE") == 1
    assert parse_scale("MILHAO") == 1_000_000


def test_an_unknown_scale_is_none_not_one():
    assert parse_scale("") is None
    assert parse_scale("QUALQUER") is None


# --- reporting basis -----------------------------------------------------


def test_consolidated_and_individual_are_distinguished():
    assert reporting_basis_for("DF Consolidado - Balanço Patrimonial Ativo") == "consolidated"
    assert reporting_basis_for("DF Individual - Balanço Patrimonial Ativo") == "parent"
    assert reporting_basis_for("") == "unknown"


# --- account mapping -----------------------------------------------------


def test_balance_sheet_totals_map_by_description():
    assert metric_for("1", "Ativo Total") == "total_assets"
    assert metric_for("2", "Passivo Total") == "total_liabilities"
    assert metric_for("1.01", "Ativo Circulante") == "current_assets"
    assert metric_for("2.01", "Passivo Circulante") == "current_liabilities"


def test_equity_maps_by_description_not_code():
    """2.03 is equity for an industrial and a bank liability line. Leading with
    the code would book a bank's funding as its equity."""
    assert metric_for("2.03", "Patrimônio Líquido Consolidado") == "total_equity"
    assert metric_for("2.03", "Passivos Financeiros ao Custo Amortizado") is None


def test_income_statement_lines():
    assert metric_for("3.01", "Receita de Venda de Bens e/ou Serviços") == "revenue"
    assert metric_for("3.03", "Resultado Bruto") == "gross_profit"
    assert metric_for("3.11", "Lucro/Prejuízo Consolidado do Período") == "net_income"


def test_a_deep_subaccount_is_not_mistaken_for_a_total():
    """1.01.01 is a sub-line of current assets, not current assets."""
    assert metric_for("1.01.01", "Caixa e Equivalentes de Caixa") == "cash_and_equivalents"
    assert metric_for("1.01.01.01", "Banco Conta Movimento") is None


def test_unmapped_lines_return_none():
    assert metric_for("9.99", "Alguma Conta Qualquer") is None
