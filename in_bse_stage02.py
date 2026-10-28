"""Statement-aware primitives for the India BSE Stage-02 rebuild.

The legacy PDF sweep matched the first label anywhere in an annual report.  It
therefore mixed narrative, cash-flow, note, standalone, and consolidated
values.  This module deliberately starts smaller: it reads primary statement
blocks, carries their scope/period/unit metadata, validates constructed
operating profit through an independent bridge, and never turns unknown debt
into zero.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable, Optional


_AMOUNT_TOKEN = re.compile(
    r"\bNil\b|(?<![A-Za-z0-9])(?:\(?-?(?:\d[\d,]*)(?:\.\d+)?\)?|(?<!\S)[-–—](?!\S))",
    re.IGNORECASE,
)
_DATE_PATTERNS = (
    re.compile(
        r"(?P<day>\d{1,2})(?:st|nd|rd|th)?(?:\s+|\s*[-\u2010-\u2015,\u2019']\s*)"
        r"(?P<month>January|February|March|April|May|June|July|August|"
        r"September|October|November|December|Jan\.?|Feb\.?|Mar\.?|Apr\.?|"
        r"Jun\.?|Jul\.?|Aug\.?|Sep\.?|Oct\.?|Nov\.?|Dec\.?)(?:\s+|\s*[-\u2010-\u2015,\u2019']\s*)"
        r"(?P<year>20\d{2})",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?P<day>\d{1,2})[./-](?P<month>\d{1,2})[./-](?P<year>20\d{2})",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?P<month>January|February|March|April|May|June|July|August|"
        r"September|October|November|December|Jan\.?|Feb\.?|Mar\.?|Apr\.?|"
        r"Jun\.?|Jul\.?|Aug\.?|Sep\.?|Oct\.?|Nov\.?|Dec\.?)\s+"
        r"(?P<day>\d{1,2})(?:(?:st|nd|rd|th)|[*%°])?,?\s*(?P<year>20\d{2})",
        re.IGNORECASE,
    ),
)

_MONTH_NAMES = (
    r"January|February|March|April|May|June|July|August|"
    r"September|October|November|December"
)


def _normalize_date_ocr(text: str) -> str:
    """Repair tightly scoped OCR corruptions in statement-heading dates."""
    normalized = re.sub(r"\bMarob\b", "March", text, flags=re.I)
    normalized = re.sub(
        r"\bMarch\s+32(?=\s*,?\s*20\d{2}\b)",
        "March 31",
        normalized,
        flags=re.I,
    )
    normalized = re.sub(
        rf"\b(?P<month>{_MONTH_NAMES})(?=(?:19|20)\d{{2}}\b)",
        lambda match: f"{match.group('month')} ",
        normalized,
        flags=re.I,
    )
    return re.sub(
        rf"\b(?:31\s*[*%°\"“”]\s*t?|31[S5]\s*[*%°\"“”]?|315[*%]?|3[lI]st|[DJ][lI]st)\s+"
        rf"(?P<month>{_MONTH_NAMES})\b",
        lambda match: f"31 {match.group('month')}",
        normalized,
        flags=re.I,
    )


@dataclass(frozen=True)
class StatementBlock:
    kind: str
    scope: str
    start: int
    end: int
    heading: str


def parse_amount(value: Optional[str]) -> Optional[float]:
    """Parse Indian/Western grouped statement amounts.

    A literal ``Nil`` is positive evidence of zero.  A dash remains unknown at
    this public boundary; table parsing separately records a dash on an
    otherwise identified line as a reported zero for bridge arithmetic.
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.lower() == "nil":
        return 0.0
    if text in {"-", "–", "—"}:
        return None
    negative = text.startswith("(") and text.endswith(")")
    text = text.strip("()").replace(",", "")
    text = re.sub(r"^(?:Rs\.?|INR|₹|`|C|H)\s*", "", text, flags=re.I)
    text = text.strip()
    if text.startswith("-"):
        negative = True
        text = text[1:]
    try:
        amount = float(text)
    except ValueError:
        return None
    return -amount if negative else amount


def _scope_from_heading(heading: str) -> str:
    low = heading.lower()
    if "consolidated" in low:
        return "consolidated"
    if "standalone" in low:
        return "standalone"
    return "unknown"


_BALANCE_HEADING = re.compile(
    r"\b(?:(?:standalone|consolidated)\s+)?(?:balance[ -]?sheet|statement\s+of\s+(?:assets\s+(?:and|&)\s+liabilities|financial\s+position)|financial\s+statements?\s+as\s+(?:on|at))\b",
    re.I,
)
_PROFIT_HEADING = re.compile(
    r"\b(?:"
    r"(?:(?:standalone|consolidated)\s+)?statements?\s+(?:of\s+)?(?:standalone\s+|consolidated\s+)?"
    r"(?:trading\s*(?:and|or|&)\s*)?profit\s*(?:and|or|&)\s*loss"
    r"|(?:(?:standalone|consolidated)\s+)?(?:trading\s*,?\s*)?profit\s*(?:and|&)\s*loss\s+"
    r"(?:statement|ac{2,3}ounts?|a\s*/\s*c|for\s+(?:the\s+)?(?:period|year))"
    r")\b",
    re.I,
)
_EXPLICIT_NO_BORROWING = re.compile(
    r"\b(?:"
    r"(?:company\s+has|company\s+had|there\s+(?:are|were))\s+no\s+borrowings?"
    r"|company\s+(?:has\s+not\s+(?:availed|taken)|does\s+not\s+have)\s+any\s+"
    r"(?:(?:loans?\s+or\s+(?:other\s+)?)?)borrowings?"
    r"|borrowings?\s+(?:are|were|is|was)?\s*nil"
    r"|secured\s+loans?\s*:\s*[-–—]\s+unsecured\s+loans?\s*:\s*[-–—]"
    r"|indebtedness(?:\s+of\s+the\s+company)?\s*:\s*n\s*i\s*l\b"
    r"|indebtedness\s*-\s*indebtedness\s+of\s+the\s+company[^\n]{0,180}?"
    r":\s*n\s*i\s*l\b"
    r"|(?:company\s+continues\s+to\s+be\s+)?free\s+from\s+debts?\b"
    r"|secured\s+loans?[^\n]{0,160}unsecured\s+loans?\s*\n\s*"
    r"n\s*i\s*l[^\n]{0,160}n\s*i\s*l\b"
    r"|indebtedness\s+at\s+the\s+end\s+of\s+the\s+financial\s+year"
    r"[\s\S]{0,180}?principal\s+amount\s+(?:[-–—]\s*){2,}"
    r"|company\s+has\s+not\s+borrowed\s+any\s+amount\s+from\s+(?:any\s+)?"
    r"financial\s+institutions?(?:\s*,?\s*banks?)?(?:\s+or\s+debenture\s+holders?)?"
    r")(?!\w)",
    re.I,
)


def _heading_matches(line: str) -> list[tuple[str, str]]:
    compact = " ".join(_normalize_statement_ocr_line(line).split())
    if not compact or len(compact) > 600:
        return []
    if re.search(r"\.{8,}", compact):
        # Dot leaders plus a terminal page number are contents/navigation
        # entries, not primary-statement boundaries.  Some malformed PDFs
        # overlay those entries on every financial-statement page.
        return []
    if re.search(
        r"\bitems?\s+that\s+(?:will|may)\s+(?:not\s+)?be\s+"
        r"reclassified\s+to\s+(?:the\s+)?statement\s+of\s+"
        r"profit\s*(?:and|or|&)\s*loss\b",
        compact,
        re.I,
    ):
        # OCI row labels can be interleaved into the same extracted line as
        # the primary P&L.  They refer to the statement but never begin one.
        return []
    if re.search(
        r"\b(?:this\s+is\s+the|the\s+above)\s+(?:(?:standalone|consolidated)\s+)?"
        r"(?:balance[ -]?sheet|statements?\s+of\s+(?:trading\s*)?profit\s*(?:and|or|&)\s*loss)\b",
        compact,
        re.I,
    ):
        # Auditor cross-references and boilerplate notes printed beside a
        # statement must not split the statement block.
        return []
    found: list[tuple[str, str]] = []
    for kind, pattern in (
        ("balance_sheet", _BALANCE_HEADING),
        ("profit_and_loss", _PROFIT_HEADING),
    ):
        match = pattern.search(compact)
        if match:
            scope = _scope_from_heading(match.group(0))
            if scope == "unknown":
                scope = _scope_from_heading(compact)
            found.append((kind, scope))
    return found


def _heading_kind(line: str) -> Optional[str]:
    """Return the first heading kind, retained for callers of the old helper."""
    matches = _heading_matches(line)
    return matches[0][0] if matches else None


def find_statement_blocks(text: str) -> list[StatementBlock]:
    lines = text.splitlines()
    starts: list[tuple[int, str, str, str]] = []
    boundaries: set[int] = set()
    for index, line in enumerate(lines):
        heading_text = line
        heading_matches = _heading_matches(line)
        if (
            index > 0
            and re.fullmatch(r"\s*C?Consolidated\s*", lines[index - 1], re.I)
            and re.match(
                r"^\s*ontents\s+(?:Balance\s+Sheet|Statement\s+of\s+"
                r"Profit\s*(?:and|or|&)\s*Loss)\b",
                line,
                re.I,
            )
        ):
            # Some two-layer annual reports interleave a persistent
            # ``Contents`` margin with the visible page title.  The text layer
            # becomes ``CConsolidated`` / ``ontents Statement ...``.  Rejoin
            # only that exact two-line corruption so the explicit scope is
            # retained without promoting ordinary contents entries.
            clean_heading = re.sub(r"^\s*ontents\s+", "", line, flags=re.I)
            heading_text = f"Consolidated {clean_heading.strip()}"
            heading_matches = _heading_matches(heading_text)
        if (
            heading_matches
            and index > 0
            and (
                re.search(
                    r"\b(?:standalone\s+|consolidated\s+)?statement\s+of\s*$",
                    lines[index - 1],
                    re.I,
                )
                or (
                    re.search(
                        r"^\s*(?:standalone|consolidated)\s+statement\s*$",
                        lines[index - 1],
                        re.I,
                    )
                    and re.search(
                        r"^\s*of\s+profit\s*(?:and|or|&)\s*loss\b",
                        line,
                        re.I,
                    )
                )
            )
        ):
            # The prior iteration already joined this wrapped title and kept
            # its explicit scope.  Do not start a second unknown-scope block.
            heading_matches = []
        if (
            not heading_matches
            and index + 1 < len(lines)
            and re.search(r"\b(?:standalone\s+|consolidated\s+)?statement\s+of\s*$", line, re.I)
        ):
            heading_text = f"{line.strip()} {lines[index + 1].strip()}"
            heading_matches = _heading_matches(heading_text)
        if (
            not heading_matches
            and index + 1 < len(lines)
            and re.search(
                r"^\s*(?:standalone|consolidated)\s+statement\s*$",
                line,
                re.I,
            )
            and re.search(
                r"^\s*of\s+profit\s*(?:and|or|&)\s*loss\b",
                lines[index + 1],
                re.I,
            )
        ):
            heading_text = f"{line.strip()} {lines[index + 1].strip()}"
            heading_matches = _heading_matches(heading_text)
        if (
            not heading_matches
            and re.search(
                r"\bstatement\s+of\s+(?P<scope>standalone|consolidated)\b",
                line,
                re.I,
            )
        ):
            scope_match = re.search(
                r"\bstatement\s+of\s+(?P<scope>standalone|consolidated)\b",
                line,
                re.I,
            )
            assert scope_match is not None
            for offset in range(1, 4):
                if index + offset >= len(lines):
                    break
                profit_line = _normalize_statement_ocr_line(lines[index + offset])
                if re.search(r"\bprofit\s*(?:and|or|&)\s*loss\b", profit_line, re.I):
                    scope = scope_match.group("scope").lower()
                    period_text = " ".join(
                        candidate.strip()
                        for candidate in lines[index + 1:index + offset]
                        if candidate.strip()
                    )
                    heading_text = (
                        f"{scope.title()} Statement of Profit or Loss {period_text}"
                    ).strip()
                    heading_matches = [("profit_and_loss", scope)]
                    break
        for kind, scope in heading_matches:
            if any(
                previous_kind == kind
                and previous_scope == scope
                and index - previous_index <= 3
                for previous_index, previous_kind, previous_scope, _ in starts[-2:]
            ):
                continue
            starts.append((index, kind, scope, heading_text.strip()))
        compact = " ".join(line.split())
        if (
            not re.search(r"\.{8,}", compact)
            and re.match(
            r"^(?:(?:standalone|consolidated)\s+)?(?:cash\s+flow\s+statement|"
            r"statement\s+of\s+cash\s+flows|statement\s+of\s+changes\s+in\s+equity)\b"
            r"|^notes?\s+forming\s+part\b",
            compact,
            re.I,
            )
        ):
            boundaries.add(index)
    blocks: list[StatementBlock] = []
    unique_starts = sorted({item[0] for item in starts} | boundaries)
    for start, kind, scope, heading in starts:
        valid_next = [
            pos
            for pos in unique_starts
            if pos > start
            and not (
                pos - start <= 1
                and any(
                    s_pos == pos and s_kind != kind
                    for s_pos, s_kind, _, _ in starts
                )
            )
        ]
        next_start = min(valid_next) if valid_next else len(lines)
        # Primary statements are compact.  The cap prevents a heading in a
        # contents page from swallowing hundreds of pages of narrative.
        end = min(next_start, start + 250)
        blocks.append(StatementBlock(kind, scope, start, end, heading))
    return blocks


def _period_end(lines: Iterable[str]) -> Optional[str]:
    text = _normalize_date_ocr(" ".join(lines))
    parsed: list[tuple[int, str]] = []
    for pattern in _DATE_PATTERNS:
        for match in pattern.finditer(text):
            month = match.group("month").rstrip(".")
            raw = f"{match.group('day')} {month} {match.group('year')}"
            try:
                fmt = (
                    "%d %m %Y"
                    if month.isdigit()
                    else "%d %b %Y"
                    if len(month) == 3
                    else "%d %B %Y"
                )
                parsed.append((match.start(), datetime.strptime(raw, fmt).date().isoformat()))
            except ValueError:
                continue
    if not parsed:
        # Some multi-column balance-sheet headers extract the month names on
        # one line and their day/year cells on the next, for example
        # ``As at March  As at March  As at April`` followed by
        # ``No. 31, 2018  31, 2017  01, 2016``.  Bind the first explicit
        # ``As at <month>`` to the first following day/year cell.  Ordinary
        # inline dates were already handled above, so this fallback remains
        # restricted to the staggered geometry.
        staggered_as_at = re.search(
            rf"\bas\s+(?:at|on)\s+(?P<month>{_MONTH_NAMES})\b"
            r".{0,180}?\b(?P<day>\d{1,2})\s*,\s*(?P<year>20\d{2})\b",
            text,
            re.I,
        )
        if staggered_as_at:
            try:
                raw = (
                    f"{staggered_as_at.group('day')} "
                    f"{staggered_as_at.group('month')} "
                    f"{staggered_as_at.group('year')}"
                )
                return datetime.strptime(raw, "%d %B %Y").date().isoformat()
            except ValueError:
                pass
        # Primary statements often split the current year into the next
        # column-header row, for example ``For the year ended 31st March``
        # followed by ``Note 2015 2014``.  Accept only an explicit March 31
        # year-ended label and the first nearby four-digit year; the narrow
        # distance bound avoids borrowing a year from statement body values.
        split_march_year = re.search(
            r"\b(?:year|period)\s+ended\b.{0,120}?"
            r"\b31(?:st)?\s+March\b.{0,120}?"
            r"\b(?P<year>20\d{2})\b",
            text,
            re.I,
        )
        if split_march_year:
            return f"{split_march_year.group('year')}-03-31"
        split_as_at_march_year = re.search(
            r"\bas\s+(?:at|on)\b.{0,60}?"
            r"\b31(?:st)?\s+March\b.{0,180}?"
            r"\b(?P<year>20\d{2})\b",
            text,
            re.I,
        )
        if split_as_at_march_year:
            return f"{split_as_at_march_year.group('year')}-03-31"
        # Older Indian statements sometimes print only the closing month and
        # year in the title ("year ended March, 2015"). March annual periods
        # have an unambiguous statutory month-end; do not generalize this to
        # months whose closing day cannot be inferred safely.
        march_year = re.search(
            r"\b(?:year|period)\s+ended(?:\s+on)?\s+March\s*,?\s*(?P<year>20\d{2})\b",
            text,
            re.I,
        )
        return f"{march_year.group('year')}-03-31" if march_year else None
    parsed.sort(key=lambda item: item[0])
    if re.search(r"\bfrom\b.{1,60}\bto\b", text, re.I):
        return parsed[-1][1]
    for (left_pos, _), (right_pos, right_date) in zip(parsed, parsed[1:]):
        if re.search(r"\bto\b", text[left_pos:right_pos], re.I):
            # Period titles are also printed as ``01-April-2016 To
            # 31-March-2017`` without the word ``from``.  The closing date,
            # not the first date encountered, is the fiscal period end.
            return right_date
    return parsed[0][1]


def _annual_report_period(lines: Iterable[str]) -> Optional[str]:
    """Recover an Indian March year-end from an explicit annual-report range."""
    text = " ".join(lines)
    match = re.search(
        r"\b(?:annual\s+report|financial\s+year)\s+"
        r"(?P<start>(?:19|20)\d{2})\s*[-–]\s*"
        r"(?P<end>\d{2}|(?:19|20)\d{2})\b",
        text,
        re.I,
    )
    if not match:
        return None
    start = int(match.group("start"))
    raw_end = match.group("end")
    end = int(raw_end)
    if len(raw_end) == 2:
        end += start // 100 * 100
        if end <= start:
            end += 100
    if end != start + 1:
        return None
    return f"{end:04d}-03-31"


def _statement_column_period(lines: Iterable[str]) -> Optional[str]:
    """Recover a March year-end from legacy primary-statement columns."""
    text = _normalize_date_ocr(
        " ".join(
            " ".join(_normalize_statement_ocr_line(line).split())
            for line in lines
        )
    )
    fiscal_range = re.search(
        r"\b(?P<start>(?:19|20)\d{2})\s*[-–]\s*(?P<end>\d{2})\b",
        text,
    )
    if fiscal_range:
        start = int(fiscal_range.group("start"))
        end = int(fiscal_range.group("end")) + start // 100 * 100
        if end <= start:
            end += 100
        if end == start + 1:
            return f"{end:04d}-03-31"
    split_march_year = re.search(
        r"\b(?:year|period)\s+ended\b.{0,100}?"
        r"\bMarch\s+31(?:st)?\b\s*,?.{0,100}?"
        r"\b(?P<year>(?:19|20)\d{2})\b",
        text,
        re.I,
    )
    if split_march_year:
        return f"{split_march_year.group('year')}-03-31"
    split_as_at = re.search(
        rf"\bas\s+(?:at|on)\s+(?P<month>{_MONTH_NAMES})\s+"
        r"(?P<day>\d{1,2})(?:st|nd|rd|th)?\b.{0,160}?"
        r"\b(?P<year>(?:19|20)\d{2})\b",
        text,
        re.I,
    )
    if split_as_at:
        try:
            raw = (
                f"{split_as_at.group('day')} {split_as_at.group('month')} "
                f"{split_as_at.group('year')}"
            )
            return datetime.strptime(raw, "%d %B %Y").date().isoformat()
        except ValueError:
            pass
    short_column_date = re.search(
        r"\b(?P<day>\d{1,2})[-/.](?P<month>[A-Za-z]{3})[-/.]"
        r"(?P<year>\d{2})\b",
        text,
        re.I,
    )
    if short_column_date:
        short_year = int(short_column_date.group("year"))
        full_year = 2000 + short_year if short_year <= 50 else 1900 + short_year
        try:
            raw = (
                f"{short_column_date.group('day')} "
                f"{short_column_date.group('month')} {full_year}"
            )
            return datetime.strptime(raw, "%d %b %Y").date().isoformat()
        except ValueError:
            pass
    return None


def _currency_and_scale(lines: Iterable[str]) -> tuple[Optional[str], Optional[int], str]:
    text = " ".join(lines)
    low = text.lower()
    currency = "INR" if re.search(r"\b(?:rs\.?|inr|(?:indian\s+)?rupees?)\b|₹", text, re.I) else None
    # PDF font substitution commonly renders the rupee sign as a backtick,
    # C, or H immediately before an Indian scale declaration.
    if currency is None and re.search(
        r"(?:`|\bC\b|\bH\b|\bI\b)\s*(?:in\s+)?(?:lakhs?|lacs?|crores?)\b", text, re.I
    ):
        currency = "INR"
    if re.search(r"\b(?:lakhs?|lacs?)\b", low):
        currency = currency or "INR"
        return currency, 100_000, "statement_header"
    if re.search(r"\bcrores?\b", low):
        currency = currency or "INR"
        return currency, 10_000_000, "statement_header"
    if re.search(r"\bmillions?\b", low):
        return currency, 1_000_000, "statement_header"
    if re.search(r"\bthousands?\b", low):
        return currency, 1_000, "statement_header"
    if re.search(
        r"\(\s*(?:[`‘’'₹]|rs\.?)?\s*(?:amounts?\s+)?in\s+"
        r"(?:[`‘’']\s*)?0{3}(?:\s*['’]s)?\s*\)",
        text,
        re.I,
    ):
        # Older embedded fonts often render ``Rs. in '000`` as
        # ``(` in ‘000)``.  The parenthesized three-zero declaration is a
        # statement unit, not a year or a note reference.
        return currency or "INR", 1_000, "statement_header"
    if re.search(
        r"\bamount\s+in\s+(?:(?:rs\.?|inr|(?:indian\s+)?rupees?)\b|[`₹])"
        r"|\(\s*amount\s+in\s+%\s*\)",
        text,
        re.I,
    ):
        return currency or "INR", 1, "statement_header"
    # Some whole-rupee statements label each comparative amount column only
    # with ``Rs.`` rather than spelling out ``Amount in Rs.``.  Requiring two
    # standalone labels distinguishes a column header from a narrative rupee
    # reference while recovering the implicit unit of one rupee.
    if len(
        re.findall(
            r"(?<![A-Za-z])(?:Rs\.?|INR|Rupees?|₹|`)(?![A-Za-z])",
            text,
            re.I,
        )
    ) >= 2:
        return currency or "INR", 1, "statement_header"
    return currency, None, "unknown"


