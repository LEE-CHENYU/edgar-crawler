"""ESEF iXBRL extraction — 16,002 filings across 27 European markets.

These are the best-structured documents in the corpus and the opposite of every
PDF market: each fact carries its own concept, period context, currency unit and
an explicit decimal `scale`. None of the defects that cost this program days can
occur here -- no caption vocabulary (so no language problem), no scale guessing,
no period-end inference, no hardcoded currency.

    name="ifrs-full:RevenueFromInterest" contextRef="c1" unitRef="EUR" scale="3"

The traps that DO exist are different, and each has a test:

  1. SCALE is a power of ten applied to the written value; ignoring it is a
     1000x error, the same magnitude as the HK/India unit-scale defect.
  2. sign="-" negates a value written as positive.
  3. A filing carries MANY contexts -- current year, prior year, and segment
     breakdowns carrying xbrldi dimensions. Taking a dimensioned fact means
     reporting one segment as the whole company.
  4. Value text uses locale formats (1.234.567,89 as well as 1,234,567.89).
"""

import pytest

from scripts.esef_extract import (
    apply_scale, canonical_metric, context_is_consolidated, parse_ixbrl_number,
)


# --- scale and sign ------------------------------------------------------


def test_scale_multiplies_by_a_power_of_ten():
    assert apply_scale(1234.0, "3", None) == 1234000.0
    assert apply_scale(5.0, "6", None) == 5_000_000.0


def test_absent_scale_means_no_multiplier():
    assert apply_scale(42.0, None, None) == 42.0
    assert apply_scale(42.0, "", None) == 42.0


def test_sign_attribute_negates():
    """A cost written as 1,234 with sign="-" is negative 1,234."""
    assert apply_scale(1234.0, "0", "-") == -1234.0
    assert apply_scale(1234.0, "3", "-") == -1234000.0


def test_a_nonsense_scale_does_not_silently_multiply():
    assert apply_scale(10.0, "abc", None) == 10.0


# --- locale number formats ----------------------------------------------


def test_dot_decimal_format():
    assert parse_ixbrl_number("1,234,567.89") == 1234567.89


def test_comma_decimal_format():
    """European filings write 1.234.567,89 -- reading it as dot-decimal gives
    1.234, off by six orders of magnitude."""
    assert parse_ixbrl_number("1.234.567,89") == 1234567.89


def test_plain_integer_and_whitespace():
    assert parse_ixbrl_number(" 1234 ") == 1234.0
    assert parse_ixbrl_number("1 234 567") == 1234567.0


def test_empty_or_dash_is_none():
    assert parse_ixbrl_number("") is None
    assert parse_ixbrl_number("—") is None


# --- concept mapping -----------------------------------------------------


def test_core_ifrs_concepts_map():
    assert canonical_metric("ifrs-full:Assets") == "total_assets"
    assert canonical_metric("ifrs-full:Liabilities") == "total_liabilities"
    assert canonical_metric("ifrs-full:Equity") == "total_equity"
    assert canonical_metric("ifrs-full:ProfitLoss") == "net_income"
    assert canonical_metric("ifrs-full:Revenue") == "revenue"


def test_revenue_from_contracts_is_also_revenue():
    assert canonical_metric(
        "ifrs-full:RevenueFromContractsWithCustomers") == "revenue"


def test_company_extension_concepts_are_ignored():
    """A filer's own taxonomy extension (htb:ZunahmeAbnahme...) has no
    standard meaning and must not be mapped."""
    assert canonical_metric("htb:ZunahmeAbnahmeDurch") is None


def test_an_unmapped_ifrs_concept_returns_none():
    assert canonical_metric("ifrs-full:OtherComprehensiveIncome") is None


# --- context selection ---------------------------------------------------


def test_a_context_without_dimensions_is_consolidated():
    assert context_is_consolidated("<xbrli:entity><xbrli:identifier/></xbrli:entity>") is True


def test_a_dimensioned_context_is_a_segment_not_the_whole_company():
    """Taking a segment fact reports one business line as the entire company."""
    xml = ('<xbrli:entity><xbrli:segment>'
           '<xbrldi:explicitMember dimension="ifrs-full:SegmentsAxis">X'
           '</xbrldi:explicitMember></xbrli:segment></xbrli:entity>')
    assert context_is_consolidated(xml) is False



def test_the_balance_sheet_identity_alias_is_deliberately_absent():
    """Tried and removed: mapping ifrs-full:EquityAndLiabilities to total_assets
    never fired once in a 60-filing sample, and because facts are read in
    document order it could have let the financing side shadow a directly
    tagged ifrs-full:Assets. Recorded so it is not re-added on a hunch."""
    from scripts.esef_extract import canonical_metric
    assert canonical_metric("ifrs-full:EquityAndLiabilities") is None


# --- canonical layout ----------------------------------------------------


def test_market_dir_comes_from_the_path_not_the_iso_subdir():
    """The corpus stores the UK under markets/uk/ while the ESEF ISO subdir
    says GB: markets/uk/01_raw/.../xbrl_org/GB/... Deriving the market from the
    ISO code writes esef_gb and leaves markets/uk/02_structured empty forever."""
    from scripts.esef_extract import market_dir_from_path
    p = "/Volumes/X/datasets/markets/uk/01_raw/europe_annual_reports/xbrl_org/GB/LEI/2020-12-31/x.zip"
    assert market_dir_from_path(p) == "uk"
    p2 = "/Volumes/X/datasets/markets/at/01_raw/europe_annual_reports/xbrl_org/AT/LEI/2020-12-31/x.zip"
    assert market_dir_from_path(p2) == "at"


def test_an_unrecognisable_path_returns_none_rather_than_guessing():
    from scripts.esef_extract import market_dir_from_path
    assert market_dir_from_path("/tmp/somewhere/else.zip") is None


def test_canonical_output_path_follows_the_documented_layout():
    """CLAUDE.md's canonical layout is markets/{cc}/02_structured/. Writing only
    a combined file under derived/ leaves all 27 European markets showing 0
    bytes in the layout the inventory treats as authoritative."""
    from scripts.esef_extract import canonical_out_path
    got = canonical_out_path("uk", root="/Volumes/X/datasets/markets")
    assert got.endswith("markets/uk/02_structured/esef/canonical_metrics_wide.parquet")
