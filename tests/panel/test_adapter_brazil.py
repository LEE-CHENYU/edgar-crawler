"""Brazil CVM adapter.

Like ESEF, the source ships what every PDF market lacked: MOEDA (currency),
ESCALA_MOEDA (unit scale), DT_FIM_EXERC (a real period end), and separate
consolidated/individual files.

The reason it needs its OWN adapter rather than reusing rows_from_canonical_
metrics: that adapter derives period_end from a fiscal-year label via the
conventions table. Pointing Brazil at it would DISCARD the real DT_FIM_EXERC and
substitute a fabricated 12-31 -- throwing away the very field that makes this
source good.
"""

from collections import Counter

import pandas as pd
import pytest

from panel.adapters.brazil import rows_from_brazil


def _frame(**over):
    row = {"ticker": "001023", "company_name": "BCO BRASIL S.A.",
           "period_end": "2023-12-31", "fiscal_year": 2023,
           "reporting_basis": "consolidated", "currency": "BRL",
           "unit_scale": 1000, "unit_scale_source": "declared",
           "doc_id": "001023_2023-12-31_consolidated", "n_metrics": 8,
           "total_assets": 2153878452.0, "revenue": 1.0e8}
    row.update(over)
    return pd.DataFrame([row])


def test_the_real_period_end_is_used_not_a_convention():
    """The whole point: DT_FIM_EXERC is read, never fabricated."""
    rows = rows_from_brazil(_frame(period_end="2023-06-30"))
    assert rows[0]["period_end"] == "2023-06-30"
    assert rows[0]["period_end_source"] == "reported"


def test_unit_scale_is_carried_not_defaulted():
    """ESCALA_MOEDA=MIL means thousands; dropping it is a 1000x error."""
    rows = rows_from_brazil(_frame())
    assert rows[0]["unit_scale"] == 1000
    assert rows[0]["unit_scale_source"] == "declared"


def test_reporting_basis_is_preserved():
    """con and ind files are separate filings for the same company-period; they
    must stay distinguishable or they collide on the panel key."""
    assert rows_from_brazil(_frame())[0]["reporting_basis"] == "consolidated"
    assert rows_from_brazil(_frame(reporting_basis="parent"))[0]["reporting_basis"] == "parent"


def test_market_is_br():
    assert rows_from_brazil(_frame())[0]["market"] == "br"


def test_the_cvm_code_is_the_local_identifier():
    assert rows_from_brazil(_frame())[0]["local_id"] == "001023"


def test_rows_without_a_period_end_are_dropped():
    drops = Counter()
    assert rows_from_brazil(_frame(period_end=None), drops=drops) == []
    assert drops["missing_period_end"] == 1


def test_rows_with_no_metrics_are_dropped():
    drops = Counter()
    assert rows_from_brazil(_frame(n_metrics=0), drops=drops) == []
    assert drops["no_metrics"] == 1


def test_an_unstated_scale_stays_none_not_one():
    """Consistent with the rest of the panel: unknown is not 1."""
    rows = rows_from_brazil(_frame(unit_scale=None, unit_scale_source="unknown"))
    assert rows[0]["unit_scale"] is None