def _tokens(text: str) -> list[str]:
    # Embedded fonts sometimes insert whitespace on either side of an Indian
    # grouping comma (``14,51 ,659`` or ``(2,77, 138)``).  Normalize only
    # digit-to-digit comma separators before tokenization so the fragments are
    # not mistaken for separate statement columns.
    text = re.sub(r"(?<=\d)\s*,\s*(?=\d)", ",", text)
    # Older BSE text layers sometimes preserve Indian digit grouping as
    # single spaces rather than commas (``(2 42 90 678)``).  Rejoin only a
    # complete Indian-grouped token: one leading group, one or more two-digit
    # groups, and a final three-digit group.  This leaves the wider whitespace
    # between statement columns untouched.
    text = re.sub(
        r"(?<!\d)(\(?-?\d{1,3}(?:[ \u00a0]\d{2})+[ \u00a0]\d{3}\)?)(?!\d)",
        lambda match: match.group(1).replace(" ", "").replace("\u00a0", ""),
        text,
    )
    # Some embedded PDF fonts drop Indian comma glyphs while preserving a
    # single space between two-digit groups (``15 84.95`` for ``1,584.95``).
    # Column separators contain multiple spaces, so only rejoin single-space
    # groups and leave note/value columns distinct.
    if re.search(r"[ \t]{2,}", text):
        text = re.sub(
            r"(?<![\d.])(\(?-?\d{1,2}(?:[ \u00a0]\d{2})+\.\d+\)?)(?![\d.])",
            lambda match: match.group(1).replace(" ", "").replace("\u00a0", ""),
            text,
        )
    # OCR sometimes substitutes dots for both an Indian grouping separator
    # and the decimal point (``1.156.79`` for ``1,156.79``).  Collapse only
    # tokens containing at least two dots, preserving the final decimal.
    text = re.sub(
        r"(?<![\d.])(\(?-?\d{1,3}(?:\.\d{2,3})+\.\d{1,2}\)?)(?![\d.])",
        lambda match: (
            ("(" if match.group(1).startswith("(") else "")
            + "".join(match.group(1).strip("()").split(".")[:-1])
            + "."
            + match.group(1).strip("()").split(".")[-1]
            + (")" if match.group(1).endswith(")") else "")
        ),
        text,
    )
    return [match.group(0).strip() for match in _AMOUNT_TOKEN.finditer(text)]


def _current_value(
    line: str,
    label_match: re.Match[str],
    *,
    drop_leading_note: bool = False,
    value_column_count: int = 2,
    leading_reference_columns: int = 1,
) -> Optional[float]:
    tail = line[label_match.end():]
    raw_tail = tail
    if value_column_count >= 2:
        # Text extraction can collapse an empty current-period cell to a
        # double dash immediately before the sole comparative amount.  Keep a
        # single dash as the first value column instead of shifting the prior
        # value into the current period.
        tail = re.sub(r"(?<!\S)[-–—]{2,}(?=\s)", " - ", tail, count=1)
    tail = re.sub(r"^\s*\((?:PBT|PAT)\)\s*", " ", tail, flags=re.I)
    tail = re.sub(
        r"^\s*\(\s*(?:see|refer(?:\s+to)?)\s+notes?\b.*?"
        r"\bschedule\s*[-–—]?\s*\d+\s*\)\s*",
        " ",
        tail,
        flags=re.I,
    )
    tail = re.sub(
        r"^\s*\(\s*(?:see|refer(?:\s+to)?)\s+notes?\b[^)]*\)\s*",
        " ",
        tail,
        flags=re.I,
    )
    tail = re.sub(
        r"^\s*\[\s*including\b[^]]*\]\s*",
        " ",
        tail,
        flags=re.I,
    )
    tail = re.sub(
        r"^\s*\(\s*before\s+adjustment\s+for\s+"
        r"(?:minority|non[- ]controlling)\s+interests?\s*\)\s*",
        " ",
        tail,
        flags=re.I,
    )
    tail = re.sub(
        r"^\s*\{\s*including\b.*?\}\s*",
        " ",
        tail,
        flags=re.I,
    )
    tail = re.sub(
        r"^\s*\(\s*including\b[^)]*\)\s*",
        " ",
        tail,
        flags=re.I,
    )
    tail = re.sub(r"\(\s*notes?\s+\d+(?:\.\d+)?\s*\)", " ", tail, flags=re.I)
    tail = re.sub(
        r"^\s*(?:credits?\s*/\s*\(?\s*charges?\s*\)?|"
        r"charges?\s*/\s*\(?\s*credits?\s*\)?)\s*",
        " ",
        tail,
        flags=re.I,
    )
    tail = re.sub(
        r"^\s*\(\s*includes?\b[^)]*\)\s*",
        " ",
        tail,
        flags=re.I,
    )
    tail = re.sub(r"^\s*(?:credit|charge)\s+", " ", tail, flags=re.I)
    tail = re.sub(
        r"^\s*[-–—]?\s*refer(?:\s+to)?\s+notes?\s+\d+(?:\.\d+)?\s*",
        " ",
        tail,
        flags=re.I,
    )
    tail = re.sub(
        r"^\s*\d{1,2}(?:\.\d{1,2})?\s*\(\s*\d{1,2}\s*\)\s*",
        " ",
        tail,
    )
    tail = re.sub(
        r"^\s*[-–—]\s*\(\s*credit\s*\)\s*",
        " ",
        tail,
        flags=re.I,
    )
    # Some embedded fonts split the leading digit of an Indian-grouped value
    # (``15 1 5,42,434.50`` = note 15, value 15,42,434.50).  Rejoin only the
    # value-side split while preserving the leading note reference.
    tail = re.sub(
        r"^(\s*\d{1,3}\s+)(\d)\s+(?=\d{1,2}(?:,\d{2})*,\d{3}(?:\.\d+)?)",
        r"\1\2",
        tail,
    )
    if tail.startswith(("- ", "– ", "— ")) and label_match.end() and line[label_match.end() - 1].isalnum():
        tail = tail[1:]
    # Row equations are presentation metadata, not statement values.  Common
    # historical layouts put expressions such as ``(1-2)``, ``(V-VI)``, or
    # ``- ( I ) - ( II )`` immediately after the label.
    tail = re.sub(
        r"^\s*(?:"
        r"[([{]\s*[IVXLCDM\d|!]+\s*(?:[-+]\s*"
        r"[IVXLCDM\d|!]+\s*)+[)\]}]"
        r"|[([{]?\s*total\s+of\s+[IVXLCDM\d|!]+\s*"
        r"(?:(?:[-+]|to)\s*[IVXLCDM\d|!]+\s*)+[)\]}]?"
        r")\s*",
        " ",
        tail,
        flags=re.I,
    )
    formula = re.compile(
        r"^\s*(?:[-=:]\s*)?(?:"
        r"[([]\s*[A-Z\d|!]+\s*(?:[-+]\s*[A-Z\d|!]+\s*)+[)\]]"
        r"|(?:\(\s*[A-Z\d]+\s*\)\s*[-+]\s*)+\(\s*[A-Z\d]+\s*\)"
        r"|\d{1,2}\s*[-+]\s*\d{1,2}(?=\s)"
        r")",
        re.I,
    )
    while formula.match(tail):
        tail = formula.sub(" ", tail, count=1)
    tail = re.sub(
        r"\(\s*[IVXLCDM\d]+\s*(?:[-+±]\s*[IVXLCDM\d]+)+\s*\)",
        " ",
        tail,
        flags=re.I,
    )
    # OCR may leave a comma after the complete current-period amount before
    # the comparative column (``137,409,386, 160,543,679``).  Remove only a
    # delimiter that follows and precedes complete grouped amounts; commas
    # within a single Indian-grouped amount remain untouched.
    tail = re.sub(
        r"(\d{1,3}(?:,\d{2,3})+),\s+"
        r"(?=\d{1,3}(?:,\d{2,3})+(?:\.\d+)?)",
        r"\1 ",
        tail,
    )
    decorative_prefix = re.match(r"^\s*[-–—]\s*[.:;|,]+\s*", tail)
    if decorative_prefix:
        remainder = tail[decorative_prefix.end():]
        if len(_tokens(remainder)) >= value_column_count:
            # A dash followed by two amount columns is OCR decoration, not an
            # empty current-period cell.  A real nil/current dash has only the
            # comparative amount after it and therefore stays intact.
            tail = remainder
    # Some issuers cross-reference two notes in one column (for example
    # ``25 & 29``).  Remove the complete reference rather than dropping only
    # its first number and mistaking the second for the current-period value.
    combined_note = re.match(
        r"^\s*\d{1,2}(?:\.\d{1,2})?\s*(?:&|and)\s*"
        r"\d{1,2}(?:\.\d{1,2})?\s+",
        tail,
        re.I,
    )
    if combined_note and len(_tokens(tail[combined_note.end():])) >= 2:
        tail = tail[combined_note.end():]
    # A note column may cite several note numbers as one comma-separated cell
    # (``2,4,6,38``).  It resembles an Indian-grouped amount after punctuation
    # normalization, so remove it before tokenizing when two statement values
    # still follow.  Requiring at least three commas and 1-2 digit components
    # avoids consuming monetary values such as ``1,173.09``.
    multi_comma_note = re.match(
        r"^\s*(?:\d{1,2}\s*,\s*){3,}\d{1,2}\s+",
        tail,
    )
    if multi_comma_note and len(_tokens(tail[multi_comma_note.end():])) >= 2:
        tail = tail[multi_comma_note.end():]
    comma_note = re.match(
        r"^\s*\d{1,2}(?:\.\d{1,2})?\s*,\s*"
        r"\d{1,2}(?:\.\d{1,2})?\s+",
        tail,
    )
    if comma_note and len(_tokens(tail[comma_note.end():])) >= 2:
        tail = tail[comma_note.end():]
    single_row_reference = re.match(
        r"^\s*\(\s*(?:\d{1,2}|[IVXLCDM]+)\s*\)\s+", tail, re.I
    )
    if (
        single_row_reference
        and len(_tokens(tail[single_row_reference.end():])) >= 2
    ):
        tail = tail[single_row_reference.end():]
    # Layout-preserving OCR commonly keeps statement columns separated by
    # large whitespace while splitting digit groups *within* a cell with one
    # space (``2 218`` or ``31 420``).  Recover cells from the whitespace
    # geometry before flattening the line into amount tokens.  A separately
    # spaced leading note reference is removed only when the statement header
    # established a note column.
    column_segments = [
        segment.strip()
        for segment in re.split(r"(?:\t+|\s{2,})", tail.strip())
        if segment.strip()
    ]

    def segment_amount(segment: str) -> Optional[float]:
        if re.search(r"(?<=\d)[ \u00a0]+(?=\d)", segment):
            spaced = segment.strip().strip("()")
            spaced = re.sub(r"[ \u00a0]+", " ", spaced)
            grouped_cell = bool(
                re.fullmatch(
                    r"-?(?:"
                    r"\d{1,3} \d{3}(?:\.\d+)?"
                    r"|\d{1,3}(?: \d{2})+ \d{3}(?:\.\d+)?"
                    r"|\d{1,2}(?: \d{2})+\.\d+"
                    r")",
                    spaced,
                )
            )
            if not grouped_cell:
                # Two complete amounts can share one OCR column segment when
                # a component and its subtotal are printed side by side.  Do
                # not concatenate them into a many-order-of-magnitude value.
                return None
        compact = re.sub(r"(?<=\d)[ \u00a0]+(?=\d)", "", segment)
        compact = re.sub(r"\(\s+|\s+\)", lambda match: match.group(0).strip(), compact)
        if compact in {"-", "–", "—"}:
            return 0.0
        if _AMOUNT_TOKEN.fullmatch(compact) is None:
            return None
        return parse_amount(compact)

    if len(column_segments) >= 2:
        parsed_segments = [segment_amount(segment) for segment in column_segments]
        if all(value is not None for value in parsed_segments):
            reference_pattern = (
                r"[1-9]\d{0,2}(?:\.\d{1,2})?"
                if drop_leading_note
                else r"[1-9]\d?(?:\.\d{1,2})?"
            )
            if drop_leading_note:
                available_reference_columns = max(
                    0, len(parsed_segments) - value_column_count
                )
                reference_columns_to_drop = min(
                    leading_reference_columns,
                    available_reference_columns,
                )
            else:
                # Without a declared note column, retain the older
                # conservative inference: only a three-or-more-cell row may
                # have an implicit leading reference.  A two-cell row such as
                # ``66.23    -`` is current/prior, not reference/current.
                reference_columns_to_drop = 1 if len(parsed_segments) >= 3 else 0
            if reference_columns_to_drop and all(
                re.fullmatch(reference_pattern, segment)
                for segment in column_segments[:reference_columns_to_drop]
            ):
                # Mirror the token-path note handling below.  The geometry
                # path was added later and originally returned the first
                # segment before applying this long-standing [note, current,
                # prior] rule.  Some statements declare both ``Notes`` and
                # ``Page`` reference columns, so remove the number of leading
                # references established by the statement header.
                parsed_segments = parsed_segments[reference_columns_to_drop:]
            if len(parsed_segments) >= value_column_count:
                return parsed_segments[0]
    if drop_leading_note and leading_reference_columns == 1:
        # In image-only legacy statements, Tesseract can collapse the note,
        # current, and prior columns to single spaces.  Before generic token
        # normalization interprets ``7 87 106`` as Indian grouping, honor the
        # declared note column and read the middle integer as current value.
        simple_note_columns = re.fullmatch(
            r"\s*[1-9]\d{0,2}\s+"
            r"(?P<current>\(?-?\d+\)?|[-–—])\s+"
            r"(?:\(?-?\d+\)?|[-–—])\s*[.·•,;:|\-–—]?\s*",
            tail,
        )
        if simple_note_columns:
            current = simple_note_columns.group("current")
            return 0.0 if current in {"-", "–", "—"} else parse_amount(current)
    tokens = _tokens(tail)
    if not tokens:
        return None
    # [note, current, prior] is the dominant primary-statement layout.  A bare
    # integer note is never selected as the value when two columns follow.
    if (
        not drop_leading_note
        and len(tokens) == 1
        and re.fullmatch(r"\d{1,2}(?:\.\d{1,2})?", tokens[0])
    ):
        return None
    if (
        drop_leading_note
        and value_column_count >= 2
        and len(tokens) == 1
        and re.fullmatch(r"[1-9]\d{0,2}(?:\.\d{1,2})?", tokens[0])
    ):
        return None
    if (
        drop_leading_note
        and value_column_count >= 2
        and len(tokens) == value_column_count
        and re.fullmatch(r"[1-9]\d{0,2}", tokens[0])
        and re.match(r"^\s*[1-9]\d{0,2}\s+", raw_tail)
    ):
        first = parse_amount(tokens[0])
        second = parse_amount(tokens[1])
        if (
            first is not None
            and second is not None
            and abs(second) >= 1_000 * max(abs(first), 1.0)
        ):
            # OCR can drop the current-period cell and leave only
            # ``[note, comparative]``.  A tiny integer followed by a value
            # three orders larger is note-column evidence, not current debt.
            return None
    if (
        drop_leading_note
        and value_column_count >= 2
        and len(tokens) == value_column_count
        and tokens[-1] in {"-", "–", "—"}
        and re.fullmatch(r"[1-9]\d{0,2}(?:\.\d{1,2})?", tokens[0])
        and re.match(r"^\s*[1-9]\d{0,2}\b(?!\.\d)", raw_tail)
    ):
        # A note reference followed by the sole displayed current-period dash
        # is common when the prior comparative cell is blank.  Do not promote
        # the note number itself to a tax amount.
        tokens = tokens[1:]
    if drop_leading_note:
        reference = re.compile(
            r"(?:[1-9]\d{0,2}|[1-9]\d?(?:\.\d{1,2})?|"
            r"\(\s*[1-9]\d{0,2}\s*\)|[-–—])"
        )
        while (
            len(tokens) > value_column_count
            and reference.fullmatch(tokens[0])
        ):
            tokens = tokens[1:]
    elif len(tokens) >= 3 and re.fullmatch(r"[1-9]\d?(?:\.\d{1,2})?", tokens[0]):
        tokens = tokens[1:]
    token = tokens[0]
    if token in {"-", "–", "—"}:
        return 0.0
    return parse_amount(token)


def _tax_component_value(
    line: str,
    label_match: re.Match[str],
    *,
    drop_leading_note: bool,
    value_column_count: int,
    leading_reference_columns: int = 1,
) -> Optional[float]:
    """Read a tax component when OCR interleaves component and subtotal columns."""
    tail = line[label_match.end():]
    cleaned_tail = re.sub(
        r"^\s*\[\s*(?:see|refer(?:\s+to)?)\s+notes?\b[^]]*]\s*",
        " ",
        tail,
        flags=re.I,
    )
    cleaned_tail = re.sub(
        r"^\s*\d{1,3}\s*\(\s*[a-z]\s*\)\s*",
        " ",
        cleaned_tail,
        flags=re.I,
    )
    cleaned_tail = re.sub(
        r"^\s*\(?\s*(?:benefit|credit|charge)\s*\)?\s*"
        r"[-–—:]\s*(?=\(?-?\d)",
        " ",
        cleaned_tail,
        flags=re.I,
    )
    cleaned_tail = re.sub(
        r"^\s*including\b.*?\btax\b"
        r"(?=\s+\d{1,3}\s*\(\s*\d{1,3}\s*\))",
        " ",
        cleaned_tail,
        flags=re.I,
    )
    tokens = _tokens(cleaned_tail)
    clean_numeric_start = bool(
        re.match(r"^\s*(?:\(?-?\d|[-–—](?:\s|$))", cleaned_tail)
    )
    if (
        drop_leading_note
        and re.match(
            r"^\s*[1-9]\d{0,2}(?:\.\d{1,2})?\s{2,}",
            cleaned_tail,
        )
    ):
        # Preserve layout geometry before the flattened-token special case.
        # In ``27.1    (5 711)    771``, 27.1 is a note reference and the
        # parenthesized Indian-grouped current value must stay one cell.
        geometry_value = _current_value(
            line,
            label_match,
            drop_leading_note=True,
            value_column_count=value_column_count,
            leading_reference_columns=leading_reference_columns,
        )
        if geometry_value is not None:
            return geometry_value
    compound_numeric_note = bool(
        re.match(r"^\s*\d{1,3}\s*\(\s*\d+", cleaned_tail)
    )
    contains_words = bool(re.search(r"[A-Za-z]", cleaned_tail))
    if (
        value_column_count >= 2
        and len(tokens) == 2 * value_column_count
        and clean_numeric_start
        and not compound_numeric_note
        and not contains_words
    ):
        return parse_amount(tokens[0])
    if cleaned_tail != tail:
        empty_match = re.match(r"^", cleaned_tail)
        assert empty_match is not None
        return _current_value(
            cleaned_tail,
            empty_match,
            drop_leading_note=False,
            value_column_count=value_column_count,
        )
    return _current_value(
        line,
        label_match,
        drop_leading_note=drop_leading_note,
        value_column_count=value_column_count,
        leading_reference_columns=leading_reference_columns,
    )


def _numeric_row_continuation(line: str) -> bool:
    """Return true only for a wrapped statement row containing amounts alone."""
    amount = r"(?:Nil|\(?-?\d[\d,]*(?:\.\d+)?\)?|-|–|—)"
    compact = line.strip().strip("|").strip()
    return bool(re.fullmatch(rf"{amount}(?:\s+{amount}){{0,2}}", compact, re.I))


def _normalize_statement_ocr_line(line: str) -> str:
    """Repair narrow OCR substitutions observed in primary statement labels."""
    normalized = line.translate(str.maketrans({"ﬁ": "fi", "ﬂ": "fl"}))
    normalized = re.sub(r"\bStanadalone\b", "Standalone", normalized, flags=re.I)
    normalized = re.sub(r"^(\s*)BCalance\b", r"\1Balance", normalized, flags=re.I)
    normalized = re.sub(r"\bBALANGE\b", "BALANCE", normalized, flags=re.I)
    normalized = re.sub(
        r"\b(Balance\s+Sheet)\s+(?:4s\s+at|asat)\b",
        r"\1 as at",
        normalized,
        flags=re.I,
    )
    normalized = re.sub(
        r"^(\s*)SContents?\s*of\s+(?=Profit\s*(?:and|or|&)\s*Loss\b)",
        r"\1Statement of ",
        normalized,
        flags=re.I,
    )
    # A recurring legacy PDF font map exposes character codes instead of the
    # visible Latin glyphs (for example ``3UR¿W`` visually reads ``Profit``).
    # Decode only exact statement vocabulary; do not apply a general cipher to
    # narrative text or numeric cells.
    for encoded, decoded in (
        (r"3UR[¿À]W", "Profit"),
        (r"/RVV", "/(Loss)"),
        (r"%HIRUH", "Before"),
        (r"7D\[", "Tax"),
        (r"\bIRU\b", "for"),
        (r"\bWKH\b", "the"),
        (r"\b3HULRG\b", "Period"),
        (r"/RQJ", "Long"),
        (r"\b7HUP\b", "Term"),
        (r"%RUURZLQJV", "Borrowings"),
        (r"\b6KRUW\b", "Short"),
        (r"\b5HYHQXH\b", "Revenue"),
        (r"\bIURP\b", "from"),
        (r"\b2SHUDWLRQV\b", "Operations"),
        (r"\b1HW\b", "Net"),
        (r"\b2WKHU\b", "Other"),
        (r",QFRPH", "Income"),
        (r"\)LQDQFH", "Finance"),
        (r"&RVWV", "Costs"),
        (r"'HSUHFLDWLRQ", "Depreciation"),
        (r"\bDQG\b", "and"),
        (r"\$PRUWLVDWLRQ", "Amortisation"),
        (r"0LQRULW\\", "Minority"),
        (r",QWHUHVW", "Interest"),
    ):
        normalized = re.sub(encoded, decoded, normalized)
    normalized = re.sub(r"\bBal\s+Sheet\b", "Balance Sheet", normalized, flags=re.I)
    normalized = re.sub(
        r"\bConsolidatedwProfit\b",
        "Consolidated Profit",
        normalized,
        flags=re.I,
    )
    normalized = re.sub(
        r"\bProfit\s*\.S\s+Loss\b",
        "Profit & Loss",
        normalized,
        flags=re.I,
    )
    normalized = re.sub(
        r"\bLossuh\s+t\b",
        "Loss Account",
        normalized,
        flags=re.I,
    )
    normalized = re.sub(r"\bpro\s+it\b", "profit", normalized, flags=re.I)
    normalized = re.sub(r"\bproÛt\b", "profit", normalized, flags=re.I)
    normalized = re.sub(r"\bProfir\b", "Profit", normalized, flags=re.I)
    normalized = re.sub(r"\bPRORT\b", "PROFIT", normalized, flags=re.I)
    normalized = re.sub(r"\bStatoment\b", "Statement", normalized, flags=re.I)
    normalized = re.sub(r"\bStatem\s+ent\b", "Statement", normalized, flags=re.I)
    normalized = re.sub(r"\bPro\s+fit\b", "Profit", normalized, flags=re.I)
    normalized = re.sub(r"\bC{2,}URRENT\b", "CURRENT", normalized, flags=re.I)
    normalized = re.sub(r"\bcurrenttax\b", "current tax", normalized, flags=re.I)
    normalized = re.sub(r"\bfor\s+for\s+(?=the\b)", "for ", normalized, flags=re.I)
    normalized = re.sub(r"\bforthe\b", "for the", normalized, flags=re.I)
    normalized = re.sub(r"\bonded\b", "ended", normalized, flags=re.I)
    normalized = re.sub(r"\(\s*4v\s*\)", "(IV)", normalized, flags=re.I)
    normalized = re.sub(r"\bit\s+ems\b", "items", normalized, flags=re.I)
    normalized = re.sub(r"\bempicyee\b", "employee", normalized, flags=re.I)
    normalized = re.sub(r"\bsenefit(s?)\b", r"benefit\1", normalized, flags=re.I)
    normalized = re.sub(
        r"\b(?:defered|deferred|deffered|defferred|defferd)\b",
        "deferred",
        normalized,
        flags=re.I,
    )
    normalized = re.sub(r"\bdeferredtax\b", "deferred tax", normalized, flags=re.I)
    normalized = re.sub(r"\botherincome\b", "other income", normalized, flags=re.I)
    normalized = re.sub(r"\bpreviour\b", "previous", normalized, flags=re.I)
    normalized = re.sub(r"\bearliar\b", "earlier", normalized, flags=re.I)
    normalized = re.sub(
        r"\botter(?=\s+(?:expenses?|expenditure)\b)",
        "other",
        normalized,
        flags=re.I,
    )
    normalized = re.sub(
        r"\bprofit\s*/\s*loss\s*[¢{(]?\s*-\s*\)",
        "Profit/(Loss)",
        normalized,
        flags=re.I,
    )
    normalized = re.sub(
        r"\bprofit\s+[I|]\s*\(\s*loss\s*\)",
        "Profit/(Loss)",
        normalized,
        flags=re.I,
    )
    normalized = re.sub(r"\batter\s+tax\b", "after tax", normalized, flags=re.I)
    normalized = re.sub(
        r"\bprofit\s*\(\s*\+\s*\)\s*/\s*loss\s*\(\s*-\s*\)",
        "Profit/(Loss)",
        normalized,
        flags=re.I,
    )
    normalized = re.sub(
        r"\bprofit\s*[?hk]\s*loss(?:\)|\b)",
        "Profit/(Loss)",
        normalized,
        flags=re.I,
    )
    normalized = re.sub(r"\bbefore\s*['’]\s*tax\b", "before tax", normalized, flags=re.I)
    normalized = re.sub(r"\bbefor\s+tax\b", "before tax", normalized, flags=re.I)
    normalized = re.sub(r"\bt\s+ax\b", "tax", normalized, flags=re.I)
    normalized = re.sub(r"\btaxtation\b", "taxation", normalized, flags=re.I)
    return re.sub(r"\bfram\b", "from", normalized, flags=re.I)


