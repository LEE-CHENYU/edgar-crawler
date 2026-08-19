#!/usr/bin/env python3
"""Per-market reporting conventions: fiscal year end, and unit scale vocabulary.

Two defect classes found by auditing every market on 2026-08-17 (see
docs/corpus_pipeline_practices.md §2.5a) share a root cause: conventions were
assumed rather than looked up.

FISCAL YEAR END. Adapters fabricated `{fiscal_year}-12-31` where the source had
no period-end column. That is wrong for two markets already in the pipeline:

    AU  filings cluster Sep/Aug/Oct  -> 30 June year ends
    IN  no filing_date at all        -> 31 March year ends

Filings land two to four months after year end, so the filing-month cluster is
the tell. A September cluster means June, not December.

UNIT SCALE. The PDF extractor's own docstring concedes it never normalised
units ("Values captured as shown"), so AU/TW/PH/IN all inherit HK's problem.
India is the worst case: a 40-report sample found lakh in 18, crore in 17,
million in 8, billion in 4 -- genuinely mixed, several reports carrying more
than one scale word. So scale must be detected per filing from a window anchored
on the statement caption, never assumed per market.

This module holds the lookup tables and the detector. It deliberately contains
no I/O and no market-specific extraction, so adapters and recovery scripts can
share it.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

# --- fiscal year ends ---------------------------------------------------

# market -> (month, day) of the fiscal year end, or None where the source
# carries a real period-end column and nothing should be fabricated.
FISCAL_YEAR_END: Dict[str, Optional[Tuple[int, int]]] = {
    "au": (6, 30),      # verified: filings cluster Sep 9,274 / Aug 7,140 / Oct 5,941
    "in_bse": (3, 31),  # verified: Indian statutory FY ends 31 March; no filing_date in corpus
    "tw": (12, 31),     # verified: filings cluster Jun 944 / May 349
    "ph": (12, 31),     # verified: filings cluster Apr 271 / May 112
    "kr": (12, 31),     # DART annual reports; Korean FY is calendar year
    "cn": None,         # Accper carries the real period end, incl. quarters
    "jp": None,         # metric_period_end carries it; JP FYs commonly end 31 March
    "hk": None,         # mixed; must come from the filing
}

# Markets whose fiscal-year LABEL refers to the year the FY ends in.
# An AU "FY2024" ends 2024-06-30; an Indian "FY2024" ends 2024-03-31.
LABEL_IS_END_YEAR = {"au", "in_bse", "tw", "ph", "kr"}


def period_end_for(market: str, fiscal_year: int) -> Optional[str]:
    """Period end for a market's fiscal-year label, or None if it must come from data.

    Returning None is a deliberate refusal: fabricating a period end for a
    market whose source carries one is how 37,669 JP rows came to be misdated.
    """
    convention = FISCAL_YEAR_END.get(market)
    if convention is None:
        return None
    month, day = convention
    return f"{int(fiscal_year):04d}-{month:02d}-{day:02d}"


def is_fabricated(market: str) -> bool:
    """True when this market's period end is derived from a label, not read."""
    return FISCAL_YEAR_END.get(market) is not None


# --- unit scale ---------------------------------------------------------

# Ordered most specific first: "百萬" (million) must beat "萬", and lakh/crore
# must be recognised before a generic digit pattern.
SCALE_PATTERNS: List[Tuple[str, int]] = [
    (r"\bcrores?\b", 10_000_000),          # India: 10^7
    (r"\blakhs?\b|\blacs?\b", 100_000),     # India: 10^5
    (r"in\s+billions?\s+of", 1_000_000_000),
    (r"\bbillions?\b", 1_000_000_000),
    (r"十\s*億", 1_000_000_000),
    (r"in\s+millions?\s+of", 1_000_000),
    (r"[A-Z$¥€£₹]{1,4}\s*(?:'|’)?\s*(?:mn|million)", 1_000_000),
    (r"\bmillions?\b", 1_000_000),
    (r"百\s*萬|百\s*万", 1_000_000),
    (r"in\s+thousands?\s+of", 1_000),
    (r"(?:'|’)\s*000\b", 1_000),
    (r"\bthousands?\b", 1_000),
    (r"千\s*元|千\s*円", 1_000),
]
_COMPILED = [(re.compile(p, re.IGNORECASE), mult) for p, mult in SCALE_PATTERNS]

