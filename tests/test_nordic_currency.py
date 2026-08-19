"""Detect Nordic reporting currency from the filing, not from the country.

Six countries were swept into one output directory, and the country is now
unrecoverable: neither the parquet nor sweep_results.jsonl retains the path. So
the sweep needs a country column AND a currency.

Country must not decide currency. The HK adapter hardcoded HKD and the live
distribution was HK$ 15,318 / RMB 11,141 / US$ 1,806 -- ~40% wrong. Nordic has
the same trap: Finnish and Baltic issuers report in EUR, most Swedish ones in
SEK, but plenty of Swedish large caps report in EUR or USD. Assigning SEK
because a file sat under markets/se/ would be that defect again, in a new market.
"""

import pytest

from scripts.market_conventions import COUNTRY_DEFAULT_CURRENCY, detect_currency


def _d(text):
    return detect_currency(text)[0]


# --- explicit declarations win -------------------------------------------


def test_swedish_thousands_declaration():
    assert _d("Belopp i TSEK om inget annat anges") == "SEK"
    assert _d("Alla belopp i tusental kronor (TSEK)") == "SEK"


def test_swedish_company_reporting_in_euro_is_not_forced_to_sek():
    """The whole point: a Swedish filer reporting in EUR must read as EUR."""
    assert _d("Belopp i TEUR om inget annat anges") == "EUR"
    assert _d("Amounts in EUR thousand") == "EUR"


def test_danish_and_norwegian():
    assert _d("Beløb i mio. DKK") == "DKK"
    assert _d("Beløp i NOK 1 000") == "NOK"


def test_finnish_and_icelandic():
    assert _d("Luvut tuhatta euroa") == "EUR"
    assert _d("Fjárhæðir í þúsundum króna (ISK)") == "ISK"


def test_usd_reporting_is_recognised():
    assert _d("Amounts in USD thousands") == "USD"


def test_no_declaration_returns_none_not_a_guess():
    assert detect_currency("no currency stated anywhere here") == (None, "none")


# --- country fallback is a LAST resort, and says so ----------------------


def test_country_defaults_exist_for_every_swept_country():
    for c in ("se", "dk", "fi", "is", "lt", "lv", "ee", "no"):
        assert COUNTRY_DEFAULT_CURRENCY[c], c


def test_baltics_are_euro_not_legacy_currencies():
    """Estonia 2011, Latvia 2014, Lithuania 2015 -- all euro now."""
    for c in ("ee", "lv", "lt"):
        assert COUNTRY_DEFAULT_CURRENCY[c] == "EUR", c


def test_detection_is_labelled_so_a_fallback_cannot_look_declared():
    _, source = detect_currency("Belopp i TSEK")
    assert source.startswith("declared")


def test_the_krona_ambiguity_is_not_resolved_by_guessing():
    """'kronor'/'kroner'/'krónur' name FOUR currencies (SEK/DKK/NOK/ISK). A bare
    mention with no country marker must not pick one."""
    assert detect_currency("belopp i tusental")[0] is None
