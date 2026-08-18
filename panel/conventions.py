"""Fiscal-calendar conventions for the panel, sourced from one table.

The table itself lives in `scripts/market_conventions.py` because the corpus
scripts need it too (unit-scale recovery, stage-02 sweeps). This module is the
panel's single door to it, for two reasons:

1. One source of truth. A second copy of "Australia closes 30 June" is a second
   thing to get wrong.
2. The import has to be path-explicit. An `easy-install.pth` in the python310
   env puts another repo on sys.path whose `scripts/` is a regular package, and
   Python resolves that ahead of this repo's namespace package -- so
   `import scripts.market_conventions` binds to the wrong project. Doing the
   path-explicit load once, here, keeps that ugliness out of every adapter.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Optional, Tuple

_MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "market_conventions.py"
_spec = importlib.util.spec_from_file_location("_panel_market_conventions", _MODULE_PATH)
_conv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_conv)

FISCAL_YEAR_END = _conv.FISCAL_YEAR_END
period_end_for = _conv.period_end_for

# Used where a market's source carries no period-end column at all and the
# fiscal year came from a directory name or a label.
CALENDAR_YEAR_END = "12-31"


def period_end_and_source(market: str, fiscal_year: int) -> Tuple[str, str]:
    """(period_end, provenance) for a fiscal-year label.

    A market in the table gets its real year end and is labelled
    `convention:<market>`. A market NOT in the table still needs a date -- the
    panel key requires one -- but it is labelled `assumed:calendar-year-end`
    rather than passed off as verified. That distinction is the whole point:
    `{fiscal_year}-12-31` was applied to Australia for months precisely because
    an assumption and a convention were indistinguishable in the output.

    Markets whose source carries a real period end (cn/jp/hk) never reach here;
    their adapters read the date from the data.
    """
    dated: Optional[str] = period_end_for(market, fiscal_year)
    if dated is not None:
        return dated, f"convention:{market}"
    return f"{int(fiscal_year):04d}-{CALENDAR_YEAR_END}", "assumed:calendar-year-end"