def _profit_value_line(lines: list[str], line_number: int) -> str:
    """Join a wrapped profit label through its first line containing amounts."""
    line = _normalize_statement_ocr_line(lines[line_number])
    wrapped_earlier_period_tax = bool(
        re.fullmatch(
            r"\s*(?:income\s+tax\s+[-–—:]?\s*)?earlier\s+periods?\s*:?\s*",
            line,
            re.I,
        )
    )
    line_tokens = _tokens(line)
    wrapped_share_label = bool(
        re.fullmatch(r"\s*share\s+(?:in|of)\s*", line, re.I)
    )
    wrapped_current_tax_overlay = bool(
        not line_tokens and re.search(r"\bcurrent\s*$", line, re.I)
    )
    inside_braced_including = bool(
        re.search(r"\{\s*including\b", line, re.I) and "}" not in line
    )
    if wrapped_current_tax_overlay:
        saw_tax_fragment = False
        for offset in range(1, 6):
            candidate_number = line_number + offset
            if candidate_number >= len(lines):
                break
            candidate = _normalize_statement_ocr_line(lines[candidate_number])
            if re.match(r"^\s*tax\b", candidate, re.I):
                saw_tax_fragment = True
                continue
            candidate_tokens = _tokens(candidate)
            if (
                saw_tax_fragment
                and candidate_tokens
                and not re.search(r"[A-Za-z]", candidate)
            ):
                # A contents-page number can be fused to the far-right edge
                # of the row (``(251.62) 88—``).  Only the first numeric cell
                # belongs to the fragmented current-tax label.
                return f"Current Tax {candidate_tokens[0]}"
    tax_component_label = re.search(
        r"\b(?:current|deferred)\s+tax\b",
        line,
        re.I,
    )
    tax_component_tokens = (
        _tokens(line[tax_component_label.end():])
        if tax_component_label
        else []
    )
    note_only_tax_component = False
    if (
        tax_component_label
        and len(tax_component_tokens) == 1
        and re.fullmatch(r"[1-9]\d{0,2}", tax_component_tokens[0])
    ):
        for offset in range(1, 4):
            if line_number + offset >= len(lines):
                break
            continuation = _normalize_statement_ocr_line(lines[line_number + offset])
            if not continuation.strip():
                continue
            note_only_tax_component = bool(
                re.search(
                    r"\bincluding\b|^\s*[-–—]?\s*current\s+year\b",
                    continuation,
                    re.I,
                )
                and len(_tokens(continuation)) >= 2
            )
            break
    wrapped_tax_component = bool(
        tax_component_label
        and (not tax_component_tokens or note_only_tax_component)
    )
    if (
        not re.search(r"\b(?:earnings|profit|loss)\b", line, re.I)
        and not wrapped_earlier_period_tax
        and not wrapped_tax_component
        and not wrapped_share_label
        and not inside_braced_including
    ) or (
        line_tokens
        and not wrapped_tax_component
        and not inside_braced_including
    ):
        return line
    pieces = [line]
    braced_disclosure_closed = False
    for offset in range(1, 6):
        candidate_number = line_number + offset
        if candidate_number >= len(lines):
            break
        candidate = _normalize_statement_ocr_line(lines[candidate_number])
        if not candidate.strip():
            continue
        if (
            re.search(r"\b(?:profit|loss)\b", line, re.I)
            and re.search(r"\bearnings?\s+per\s+(?:equity\s+)?share\b", candidate, re.I)
        ):
            # A wrapped net-profit label with its amounts printed on the
            # preceding Roman-total row must not walk forward into the EPS
            # section and borrow ``(1) Basic`` as a value.
            return line
        if wrapped_tax_component and re.match(
            r"^\s*(?:(?:[IVXLCDM|!]+|\d+)[.)]?\s+)?"
            r"(?:net\s+)?(?:profit|loss)\b",
            candidate,
            re.I,
        ):
            # A visually blank tax component (most often deferred tax) is
            # followed immediately by the independently labelled net-profit
            # row.  Its amount must not be borrowed as the missing tax cell.
            return line
        if (
            re.search(r"\b(?:earnings|profit|loss)\b", line, re.I)
            and not _tokens(candidate)
            and re.match(r"^\s*tax(?:es)?\s+expenses?\b", candidate, re.I)
        ):
            # A blank reported PBT row followed by the tax-section heading is
            # genuinely blank.  Do not walk through that independent label
            # and borrow the first current-tax amount as PBT.
            return line
        pieces.append(candidate)
        if re.search(r"\{\s*including\b", candidate, re.I):
            inside_braced_including = "}" not in candidate
            braced_disclosure_closed = "}" in candidate
        elif inside_braced_including and "}" in candidate:
            inside_braced_including = False
            braced_disclosure_closed = True
        if inside_braced_including:
            continue
        if _tokens(candidate):
            joined = " ".join(pieces)
            if braced_disclosure_closed:
                closing_tail = candidate.split("}", 1)[-1]
                if not _tokens(closing_tail):
                    continue
            if wrapped_share_label:
                joined = re.sub(
                    r"\bShare\s+of\s+Corporate\s+(?=Profit\b)",
                    "Share of ",
                    joined,
                    flags=re.I,
                )
                joined = re.sub(
                    r"\bGovernance\s+of\s+Joint\s+Report\s+Ventures\b",
                    "of Joint Ventures",
                    joined,
                    flags=re.I,
                )
            return joined
    return line


def _next_numeric_row_continuation(
    lines: list[str], line_number: int, *, max_lookahead: int = 3
) -> Optional[str]:
    """Return the next numeric-only row, allowing blank cropped-column lines."""
    for offset in range(1, max_lookahead + 1):
        candidate_number = line_number + offset
        if candidate_number >= len(lines):
            break
        candidate = lines[candidate_number]
        if not candidate.strip():
            continue
        return candidate if _numeric_row_continuation(candidate) else None
    return None


_PL_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("revenue_from_operations", re.compile(r"\b(?:(?:total\s+)?(?:revenue|income)\s+from\s+operations?|(?:net\s+)?sales|(?:gross|net)\s+turnover|commission\s*(?:and|&)\s*brokerage)\b", re.I)),
    (
        "other_operating_income",
        re.compile(r"\b(?:other\s+)?operating\s+income\b", re.I),
    ),
    ("other_income", re.compile(r"\b(?:(?:other|miscellaneous)\s+income|compensation\s*/\s*d[ea]mages?\s+received)\b", re.I)),
    ("other_gain_loss", re.compile(r"\bother\s+(?:gains?|gain\s*/\s*\(?\s*loss(?:es)?\s*\)?|gains?\s*(?:and|&)\s*losses?)\b", re.I)),
    (
        "associate_impairment",
        re.compile(
            r"\bloss\s+on\s+impairment\s+of\s+(?:an?\s+)?associate\b",
            re.I,
        ),
    ),
    ("finance_income", re.compile(r"\bfinance\s+income\b", re.I)),
    ("total_income", re.compile(r"\btotal\s+(?:income|revenues?(?!\s+from\s+opera))\b", re.I)),
    ("cost_of_materials", re.compile(r"\bcost\s+of\s+materials?\s+consumed\b", re.I)),
    ("purchases_stock_in_trade", re.compile(r"\bpurchases?\s+of\s+stock[-\s]in[-\s]trade\b", re.I)),
    ("inventory_changes", re.compile(r"\bchanges?\s+in\s+inventor", re.I)),
    ("employee_benefits", re.compile(r"\bemployee\s+(?:benefits?|costs?)", re.I)),
    (
        "earnings_before_interest_tax_da",
        re.compile(
            r"\b(?:earnings?|profit(?:\s*(?:/\s*)?\(?loss\)?)?)\s+before\s+"
            r"(?=[^\n]{0,180}\b(?:interest|finance|financial)\b)"
            r"(?=[^\n]{0,180}\b(?:depreciation|amorti[sz])\w*\b)",
            re.I,
        ),
    ),
    ("finance_costs", re.compile(r"\b(?:fi(?:nance|anance)\s+costs?|financial\s+(?:expenses?|charges?)|interest(?!\s+income))\b", re.I)),
    ("depreciation_amortization", re.compile(r"\bdepreciation(?:\s*(?:/|and|&)\s*amorti[sz](?:ation|e))?(?:\s+expenses?)?", re.I)),
    ("other_expenses", re.compile(r"\bother\s+(?:expenses?|expenditure)\b", re.I)),
    ("total_expenses", re.compile(r"\btotal\s+(?:expenses?|expenditure)\b", re.I)),
    ("profit_before_exceptional_items_and_tax", re.compile(r"\b(?:profit(?:\s*(?:/\s*)?\(?loss\)?)?|loss)\s+(?:for\s+(?:the\s+)?(?:year|period)\s+)?before\s+(?:exceptional|extra\s*ordinary)(?:\s+items?)?(?:\s+(?:and|&)(?:\s+extraordinary\s+items?)?(?:\s+(?:and|&))?(?:\s+tax(?:\s+expenses?)?)?)?\b", re.I)),
    ("profit_before_exceptional_items_and_tax", re.compile(r"\b(?:profit(?:\s*(?:/\s*)?\(?loss\)?)?|loss)\s+before\s+tax\s+(?:and|&)\s+exceptional(?:\s+items?)?\b", re.I)),
    (
        "profit_before_exceptional_items_and_tax",
        re.compile(
            r"\b(?:net\s+)?(?:profit(?:\s*(?:/\s*)?\(?loss\)?)?|loss)\s+"
            r"before\s+tax\s*,?\s*(?:minority|non[- ]controlling)\s+"
            r"interest\s*(?:and|&)\s*exceptional(?:\s+items?)?\b",
            re.I,
        ),
    ),
    # Anchor exceptional items to the row label.  Otherwise the preceding
    # "profit before exceptional items and tax" subtotal is a false match.
    (
        "prior_period_items",
        re.compile(
            r"^\s*(?:(?:[IVX]+|\d+)[.)]?\s+)?prior\s+period\s+"
            r"(?:items?|expenses?)\b",
            re.I,
        ),
    ),
    ("exceptional_items", re.compile(r"\b(?:exceptional|extra[-\s]*ordinary)\s+items?\b", re.I)),
    (
        "associate_jv_share",
        re.compile(
            r"\bs?hare\s+(?:in|of)\s+(?:net\s+)?"
            r"(?:profit(?:\s*(?:/\s*)?\(?loss\)?)?|\(?\s*loss\s*\)?).*?"
            r"(?:associates?|joint\s+ventures?|jointly\s+controlled(?:\s+entities)?|"
            r"equity\s+accounted\s+investees)\b",
            re.I,
        ),
    ),
    (
        "minority_interest",
        re.compile(
            r"^\s*(?:(?:[IVX]+|\d+)[.)]?\s+)?(?:minority\s+(?:interest|share)"
            r"|non[- ]controlling\s+interests?)\b",
            re.I,
        ),
    ),
    ("profit_before_associate_and_tax", re.compile(r"\bprofit\s+before\s+share\s+of\s+(?:net\s+)?(?:profit(?:\s*(?:/\s*)?\(?loss\)?)?|\(?\s*loss\s*\)?).*?(?:associates?|joint\s+ventures?|jointly\s+controlled\s+entities).*?\band\s+tax\b", re.I)),
    (
        "profit_before_tax_from_continuing_operations",
        re.compile(
            r"\b(?:profit(?:\s*(?:(?:/|[I|])\s*)?\(?\s*loss\s*\)?)?|"
            r"\(?\s*loss\s*\)?)\s+before\s+tax(?:es|ation)?"
            r"(?:\s+for\s+(?:the\s+)?(?:year|period))?\s+from\s+"
            r"continuing(?:\s+operations?)?\b",
            re.I,
        ),
    ),
    ("profit_before_tax", re.compile(r"\b(?:profit(?:\s*(?:(?:/|[I|])\s*)?\(?\s*loss\s*\)?)?|\(?\s*loss\s*\)?)\s+after\s+exceptional(?:\s+and\s+extra\s*ordinary)?\s+items?\s+(?:but\s+before|and)\s+tax\b", re.I)),
    ("profit_before_tax", re.compile(r"\b(?:profit(?:\s*(?:(?:/|[I|])\s*)?\(?\s*loss\s*\)?)?|\(?\s*loss\s*\)?)\s*(?:(?:for\s+(?:the\s+)?(?:year|period)|from\s+continuing(?:\s+operations?)?)\s+)?(?:after\s+exceptional\s+items?\s+(?:and|but)\s+)?before\s+(?:income\s+)?tax(?:es|ation)?(?:\s+expenses?)?(?:\s+from\s+operations?)?\b", re.I)),
    (
        "discontinued_operations_pre_tax",
        re.compile(
            r"^\s*(?:(?:[IVX]+|\d+)[.)]?\s+)?"
            r"(?:income|profit(?:\s*(?:/\s*)?\(?loss\)?)?|loss)\s+from\s+"
            r"discontinu(?:ed|ing)\s+operations?\b",
            re.I,
        ),
    ),
    (
        "discontinued_operations_tax",
        re.compile(
            r"\btax(?:es)?\s+expenses?\s+(?:of|on)\s+"
            r"discontinu(?:ed|ing)\s+operations?\b",
            re.I,
        ),
    ),
    (
        "earlier_year_tax",
        re.compile(
            r"^\s*(?:(?:[IVX]+|\d+)[.)]?\s+)?earlier\s+tax\b",
            re.I,
        ),
    ),
    ("earlier_year_tax", re.compile(r"\b(?:tax\s+on\s+prior\s+period\s+income|prior\s+years?\s+tax\s+adjustments?|prior\s+(?:period\s+tax(?:es|ation)?|years?(?:\s*\(\s*net\s*\))?)|previous\s+years?|current\s+tax\s+expense\s+relating\s+to\s+prior\s+years?|earlier\s+tax\s+provision|earlier\s+years?'?s?|earlier\s+periods?|tax(?:es)?\s+(?:in\s+respect\s+of\s+|of\s+)?earlier\s+(?:years?|periods?)|tax\s+adjustments?\s+for\s+earlier\s+(?:years?|periods?)|adjustment(?:s)?(?:\s+of\s+tax)?\s+(?:relating\s+to|in\s+respect\s+of)?\s*earlier\s+(?:years?|periods?)|income\s+tax\s+(?:(?:adjustment|provision).*?|(?:pertaining\s+to|paid\s+for)\s+)earlier\s+(?:years?|periods?))\b", re.I)),
    ("tax_provision_adjustment", re.compile(r"(?:\b|\()(?:(?:short\s*\)?\s*/\s*\(?\s*excess|short\s*/\s*\(?\s*excess)\s*\)?\s+(?:tax\s+)?provision(?:\s+(?:of|for)\s+(?:income\s+)?tax)?(?:\s+(?:reversal|adjustment))?|excess\s*/\s*short\s+provision\s+of\s+(?:income\s+)?tax|(?:short|excess)\s+(?:tax\s+provision|prov(?:ision)?\s+(?:of|for)\s+(?:income\s+)?tax)(?:\s+(?:reversal|adjustment))?|impact\s+of\s+deferred\s+tax\s+liabilit(?:y|ies)\s+remeasurement|tax\s+effect\s*\([^)]{1,120}\))", re.I)),
    ("current_tax_net", re.compile(r"\bnet\s+current\s+tax\b", re.I)),
    (
        "current_tax",
        re.compile(
            r"^\s*(?:(?:(?:[IVX]+|\d+)[.)]?|\((?:[IVX]+|\d+)\))\s+)?taxes?\b",
            re.I,
        ),
    ),
    (
        "tax_provision_adjustment",
        re.compile(
            r"^\s*(?:(?:[IVX]+|\d+)[.)]?\s+)?"
            r"(?:(?:add|less)\s*:?\s*)?provision\s+for\s+wealth\s+tax\b",
            re.I,
        ),
    ),
    ("current_tax", re.compile(r"\b(?:current\s+(?:year\s+)?tax(?:es)?|income\s+tax|minimum\s+a(?:l)?ternat(?:e|ive)\s+tax(?!\s+(?:credit|receivable))|of\s+current\s+year|provision\s+for\s+tax(?:es|ation)?|current)\b", re.I)),
    (
        "tax_reserve_transfer",
        re.compile(
            r"\bdeferred\s+tax\s+reserve\s+(?:withdrawn|written\s+back)\b",
            re.I,
        ),
    ),
    (
        "income_tax_expense_reported",
        re.compile(
            r"\btax\s+provision\s*\(\s*net\s+of\s+"
            r"(?:adj\.?|adjustments?)\s*\)",
            re.I,
        ),
    ),
    ("deferred_tax_earlier_adjustment", re.compile(r"\bdeferred\s+tax\s*[-–—]\s*earlier\s+tax\b", re.I)),
    ("deferred_tax", re.compile(r"\bdeferred(?:\s+tax(?:es)?(?:\s+adjustment)?)?\b", re.I)),
    ("mat_credit", re.compile(r"\b(?:(?:mat|minimum\s+a(?:l)?ternat(?:e|ive)\s+tax)\s+credit(?:\s+(?:availed|entitlement|recognition|utili[sz]ation))?|mat\s+entitlement|minimum\s+a(?:l)?ternat(?:e|ive)\s+tax\s+receivable)\b", re.I)),
    ("minimum_alternative_tax", re.compile(r"^\s*(?:[IVX]+\s+)?(?:\(?[a-z0-9]+\)?[.)]?\s+)?MAT\b(?!\s+credit|\s+entitlement)", re.I)),
    ("tax_reserve_transfer", re.compile(r"\b(?:transferred|trf\.?)\s+to\s+(?:(?:spl\.?|special)\s+reserve|bad\s*(?:&|and)\s*doubtful\s+reserve)\b", re.I)),
    (
        "income_tax_expense_reported",
        re.compile(
            r"\b(?:total\s+(?:income\s+)?tax(?:\s+(?:expenses?|charges?|credits?))?"
            r"|(?:total\s+)?tax\s+expenses?)\b"
            r"(?!\s+of\s+discontinu(?:ed|ing))",
            re.I,
        ),
    ),
    ("profit_before_regulatory_deferral", re.compile(r"\bprofit\s+for\s+the\s+period\s+before\s+regulatory\s+deferral\s+account(?:\s+balances)?\b", re.I)),
    ("profit_after_tax_before_minority_interest", re.compile(r"\b(?:profit(?:\s*(?:/\s*)?\(?loss\)?)?|loss)(?:\s+for\s+(?:the\s+)?(?:year|period))?(?:\s+after\s+tax\s+(?:but\s+)?)?\s+before\s+(?:minority|non[- ]controlling)\s+interest\b", re.I)),
    ("profit_from_continuing_operations", re.compile(r"\b(?:profit(?:\s*(?:/\s*)?\(?loss\)?)?|loss)\s+(?:from\s+(?:the\s+)?period\s+from\s+|(?:(?:for\s+(?:the\s+)?(?:year|period)\s+)?(?:after\s+tax\s+)?)?(?:from\s+)?)continuing(?:\s+operations?)?\b", re.I)),
    ("profit_after_tax", re.compile(r"\b(?:net\s+)?(?:profit(?:\s*(?:/\s*)?\(?loss\)?)?|\(?\s*loss\s*\)?)\s+(?:after\s+tax(?:es|ation)?(?:\s+before\s+(?:minority|non[- ]controlling)\s+interest)?(?:\s+for\s+(?:the\s+)?(?:year|period))?|for\s+(?:the\s+)?(?:year|period)\s+before\s+share\s+of\s+(?:profit(?:\s*(?:/\s*)?\(?loss\)?)?|loss))\b", re.I)),
    ("profit_after_tax", re.compile(r"^\s*(?:(?:[IVX]+|\d+)[.)]?\s+)?net\s+profit\b", re.I)),
    ("profit_for_period", re.compile(r"\b(?:profit(?:\s*(?:/\s*)?\(?loss\)?)?|loss)\s+for\s+(?:the\s+)?(?:years?|periods?)\b", re.I)),
    ("interest_income", re.compile(r"\binterest\s+income\b", re.I)),
    ("dividend_income", re.compile(r"\bdividend\s+income\b", re.I)),
    ("fair_value_changes", re.compile(r"\bfair\s+value\s+changes\b", re.I)),
]