# Where a statement begins. The scale caption sits just after one of these, not
# anywhere in the document -- 4 of 8 sampled Indian reports had no scale word in
# their first 12 pages because the statements are deep in the filing.
STATEMENT_ANCHORS = re.compile(
    r"(consolidated\s+(?:statement|balance\s+sheet)|balance\s+sheet|"
    r"statement\s+of\s+(?:financial\s+position|profit\s+and\s+loss)|"
    r"income\s+statement|profit\s+and\s+loss|"
    r"綜合(?:財務狀況表|損益表|全面收益表)|資產負債表|貸借対照表)",
    re.IGNORECASE,
)
WINDOW = 1200


def scale_in_window(window: str) -> Optional[int]:
    """Earliest-matching scale multiplier in a text window, or None."""
    best: Optional[Tuple[int, int]] = None
    for pattern, mult in _COMPILED:
        m = pattern.search(window)
        if m and (best is None or m.start() < best[0]):
            best = (m.start(), mult)
    return best[1] if best else None


def detect_scale(text: str) -> Tuple[Optional[int], str]:
    """(multiplier, evidence) for a document.

    Votes across every statement anchor rather than trusting the first hit: a
    single Indian report can mention crore, lakh and million in different
    contexts, so the caption nearest each statement is what counts.
    """
    votes: Dict[int, int] = {}
    for anchor in STATEMENT_ANCHORS.finditer(text):
        scale = scale_in_window(text[anchor.end() : anchor.end() + WINDOW])
        if scale:
            votes[scale] = votes.get(scale, 0) + 1
    if votes:
        top = max(votes.items(), key=lambda kv: kv[1])
        return top[0], f"anchored:{top[1]}votes:{votes}"
    fallback = scale_in_window(text[:20000])
    if fallback:
        return fallback, "document-head"
    return None, "none"


# --- reporting currency -------------------------------------------------

# Country default, used ONLY when a filing declares nothing. Country must never
# override a declaration: the HK adapter hardcoded HKD and the live tally was
# HK$ 15,318 / RMB 11,141 / US$ 1,806 -- ~40% wrong. Nordic carries the same
# trap, since Finnish and Baltic issuers report in EUR and some Swedish large
# caps do too.
COUNTRY_DEFAULT_CURRENCY: Dict[str, str] = {
    "se": "SEK", "dk": "DKK", "no": "NOK", "is": "ISK",
    "fi": "EUR",                       # euro since 1999
    "ee": "EUR", "lv": "EUR", "lt": "EUR",  # 2011 / 2014 / 2015
}

# Ordered most specific first. "kronor"/"kroner"/"krónur" name FOUR different
# currencies, so a bare krona word resolves nothing and is deliberately absent.
CURRENCY_PATTERNS: List[Tuple[str, str]] = [
    (r"\bT?SEK\b|\bsvenska\s+kronor\b|\bMSEK\b|\bKSEK\b", "SEK"),
    (r"\bT?DKK\b|\bdanske\s+kroner\b|\bmio\.?\s*DKK\b", "DKK"),
    (r"\bT?NOK\b|\bnorske\s+kroner\b|\bMNOK\b", "NOK"),
    (r"\bISK\b|\bíslenskar\s+krónur\b|krónum?\s*\(ISK\)", "ISK"),
    (r"\bT?EUR\b|\beuroa?\b|\beuron?\b|\beuro\b|\bMEUR\b|€", "EUR"),
    (r"\bT?USD\b|\bU\.?S\.?\s*dollars?\b|\$\s*(?:thousand|million)", "USD"),
    (r"\bGBP\b|\bpounds?\s+sterling\b|£", "GBP"),
    (r"\bPLN\b|\bz[łl]oty", "PLN"),
]
_CURRENCY_COMPILED = [(re.compile(p, re.IGNORECASE | re.UNICODE), c)
                      for p, c in CURRENCY_PATTERNS]


def detect_currency(text: str, country: Optional[str] = None) -> Tuple[Optional[str], str]:
    """(currency, provenance) for a filing.

    Reads the earliest explicit declaration in the text. Falls back to the
    country default ONLY when nothing is declared, and labels which happened, so
    a fallback can never be mistaken for a reading -- the same discipline the HK
    currency defect taught.
    """
    best: Optional[Tuple[int, str]] = None
    for pattern, code in _CURRENCY_COMPILED:
        m = pattern.search(text or "")
        if m and (best is None or m.start() < best[0]):
            best = (m.start(), code)
    if best:
        return best[1], "declared"
    if country and country in COUNTRY_DEFAULT_CURRENCY:
        return COUNTRY_DEFAULT_CURRENCY[country], f"country-default:{country}"
    return None, "none"