def _extract_profit_and_loss(lines: list[str]) -> dict[str, Optional[float]]:
    values: dict[str, Optional[float]] = {}
    revenue_priority = -1
    current_tax_priority = -1
    deferred_tax_priority = -1
    reported_tax_priority = -1
    tax_section_started = False
    tax_section_closed = False
    tax_deduction_presentation = False
    statement_section: Optional[str] = None
    associate_line: Optional[int] = None
    profit_before_tax_line: Optional[int] = None
    deferred_tax_named_components: dict[str, float] = {}
    header_lines = lines[:10]
    header_text = _normalize_date_ocr(" ".join(header_lines))
    header_date_count = sum(
        len(pattern.findall(header_text)) for pattern in _DATE_PATTERNS
    )
    header_years = set(re.findall(r"\b(?:19|20)\d{2}\b", header_text))
    short_header_dates = re.findall(
        r"\b\d{1,2}[./-]\d{1,2}[./-]\d{2}\b", header_text
    )
    abbreviated_month_year_columns = re.findall(
        r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
        r"(?:[a-z]+)?(?:[-/']\d{2,4}|\s+\d{4})\b",
        header_text,
        re.I,
    )
    repeated_year_ended = any(
        len(re.findall(r"\byear\s+ended\b", line, re.I)) >= 2
        for line in header_lines
    )
    current_previous_columns = bool(
        re.search(r"\bcurrent\s+year\b.*\bprevious\s+year\b", header_text, re.I)
    )
    previous_year_column = bool(
        re.search(r"\bprevious\s+year\b", header_text, re.I)
    )
    note_column = any(
        re.search(r"\bparticulars\b.*\bnotes?(?:\s+no\.?)?\b", line, re.I)
        or re.search(r"^\s*(?:notes?(?:\s+no\.?)?|schedules?)\b", line, re.I)
        or re.search(
            r"^\s*(?:s\.?|sl)(?:\s*no\.?)?\s+.*\bnotes?(?:\s+no\.?)?\b",
            line,
            re.I,
        )
        for line in header_lines
    )
    leading_reference_columns = (
        2
        if re.search(
            r"\bnotes?(?:\s+no\.?)?\s+page\b|\bpage\s+notes?(?:\s+no\.?)?\b",
            header_text,
            re.I,
        )
        else 1
    )
    value_column_count = (
        2
        if (
            header_date_count >= 3
            or len(header_years) >= 2
            or len(short_header_dates) >= 2
            or len(abbreviated_month_year_columns) >= 2
            or repeated_year_ended
            or current_previous_columns
            or previous_year_column
        )
        else 1
    )
    if value_column_count == 1 and note_column:
        def value_tokens_after_label(candidate_line: str) -> int:
            normalized_line = _normalize_statement_ocr_line(candidate_line)
            matches = [
                match
                for _, pattern in _PL_PATTERNS
                if (match := pattern.search(normalized_line)) is not None
            ]
            if not matches:
                return 0
            label_match = min(matches, key=lambda match: match.start())
            return len(_tokens(normalized_line[label_match.end():]))

        rows_with_note_and_two_values = sum(
            value_tokens_after_label(line) >= 3
            and not re.search(
                r"\(\s*[IVXLCDM\d]+\s*[-+]\s*[IVXLCDM\d]+\s*\)",
                line,
                re.I,
            )
            for line in lines[1:40]
        )
        if rows_with_note_and_two_values >= 2:
            value_column_count = 2

    def next_unlabelled_numeric_value(after_line: int) -> Optional[float]:
        for candidate_number in range(after_line + 1, min(len(lines), after_line + 5)):
            candidate = _normalize_statement_ocr_line(lines[candidate_number])
            if not candidate.strip():
                continue
            if not _numeric_row_continuation(candidate):
                return None
            candidate_tokens = _tokens(candidate)
            return parse_amount(candidate_tokens[0]) if candidate_tokens else None
        return None

    for line_number, _ in enumerate(lines):
        line = _profit_value_line(lines, line_number)
        if re.fullmatch(
            r"\s*(?:[ivxlcdm]+\s*[|.)]?\s*)?income\s*:?\s*[-–—|]*\s*",
            line,
            re.I,
        ):
            statement_section = "income"
        elif re.fullmatch(
            r"\s*(?:[ivxlcdm]+\s*[|.)]?\s*)?(?:expenditure|expenses?)\s*:?\s*[-–—|]*\s*",
            line,
            re.I,
        ):
            statement_section = "expenses"
        if _numeric_row_continuation(line):
            numeric_tokens = _tokens(line)
            unlabeled_value = (
                parse_amount(numeric_tokens[0]) if numeric_tokens else None
            )
            income_subtotal = values.get("total_income")
            if (
                income_subtotal is None
                and values.get("revenue_from_operations") is not None
                and values.get("other_income") is not None
            ):
                income_subtotal = sum(
                    values.get(name, 0.0) or 0.0
                    for name in (
                        "revenue_from_operations",
                        "other_operating_income",
                        "other_income",
                        "finance_income",
                        "other_gain_loss",
                    )
                )
            next_unlabeled_value = next_unlabelled_numeric_value(line_number)
            if (
                unlabeled_value is not None
                and income_subtotal is not None
                and values.get("total_expenses") is None
                and statement_section == "expenses"
                and any(
                    values.get(name) is not None
                    for name in (
                        "employee_benefits",
                        "finance_costs",
                        "depreciation_amortization",
                        "other_expenses",
                    )
                )
                and next_unlabeled_value is not None
                and _within_one_percent(
                    _ratio(
                        income_subtotal - unlabeled_value,
                        next_unlabeled_value,
                    )
                )
            ):
                # A few legacy statements lose the printed ``Total expenses``
                # label but preserve both the subtotal and the immediately
                # following pre-tax subtotal.  The independent income-minus-
                # expenses identity makes the pair auditable.
                values["total_expenses"] = unlabeled_value
                values["total_expenses_source"] = (
                    "unlabelled_subtotal_validated_by_income_identity"
                )
                values["_total_expenses_line"] = line_number
            elif (
                unlabeled_value is not None
                and income_subtotal is not None
                and values.get("total_expenses") is not None
                and values.get("profit_before_exceptional_items_and_tax") is None
                and values.get("profit_before_tax") is None
                and _within_one_percent(
                    _ratio(
                        income_subtotal - values["total_expenses"],
                        unlabeled_value,
                    )
                )
            ):
                values["profit_before_exceptional_items_and_tax"] = (
                    unlabeled_value
                )
                values["profit_before_exceptional_items_and_tax_source"] = (
                    "unlabelled_total_income_less_total_expenses_identity"
                )
                values["_profit_before_exceptional_items_and_tax_line"] = (
                    line_number
                )
            elif (
                unlabeled_value is not None
                and values.get("profit_before_tax") is None
                and values.get("profit_before_exceptional_items_and_tax")
                is not None
                and values.get("exceptional_items") is not None
            ):
                preexceptional = values[
                    "profit_before_exceptional_items_and_tax"
                ]
                exceptional = values["exceptional_items"]
                less_ratio = _ratio(
                    preexceptional - exceptional, unlabeled_value
                )
                add_ratio = _ratio(
                    preexceptional + exceptional, unlabeled_value
                )
                if _within_one_percent(min(less_ratio, add_ratio)):
                    values["profit_before_tax"] = unlabeled_value
                    values["profit_before_tax_source"] = (
                        "unlabelled_preexceptional_bridge_identity"
                    )
                    values["profit_before_tax_position"] = (
                        "after_exceptional_items"
                    )
                    if less_ratio <= add_ratio:
                        values["exceptional_items_presentation"] = "less"
        generic_total = re.match(
            r"^\s*(?:[ivxlcdm]+\s*[|.)]?\s*)?(?:sub\s+)?total\b",
            line,
            re.I,
        )
        if (
            generic_total
            and statement_section is not None
            and not any(
                values.get(name) is not None
                for name in (
                    "profit_before_exceptional_items_and_tax",
                    "profit_before_associate_and_tax",
                    "profit_before_tax",
                    "profit_before_regulatory_deferral",
                    "profit_after_tax_before_minority_interest",
                    "profit_after_tax",
                    "profit_for_period",
                )
            )
            and not re.search(
                r"\b(?:income|revenue|expenses?|expenditure|exceptional|tax|"
                r"profit|loss)\b",
                line[generic_total.end():],
                re.I,
            )
        ):
            generic_value = _current_value(
                line,
                generic_total,
                drop_leading_note=note_column,
                value_column_count=value_column_count,
            )
            if generic_value is not None:
                values[
                    "total_income" if statement_section == "income" else "total_expenses"
                ] = generic_value
        if (
            any(
                values.get(name) is not None
                for name in (
                    "profit_before_tax",
                    "profit_after_tax_before_minority_interest",
                    "profit_after_tax",
                    "profit_for_period",
                )
            )
            and not _PROFIT_HEADING.search(" ".join(line.split()))
            and not re.search(r"\.{8,}", line)
            and re.search(
                r"\b(?:cash\s+flow\s+statement|statement\s+of\s+cash\s+flows|"
                r"cash\s+flows?\s+from\s+operating\s+activities|"
                r"statement\s+of\s+changes\s+in\s+equity)\b",
                line,
                re.I,
            )
        ):
            break
        if re.search(r"\bless\s*:?\s*tax\s+expenses?\b", line, re.I):
            tax_deduction_presentation = True
        if _PROFIT_HEADING.search(" ".join(line.split())):
            continue
        doubtful_debt_exceptional = re.search(
            r"^\s*less\s*:?\s*provision\s+for\s+doubtful\s+debts?\b",
            line,
            re.I,
        )
        if (
            doubtful_debt_exceptional
            and values.get("profit_before_exceptional_items_and_tax") is not None
            and values.get("profit_before_tax") is None
            and not tax_section_started
        ):
            exceptional_value = _current_value(
                line,
                doubtful_debt_exceptional,
                drop_leading_note=note_column,
                value_column_count=value_column_count,
            )
            if exceptional_value is not None:
                values["exceptional_items"] = exceptional_value
                values["exceptional_items_presentation"] = "less"
                values["_exceptional_items_line"] = line_number
                continue
        if re.match(r"^\s*\(\s*including\b", line, re.I) and re.search(
            r"\b(?:mat\s+credit|wealth\s+tax)\b",
            line,
            re.I,
        ):
            # This is a parenthetical disclosure attached to the preceding
            # current-tax row, not a separately reported MAT/wealth component.
            # The preceding iteration joins the line when it carries the
            # statement amounts.
            continue
        for metric, pattern in _PL_PATTERNS:
            if metric in values and metric not in {
                "revenue_from_operations",
                "profit_before_tax",
                "current_tax",
                "deferred_tax",
                "income_tax_expense_reported",
                "earlier_year_tax",
                "tax_provision_adjustment",
                "deferred_tax_earlier_adjustment",
                "tax_reserve_transfer",
                "exceptional_items",
                "associate_jv_share",
            }:
                continue
            matches = list(pattern.finditer(line))
            match = matches[0] if matches else None
            if (
                metric
                in {
                    "current_tax",
                    "current_tax_net",
                    "deferred_tax",
                    "deferred_tax_earlier_adjustment",
                    "earlier_year_tax",
                    "tax_provision_adjustment",
                    "mat_credit",
                    "minimum_alternative_tax",
                    "tax_reserve_transfer",
                    "income_tax_expense_reported",
                }
                and len(matches) > 1
            ):
                explicit_tax_matches = [
                    candidate
                    for candidate in matches
                    if re.search(r"\btax\b", candidate.group(0), re.I)
                ]
                match = (explicit_tax_matches or matches)[-1]
            if match:
                if (
                    metric == "discontinued_operations_pre_tax"
                    and re.search(r"\b(?:net\s+of|after)\s+tax\b", line, re.I)
                ):
                    continue
                if (
                    metric
                    in {
                        "current_tax",
                        "current_tax_net",
                        "deferred_tax",
                        "deferred_tax_earlier_adjustment",
                        "earlier_year_tax",
                        "tax_provision_adjustment",
                        "mat_credit",
                        "minimum_alternative_tax",
                        "tax_reserve_transfer",
                        "income_tax_expense_reported",
                    }
                    and values.get("profit_before_tax") is None
                    and values.get(
                        "profit_before_tax_from_continuing_operations"
                    )
                    is None
                    and values.get("profit_before_associate_and_tax") is None
                    and values.get("profit_before_exceptional_items_and_tax") is None
                    and not (
                        metric == "deferred_tax"
                        and any(
                            values.get(name) is not None
                            for name in (
                                "employee_benefits",
                                "finance_costs",
                                "depreciation_amortization",
                                "other_expenses",
                                "total_expenses",
                            )
                        )
                    )
                ):
                    continue
                if metric == "income_tax_expense_reported":
                    tax_section_started = True
                if (
                    metric == "current_tax"
                    and re.fullmatch(r"current", match.group(0), re.I)
                    and (
                        not tax_section_started
                        or re.search(r"\bdeferred\s+tax\b", line[:match.start()], re.I)
                    )
                ):
                    continue
                if metric == "current_tax" and re.search(
                    r"\btotal\s+(?:income\s+)?tax\b", line, re.I
                ):
                    continue
                if (
                    metric
                    in {
                        "current_tax",
                        "current_tax_net",
                        "deferred_tax",
                        "deferred_tax_earlier_adjustment",
                        "earlier_year_tax",
                        "tax_provision_adjustment",
                        "mat_credit",
                        "minimum_alternative_tax",
                        "tax_reserve_transfer",
                        "income_tax_expense_reported",
                    }
                    and tax_section_closed
                ):
                    continue
                if (
                    metric
                    in {
                        "profit_before_tax",
                        "profit_before_regulatory_deferral",
                        "profit_after_tax_before_minority_interest",
                        "profit_after_tax",
                        "profit_for_period",
                        "current_tax",
                        "current_tax_net",
                        "deferred_tax",
                        "deferred_tax_earlier_adjustment",
                        "earlier_year_tax",
                        "tax_provision_adjustment",
                        "mat_credit",
                        "minimum_alternative_tax",
                        "tax_reserve_transfer",
                        "income_tax_expense_reported",
                    }
                    and re.search(
                        r"\bdiscontinu(?:ed|ing)\b",
                        re.sub(
                            r"\{\s*including\b.*?\}",
                            " ",
                            line,
                            flags=re.I,
                        ),
                        re.I,
                    )
                ):
                    continue
                if (
                    metric == "income_tax_expense_reported"
                    and (
                        re.search(
                            r"\brelating\s+to\s+(?:the\s+above\s+)?items?\b",
                            line,
                            re.I,
                        )
                        or re.search(r"\bcomprehensive\b", line, re.I)
                    )
                ):
                    continue
                if metric == "income_tax_expense_reported" and note_column:
                    trailing_tokens = _tokens(line[match.end():])
                    if len(trailing_tokens) == 1 and re.fullmatch(
                        r"[1-9]\d{0,2}", trailing_tokens[0]
                    ):
                        continue
                if metric == "income_tax_expense_reported" and re.fullmatch(
                    r"\s*(?:/\s*\(?\s*credits?\s*\)?\s*)?"
                    r"\d{1,3}\s*\(\s*\d+(?:\s*[a-z])?(?:\.\d+)?\s*\)\s*",
                    line[match.end():],
                    re.I,
                ) and not re.search(
                    r"\btotal\s+(?:income\s+)?tax\b",
                    match.group(0),
                    re.I,
                ):
                    continue
                if (
                    metric == "earlier_year_tax"
                    and values.get("profit_before_tax") is None
                    and values.get(
                        "profit_before_tax_from_continuing_operations"
                    )
                    is None
                    and values.get("profit_before_associate_and_tax") is None
                    and values.get("profit_before_exceptional_items_and_tax") is None
                ):
                    break
                if (
                    metric == "earlier_year_tax"
                    and re.fullmatch(r"previous\s+years?", match.group(0), re.I)
                    and not tax_section_started
                ):
                    continue
                if (
                    metric == "earlier_year_tax"
                    and re.search(
                        r"\bprevious\s+year\s+rs\.?\s*\d",
                        line,
                        re.I,
                    )
                ):
                    continue
                if (
                    metric == "deferred_tax"
                    and not re.search(r"\btax", match.group(0), re.I)
                    and not tax_section_started
                ):
                    continue
                prefix = line[:match.start()].lower()
                if metric == "earlier_year_tax" and re.search(
                    r"\bcurrent\s+tax\b.*\binclud(?:e[sd]?|ing)\b",
                    prefix,
                    re.I,
                ):
                    continue
                if metric == "tax_reserve_transfer" and not tax_section_started:
                    continue
                if metric == "current_tax" and re.search(
                    r"\b(?:mat|minimum\s+a(?:l)?ternative\s+tax)\s+credit\b",
                    prefix,
                    re.I,
                ):
                    continue
                if metric == "current_tax" and re.search(
                    r"\bdeferred\s*$",
                    prefix,
                    re.I,
                ):
                    continue
                if metric == "earlier_year_tax" and re.search(
                    r"\b(?:profit|loss)\b", prefix
                ):
                    continue
                if metric == "earlier_year_tax" and re.search(
                    r"\b(?:profit|loss).*?\b(?:before|after)\b", prefix
                ):
                    continue
                if metric == "finance_costs" and re.search(
                    r"\b(?:minority|non[- ]controlling)\s*$|"
                    r"\b(?:earnings|profit|loss)\b.*\bbefore\b",
                    prefix,
                    re.I,
                ):
                    continue
                if metric == "depreciation_amortization" and re.search(
                    r"\b(?:earnings|profit|loss)\b.*\bbefore\b",
                    prefix,
                    re.I,
                ):
                    continue
                if metric == "exceptional_items" and re.search(
                    r"\b(?:earnings|profit|loss).*?\b(?:before|after)\b", prefix
                ):
                    continue
                if metric == "exceptional_items" and re.search(
                    r"\b(?:basic|diluted|earnings?\s+per\s+share|eps)\b",
                    prefix,
                    re.I,
                ):
                    continue
                if metric == "associate_jv_share" and re.search(
                    r"\b(?:profit|loss).*?\bbefore\b", prefix
                ):
                    continue
                if metric == "profit_from_continuing_operations" and re.search(
                    r"\b(?:computed|calculated)\s+(?:on\s+)?(?:the\s+)?basis\b|"
                    r"\bearnings?\s+per\s+share\b|\beps\b",
                    prefix,
                    re.I,
                ):
                    continue
                if metric == "profit_for_period" and re.search(
                    r"\b(?:other|total)\s+comprehensive\b", prefix
                ):
                    continue
                if (
                    metric == "current_tax"
                    and re.search(r"\bincome\s+tax\b", match.group(0), re.I)
                    and not re.search(r"\bcurrent\s+tax\b", match.group(0), re.I)
                    and any(
                        values.get(name) is not None
                        for name in (
                            "profit_after_tax_before_minority_interest",
                            "profit_from_continuing_operations",
                            "profit_after_tax",
                            "profit_for_period",
                        )
                    )
                ):
                    continue
                if metric in {
                    "current_tax",
                    "current_tax_net",
                    "deferred_tax",
                    "deferred_tax_earlier_adjustment",
                    "earlier_year_tax",
                    "tax_provision_adjustment",
                    "mat_credit",
                    "minimum_alternative_tax",
                    "tax_reserve_transfer",
                    "income_tax_expense_reported",
                }:
                    tax_section_started = True
                value = _current_value(
                    line,
                    match,
                    drop_leading_note=note_column,
                    value_column_count=value_column_count,
                    leading_reference_columns=leading_reference_columns,
                )
                if metric in {
                    "current_tax",
                    "current_tax_net",
                    "deferred_tax",
                    "deferred_tax_earlier_adjustment",
                    "earlier_year_tax",
                    "tax_provision_adjustment",
                    "mat_credit",
                    "minimum_alternative_tax",
                    "tax_reserve_transfer",
                }:
                    value = _tax_component_value(
                        line,
                        match,
                        drop_leading_note=note_column,
                        value_column_count=value_column_count,
                        leading_reference_columns=leading_reference_columns,
                    )
                if (
                    metric
                    in {
                        "profit_after_tax_before_minority_interest",
                        "profit_from_continuing_operations",
                        "profit_after_tax",
                        "profit_for_period",
                    }
                    and value is None
                    and line_number > 0
                    and re.search(
                        r"\(\s*[IVXLCDM]+\s*(?:[-+]\s*[IVXLCDM]+)+\s*\)",
                        line[match.end():],
                        re.I,
                    )
                ):
                    preceding = _normalize_statement_ocr_line(
                        lines[line_number - 1]
                    )
                    preceding_tokens = _tokens(preceding)
                    if (
                        re.match(r"^\s*[IVXLCDM]+\s+", preceding, re.I)
                        and len(preceding_tokens) == value_column_count
                    ):
                        value = parse_amount(preceding_tokens[0])
                        values[f"{metric}_source"] = (
                            "preceding_roman_total_amount_row"
                        )
                if metric == "exceptional_items" and value is None:
                    # Legacy statements sometimes print an explicit
                    # ``Exceptional items`` header and put the sole component
                    # (for example an OTS write-back) on the following line.
                    for offset in range(1, 5):
                        if line_number + offset >= len(lines):
                            break
                        wrapped = _normalize_statement_ocr_line(
                            lines[line_number + offset]
                        )
                        if re.search(
                            r"\bprofit\s*(?:/\s*)?\(?loss\)?\s+before\s+tax\b",
                            wrapped,
                            re.I,
                        ):
                            break
                        if not re.search(
                            r"\b(?:written\s+back|write[- ]back|settlement|"
                            r"one[- ]time\s+write[- ]off)\b",
                            wrapped,
                            re.I,
                        ):
                            continue
                        empty_match = re.match(r"^", wrapped)
                        assert empty_match is not None
                        value = _current_value(
                            wrapped,
                            empty_match,
                            drop_leading_note=note_column,
                            value_column_count=value_column_count,
                        )
                        if value is not None:
                            break
                if metric == "earlier_year_tax" and value is None:
                    single_earlier_tax_token = _tokens(line[match.end():])
                    if (
                        len(single_earlier_tax_token) == 1
                        and single_earlier_tax_token[0] not in {"-", "–", "—"}
                        and re.search(
                            r"\b(?:earlier|prior|previous)\s+(?:years?|periods?)\b",
                            match.group(0),
                            re.I,
                        )
                    ):
                        value = parse_amount(single_earlier_tax_token[0])
                if metric == "earlier_year_tax" and value is None:
                    grouped_earlier_tax: list[float] = []
                    for offset in range(1, 4):
                        if line_number + offset >= len(lines):
                            break
                        grouped_line = _normalize_statement_ocr_line(
                            lines[line_number + offset]
                        )
                        grouped_match = re.match(
                            r"^\s*[-–—]?\s*(?:current|deferred)\s+tax\b",
                            grouped_line,
                            re.I,
                        )
                        if grouped_match is None:
                            if grouped_earlier_tax and grouped_line.strip():
                                break
                            continue
                        grouped_value = _current_value(
                            grouped_line,
                            grouped_match,
                            drop_leading_note=note_column,
                            value_column_count=value_column_count,
                        )
                        if grouped_value is not None:
                            grouped_earlier_tax.append(grouped_value)
                    if grouped_earlier_tax:
                        value = sum(grouped_earlier_tax)
                if metric == "current_tax":
                    current_tax_mat = re.search(
                        r"\bcurrent\s+tax\s*[-–—:]?\s*mat\b",
                        line,
                        re.I,
                    )
                    if current_tax_mat is not None:
                        # The complete label ends at ``MAT``; any following
                        # amounts are statement columns, never a note number.
                        # Re-read from that boundary instead of indexing from
                        # the right, which shifted weak-header layouts to the
                        # comparative period when ``value_column_count == 1``.
                        value = _current_value(
                            line,
                            current_tax_mat,
                            drop_leading_note=False,
                            value_column_count=value_column_count,
                        )
                    if re.search(
                        r"\bcurrent\s+tax\b.*\bincluding\b.*\bearlier\s+years?\b",
                        line,
                        re.I,
                    ):
                        current_tax_tokens = _tokens(line)
                        if len(current_tax_tokens) >= value_column_count:
                            value = parse_amount(
                                current_tax_tokens[-value_column_count]
                            )
                    closed_inline_wealth_disclosure = bool(
                        re.search(
                            r"\(\s*including\b[^)]*\bwealth\s+tax\b[^)]*\)"
                            r"\s+[-–—(\d]",
                            line,
                            re.I,
                        )
                    )
                    if re.search(
                        r"\bcurrent\s+tax\b.*\bincluding\b.*\bwealth\s+tax\b",
                        line,
                        re.I,
                    ) and "]" not in line and not closed_inline_wealth_disclosure:
                        for offset in range(1, 4):
                            if line_number + offset >= len(lines):
                                break
                            note_or_value_line = _normalize_statement_ocr_line(
                                lines[line_number + offset]
                            )
                            note_or_value_tokens = _tokens(note_or_value_line)
                            if len(note_or_value_tokens) >= value_column_count:
                                value = parse_amount(
                                    note_or_value_tokens[-value_column_count]
                                )
                                break
                            if not (
                                not note_or_value_line.strip()
                                or re.search(
                                    r"\b(?:previous\s+year|wealth\s+tax)\b",
                                    note_or_value_line,
                                    re.I,
                                )
                            ):
                                break
                if metric == "current_tax" and re.search(
                    r"\bwealth\s+tax\b", line, re.I
                ):
                    for offset in range(1, 3):
                        if line_number + offset >= len(lines):
                            break
                        wrapped = lines[line_number + offset]
                        if "]" not in wrapped:
                            continue
                        wrapped_tokens = _tokens(wrapped)
                        if len(wrapped_tokens) >= value_column_count:
                            value = parse_amount(
                                wrapped_tokens[-value_column_count]
                            )
                            break
                if metric == "current_tax" and re.search(
                    r"\bincluding\b.*\b(?:excess|short)\b", line, re.I
                ):
                    trailing_tokens = _tokens(line[match.end():])
                    if len(trailing_tokens) >= value_column_count:
                        value = parse_amount(trailing_tokens[-value_column_count])
                continuation_line = _next_numeric_row_continuation(lines, line_number)
                if (
                    metric == "profit_before_tax"
                    and value is not None
                    and continuation_line is not None
                ):
                    continuation_tokens = _tokens(continuation_line)
                    continuation_value = (
                        parse_amount(continuation_tokens[0])
                        if continuation_tokens
                        else None
                    )
                    total_income = values.get("total_income")
                    total_expenses = values.get("total_expenses")
                    expected_pbt = (
                        total_income - total_expenses
                        if total_income is not None and total_expenses is not None
                        else None
                    )
                    continuation_has_accounting_support = (
                        continuation_value is not None
                        and expected_pbt is not None
                        and _ratio(expected_pbt, continuation_value) <= 0.01
                        and _ratio(expected_pbt, continuation_value)
                        < _ratio(expected_pbt, value)
                    )
                    if continuation_has_accounting_support:
                        value = continuation_value
                        values["profit_before_tax_source"] = (
                            "numeric_continuation_after_side_by_side_label"
                        )
                if (
                    metric
                    in {
                        "profit_after_tax_before_minority_interest",
                        "profit_from_continuing_operations",
                        "profit_after_tax",
                        "profit_for_period",
                    }
                    and value in (None, 0.0)
                    and re.search(r"\(\s*[IVXLCDM]+\s*[-–—]\s*$", line, re.I)
                ):
                    for offset in range(1, 3):
                        if line_number + offset >= len(lines):
                            break
                        wrapped_tokens = _tokens(lines[line_number + offset])
                        wrapped_value = next(
                            (
                                parse_amount(token)
                                for token in wrapped_tokens
                                if token not in {"-", "–", "—"}
                            ),
                            None,
                        )
                        if wrapped_value is not None:
                            value = wrapped_value
                            values[f"{metric}_source"] = (
                                "wrapped_numeric_after_roman_formula"
                            )
                            break
                if metric == "deferred_tax" and value is None:
                    deferred_parts: list[float] = []
                    for offset in range(1, 4):
                        if line_number + offset >= len(lines):
                            break
                        deferred_line = _normalize_statement_ocr_line(
                            lines[line_number + offset]
                        )
                        deferred_match = re.match(
                            r"^\s*[-:]?\s*(?:current\s+year|prior\s+period)\b",
                            deferred_line,
                            re.I,
                        )
                        if deferred_match is None:
                            if deferred_parts and deferred_line.strip():
                                break
                            continue
                        deferred_value = _current_value(
                            deferred_line,
                            deferred_match,
                            value_column_count=value_column_count,
                        )
                        if deferred_value is not None:
                            deferred_parts.append(deferred_value)
                    if deferred_parts:
                        value = sum(deferred_parts)
                if metric == "deferred_tax":
                    deferred_tail = re.sub(
                        r"^\s*\d{1,2}(?:\.\d{1,2})?\s*"
                        r"\(\s*\d{1,2}\s*\)\s*",
                        " ",
                        line[match.end():],
                    )
                    deferred_tokens = _tokens(deferred_tail)
                    if len(deferred_tokens) >= 4:
                        displayed_deferred = (
                            0.0
                            if deferred_tokens[0] in {"-", "–", "—"}
                            else parse_amount(deferred_tokens[0])
                        )
                        displayed_total = parse_amount(deferred_tokens[1])
                        existing_current = values.get("current_tax")
                        if (
                            displayed_deferred is not None
                            and displayed_total is not None
                            and existing_current is not None
                            and _ratio(
                                existing_current + displayed_deferred,
                                displayed_total,
                            )
                            <= 0.01
                        ):
                            value = displayed_deferred
                            values["income_tax_expense_reported"] = displayed_total
                            reported_tax_priority = max(reported_tax_priority, 2)
                        elif values.get("current_tax_net") is not None:
                            value = parse_amount(deferred_tokens[0])
                if (
                    value is None
                    and tax_section_started
                    and metric
                    in {
                        "current_tax",
                        "deferred_tax",
                        "earlier_year_tax",
                        "tax_provision_adjustment",
                        "mat_credit",
                        "minimum_alternative_tax",
                    }
                    and re.match(r"^\s*\(\s*[a-z0-9]+\s*\)", line, re.I)
                ):
                    enumerated_tokens = _tokens(line[match.end():])
                    if len(enumerated_tokens) == 1:
                        value = parse_amount(enumerated_tokens[0])
                if (
                    metric == "mat_credit"
                    and re.search(r"\bless\s*:", line, re.I)
                    and continuation_line is not None
                ):
                    subtotal_tokens = _tokens(continuation_line)
                    if subtotal_tokens:
                        subtotal = parse_amount(subtotal_tokens[0])
                        signed_mat = value
                        if signed_mat is not None and signed_mat > 0:
                            signed_mat = -signed_mat
                        total_parts = [
                            values.get(name)
                            for name in (
                                "current_tax",
                                "deferred_tax",
                                "deferred_tax_earlier_adjustment",
                                "earlier_year_tax",
                                "tax_provision_adjustment",
                                "tax_reserve_transfer",
                            )
                            if values.get(name) is not None
                        ]
                        non_current_tax_parts_present = any(
                            values.get(name) is not None
                            for name in (
                                "deferred_tax",
                                "deferred_tax_earlier_adjustment",
                                "earlier_year_tax",
                                "tax_provision_adjustment",
                                "tax_reserve_transfer",
                            )
                        )
                        if signed_mat is not None:
                            total_parts.append(signed_mat)
                        current_and_mat = [
                            part
                            for part in (values.get("current_tax"), signed_mat)
                            if part is not None
                        ]
                        if (
                            subtotal is not None
                            and total_parts
                            and non_current_tax_parts_present
                            and _ratio(sum(total_parts), subtotal) <= 0.01
                        ):
                            values["income_tax_expense_reported"] = subtotal
                            reported_tax_priority = max(reported_tax_priority, 2)
                            continuation_line = None
                        elif (
                            subtotal is not None
                            and current_and_mat
                            and _ratio(sum(current_and_mat), subtotal) <= 0.01
                        ):
                            values["current_tax_net"] = subtotal
                if (
                    metric == "mat_credit"
                    and value is None
                    and continuation_line is not None
                    and re.match(r"^\s*(?:[IVX]+\s+)?\(?[a-z]\)", line, re.I)
                ):
                    subtotal_tokens = _tokens(continuation_line)
                    existing_tax_parts = [
                        values.get(name)
                        for name in (
                            "current_tax",
                            "deferred_tax",
                            "deferred_tax_earlier_adjustment",
                            "earlier_year_tax",
                            "tax_provision_adjustment",
                        )
                        if values.get(name) is not None
                    ]
                    if subtotal_tokens and existing_tax_parts:
                        subtotal = parse_amount(subtotal_tokens[0])
                        if (
                            subtotal is not None
                            and _ratio(sum(existing_tax_parts), subtotal) <= 0.01
                        ):
                            values["income_tax_expense_reported"] = subtotal
                            reported_tax_priority = max(reported_tax_priority, 2)
                            continuation_line = None
                if (
                    metric == "deferred_tax"
                    and value is not None
                    and continuation_line is not None
                ):
                    subtotal_tokens = _tokens(continuation_line)
                    if len(subtotal_tokens) >= 2 and values.get("current_tax") == 0.0:
                        wrapped_current = parse_amount(subtotal_tokens[0])
                        wrapped_total = parse_amount(subtotal_tokens[1])
                        if (
                            wrapped_current is not None
                            and wrapped_total is not None
                            and _ratio(wrapped_current + value, wrapped_total) <= 0.01
                        ):
                            values["current_tax"] = wrapped_current
                            current_tax_priority = max(current_tax_priority, 3)
                            values["income_tax_expense_reported"] = wrapped_total
                            reported_tax_priority = max(reported_tax_priority, 2)
                            continuation_line = None
                    if subtotal_tokens and continuation_line is not None:
                        values["profit_after_tax_subtotal_candidate"] = parse_amount(
                            subtotal_tokens[0]
                        )
                if (
                    value is None
                    and continuation_line is not None
                ):
                    value = _current_value(
                        f"{line} {continuation_line.strip()}",
                        match,
                        drop_leading_note=note_column,
                        value_column_count=value_column_count,
                    )
                if value is not None:
                    if metric in {
                        "finance_costs",
                        "finance_income",
                        "depreciation_amortization",
                    }:
                        value = abs(value)
                    if metric == "exceptional_items" and re.search(
                        r"\bexceptional\s+items?\s*\(\s*gains?\s*\)", line, re.I
                    ):
                        value = -abs(value)
                    if (
                        metric == "current_tax"
                        and tax_deduction_presentation
                        and value < 0
                        and not re.search(r"\b(?:credit|refund|reversal)\b", line, re.I)
                    ):
                        value = -value
                    if (
                        metric == "mat_credit"
                        and value > 0
                        and re.search(r"\bless\s*:", line, re.I)
                    ):
                        value = -value
                    if (
                        metric == "mat_credit"
                        and value > 0
                        and not re.search(
                            r"\bless\s*:|\b(?:reversal|reversed|written\s+off|withdrawal|utili[sz]ation)\b",
                            line,
                            re.I,
                        )
                        and not re.match(r"^\s*\(\s*[a-z0-9]+\s*\)", line, re.I)
                        and not (
                            values.get("current_tax") is not None
                            and values["current_tax"] < 0
                        )
                    ):
                        value = -value
                    if metric == "tax_reserve_transfer" and re.search(
                        r"\b(?:withdrawn|written\s+back)\b",
                        line,
                        re.I,
                    ):
                        value = -abs(value)
                    if metric == "revenue_from_operations":
                        low = line.lower()
                        if re.search(r"\b(?:revenue|income)\s+from\s+operations?\b", low):
                            priority = (
                                5
                                if "(net)" in low
                                or re.search(r"\bnet\s+revenue\s+from\s+operations?\b", low)
                                else 4
                            )
                        elif re.search(r"\b(?:net\s+sales|net\s+turnover)\b", low):
                            priority = 3
                        else:
                            priority = 2
                        if priority > revenue_priority:
                            values[metric] = value
                            values["revenue_from_operations_match_priority"] = priority
                            if (
                                "(gross)" in low
                                or (
                                    line_number + 1 < len(lines)
                                    and re.search(
                                        r"\bless\s*:?\s*excise\s+dut",
                                        lines[line_number + 1],
                                        re.I,
                                    )
                                )
                            ):
                                values["revenue_from_operations_is_gross"] = True
                            revenue_priority = priority
                    elif metric == "current_tax":
                        low = line.lower()
                        if re.search(r"\bprovision\s+for\s+income\s+tax\b", low):
                            priority = 5
                        elif re.search(r"\bcurrent\s+year\s+tax\b", low):
                            priority = 4
                        elif re.search(
                            r"^\s*(?:\(\s*[a-z0-9]+\s*\)|[IVX]+[.)]?)?\s*"
                            r"(?:in\s+respect\s+of\s+)?current\s+year\b",
                            low,
                            re.I,
                        ):
                            priority = 4
                        elif re.search(r"\bcurrent\s+tax\b|\bof\s+current\s+year\b", low):
                            priority = 3
                        elif re.search(r"\bprovision\s+for\s+tax(?:es|ation)?\b", low):
                            priority = 2
                        else:
                            priority = 1
                        if priority > current_tax_priority:
                            values[metric] = value
                            trailing_current_tax_tokens = _tokens(line[match.end():])
                            if (
                                value_column_count >= 2
                                and len(trailing_current_tax_tokens) == 1
                                and trailing_current_tax_tokens[0]
                                not in {"-", "–", "—"}
                            ):
                                # PDF text extraction collapses an empty current-period
                                # cell on some two-period tax rows, leaving only the
                                # comparative amount.  Keep the displayed value, but
                                # allow the independent PBT/net identity to omit it as a
                                # candidate later; it is never discarded on layout alone.
                                values[
                                    "current_tax_single_value_two_column_candidate"
                                ] = True
                            else:
                                values.pop(
                                    "current_tax_single_value_two_column_candidate",
                                    None,
                                )
                            current_tax_priority = priority
                    elif metric == "deferred_tax":
                        low = line.lower()
                        post_pbt_tax_window = bool(
                            profit_before_tax_line is not None
                            and profit_before_tax_line < line_number
                            and line_number - profit_before_tax_line <= 20
                            and tax_section_started
                            and not tax_section_closed
                        )
                        named_deferred_component = re.search(
                            r"\bdeferred\s+tax\s+"
                            r"(?P<kind>assets?|liabilit(?:y|ies))\b",
                            low,
                            re.I,
                        )
                        if post_pbt_tax_window and named_deferred_component:
                            component_kind = (
                                "asset"
                                if named_deferred_component.group("kind").startswith(
                                    "asset"
                                )
                                else "liability"
                            )
                            deferred_tax_named_components[component_kind] = value
                            values[metric] = sum(
                                deferred_tax_named_components.values()
                            )
                            values["deferred_tax_source"] = (
                                "summed_named_primary_tax_components"
                            )
                            deferred_tax_priority = 6
                        elif post_pbt_tax_window and re.search(
                            r"\bcurrent\s+year\b", low
                        ):
                            priority = 5
                        elif post_pbt_tax_window and re.search(
                            r"\b(?:prior|earlier|previous)\s+(?:period|year)\b|"
                            r"\breversal\s+due\b",
                            low,
                        ):
                            priority = 3
                        elif post_pbt_tax_window:
                            priority = 4
                        elif re.search(r"\bcurrent\s+year\b", low):
                            priority = 3
                        elif re.search(
                            r"\b(?:prior|earlier|previous)\s+(?:period|year)\b|"
                            r"\breversal\s+due\b",
                            low,
                        ):
                            priority = 1
                        else:
                            priority = 2
                        if (
                            not (post_pbt_tax_window and named_deferred_component)
                            and priority > deferred_tax_priority
                        ):
                            values[metric] = value
                            deferred_tax_priority = priority
                    elif metric == "income_tax_expense_reported":
                        priority = 2 if re.search(r"\btotal\s+tax\s+expenses?\b", line, re.I) else 1
                        if priority >= reported_tax_priority:
                            values[metric] = value
                            reported_tax_priority = priority
                    elif metric in {
                        "earlier_year_tax",
                        "tax_provision_adjustment",
                        "deferred_tax_earlier_adjustment",
                        "tax_reserve_transfer",
                    }:
                        values[metric] = values.get(metric, 0.0) + value
                    elif metric == "profit_before_tax":
                        if not tax_section_started or metric not in values:
                            values[metric] = value
                            profit_before_tax_line = line_number
                            if associate_line is not None and associate_line < line_number:
                                values["profit_before_tax_position"] = "after_associate_share"
                    else:
                        if metric == "exceptional_items":
                            if re.match(
                                r"^\s*(?:(?:[IVX]+|\d+)[.)]?\s+)?total\s+"
                                r"(?:exceptional|extra[-\s]*ordinary)\s+items?\b",
                                line,
                                re.I,
                            ):
                                values[metric] = value
                            else:
                                values[metric] = values.get(metric, 0.0) + value
                        elif metric == "associate_jv_share":
                            values[metric] = values.get(metric, 0.0) + value
                        else:
                            values[metric] = value
                        if metric in {
                            "earnings_before_interest_tax_da",
                            "other_income",
                            "other_gain_loss",
                            "finance_income",
                            "finance_costs",
                            "depreciation_amortization",
                            "total_expenses",
                            "associate_jv_share",
                            "associate_impairment",
                            "prior_period_items",
                            "profit_before_exceptional_items_and_tax",
                        }:
                            values[f"_{metric}_line"] = line_number
                        if metric == "profit_before_tax_from_continuing_operations":
                            profit_before_tax_line = line_number
                            if associate_line is not None and associate_line < line_number:
                                values["profit_before_tax_position"] = (
                                    "after_associate_share"
                                )
                        elif metric == "associate_jv_share":
                            associate_line = line_number
                            if values.get("profit_from_continuing_operations") is not None:
                                values["associate_jv_share_position"] = (
                                    "after_continuing_operations"
                                )
                        elif (
                            metric
                            in {
                                "profit_after_tax_before_minority_interest",
                                "profit_from_continuing_operations",
                                "profit_after_tax",
                                "profit_for_period",
                            }
                            and associate_line is not None
                            and associate_line < line_number
                            and values.get("associate_jv_share_position") is None
                        ):
                            values["associate_jv_share_position"] = (
                                f"before_{metric}"
                            )
                        elif (
                            metric == "profit_before_exceptional_items_and_tax"
                            and associate_line is not None
                            and associate_line < line_number
                        ):
                            values[
                                "profit_before_exceptional_items_and_tax_position"
                            ] = "after_associate_share"
                    if metric in {
                        "profit_before_regulatory_deferral",
                        "profit_after_tax_before_minority_interest",
                        "profit_from_continuing_operations",
                        "profit_after_tax",
                        "profit_for_period",
                    }:
                        tax_section_closed = True
                break
    if tax_section_started:
        values["tax_section_present"] = True
    return values


def _extract_balance_sheet(lines: list[str]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    liability_context: Optional[str] = None
    reported_total: Optional[float] = None
    saw_financial_liabilities = False
    saw_non_current_liabilities = False
    saw_current_liabilities = False
    saw_debt_label = False
    in_assets = False
    in_equity = False
    in_legacy_loan_funds = False
    legacy_loan_components: list[float] = []
    legacy_loan_labels: set[str] = set()
    cash_source_priority: int = 0
    in_current_assets: bool = False
    for line_number, line in enumerate(lines):
        line = _normalize_statement_ocr_line(line)
        line = re.sub(r"\bBorr(?:wo|w)ings\b", "Borrowings", line, flags=re.I)
        if re.search(
            r"\b(?:cash\s+flow\s+statement|statement\s+of\s+cash\s+flows|"
            r"cash\s+flows?\s+from\s+operating\s+activities)\b",
            line,
            re.I,
        ):
            break
        low = " ".join(re.sub(r"[-‐‑–—]", " ", line.lower()).split())
        if re.fullmatch(r"(?:(?:[ivx]+|\d+)\s*[.)]?\s*)?equity(?:\s*(?:&|and)\s*liabilities)?", low):
            in_equity = True
        elif re.search(r"^(?:(?:[ivx]+|\d+)\s*[.)]?\s*)?(?:non current liabilities|current liabilities|total equity)\b", low) or re.search(r"statement of (?:profit|income)", low):
            in_equity = False
        if in_equity and "non_controlling_interest" not in result:
            nci_match = re.search(r"^\s*(?:(?:[a-z]|\d+)\s*[.)]?\s*)?(?:non[- ]?controlling\s+interests?|minority\s+interests?)\b", line, re.I)
            if nci_match:
                value = _current_value(line, nci_match, drop_leading_note=True)
                if value is not None:
                    result["non_controlling_interest"] = value
                    result["non_controlling_interest_source"] = {
                        "line": line, "line_index": line_number,
                        "statement_section": "equity",
                    }
        next_low = (
            " ".join(
                re.sub(r"[-‐‑–—]", " ", lines[line_number + 1].lower()).split()
            )
            if line_number + 1 < len(lines)
            else ""
        )
        liability_section_text = low
        if (
            re.search(r"\b(?:non\s+current|current)\s*$", low)
            and re.match(r"^(?:(?:[a-z]|[ivxlcdm]+)\s*[.)]?\s+)?liabil", next_low)
        ):
            liability_section_text = f"{low} {next_low}"
        elif (
            re.fullmatch(
                r"current\s+statements?\s+(?:standalone|consolidated)", low
            )
            and re.match(
                r"^(?:(?:[a-z]|[ivxlcdm]+)\s*[.)]?\s+)?liabil",
                next_low,
            )
        ):
            # A persistent contents overlay can split the visible ``Current
            # Liabilities`` heading into ``Current stAteMents - <scope>`` and
            # ``Liabilities``.  Recover the section switch before reading the
            # following borrowing row.
            liability_section_text = "current liabilities"
        section_prefix = (
            r"(?:(?:\[\s*)?(?:[a-z]|\d+|[ivxlcdm]+)(?:\s*\])?"
            r"\s*[.)]?\s*)?"
        )
        legacy_funds_heading = re.match(
            rf"^{section_prefix}(?:loan\s+funds?|sources?\s+of\s+funds)\b",
            low,
        )
        if legacy_funds_heading:
            in_legacy_loan_funds = True
            if re.match(
                rf"^{section_prefix}loan\s+funds?\b",
                low,
            ):
                loan_funds_label = re.search(r"\bloan\s+funds?\b", line, re.I)
                if (
                    loan_funds_label
                    and not re.search(
                        r"[A-Za-z]", line[loan_funds_label.end():]
                    )
                ):
                    value = _current_value(
                        line,
                        loan_funds_label,
                        drop_leading_note=True,
                    )
                    if value is not None:
                        reported_total = value
        elif re.match(
            rf"^{section_prefix}(?:deferred\s+tax|applications?\s+of\s+funds?|assets?)\b",
            low,
        ):
            in_legacy_loan_funds = False
        if in_legacy_loan_funds:
            loan_matches = list(
                re.finditer(
                    r"\b(?P<label>un[\s-]?secured|secured)(?:\s+loans?)?\b",
                    line,
                    re.I,
                )
            )
            for index, loan in enumerate(loan_matches):
                segment_end = (
                    loan_matches[index + 1].start()
                    if index + 1 < len(loan_matches)
                    else len(line)
                )
                value = _current_value(
                    line[:segment_end], loan, drop_leading_note=True
                )
                if value is not None:
                    legacy_loan_components.append(value)
                    label = loan.group("label").lower().replace("-", "").replace(" ", "")
                    legacy_loan_labels.add(
                        "unsecured" if label.startswith("un") else "secured"
                    )
            subtotal = re.search(r"\bsub[ -]?total\b", line, re.I)
            if subtotal:
                value = _current_value(line, subtotal, drop_leading_note=True)
                if value is not None:
                    reported_total = value
        if re.match(
            r"^(?:\(?[a-z0-9ivxlcdm]+\)?\s*[|.)]?\s*)?"
            r"(?:(?:non\s+current|current)\s+)?assets\b",
            low,
        ):
            in_assets = True
        elif re.match(
            r"^(?:\(?[a-z0-9ivxlcdm]+\)?\s*[|.)]?\s*)?"
            r"(?:equity|shareholders?'?\s+funds?|(?:non\s+current|current)?\s*"
            r"liabilities)\b",
            low,
        ):
            in_assets = False
            in_current_assets = False

        if re.search(r"\bnon\s+current\s+assets?\b", low):
            in_current_assets = False
        elif re.search(r"\bcurrent\s+assets?\b", low) and not re.search(r"\bnon\s+current\b", low):
            in_current_assets = True
        direct_context: Optional[str] = None
        if re.search(r"\b(?:non[ -]?current|long[ -]?term)\s+borrowings?\b", line, re.I):
            direct_context = "non_current"
        elif re.search(r"\b(?:current|short[ -]?term)\s+borrowings?\b", line, re.I):
            direct_context = "current"
        if re.search(r"\bnon\s+current\s+liabil", liability_section_text):
            liability_context = "non_current"
            saw_non_current_liabilities = True
        elif re.search(r"\bcurrent\s+liabil", liability_section_text):
            liability_context = "current"
            saw_current_liabilities = True
        if liability_context and not in_assets:
            for family, pattern in (
                ("trade_payables", r"^\s*(?:\(?[a-z0-9]+\)?[.)]?\s*)?trade\s+payables\b"),
                ("contract_liabilities", r"^\s*(?:\(?[a-z0-9]+\)?[.)]?\s*)?contract\s+liabilities\b"),
            ):
                matched = re.search(pattern, line, re.I)
                if matched:
                    value = _current_value(line, matched, drop_leading_note=True)
                    split_sources = []
                    if family == "trade_payables" and value is None:
                        parts = {}
                        # Only the adjacent, explicitly disjoint statutory
                        # creditor classes establish a payable subtotal.
                        for offset, child in enumerate(lines[line_number + 1:line_number + 7], 1):
                            child = _normalize_statement_ocr_line(child)
                            label = re.search(
                                r"total\s+outstanding\s+dues\s+of\s+"
                                r"(?P<kind>creditors\s+other\s+than\s+micro|micro)\b",
                                child, re.I)
                            if label is None:
                                if not child.strip() or re.fullmatch(
                                    r"\s*and\s+small\s+enterprises[. ]*", child, re.I):
                                    continue
                                break
                            kind = "other" if label.group("kind").lower().startswith("creditors") else "msme"
                            amount = _current_value(child, label, drop_leading_note=False)
                            if kind in parts or amount is None or amount < 0:
                                break
                            parts[kind] = amount
                            split_sources.append({"line_index": line_number + offset,
                                                  "line": child, "class": kind, "value": amount})
                            if set(parts) == {"msme", "other"}:
                                value = sum(parts.values())
                                break
                    if value is not None:
                        field = (f"trade_payables_{liability_context}" if family == "trade_payables"
                                 else f"{liability_context}_contract_liabilities")
                        result[field] = value
                        result[field + "_source"] = {"line_index": line_number, "line": line,
                                                     "maturity_section": liability_context}
                        if split_sources:
                            result[field + "_source"].update(
                                aggregation="sum_disjoint_msme_other_creditors",
                                components=split_sources)

        if liability_context and not in_assets:
            # Preserve separately presented claims. Whether they are additive
            # to financing debt requires note-level scope/overlap review.
            for family, pattern in (
                ("acceptances_factoring_liabilities", r"\bacceptances\s*/\s*factoring\s+liabilities\b"),
                ("investor_in_subsidiary_obligation", r"\bobligation\s+towards?\s+investor\s+in\s+subsidiary\b"),
            ):
                matched = re.search(pattern, line, re.I)
                if matched:
                    value = _current_value(line, matched, drop_leading_note=True)
                    if value is not None:
                        field = f"{liability_context}_{family}"
                        result[field] = value
                        result[field + "_source"] = {
                            "line_index": line_number, "line": line,
                            "maturity_section": liability_context,
                            "classification": "separate_claim_requires_note_reconciliation",
                        }

        if re.search(r"\bfinancial\s+liabil", low):
            saw_financial_liabilities = True
        if re.search(
            r"\b(?:borrowings?|loan\s+funds?|(?:un[\s-]?secured|secured)\s+loans?)\b",
            line,
            re.I,
        ):
            saw_debt_label = True

        if _EXPLICIT_NO_BORROWING.search(line):
            continue

        match = re.search(r"\btotal\s+borrowings?\b", line, re.I)
        if match:
            reported_total = _current_value(
                line, match, drop_leading_note=True
            )
            continue
        match = re.search(r"\bborrowings?\b", line, re.I)
        if match:
            value = _current_value(line, match, drop_leading_note=True)
            bucket = direct_context or liability_context
            if value is not None and bucket:
                result[f"{bucket}_borrowings"] = value
            elif value is not None and saw_financial_liabilities:
                # NBFC-style balance sheets often report one unclassified
                # Borrowings row inside a complete Financial Liabilities
                # section.  It is the reported debt total, not an unknown
                # current/non-current component.
                reported_total = value
        elif not in_legacy_loan_funds and not in_assets:
            loan = re.search(
                r"^\s*(?:\(?[a-z0-9ivxlcdm]+\)?\s*[.)]?\s*)?"
                r"(?P<label>un[\s-]?secured|secured)\s+loans?\b",
                line,
                re.I,
            )
            if loan:
                value = _current_value(line, loan, drop_leading_note=True)
                if value is not None:
                    if liability_context:
                        field = f"{liability_context}_borrowings"
                        result[field] = result.get(field, 0.0) + value
                    else:
                        legacy_loan_components.append(value)
                        label = loan.group("label").lower().replace("-", "").replace(" ", "")
                        legacy_loan_labels.add(
                            "unsecured" if label.startswith("un") else "secured"
                        )

        lease_context: Optional[str] = None
        if re.search(r"\b(?:non[ -]?current|long[ -]?term)\s+lease\s+liabil", line, re.I):
            lease_context = "non_current"
        elif re.search(r"\b(?:current|short[ -]?term)\s+lease\s+liabil", line, re.I):
            lease_context = "current"
        lease = re.search(r"\blease\s+liabilit(?:y|ies)\b", line, re.I)
        if lease:
            value = _current_value(line, lease, drop_leading_note=True)
            bucket = lease_context or liability_context
            if value is not None and bucket:
                result[f"{bucket}_lease_liabilities"] = value

        # Other bank balances
        if "other_bank_balances" not in result:
            other_bank = re.search(
                r"\b(?:"
                r"bank\s+balances?\s+other\s+than\s+(?:cash\s*(?:and|&)\s*cash\s*equival[ea]nts?|cash\s*(?:and|&)\s*equival[ea]nts?|\(?[a-z0-9ivxlcdm]+\)?\s*above|the\s+above|above|cash)"
                r"|bank\s+balances?\s+other\s+than\b"
                r"|other\s+bank\s+balances?"
                r"|other\s+balances?\s+with\s+banks?"
                r")\b",
                line,
                re.I,
            )
            if other_bank:
                val = _current_value(line, other_bank, drop_leading_note=True)
                if val is not None:
                    result["other_bank_balances"] = val

        # Cash and cash equivalents: Tier 1 (Priority 3)
        cash_match = re.search(
            r"\b(?:"
            r"cash\s*(?:and|&)\s*(?:cash\s*)?equival[ea]nts?"
            r"|cash\s*(?:and|&)\s*cash\s*equvalents?"
            r")\b",
            line,
            re.I,
        )
        if cash_match and not re.search(r"\bother\s+than\b", line[:cash_match.start()], re.I):
            if cash_source_priority < 3:
                val = _current_value(line, cash_match, drop_leading_note=True)
                if val is not None:
                    result["cash_and_equivalents"] = val
                    cash_source_priority = 3

        # Cash and bank balances: Tier 2 (Priority 2)
        if cash_source_priority < 2:
            cash_bank_match = re.search(
                r"\bcash\s*(?:and|&)\s*bank\s*balances?\b",
                line,
                re.I,
            )
            if cash_bank_match and not re.search(r"\bother\s+than\b", line[:cash_bank_match.start()], re.I):
                val = _current_value(line, cash_bank_match, drop_leading_note=True)
                if val is not None:
                    result["cash_and_equivalents"] = val
                    cash_source_priority = 2

        # Balances with banks: Tier 3 (Priority 1)
        if cash_source_priority < 1 and in_assets:
            bwb_match = re.search(
                r"\bbalances?\s+with\s+banks?\b",
                line,
                re.I,
            )
            if bwb_match and not re.search(r"\bother\s+(?:than|balances?\s+with)\b", line[:bwb_match.start()], re.I):
                val = _current_value(line, bwb_match, drop_leading_note=True)
                if val is not None:
                    result["cash_and_equivalents"] = val
                    cash_source_priority = 1

        # Current investments
        if "current_investments" not in result:
            ci_match = re.search(
                r"\b(?:"
                r"(?<!non[- ])(?<!non\s)current\s+investments?"
                r"|investments?\s+in\s+mutual\s+funds?"
                r")\b",
                line,
                re.I,
            )
            if ci_match and not re.search(r"\bnon\s*[- ]\s*current\b", line[:ci_match.start()], re.I):
                val = _current_value(line, ci_match, drop_leading_note=True)
                if val is not None:
                    result["current_investments"] = val
            elif in_current_assets:
                ci_asset_match = re.search(
                    r"^\s*(?:\(?[a-z0-9ivxlcdm]+\)?\s*[.)]?\s*)?"
                    r"(?<!non[- ])(?<!non\s)investments?\b",
                    line,
                    re.I,
                )
                if ci_asset_match and not re.search(r"\bnon\s*[- ]\s*current\b", line[:ci_asset_match.start()], re.I):
                    val = _current_value(line, ci_asset_match, drop_leading_note=True)
                    if val is not None:
                        result["current_investments"] = val

        for metric, pattern in (
            ("total_assets", r"\btotal\s+assets\b"),
            ("total_equity", r"\btotal\s+(?:[-–—]\s*)?equity\b(?!\s*(?:and|&)\s+liabilit)"),
        ):
            if metric in result:
                continue
            item = re.search(pattern, line, re.I)
            if item:
                value = _current_value(line, item, drop_leading_note=True)
                if value is not None:
                    result[metric] = value

        if (
            in_assets
            and result.get("total_assets") is None
            and _numeric_row_continuation(line)
        ):
            next_nonempty = ""
            for offset in range(1, 5):
                if line_number + offset >= len(lines):
                    break
                candidate = _normalize_statement_ocr_line(
                    lines[line_number + offset]
                )
                if candidate.strip():
                    next_nonempty = " ".join(candidate.split())
                    break
            if re.match(
                r"^\s*(?:\(?[a-z0-9ivxlcdm]+\)?\s*[|.)]?\s*)?"
                r"(?:equity(?:\s+share\s+capital)?|shareholders?'?\s+funds?)\b",
                next_nonempty,
                re.I,
            ):
                numeric_tokens = _tokens(line)
                if numeric_tokens:
                    result["total_assets"] = parse_amount(numeric_tokens[0])
                    result["total_assets_source"] = (
                        "unlabelled_subtotal_before_equity_section"
                    )

        if in_assets and result.get("total_assets") is None:
            generic_total = re.search(r"^\s*total\s+(?=[(\d])", line, re.I)
            if generic_total:
                value = _current_value(
                    line, generic_total, drop_leading_note=True
                )
                if value is not None:
                    result["total_assets"] = value

    legacy_total = sum(legacy_loan_components) if legacy_loan_components else None
    if (
        reported_total is not None
        and legacy_total is not None
        and legacy_loan_labels == {"secured", "unsecured"}
        and _ratio(reported_total, legacy_total) > 0.01
    ):
        # In legacy side-by-side pages the first number after ``LOAN FUNDS``
        # can belong to the adjacent profit-and-loss statement (usually total
        # income).  A complete secured + unsecured decomposition is the
        # independent debt identity and supersedes that geometrically
        # impossible total when the two disagree.
        reported_total = legacy_total
    if reported_total is None and legacy_total is not None and (
        legacy_total != 0.0
        or legacy_loan_labels == {"secured", "unsecured"}
    ):
        reported_total = legacy_total
    current = result.get("current_borrowings")
    non_current = result.get("non_current_borrowings")
    components = [value for value in (current, non_current) if value is not None]
    if reported_total is not None:
        result["total_borrowings"] = reported_total
        if (
            reported_total == 0.0
            and legacy_loan_labels == {"secured", "unsecured"}
        ):
            result["debt_reconciliation_status"] = "true_zero"
        elif components:
            residual = abs(reported_total - sum(components))
            tolerance = 0.01 * max(abs(reported_total), abs(sum(components)), 1.0)
            result["debt_reconciliation_status"] = "pass" if residual <= tolerance else "fail"
        else:
            result["debt_reconciliation_status"] = "reported_total_only"
    elif components:
        result["total_borrowings"] = sum(components)
        result["debt_reconciliation_status"] = "components_only"
    lease_components = [
        value
        for value in (
            result.get("current_lease_liabilities"),
            result.get("non_current_lease_liabilities"),
        )
        if value is not None
    ]
    if lease_components:
        result["total_lease_liabilities"] = sum(lease_components)
    if (
        result.get("total_borrowings") is None
        and not saw_debt_label
        and (
            saw_financial_liabilities
            or (saw_non_current_liabilities and saw_current_liabilities)
        )
        and result.get("total_assets") is not None
    ):
        result["total_borrowings"] = 0.0
        result["debt_reconciliation_status"] = "true_zero"
    if result.get("total_borrowings") is not None:
        result["total_interest_bearing_debt"] = result["total_borrowings"] + sum(
            lease_components
        )
    result["financial_liabilities_enumerated"] = saw_financial_liabilities
    return result


def _ratio(left: float, right: float) -> float:
    return abs(left - right) / max(abs(left), abs(right), 1.0)


def _within_one_percent(value: float) -> bool:
    """Apply the one-percent gate without rejecting a binary-float boundary."""
    return value <= 0.01 + 1e-12


def _derive(values: dict[str, Any], template: str) -> None:
    if template != "industrial":
        values["operating_profit_after_da"] = None
        values["operating_profit_null_reason"] = "wrong_template"
        values["operating_profit_closure_status"] = "not_applicable"
    else:
        reported_total = values.get("total_income")
        reported_revenue = values.get("revenue_from_operations")
        reported_other_operating = values.get("other_operating_income", 0.0)
        reported_other = values.get("other_income")
        reported_finance_income = values.get("finance_income", 0.0)
        reported_other_gain_loss = values.get("other_gain_loss", 0.0)
        if (
            reported_other_operating not in (None, 0.0)
            and None not in (reported_total, reported_revenue, reported_other)
            and _within_one_percent(
                _ratio(reported_total, reported_revenue + reported_other)
            )
            and not _within_one_percent(
                _ratio(
                    reported_total,
                    reported_revenue + reported_other_operating + reported_other,
                )
            )
        ):
            # Some legacy statements show other operating income above a
            # later ``Income from operations`` subtotal.  That subtotal is
            # already the reported revenue-from-operations value, so adding
            # the displayed component again would manufacture a negative
            # residual other-income figure.
            values["other_operating_income_included_in_revenue"] = True
            reported_other_operating = 0.0
        income_finance_income = reported_finance_income
        if (
            None not in (reported_total, reported_revenue, reported_other)
            and _ratio(
                reported_total,
                reported_revenue
                + reported_other_operating
                + reported_other
                + reported_other_gain_loss,
            )
            <= 0.01
        ):
            # Finance income printed below an EBITDA subtotal is not part of
            # the earlier reported total-income line.
            income_finance_income = 0.0
        if (
            reported_total is None
            and reported_revenue is not None
            and reported_other is not None
        ):
            reported_total = (
                reported_revenue
                + reported_other_operating
                + reported_other
                + income_finance_income
                + reported_other_gain_loss
            )
            values["total_income"] = reported_total
            values["total_income_source"] = "summed_income_components"
        if (
            reported_other is None
            and None not in (reported_total, reported_revenue)
            and _ratio(
                reported_total,
                reported_revenue
                + reported_other_operating
                + income_finance_income
                + reported_other_gain_loss,
            )
            <= 0.01
        ):
            reported_other = (
                reported_total
                - reported_revenue
                - reported_other_operating
                - income_finance_income
                - reported_other_gain_loss
            )
            values["other_income"] = reported_other
            values["other_income_source"] = (
                "derived_total_income_less_reported_revenue"
            )
        if (
            values.get("revenue_from_operations_is_gross")
            and None not in (reported_total, reported_other)
        ):
            values["revenue_from_operations"] = (
                reported_total
                - reported_other
                - reported_other_operating
                - income_finance_income
                - reported_other_gain_loss
            )
            values["revenue_from_operations_source"] = (
                "derived_total_income_less_other_income"
            )
            reported_revenue = values["revenue_from_operations"]
        if (
            None not in (reported_total, reported_revenue, reported_other)
            and values.get("revenue_from_operations_match_priority", 0) > 2
        ):
            income_scale = max(
                abs(reported_total), abs(reported_revenue), 1.0
            )
            income_gap = (
                reported_total
                - reported_revenue
                - reported_other_operating
                - reported_other
                - income_finance_income
                - reported_other_gain_loss
            )
            if abs(income_gap) / income_scale > 0.01:
                residual_other = (
                    reported_total
                    - reported_revenue
                    - reported_other_operating
                    - income_finance_income
                    - reported_other_gain_loss
                )
                if abs(residual_other) / income_scale <= 0.05:
                    values["other_income"] = residual_other
                    values["other_income_source"] = (
                        "derived_total_income_less_reported_revenue"
                    )
                elif abs(reported_other) / income_scale <= 0.05:
                    reported_total_is_only_income = (
                        reported_total != 0
                        and reported_revenue == 0
                        and reported_other_operating == 0
                        and reported_other == 0
                        and income_finance_income == 0
                        and reported_other_gain_loss == 0
                    )
                    if (
                        0 < income_gap <= income_scale * 0.5
                        or reported_total_is_only_income
                    ):
                        reported_other_operating += income_gap
                        values["other_operating_income"] = (
                            reported_other_operating
                        )
                        values["other_operating_income_source"] = (
                            "derived_reported_total_residual"
                        )
                        values.setdefault("total_income_source", "reported")
                    else:
                        values["total_income"] = (
                            reported_revenue
                            + reported_other_operating
                            + reported_other
                            + income_finance_income
                            + reported_other_gain_loss
                        )
                        values["total_income_source"] = (
                            "derived_reported_revenue_plus_other_income"
                        )
        derived_revenue = None
        if values.get("total_income") is not None and values.get("other_income") is not None:
            derived_revenue = (
                values["total_income"]
                - values["other_income"]
                - reported_other_operating
                - income_finance_income
                - reported_other_gain_loss
            )
        reported_revenue = values.get("revenue_from_operations")
        legacy_sales_conflict = (
            reported_revenue is not None
            and derived_revenue is not None
            and values.get("revenue_from_operations_match_priority", 0) <= 2
            and _ratio(reported_revenue, derived_revenue) > 0.01
        )
        if derived_revenue is not None and (
            reported_revenue is None or legacy_sales_conflict
        ):
            values["revenue_from_operations"] = derived_revenue
            values["revenue_from_operations_source"] = (
                "derived_total_income_less_other_income"
            )
        elif (
            values.get("revenue_from_operations") is not None
            and values.get("revenue_from_operations_source") is None
        ):
            values["revenue_from_operations_source"] = "reported"
        revenue = values.get("revenue_from_operations")
        expenses = values.get("total_expenses")
        if expenses is None:
            reported_expense_parts = [
                values.get(name)
                for name in (
                    "cost_of_materials",
                    "purchases_stock_in_trade",
                    "inventory_changes",
                    "employee_benefits",
                    "finance_costs",
                    "depreciation_amortization",
                    "other_expenses",
                )
                if values.get(name) is not None
            ]
            if len(reported_expense_parts) >= 2:
                expenses = sum(reported_expense_parts)
                values["total_expenses"] = expenses
                values["total_expenses_source"] = "summed_expense_components"
        finance = values.get("finance_costs")
        if finance is None and expenses is not None:
            non_finance_expense_parts = [
                values.get(name)
                for name in (
                    "cost_of_materials",
                    "purchases_stock_in_trade",
                    "inventory_changes",
                    "employee_benefits",
                    "depreciation_amortization",
                    "other_expenses",
                )
                if values.get(name) is not None
            ]
            if (
                non_finance_expense_parts
                and _ratio(sum(non_finance_expense_parts), expenses) <= 0.01
            ):
                finance = 0.0
                values["finance_costs"] = finance
                values["finance_costs_source"] = (
                    "derived_zero_from_complete_expense_reconciliation"
                )
        reported_preexceptional = values.get(
            "profit_before_exceptional_items_and_tax"
        )
        if (
            reported_preexceptional is not None
            and values.get("total_income") is not None
            and values.get("total_expenses") is not None
        ):
            total_expenses_line = values.get("_total_expenses_line")
            expense_component_lines = [
                values.get(name)
                for name in (
                    "_finance_costs_line",
                    "_depreciation_amortization_line",
                )
                if values.get(name) is not None
            ]
            total_expenses_is_complete_subtotal = bool(
                total_expenses_line is not None
                and all(
                    component_line < total_expenses_line
                    for component_line in expense_component_lines
                )
            )
            identity_preexceptional = (
                (reported_total if reported_total is not None else values["total_income"])
                - values["total_expenses"]
            )
            preexceptional_line = values.get(
                "_profit_before_exceptional_items_and_tax_line"
            )
            intervening_prior_period = False
            if total_expenses_line is not None and preexceptional_line is not None:
                for metric in ("associate_jv_share", "associate_impairment"):
                    metric_line = values.get(f"_{metric}_line")
                    if (
                        values.get(metric) is not None
                        and metric_line is not None
                        and total_expenses_line < metric_line < preexceptional_line
                    ):
                        identity_preexceptional += values[metric]
                prior_period_line = values.get("_prior_period_items_line")
                intervening_prior_period = bool(
                    prior_period_line is not None
                    and total_expenses_line
                    < prior_period_line
                    < preexceptional_line
                )
            if (
                total_expenses_is_complete_subtotal
                and not intervening_prior_period
                and _ratio(reported_preexceptional, identity_preexceptional) > 0.01
            ):
                # This subtotal has an exact statement identity.  OCR often
                # damages its leading digit while leaving both independently
                # reported totals intact, so prefer the auditable identity
                # whenever the displayed subtotal does not reconcile.
                values[
                    "profit_before_exceptional_items_and_tax_reported"
                ] = reported_preexceptional
                values["profit_before_exceptional_items_and_tax"] = (
                    identity_preexceptional
                )
                values["profit_before_exceptional_items_and_tax_source"] = (
                    "total_income_less_total_expenses_identity"
                )
                displayed_pbt = values.get("profit_before_tax")
                if (
                    displayed_pbt is not None
                    and displayed_pbt * identity_preexceptional < 0
                    and _ratio(
                        abs(displayed_pbt), abs(identity_preexceptional)
                    )
                    <= 0.01
                ):
                    values["profit_before_tax"] = identity_preexceptional
                    values["profit_before_tax_source"] = (
                        "sign_recovered_from_total_income_less_total_expenses"
                    )
        finance_income = values.get("finance_income", 0.0)
        pbt = values.get("profit_before_tax")
        if (
            pbt is None
            and values.get("profit_before_tax_from_continuing_operations")
            is not None
        ):
            pbt = values["profit_before_tax_from_continuing_operations"]
            values["profit_before_tax"] = pbt
            values["profit_before_tax_source"] = (
                "reported_continuing_operations_profit_before_tax"
            )
        pre_exceptional = values.get("profit_before_exceptional_items_and_tax")
        reported_exceptional = values.get("exceptional_items")
        if None not in (pbt, values.get("total_income"), values.get("total_expenses")):
            expected_pre_tax = values["total_income"] - values["total_expenses"]
            same_preexceptional_subtotal = (
                pre_exceptional is None
                or _ratio(pre_exceptional, pbt) <= 0.01
            )
            no_reported_exceptional_effect = reported_exceptional in (None, 0.0)
            if (
                pbt * expected_pre_tax < 0
                and _ratio(abs(pbt), abs(expected_pre_tax)) <= 0.01
                and same_preexceptional_subtotal
                and no_reported_exceptional_effect
            ):
                pbt = -pbt
                values["profit_before_tax"] = pbt
                values["profit_before_tax_source"] = (
                    "sign_recovered_from_total_income_less_total_expenses"
                )
                if pre_exceptional is not None:
                    pre_exceptional = -pre_exceptional
                    values["profit_before_exceptional_items_and_tax"] = (
                        pre_exceptional
                    )
        if (
            pbt is None
            and pre_exceptional is not None
            and any(
                values.get(name) is not None
                for name in (
                    "income_tax_expense_reported",
                    "current_tax",
                    "current_tax_net",
                    "deferred_tax",
                    "earlier_year_tax",
                    "mat_credit",
                    "minimum_alternative_tax",
                )
            )
        ):
            if (
                reported_exceptional is not None
                and values.get("exceptional_items_presentation") == "less"
            ):
                pbt = pre_exceptional - reported_exceptional
                pbt_source = "derived_preexceptional_less_reported_exceptional"
            else:
                pbt = pre_exceptional
                pbt_source = "direct_preexceptional_subtotal_before_tax_rows"
            values["profit_before_tax"] = pbt
            values["profit_before_tax_source"] = pbt_source
        pbt_basis = "after_associate_share"
        if pbt is None and values.get("profit_before_associate_and_tax") is not None:
            pbt = values["profit_before_associate_and_tax"]
            values["profit_before_tax"] = pbt
            pbt_basis = "before_associate_share"
        elif (
            values.get("associate_jv_share") is not None
            and values.get("profit_before_tax_position") != "after_associate_share"
        ):
            pbt_basis = "before_associate_share"
        values["profit_before_tax_basis"] = pbt_basis
        other = values.get("other_income")
        other_gain_loss = values.get("other_gain_loss", 0.0)
        associate_impairment = values.get("associate_impairment", 0.0)
        exceptional = values.get("exceptional_items", 0.0)
        prior_period = values.get("prior_period_items", 0.0)
        reported_ebitda = values.get("earnings_before_interest_tax_da")
        depreciation = values.get("depreciation_amortization")
        if (
            finance is None
            and None not in (reported_ebitda, depreciation, pbt)
            and _ratio(reported_ebitda - depreciation, pbt) <= 0.01
        ):
            finance = 0.0
            values["finance_costs"] = finance
            values["finance_costs_source"] = (
                "derived_zero_from_ebitda_less_da_reconciliation"
            )
        if None not in (pre_exceptional, pbt):
            subtotal_scale_ratio = max(abs(pre_exceptional), abs(pbt), 1.0) / max(
                min(abs(pre_exceptional), abs(pbt)), 1.0
            )
            if subtotal_scale_ratio > 10:
                values["profit_before_exceptional_items_and_tax_rejected"] = (
                    "order_of_magnitude_inconsistent_with_final_pbt_but_retained_for_bridge"
                )
        associate = (
            values.get("associate_jv_share", 0.0)
            if values.get("profit_before_tax_position") == "after_associate_share"
            else 0.0
        )
        if None not in (reported_ebitda, depreciation):
            ebitda_line = values.get("_earnings_before_interest_tax_da_line")
            included_non_operating_income = 0.0
            for metric in ("other_income", "other_gain_loss", "finance_income"):
                metric_line = values.get(f"_{metric}_line")
                if (
                    values.get(metric) is not None
                    and ebitda_line is not None
                    and metric_line is not None
                    and metric_line < ebitda_line
                ):
                    included_non_operating_income += values[metric]
            values["operating_profit_after_da"] = (
                reported_ebitda - depreciation - included_non_operating_income
            )
            values["operating_profit_component_source"] = (
                "reported_ebitda_less_da_and_included_non_operating_income"
            )
        elif None not in (values.get("total_income"), other, expenses, finance):
            operating_income = (
                values["total_income"]
                - other
                - other_gain_loss
                - finance_income
            )
            values["operating_profit_after_da"] = operating_income - (
                expenses - finance
            )
            values["operating_profit_component_source"] = (
                "total_income_less_non_operating_income_and_operating_expenses"
            )
        elif None not in (revenue, expenses, finance):
            values["operating_profit_after_da"] = revenue - (expenses - finance)
        else:
            values["operating_profit_after_da"] = None
        bridge_candidates: list[tuple[str, float]] = []
        if None not in (pre_exceptional, other, finance):
            preexceptional_impairment = (
                associate_impairment
                if (
                    values.get("_associate_impairment_line") is not None
                    and values.get(
                        "_profit_before_exceptional_items_and_tax_line"
                    )
                    is not None
                    and values["_associate_impairment_line"]
                    < values["_profit_before_exceptional_items_and_tax_line"]
                )
                else 0.0
            )
            pre_exceptional_associate = (
                values.get("associate_jv_share", 0.0)
                if values.get("profit_before_exceptional_items_and_tax_position")
                == "after_associate_share"
                else 0.0
            )
            bridge_candidates.append(
                (
                    "pre_exceptional_subtotal",
                    pre_exceptional
                    - other
                    - finance_income
                    - other_gain_loss
                    - preexceptional_impairment
                    + finance
                    - pre_exceptional_associate,
                )
            )
        if None not in (pbt, other, finance):
            post_pbt_base = (
                pbt
                - other
                - finance_income
                + finance
                - associate
                - other_gain_loss
                - associate_impairment
            )
            bridge_candidates.append(
                (
                    "pbt_without_adjustments",
                    post_pbt_base,
                )
            )
            bridge_candidates.append(
                (
                    "post_exceptional_pbt",
                    post_pbt_base + exceptional + prior_period,
                )
            )
            if exceptional:
                bridge_candidates.append(
                    (
                        "post_exceptional_pbt_inverse_exceptional_sign",
                        post_pbt_base - exceptional + prior_period,
                    )
                )
            if prior_period:
                bridge_candidates.extend(
                    (
                        (
                            "post_exceptional_pbt_inverse_prior_period_sign",
                            post_pbt_base + exceptional - prior_period,
                        ),
                        (
                            "post_exceptional_pbt_inverse_all_adjustment_signs",
                            post_pbt_base - exceptional - prior_period,
                        ),
                    )
                )
        component_candidates: list[tuple[str, float]] = []
        selected_component = values.get("operating_profit_after_da")
        if selected_component is not None:
            component_candidates.append(
                (
                    values.get("operating_profit_component_source")
                    or "selected_operating_component",
                    selected_component,
                )
            )
        if None not in (values.get("total_income"), other, expenses, finance):
            statement_component = (
                values["total_income"]
                - other
                - other_gain_loss
                - finance_income
                - (expenses - finance)
            )
            if not any(
                _ratio(candidate, statement_component) <= 1e-12
                for _, candidate in component_candidates
            ):
                component_candidates.append(
                    (
                        "total_income_less_non_operating_income_and_operating_expenses",
                        statement_component,
                    )
                )
        elif None not in (revenue, expenses, finance):
            revenue_component = revenue - (expenses - finance)
            if not any(
                _ratio(candidate, revenue_component) <= 1e-12
                for _, candidate in component_candidates
            ):
                component_candidates.append(
                    ("revenue_less_operating_expenses", revenue_component)
                )
        if bridge_candidates and component_candidates:
            component_source, component_value, bridge_basis, bridge_value = min(
                (
                    (component_source, component_value, bridge_basis, bridge_value)
                    for component_source, component_value in component_candidates
                    for bridge_basis, bridge_value in bridge_candidates
                ),
                key=lambda candidate: _ratio(candidate[1], candidate[3]),
            )
            values["operating_profit_after_da"] = component_value
            values["operating_profit_component_source"] = component_source
            values["operating_profit_pbt_bridge"] = bridge_value
            values["operating_profit_bridge_basis"] = bridge_basis
        elif bridge_candidates:
            bridge_basis, bridge_value = bridge_candidates[0]
            values["operating_profit_pbt_bridge"] = bridge_value
            values["operating_profit_bridge_basis"] = bridge_basis
        else:
            values["operating_profit_pbt_bridge"] = None
        component = values.get("operating_profit_after_da")
        bridge = values.get("operating_profit_pbt_bridge")
        if component is not None and bridge is not None:
            closure = _ratio(component, bridge)
            values["operating_profit_closure_ratio"] = closure
            if _within_one_percent(closure):
                values["operating_profit_closure_status"] = "pass"
                values["operating_profit_null_reason"] = None
            elif values.get("total_expenses_source") == "summed_expense_components":
                values["operating_profit_closure_status"] = "insufficient_components"
                values["operating_profit_null_reason"] = "incomplete_expense_component_sum"
            else:
                values["operating_profit_closure_status"] = "fail"
                values["operating_profit_null_reason"] = None
        else:
            values["operating_profit_closure_status"] = "insufficient_components"
            values["operating_profit_null_reason"] = "not_collected"

        for key in [key for key in values if key.startswith("_") and key.endswith("_line")]:
            values.pop(key, None)

    if (
        values.get("profit_before_tax") is not None
        and values.get("profit_before_tax_basis") is None
    ):
        values["profit_before_tax_basis"] = (
            "before_associate_share"
            if values.get("associate_jv_share") is not None
            and values.get("profit_before_tax_position") != "after_associate_share"
            else "after_associate_share"
        )

    reported_tax = values.get("income_tax_expense_reported")
    current_tax = values.get("current_tax")
    mat_credit = values.get("mat_credit")
    if (
        current_tax is not None
        and mat_credit is not None
        and current_tax < 0
        and mat_credit < 0
        and _ratio(abs(current_tax), abs(mat_credit)) <= 0.01
    ):
        values["mat_credit"] = abs(mat_credit)
        values["mat_credit_sign_source"] = (
            "countervailing_credit_matching_negative_current_tax"
        )
    tax_component_names = (
        (
            "current_tax_net",
            "deferred_tax",
            "deferred_tax_earlier_adjustment",
            "earlier_year_tax",
            "tax_provision_adjustment",
            "tax_reserve_transfer",
        )
        if values.get("current_tax_net") is not None
        else (
            "current_tax",
            "deferred_tax",
            "deferred_tax_earlier_adjustment",
            "earlier_year_tax",
            "tax_provision_adjustment",
            "mat_credit",
            "minimum_alternative_tax",
            "tax_reserve_transfer",
        )
    )
    tax_parts = [
        values.get(name)
        for name in tax_component_names
        if values.get(name) is not None
    ]
    component_tax = sum(tax_parts) if tax_parts else None
    if reported_tax is None or (
        reported_tax == 0.0 and component_tax not in (None, 0.0)
    ):
        reported_tax = component_tax
        values["income_tax_expense_source"] = (
            "summed_tax_components" if component_tax is not None else None
        )
    else:
        values["income_tax_expense_source"] = "reported_tax_expense"
    values["income_tax_expense"] = reported_tax
    pbt = values.get("profit_before_tax")
    if pbt is not None and reported_tax is not None:
        for net_metric in (
            "profit_before_regulatory_deferral",
            "profit_after_tax_before_minority_interest",
            "profit_from_continuing_operations",
            "profit_after_tax",
            "profit_for_period",
        ):
            displayed_net = values.get(net_metric)
            if displayed_net is None or displayed_net * pbt >= 0:
                continue
            displayed_ratio = min(
                _ratio(pbt - reported_tax, displayed_net),
                _ratio(pbt + reported_tax, displayed_net),
            )
            flipped_ratio = min(
                _ratio(pbt - reported_tax, -displayed_net),
                _ratio(pbt + reported_tax, -displayed_net),
            )
            if flipped_ratio <= 0.01 and flipped_ratio < displayed_ratio:
                values[net_metric] = -displayed_net
                values[f"{net_metric}_source"] = (
                    "sign_recovered_with_pre_tax_total_and_tax_identity"
                )
    subtotal_candidate = values.pop("profit_after_tax_subtotal_candidate", None)
    if (
        values.get("profit_after_tax") is None
        and values.get("profit_before_regulatory_deferral") is None
        and values.get("profit_after_tax_before_minority_interest") is None
        and values.get("profit_from_continuing_operations") is None
        and values.get("profit_for_period") is None
        and None not in (pbt, reported_tax, subtotal_candidate)
        and min(
            _ratio(pbt - reported_tax, subtotal_candidate),
            _ratio(pbt + reported_tax, subtotal_candidate),
        )
        <= 0.01
    ):
        values["profit_after_tax"] = subtotal_candidate
        values["profit_after_tax_source"] = "reported_post_tax_subtotal"
    regulatory_net = values.get("profit_before_regulatory_deferral")
    before_minority_net = values.get("profit_after_tax_before_minority_interest")
    continuing_net = values.get("profit_from_continuing_operations")
    tax_identity_pbt = pbt
    if (
        values.get("profit_before_tax_basis") == "before_associate_share"
        and values.get("profit_after_tax") is not None
    ):
        net = values["profit_after_tax"]
        values["tax_identity_profit_basis"] = "profit_after_tax_before_associate"
    else:
        if regulatory_net is not None:
            net = regulatory_net
            values["tax_identity_profit_basis"] = "profit_before_regulatory_deferral"
        elif before_minority_net is not None:
            net = before_minority_net
            values["tax_identity_profit_basis"] = (
                "profit_after_tax_before_minority_interest"
            )
        elif continuing_net is not None:
            net = continuing_net
            continuing_pbt = values.get(
                "profit_before_tax_from_continuing_operations"
            )
            if continuing_pbt is not None:
                tax_identity_pbt = continuing_pbt
                values["tax_identity_profit_basis"] = "continuing_operations"
            elif (
                pbt is not None
                and values.get("discontinued_operations_pre_tax") is not None
                and values.get("discontinued_operations_tax") is not None
            ):
                tax_identity_pbt = (
                    pbt - values["discontinued_operations_pre_tax"]
                )
                values["tax_identity_profit_basis"] = (
                    "derived_continuing_pbt_excluding_discontinued_operations"
                )
            elif (
                (
                    values.get("profit_before_tax_basis") == "before_associate_share"
                    or (
                        values.get("associate_jv_share") is not None
                        and values.get("profit_before_tax_position")
                        != "after_associate_share"
                    )
                )
                and values.get("associate_jv_share") is not None
                and values.get("associate_jv_share_position")
                != "after_continuing_operations"
                and pbt is not None
            ):
                tax_identity_pbt = pbt + values["associate_jv_share"]
                values["tax_identity_profit_basis"] = (
                    "continuing_operations_after_associate_share"
                )
            else:
                values["tax_identity_profit_basis"] = "continuing_operations"
        elif values.get("profit_after_tax") is not None:
            net = values.get("profit_after_tax")
            values["tax_identity_profit_basis"] = "profit_after_tax"
        else:
            net = values.get("profit_for_period")
            if (
                values.get("profit_before_tax_basis") == "before_associate_share"
                and values.get("associate_jv_share") is not None
                and values.get("associate_jv_share_position")
                == "before_profit_for_period"
                and pbt is not None
            ):
                tax_identity_pbt = pbt + values["associate_jv_share"]
                values["tax_identity_profit_basis"] = (
                    "profit_for_period_after_associate_share"
                )
            else:
                values["tax_identity_profit_basis"] = "profit_for_period"
    if (
        reported_tax is None
        and values.get("tax_section_present")
        and tax_identity_pbt is not None
        and net is not None
        and _within_one_percent(_ratio(tax_identity_pbt, net))
    ):
        # An explicit tax section with blank component rows plus independently
        # reported equal pre- and post-tax profit is positive evidence of a
        # zero total tax charge.  The identity is required so OCR blanks alone
        # can never be promoted to zero.
        reported_tax = 0.0
        values["income_tax_expense"] = 0.0
        values["income_tax_expense_source"] = (
            "derived_pbt_equals_net_with_explicit_tax_section"
        )
    if None not in (tax_identity_pbt, reported_tax, net):
        selected_ratio = min(
            _ratio(tax_identity_pbt - reported_tax, net),
            _ratio(tax_identity_pbt + reported_tax, net),
        )
        if not _within_one_percent(selected_ratio) and pbt is not None:
            tax_candidates: list[tuple[str, float]] = [
                (
                    values.get("income_tax_expense_source") or "selected_tax",
                    reported_tax,
                )
            ]

            def add_tax_candidate(source: str, candidate: Optional[float]) -> None:
                if candidate is None:
                    return
                if any(_ratio(existing, candidate) <= 1e-12 for _, existing in tax_candidates):
                    return
                tax_candidates.append((source, candidate))

            direct_reported_tax = values.get("income_tax_expense_reported")
            add_tax_candidate("reported_tax_expense", direct_reported_tax)
            collected_tax_components = sum(
                values.get(name) is not None
                for name in (
                    "current_tax",
                    "current_tax_net",
                    "deferred_tax",
                    "deferred_tax_earlier_adjustment",
                    "earlier_year_tax",
                    "tax_provision_adjustment",
                    "mat_credit",
                    "minimum_alternative_tax",
                    "tax_reserve_transfer",
                )
            )
            if subtotal_candidate is not None and (
                direct_reported_tax is not None or collected_tax_components >= 2
            ):
                add_tax_candidate(
                    "numeric_subtotal_after_tax_components",
                    subtotal_candidate,
                )
            current_tax_net = values.get("current_tax_net")
            if current_tax_net is not None:
                net_current_parts = [current_tax_net]
                net_current_parts.extend(
                    values[name]
                    for name in (
                        "deferred_tax",
                        "deferred_tax_earlier_adjustment",
                        "tax_reserve_transfer",
                    )
                    if values.get(name) is not None
                )
                add_tax_candidate(
                    "net_current_tax_plus_separate_deferred_components",
                    sum(net_current_parts),
                )
                add_tax_candidate(
                    "net_current_tax_reported_subtotal",
                    current_tax_net,
                )
            gross_tax_parts = [
                values[name]
                for name in (
                    "current_tax",
                    "deferred_tax",
                    "deferred_tax_earlier_adjustment",
                    "earlier_year_tax",
                    "tax_provision_adjustment",
                    "mat_credit",
                    "minimum_alternative_tax",
                    "tax_reserve_transfer",
                )
                if values.get(name) is not None
            ]
            if gross_tax_parts:
                add_tax_candidate(
                    "summed_gross_tax_components",
                    sum(gross_tax_parts),
                )
            if values.get("current_tax_single_value_two_column_candidate"):
                tax_parts_without_current = [
                    values[name]
                    for name in (
                        "deferred_tax",
                        "deferred_tax_earlier_adjustment",
                        "earlier_year_tax",
                        "tax_provision_adjustment",
                        "mat_credit",
                        "minimum_alternative_tax",
                        "tax_reserve_transfer",
                    )
                    if values.get(name) is not None
                ]
                if tax_parts_without_current:
                    add_tax_candidate(
                        "omitted_comparative_only_current_tax",
                        sum(tax_parts_without_current),
                    )
            fixed_tax_name = (
                "current_tax_net"
                if values.get("current_tax_net") is not None
                else "current_tax"
            )
            fixed_tax = values.get(fixed_tax_name)
            sign_ambiguous_parts = [
                values[name]
                for name in (
                    "deferred_tax",
                    "deferred_tax_earlier_adjustment",
                    "earlier_year_tax",
                    "tax_provision_adjustment",
                    "mat_credit",
                    "minimum_alternative_tax",
                    "tax_reserve_transfer",
                )
                if values.get(name) not in (None, 0.0)
            ]
            if fixed_tax is not None and sign_ambiguous_parts:
                sign_grid = {fixed_tax}
                for part in sign_ambiguous_parts:
                    sign_grid = {
                        candidate + signed_part
                        for candidate in sign_grid
                        for signed_part in (part, -part)
                    }
                for sign_candidate in sorted(sign_grid):
                    add_tax_candidate(
                        "component_sign_grid",
                        sign_candidate,
                    )
                # Rows for earlier-year adjustments, MAT credits, and similar
                # secondary components are frequently comparative-only even
                # when PDF text extraction loses their empty current-period
                # cell.  Let those parts be omitted, but only as candidates;
                # adoption still requires the independently reported PBT/net
                # identity below.  Deferred tax remains required, although
                # its displayed sign may be ambiguous.
                optional_names = {
                    "deferred_tax_earlier_adjustment",
                    "earlier_year_tax",
                    "tax_provision_adjustment",
                    "mat_credit",
                    "minimum_alternative_tax",
                    "tax_reserve_transfer",
                }
                optional_grid = {fixed_tax}
                for name in (
                    "deferred_tax",
                    "deferred_tax_earlier_adjustment",
                    "earlier_year_tax",
                    "tax_provision_adjustment",
                    "mat_credit",
                    "minimum_alternative_tax",
                    "tax_reserve_transfer",
                ):
                    part = values.get(name)
                    if part in (None, 0.0):
                        continue
                    variants = (part, -part, 0.0) if name in optional_names else (part, -part)
                    optional_grid = {
                        candidate + signed_part
                        for candidate in optional_grid
                        for signed_part in variants
                    }
                for optional_candidate in sorted(optional_grid):
                    add_tax_candidate(
                        "optional_component_grid",
                        optional_candidate,
                    )
            pbt_candidates = [("reported_profit_before_tax", pbt)]
            continuing_pbt = values.get(
                "profit_before_tax_from_continuing_operations"
            )
            if continuing_pbt is not None:
                pbt_candidates.append(
                    ("reported_continuing_operations_profit_before_tax", continuing_pbt)
                )
            reported_preexceptional = values.get(
                "profit_before_exceptional_items_and_tax"
            )
            displayed_preexceptional = values.get(
                "profit_before_exceptional_items_and_tax_reported"
            )
            if (
                displayed_preexceptional is not None
                and (
                    reported_preexceptional is None
                    or _ratio(displayed_preexceptional, reported_preexceptional)
                    > 1e-12
                )
            ):
                pbt_candidates.append(
                    (
                        "displayed_preexceptional_before_total_identity_repair",
                        displayed_preexceptional,
                    )
                )
            reported_exceptional = values.get("exceptional_items")
            if reported_preexceptional is not None:
                pbt_candidates.append(
                    (
                        "reported_preexceptional_subtotal",
                        reported_preexceptional,
                    )
                )
                if reported_exceptional in (None, 0.0):
                    pbt_candidates.append(
                        ("preexceptional_subtotal_no_exceptional_effect", reported_preexceptional)
                    )
                else:
                    pbt_candidates.extend(
                        (
                            (
                                "preexceptional_plus_exceptional",
                                reported_preexceptional + reported_exceptional,
                            ),
                            (
                                "preexceptional_minus_exceptional",
                                reported_preexceptional - reported_exceptional,
                            ),
                        )
                    )
            alternative_nets: list[
                tuple[str, float, float, float, str, float, str, float]
            ] = []
            for tax_source, candidate_tax in tax_candidates:
                for pbt_source, base_candidate_pbt in pbt_candidates:
                    for metric, basis in (
                        (
                            "profit_before_regulatory_deferral",
                            "profit_before_regulatory_deferral",
                        ),
                        (
                            "profit_after_tax_before_minority_interest",
                            "profit_after_tax_before_minority_interest",
                        ),
                        ("profit_from_continuing_operations", "continuing_operations"),
                        ("profit_after_tax", "profit_after_tax"),
                        ("profit_for_period", "profit_for_period"),
                    ):
                        candidate_net = values.get(metric)
                        if candidate_net is None:
                            continue
                        candidate_pbt = base_candidate_pbt
                        candidate_basis = basis
                        if metric == "profit_from_continuing_operations" and (
                            (
                                values.get("profit_before_tax_basis")
                                == "before_associate_share"
                                or (
                                    values.get("associate_jv_share") is not None
                                    and values.get("profit_before_tax_position")
                                    != "after_associate_share"
                                )
                            )
                            and values.get("associate_jv_share") is not None
                            and values.get("associate_jv_share_position")
                            != "after_continuing_operations"
                        ):
                            candidate_pbt = (
                                base_candidate_pbt + values["associate_jv_share"]
                            )
                            candidate_basis = (
                                "continuing_operations_after_associate_share"
                            )
                        elif (
                            metric == "profit_for_period"
                            and values.get("profit_before_tax_basis")
                            == "before_associate_share"
                            and values.get("associate_jv_share") is not None
                            and values.get("associate_jv_share_position")
                            == "before_profit_for_period"
                        ):
                            candidate_pbt = (
                                base_candidate_pbt + values["associate_jv_share"]
                            )
                            candidate_basis = (
                                "profit_for_period_after_associate_share"
                            )
                        elif (
                            metric == "profit_after_tax"
                            and values.get("profit_before_tax_basis")
                            == "before_associate_share"
                        ):
                            candidate_basis = "profit_after_tax_before_associate"
                        net_variants = [(candidate_basis, candidate_net)]
                        discontinued_pre_tax = values.get(
                            "discontinued_operations_pre_tax"
                        )
                        discontinued_tax = values.get(
                            "discontinued_operations_tax"
                        )
                        if (
                            metric in {"profit_after_tax", "profit_for_period"}
                            and discontinued_pre_tax is not None
                            and discontinued_tax is not None
                        ):
                            net_variants.append(
                                (
                                    "derived_continuing_profit_excluding_discontinued_operations",
                                    candidate_net
                                    - discontinued_pre_tax
                                    + discontinued_tax,
                                )
                            )
                        minority_interest = values.get("minority_interest")
                        if (
                            metric in {"profit_after_tax", "profit_for_period"}
                            and minority_interest not in (None, 0.0)
                        ):
                            net_variants.extend(
                                (
                                    (
                                        "derived_pre_minority_profit",
                                        candidate_net + minority_interest,
                                    ),
                                    (
                                        "derived_pre_minority_profit",
                                        candidate_net - minority_interest,
                                    ),
                                )
                            )
                        for variant_basis, variant_net in net_variants:
                            candidate_ratio = min(
                                _ratio(candidate_pbt - candidate_tax, variant_net),
                                _ratio(candidate_pbt + candidate_tax, variant_net),
                            )
                            alternative_nets.append(
                                (
                                    variant_basis,
                                    variant_net,
                                    candidate_pbt,
                                    candidate_ratio,
                                    pbt_source,
                                    base_candidate_pbt,
                                    tax_source,
                                    candidate_tax,
                                )
                            )
            if alternative_nets:
                (
                    best_basis,
                    best_net,
                    best_pbt,
                    best_ratio,
                    best_pbt_source,
                    best_reported_pbt,
                    best_tax_source,
                    best_tax,
                ) = min(
                    alternative_nets, key=lambda candidate: candidate[3]
                )
                if _within_one_percent(best_ratio) and best_ratio < selected_ratio:
                    net = best_net
                    tax_identity_pbt = best_pbt
                    values["tax_identity_profit_basis"] = best_basis
                    if best_basis == "derived_pre_minority_profit":
                        values["profit_after_tax_before_minority_interest"] = best_net
                        values[
                            "profit_after_tax_before_minority_interest_source"
                        ] = "derived_reported_net_plus_or_minus_minority_interest"
                    elif (
                        best_basis
                        == "derived_continuing_profit_excluding_discontinued_operations"
                    ):
                        values["profit_from_continuing_operations"] = best_net
                        values["profit_from_continuing_operations_source"] = (
                            "derived_total_net_less_pre_tax_discontinued_plus_discontinued_tax"
                        )
                    if best_pbt_source != "reported_profit_before_tax":
                        values["profit_before_tax"] = best_reported_pbt
                        values["profit_before_tax_source"] = (
                            f"tax_identity_validated_{best_pbt_source}"
                        )
                    if _ratio(best_tax, reported_tax) > 1e-12:
                        reported_tax = best_tax
                        values["income_tax_expense"] = best_tax
                        values["income_tax_expense_source"] = (
                            f"tax_identity_validated_{best_tax_source}"
                        )
    if None not in (tax_identity_pbt, reported_tax, net):
        subtract_ratio = _ratio(tax_identity_pbt - reported_tax, net)
        add_ratio = _ratio(tax_identity_pbt + reported_tax, net)
        best = min(subtract_ratio, add_ratio)
        values["tax_identity_ratio"] = best
        values["tax_sign_convention"] = "subtract" if subtract_ratio <= add_ratio else "add"
        values["tax_identity_status"] = (
            "pass" if _within_one_percent(best) else "fail"
        )
    else:
        values["tax_sign_convention"] = None
        values["tax_identity_status"] = "insufficient_components"


def _template(values: dict[str, Any], company_name: Optional[str] = None) -> tuple[str, str]:
    if company_name and re.search(
        r"\b(?:bank|finance|financial|finserv|leasing|insurance|credit|securities|"
        r"asset\s+management|mutual\s+fund|custodian\s+fund)\b",
        company_name,
        re.I,
    ):
        return "financial_services", "company_name"
    financial_markers = sum(
        values.get(name) is not None
        for name in ("interest_income", "dividend_income", "fair_value_changes")
    )
    industrial_markers = sum(
        values.get(name) is not None
        for name in ("cost_of_materials", "purchases_stock_in_trade", "inventory_changes")
    )
    if financial_markers >= 1 and industrial_markers == 0:
        return "financial_services", "statement_presentation"
    return "industrial", "statement_presentation"


def _profit_block_lines(block: StatementBlock, lines: list[str]) -> list[str]:
    block_lines = lines[block.start:block.end]
    normalized_heading = _normalize_statement_ocr_line(block.heading)
    balance_match = _BALANCE_HEADING.search(normalized_heading)
    profit_match = _PROFIT_HEADING.search(normalized_heading)
    if not profit_match:
        return block_lines
    column_start = 0
    if (
        balance_match
        and profit_match.start() - balance_match.end() >= 20
    ):
        column_start = max(0, profit_match.start() - 4)
    right_statement = re.search(
        r"\b(?:cash\s+flow\s+statement|statement\s+of\s+cash\s+flows|"
        r"statement\s+of\s+changes\s+in\s+equity|balance\s+sheet)\b",
        normalized_heading[profit_match.end():],
        re.I,
    )
    column_end: Optional[int] = None
    if right_statement:
        column_end = profit_match.end() + right_statement.start() - 4
    if column_start == 0 and block.start < len(lines):
        source_heading = _normalize_statement_ocr_line(lines[block.start])
        source_profit = _PROFIT_HEADING.search(source_heading)
        if (
            source_profit is not None
            and source_profit.start() >= 80
            and source_heading[:source_profit.start()].strip()
        ):
            column_start = source_profit.start() - 4
    if column_start == 0 and column_end is None:
        return block_lines
    return [
        line[column_start:column_end] if len(line) > column_start else ""
        for line in block_lines
    ]


def _profit_candidate(
    block: StatementBlock,
    lines: list[str],
    company_name: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    source_heading = (
        _normalize_statement_ocr_line(lines[block.start])
        if block.start < len(lines)
        else block.heading
    )
    source_profit_match = _PROFIT_HEADING.search(source_heading)
    right_column_heading = bool(
        source_profit_match is not None
        and source_profit_match.start() >= 80
        and source_heading[:source_profit_match.start()].strip()
    )
    compact_heading = " ".join(_normalize_statement_ocr_line(block.heading).split())
    if re.search(
        r"(?:^\s*reconciliation\b|\breconciliation\s+of\b|"
        r"\beffect\s+of\s+ind\s+as\s+adoption\b|"
        r"^\s*notes?\s+to\s+(?:the\s+)?financial\s+statements?\b)",
        compact_heading,
        re.I,
    ):
        return None
    heading_match = _PROFIT_HEADING.search(compact_heading)
    mislabeled_balance_heading = block.kind == "balance_sheet" and not heading_match
    if not heading_match and not mislabeled_balance_heading:
        return None
    if heading_match:
        prefix = compact_heading[:heading_match.start()].strip()
        prefix = re.sub(r"^[\s|:;,._\-–—]+|[\s|:;,._\-–—]+$", "", prefix)
        company_aliases = {
            "limited": "ltd",
            "incorporated": "inc",
            "corporation": "corp",
        }

        def company_identity_key(value: str) -> str:
            return "".join(
                company_aliases.get(token, token)
                for token in re.findall(r"[a-z0-9]+", value.lower())
            )

        prefix_key = company_identity_key(prefix)
        company_key = company_identity_key(company_name or "")
        company_name_prefix = bool(
            prefix_key
            and company_key
            and (prefix_key in company_key or company_key in prefix_key)
        )
        if (
            prefix
            and not company_name_prefix
            and not right_column_heading
            and not _BALANCE_HEADING.search(prefix)
            and not re.fullmatch(
            r"audited|revised|ind\s+as|part\s+ii\s*[-–]\s*form\s+of|"
            r"part\s*(?:(?:i|1|l)\s*)?(?:[-|]+\s*)?(?:combined)?|"
            r"s[l1i]|table\s+\d+|(?:[IVX]+|\d+)[.)]?|annual\s+report\s+(?:19|20)\d{2}\s*[-–]\s*"
            r"(?:\d{2}|(?:19|20)\d{2})(?:\s+chapter\s*:?)?",
            prefix,
            re.I,
            )
        ):
            return None
    block_lines = _profit_block_lines(block, lines)
    values = _extract_profit_and_loss(block_lines)
    period = (
        _annual_report_period([block.heading])
        or _period_end([block.heading])
        or _period_end(block_lines[:20])
        or _statement_column_period(block_lines[:20])
    )
    income_anchor = any(
        values.get(name) is not None
        for name in (
            "revenue_from_operations",
            "other_income",
            "interest_income",
            "total_income",
        )
    )
    result_anchor = any(
        values.get(name) is not None
        for name in (
            "profit_before_exceptional_items_and_tax",
            "profit_before_tax",
            "profit_before_tax_from_continuing_operations",
            "profit_before_associate_and_tax",
            "profit_before_regulatory_deferral",
            "profit_after_tax_before_minority_interest",
            "profit_after_tax",
            "profit_for_period",
        )
    )
    expense_anchor = any(
        values.get(name) is not None
        for name in (
            "total_expenses",
            "other_expenses",
            "employee_benefits",
            "finance_costs",
            "depreciation_amortization",
            "cost_of_materials",
        )
    )
    explicit_income_expenditure_sections = bool(
        re.search(r"(?im)^\s*income\s*:?\s*$", "\n".join(block_lines))
        and re.search(
            r"(?im)^\s*(?:expenditure|expenses)\s*:?\s*$",
            "\n".join(block_lines),
        )
    )
    numeric_value_count = sum(
        isinstance(value, (int, float)) for value in values.values()
    )
    if mislabeled_balance_heading:
        strong_profit_anchors = all(
            (
                income_anchor,
                expense_anchor,
                values.get("profit_before_tax") is not None,
                any(
                    values.get(name) is not None
                    for name in (
                        "profit_before_regulatory_deferral",
                        "profit_after_tax_before_minority_interest",
                        "profit_from_continuing_operations",
                        "profit_after_tax",
                        "profit_for_period",
                    )
                ),
                numeric_value_count >= 6,
            )
        )
        if not (period and strong_profit_anchors):
            return None
        values["source_heading_recovery"] = "ocr_mislabeled_balance_sheet"
    complete_primary = (
        period
        and income_anchor
        and (expense_anchor or explicit_income_expenditure_sections)
        and result_anchor
        and numeric_value_count >= 4
    )
    # A source-shaped primary heading plus a resolved period and two of the
    # three independent statement sections is enough to catalogue an
    # incomplete row. Downstream identity/screening gates remain strict, so
    # this recovers document coverage without fabricating the missing section.
    incomplete_primary = bool(
        period
        and heading_match
        and sum((income_anchor, expense_anchor, result_anchor)) >= 2
        and numeric_value_count >= 5
    )
    if not (complete_primary or incomplete_primary) and not mislabeled_balance_heading:
        return None
    currency, scale, _ = _currency_and_scale(block_lines[:20])
    score = sum(value is not None for value in values.values())
    score += 2 if scale is not None else 0
    score += 1 if currency is not None else 0
    score += 1 if block.scope != "unknown" else 0
    score += 1 if re.search(r"\b(?:year|period)\s+ended\b", block.heading, re.I) else 0
    return {"block": block, "values": values, "period": period, "score": score}


def _profit_candidate_rank(
    candidate: dict[str, Any],
) -> tuple[bool, bool, str, int, int]:
    """Rank formal period-ended statements ahead of narrative summaries."""
    heading = " ".join(
        _normalize_statement_ocr_line(candidate["block"].heading).split()
    )
    explicit_profit_statement = bool(_PROFIT_HEADING.search(heading))
    explicit_heading_period = bool(
        _annual_report_period([heading]) or _period_end([heading])
    )
    formal_period_ended_statement = bool(
        explicit_profit_statement
        and explicit_heading_period
        and re.search(r"\b(?:year|period)\s+ended\b", heading, re.I)
    )
    return (
        explicit_profit_statement,
        formal_period_ended_statement,
        candidate["period"],
        candidate["score"],
        candidate["block"].start,
    )


def _select_primary_profits(
    blocks: list[StatementBlock],
    lines: list[str],
    company_name: Optional[str] = None,
) -> list[dict[str, Any]]:
    candidates = [
        candidate
        for block in blocks
        if block.kind in {"profit_and_loss", "balance_sheet"}
        for candidate in [_profit_candidate(block, lines, company_name)]
        if candidate is not None
    ]
    if not candidates:
        return []

    selected: list[dict[str, Any]] = []
    explicit: dict[str, list[dict[str, Any]]] = {
        "standalone": [],
        "consolidated": [],
    }
    unknown: list[dict[str, Any]] = []
    for candidate in candidates:
        scope = candidate["block"].scope
        (unknown if scope == "unknown" else explicit[scope]).append(candidate)

    # One filing contributes at most one primary statement per scope.  Annual
    # reports often repeat prior-year statements in summaries or place a
    # table-of-contents heading near a signature date; selecting once across
    # periods prevents those weaker blocks from becoming extra fiscal rows.
    for scope in ("standalone", "consolidated"):
        if explicit[scope]:
            winner = max(
                explicit[scope],
                key=_profit_candidate_rank,
            )
            winner["scope"] = scope
            winner["scope_source"] = "statement_heading"
            selected.append(winner)

    # An unqualified primary statement is the company's standalone statement.
    # Keep only the strongest table if no explicitly standalone statement won.
    if unknown and not explicit["standalone"]:
        winner = max(
            unknown,
            key=_profit_candidate_rank,
        )
        winner["scope"] = "standalone"
        winner["scope_source"] = (
            "inferred_single_primary"
            if len(candidates) == 1
            else "inferred_unqualified_primary"
        )
        selected.append(winner)
    return sorted(selected, key=lambda item: item["block"].start)


def _balance_candidates(
    blocks: list[StatementBlock], lines: list[str]
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for block in blocks:
        if block.kind != "balance_sheet":
            continue
        block_lines = lines[block.start:block.end]
        period = (
            _annual_report_period(block_lines[:20])
            or _period_end(block_lines[:20])
            or _statement_column_period(block_lines[:20])
        )
        values = _extract_balance_sheet(block_lines)
        if period and any(
            values.get(name) is not None
            for name in ("total_assets", "total_equity", "total_borrowings")
        ):
            candidates.append({"block": block, "period": period, "values": values})
    return candidates


def _matching_balance(
    profit: dict[str, Any], balances: list[dict[str, Any]]
) -> Optional[dict[str, Any]]:
    scope = profit["scope"]
    block = profit["block"]
    compatible = [
        candidate
        for candidate in balances
        if candidate["period"] == profit["period"]
        and candidate["block"].scope in {scope, "unknown"}
    ]
    if not compatible:
        compatible = [
            candidate
            for candidate in balances
            if candidate["block"].scope in {scope, "unknown"}
        ]
    if not compatible:
        return None
    winner = min(
        compatible,
        key=lambda candidate: (
            candidate["block"].start > block.start,
            abs(candidate["block"].start - block.start),
        ),
    )
    continuations = [
        candidate
        for candidate in compatible
        if candidate["block"].scope == winner["block"].scope
        and abs(candidate["block"].start - winner["block"].start) <= 150
    ]
    if len(continuations) == 1:
        return winner
    merged = dict(winner)
    merged_values: dict[str, Any] = {}
    for candidate in sorted(continuations, key=lambda item: item["block"].start):
        merged_values.update(candidate["values"])
    if (
        merged_values.get("total_borrowings") is None
        and merged_values.get("financial_liabilities_enumerated")
        and merged_values.get("total_assets") is not None
        and merged_values.get("total_lease_liabilities") is not None
    ):
        merged_values["total_borrowings"] = 0.0
        merged_values["debt_reconciliation_status"] = "true_zero"
        merged_values["total_interest_bearing_debt"] = merged_values[
            "total_lease_liabilities"
        ]
    merged["values"] = merged_values
    return merged


def extract_document(text: str, company_name: Optional[str] = None) -> list[dict[str, Any]]:
    """Extract one candidate row per primary profit-and-loss statement."""
    # A recurring embedded-font ligature maps the ``fi`` in ``profit`` to the
    # C0 byte 0x02.  Repair that exact word before the generic control-byte
    # cleanup below; otherwise ``Pro\x02t`` becomes ``Pro t`` and hides PBT/PAT.
    text = re.sub(r"\bpro\x02t\b", "profit", text, flags=re.I)
    # Some legacy BSE PDFs expose an ASCII control byte wherever the visual
    # page has a word separator.  Preserve page/line/tab delimiters, but turn
    # the remaining C0 separators into ordinary spaces before any matching.
    text = re.sub(r"[\x00-\x08\x0b\x0e-\x1f\x7f]", " ", text)
    lines = text.splitlines()
    blocks = find_statement_blocks(text)
    balances = _balance_candidates(blocks, lines)
    profits = _select_primary_profits(blocks, lines, company_name)
    rows: list[dict[str, Any]] = []
    explicit_zero = bool(_EXPLICIT_NO_BORROWING.search(text))
    for candidate in profits:
        profit = candidate["block"]
        pl_lines = _profit_block_lines(profit, lines)
        scope = candidate["scope"]
        balance_candidate = _matching_balance(candidate, balances)
        balance = balance_candidate["block"] if balance_candidate else None
        bs_lines = lines[balance.start:balance.end] if balance else []

        period_end = (
            candidate["period"]
            or _period_end(pl_lines[:12])
            or _period_end(bs_lines[:12])
        )
        currency, unit_scale, unit_source = _currency_and_scale(pl_lines[:12])
        currency_source = "statement_header" if currency is not None else "unknown"
        if currency is None or unit_scale is None:
            b_currency, b_scale, b_source = _currency_and_scale(bs_lines[:12])
            if currency is None and b_currency is not None:
                currency = b_currency
                currency_source = "balance_sheet_header"
            if unit_scale is None:
                unit_scale, unit_source = b_scale, b_source
        if currency is None and re.search(
            r"(?:financial\s+statements?\s+(?:are|is)\s+(?:prepared|presented)\s+in\s+"
            r"Indian\s+rupees?|functional\s+(?:and\s+presentation\s+)?currency.*?"
            r"Indian\s+rupees?)",
            text,
            re.I | re.S,
        ):
            currency = "INR"
            currency_source = "document_accounting_policy"
        if unit_scale is None and re.search(
            r"financial\s+statements?\s+(?:are|is)\s+presented\s+in\s+"
            r"Indian\s+rupees?\s*(?:\(\s*INR\s*\))?",
            text,
            re.I | re.S,
        ):
            # Presentation in INR, with no multiplier stated at either primary
            # statement, is explicit document-level evidence for whole rupees.
            unit_scale = 1
            unit_source = "document_presentation_currency"
        if currency is None:
            # This extractor is exclusively for Indian BSE issuers.  Retain a
            # jurisdiction-level INR default for cataloguing, but keep it
            # distinct from statement evidence and exclude it from screening.
            currency = "INR"
            currency_source = "market_jurisdiction_default"

        values: dict[str, Any] = dict(candidate["values"])
        if balance_candidate:
            values.update(balance_candidate["values"])
        template, template_source = _template(values, company_name)
        values.update(
            {
                "statement_scope": scope,
                "statement_scope_source": candidate["scope_source"],
                "period_end": period_end,
                "currency": currency,
                "currency_source": currency_source,
                "unit_scale": unit_scale,
                "unit_scale_source": unit_source,
                "industry_template": template,
                "industry_template_source": template_source,
                "source_profit_heading": profit.heading,
                "source_balance_heading": balance.heading if balance else None,
            }
        )
        if values.get("total_borrowings") is not None:
            values["total_borrowings_null_reason"] = (
                "true_zero"
                if values.get("debt_reconciliation_status") == "true_zero"
                else None
            )
        elif explicit_zero:
            values["total_borrowings"] = 0.0
            values["total_borrowings_null_reason"] = "true_zero"
            values["debt_reconciliation_status"] = "true_zero"
        else:
            values["total_borrowings"] = None
            values["total_borrowings_null_reason"] = "not_reported"
            values["debt_reconciliation_status"] = "unavailable"
        _derive(values, template)
        values["screen_eligible"] = all(
            (
                scope != "unknown",
                template == "industrial",
                period_end is not None,
                currency is not None,
                currency_source != "market_jurisdiction_default",
                unit_scale is not None,
                values.get("operating_profit_closure_status") == "pass",
                values.get("tax_identity_status") == "pass",
                values.get("total_borrowings") is not None,
            )
        )
        rows.append(values)
    return rows


def normalize_manifest_record(record: dict[str, Any]) -> dict[str, Any]:
    raw = record.get("raw_metadata") or {}
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            raw = {}
    report = raw.get("report") or {}
    scrip = raw.get("scrip") or {}
    published = str(report.get("dt_tm") or "").strip()
    publication_date = published[:10] if re.fullmatch(r"\d{4}-\d{2}-\d{2}.*", published) else None
    return {
        "filing_id": record.get("filing_id"),
        "ticker": str(scrip.get("SCRIP_CD") or record.get("stock_code") or "").strip() or None,
        "symbol": str(scrip.get("scrip_id") or "").strip() or None,
        "isin": str(scrip.get("ISIN_NUMBER") or "").strip() or None,
        "company_name": record.get("company_name") or scrip.get("Issuer_Name"),
        "listing_status": scrip.get("Status"),
        "listing_segment": scrip.get("Segment"),
        "publication_date": publication_date,
        "source_year": report.get("year") or record.get("filing_date"),
        "document_url": record.get("document_url"),
    }
