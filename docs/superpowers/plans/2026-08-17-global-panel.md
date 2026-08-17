# Global Listing Spine + Fundamentals Panel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a global securities master ("spine") and a fundamentals panel with one row per security per reporting period, assembled from every market corpus that already has stage-02 output.

**Architecture:** Three independently testable layers. Adapters convert each market's stage-02 artifact into normalized rows; the spine resolves market-local identifiers to FIGIs via OpenFIGI with a cached surrogate-key fallback; the panel writes partitioned parquet at native reporting frequency, with a quarterly *view function* rather than forward-filled storage.

**Tech Stack:** Python 3.10 (`/Users/lichenyu/GitHub/edgar-crawler/.venv`), pandas, pyarrow, requests, duckdb (JP only), pytest.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-08-17-global-panel-design.md`
- Repo: `/Users/lichenyu/GitHub/edgar-crawler`, branch `codex/non-us-market-filings`
- Run tests with `.venv/bin/python -m pytest`
- `duckdb` is NOT in `.venv` (it is in the `python310` conda env). Task 8 installs it into `.venv`; do not use `conda run` for panel code.
- Metric vocabulary is exactly `asx_financials_extract.CANONICAL_METRICS`: `revenue, cost_of_revenue, gross_profit, profit_before_tax, income_tax_expense, net_income, basic_eps, cash_and_equivalents, accounts_receivable, inventories, current_assets, non_current_assets, total_assets, current_liabilities, non_current_liabilities, total_liabilities, total_equity, operating_cash_flow, investing_cash_flow, financing_cash_flow`
- `period_type` values are exactly `Q`, `H`, `A`, `OPEN`
- Panel output root: `/Users/lichenyu/datasets/panel` (boot volume — OWC is at 94%)
- Data root for sources: `/Volumes/OWC Express 1M2/datasets`
- Never forward-fill in storage
- Every USD value must carry `fx_rate` and `fx_asof`
- All writes atomic: write `.tmp`, then `os.replace`
- OpenFIGI: `POST https://api.openfigi.com/v3/mapping`, batches of ≤100, no API key, `idType: "TICKER"` with `exchCode` (NOT `BASE_TICKER`, which additionally requires `securityType2`)

## File Structure

| File | Responsibility |
|---|---|
| `panel/schema.py` | Row dataclass, `PANEL_COLUMNS`, `period_type_for`, plausibility checks |
| `panel/spine.py` | OpenFIGI resolution, cache, surrogate keys |
| `panel/adapters/screening_input.py` | AU/TW/PH/KR (shared header) |
| `panel/adapters/cn.py` | CN parquet trio, incl. `OPEN` rows |
| `panel/adapters/in_bse.py` | IN_BSE canonical_metrics parquet |
| `panel/adapters/hk.py` | HK facts long→wide pivot |
| `panel/adapters/jp.py` | JP duckdb `annual_metrics_wide` |
| `panel/adapters/__init__.py` | `ADAPTERS` registry |
| `panel/fx.py` | Live FX fetch + USD conversion carrying rate/asof |
| `panel/build.py` | Orchestrator + CLI |
| `panel/views.py` | `quarterly_view` |
| `tests/panel/test_*.py` | One test module per unit above |

---

### Task 1: Panel schema and period typing

**Files:**
- Create: `panel/__init__.py`, `panel/schema.py`
- Test: `tests/panel/test_schema.py`

**Interfaces:**
- Consumes: nothing
- Produces: `PANEL_COLUMNS: list[str]`, `METRIC_COLUMNS: list[str]`, `period_type_for(period_end: str, cadence: str) -> str`, `is_plausible_fiscal_year(year) -> bool`, `normalize_period_end(value) -> str | None`

- [ ] **Step 1: Write the failing test**

```python
# tests/panel/test_schema.py
import pytest
from panel.schema import (
    METRIC_COLUMNS, PANEL_COLUMNS, is_plausible_fiscal_year,
    normalize_period_end, period_type_for,
)

def test_metric_columns_match_canonical_vocabulary():
    assert METRIC_COLUMNS[0] == "revenue"
    assert "operating_cash_flow" in METRIC_COLUMNS
    assert len(METRIC_COLUMNS) == 20

def test_panel_columns_lead_with_identity_then_metrics():
    assert PANEL_COLUMNS[:6] == [
        "spine_key", "market", "local_id", "period_end", "period_type", "fiscal_year",
    ]
    for m in METRIC_COLUMNS:
        assert m in PANEL_COLUMNS
    for c in ("currency", "fx_rate", "fx_asof", "source_artifact"):
        assert c in PANEL_COLUMNS

def test_period_type_annual_for_december_year_end():
    assert period_type_for("2024-12-31", cadence="annual") == "A"

def test_period_type_january_first_is_open():
    """CN year-start rows are restated opening balances, never a quarter."""
    assert period_type_for("2024-01-01", cadence="quarterly") == "OPEN"

def test_period_type_quarterly_marks_q_ends():
    for d in ("2024-03-31", "2024-06-30", "2024-09-30"):
        assert period_type_for(d, cadence="quarterly") == "Q"

def test_period_type_semiannual_marks_h():
    assert period_type_for("2024-06-30", cadence="semiannual") == "H"

def test_period_type_december_is_annual_even_when_cadence_quarterly():
    assert period_type_for("2024-12-31", cadence="quarterly") == "A"

def test_period_type_rejects_unknown_cadence():
    with pytest.raises(ValueError):
        period_type_for("2024-12-31", cadence="weekly")

def test_normalize_period_end_accepts_common_forms():
    assert normalize_period_end("20241231") == "2024-12-31"
    assert normalize_period_end("2024-12-31") == "2024-12-31"
    assert normalize_period_end("2024-12-31 00:00:00") == "2024-12-31"

def test_normalize_period_end_returns_none_on_junk():
    for bad in ("", None, "not-a-date", "430"):
        assert normalize_period_end(bad) is None

def test_plausible_fiscal_year_rejects_the_pse_outliers():
    """PSE emitted fiscal_year=430 on 2 rows."""
    assert is_plausible_fiscal_year(430) is False
    assert is_plausible_fiscal_year(2024) is True
    assert is_plausible_fiscal_year(1989) is False
    assert is_plausible_fiscal_year(None) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/panel/test_schema.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'panel'`

- [ ] **Step 3: Write minimal implementation**

```python
# panel/__init__.py
```

```python
# panel/schema.py
"""Panel row schema and period typing."""
from __future__ import annotations

import re
from datetime import datetime
from typing import Optional

METRIC_COLUMNS = [
    "revenue", "cost_of_revenue", "gross_profit", "profit_before_tax",
    "income_tax_expense", "net_income", "basic_eps", "cash_and_equivalents",
    "accounts_receivable", "inventories", "current_assets", "non_current_assets",
    "total_assets", "current_liabilities", "non_current_liabilities",
    "total_liabilities", "total_equity", "operating_cash_flow",
    "investing_cash_flow", "financing_cash_flow",
]

PANEL_COLUMNS = [
    "spine_key", "market", "local_id", "period_end", "period_type", "fiscal_year",
    "company_name", "currency", "fx_rate", "fx_asof", "source_artifact",
] + METRIC_COLUMNS

CADENCES = ("annual", "semiannual", "quarterly")
_QUARTER_ENDS = {"03-31", "06-30", "09-30"}


def normalize_period_end(value) -> Optional[str]:
    text = str(value or "").strip()
    if not text:
        return None
    text = text.split(" ")[0]
    if re.fullmatch(r"\d{8}", text):
        text = f"{text[:4]}-{text[4:6]}-{text[6:]}"
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return None
    try:
        datetime.strptime(text, "%Y-%m-%d")
    except ValueError:
        return None
    return text


def period_type_for(period_end: str, cadence: str) -> str:
    """Q/H/A/OPEN for a period end.

    A January 1st period end is an opening balance, never a quarter: CN
    publishes restated year-start balances that would otherwise double-count
    against the prior December close.
    """
    if cadence not in CADENCES:
        raise ValueError(f"unknown cadence: {cadence}")
    normalized = normalize_period_end(period_end)
    if normalized is None:
        raise ValueError(f"unusable period_end: {period_end!r}")
    md = normalized[5:]
    if md == "01-01":
        return "OPEN"
    if md == "12-31":
        return "A"
    if cadence == "quarterly" and md in _QUARTER_ENDS:
        return "Q"
    if cadence == "semiannual" and md == "06-30":
        return "H"
    return "A" if cadence == "annual" else ("H" if md == "06-30" else "Q")


def is_plausible_fiscal_year(year) -> bool:
    try:
        value = int(year)
    except (TypeError, ValueError):
        return False
    return 1990 <= value <= datetime.now().year + 1
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/panel/test_schema.py -q`
Expected: PASS (11 tests)

- [ ] **Step 5: Commit**

```bash
git add panel/__init__.py panel/schema.py tests/panel/test_schema.py
git commit -m "feat(panel): row schema and period typing (Q/H/A/OPEN)"
```

---

### Task 2: Spine — OpenFIGI resolution with cache and surrogate fallback

**Files:**
- Create: `panel/spine.py`
- Test: `tests/panel/test_spine.py`

**Interfaces:**
- Consumes: nothing from Task 1
- Produces: `surrogate_key(exchange, local_id) -> str`, `figi_request(market, local_id) -> dict`, `parse_figi_response(blocks, requests_sent) -> list[dict]`, `resolve(identifiers, fetcher=None, cache=None) -> dict[tuple[str, str], dict]`, `EXCH_CODES: dict[str, str]`

- [ ] **Step 1: Write the failing test**

```python
# tests/panel/test_spine.py
from panel.spine import (
    EXCH_CODES, figi_request, parse_figi_response, resolve, surrogate_key,
)

def test_surrogate_key_is_stable_and_namespaced():
    assert surrogate_key("AU", "BHP") == "AU:BHP"
    assert surrogate_key("au", " bhp ") == "AU:BHP"

def test_figi_request_uses_ticker_not_base_ticker():
    """BASE_TICKER additionally requires securityType2 and errors without it."""
    req = figi_request("au", "BHP")
    assert req == {"idType": "TICKER", "idValue": "BHP", "exchCode": "AU"}

def test_exch_codes_cover_v1_markets():
    for market in ("au", "tw", "ph", "kr", "cn", "in_bse", "hk", "jp"):
        assert market in EXCH_CODES

def test_parse_figi_response_maps_hits_to_requests():
    sent = [("au", "BHP"), ("tw", "2330")]
    blocks = [
        {"data": [{"figi": "BBG000D0D358", "name": "BHP GROUP LTD",
                   "exchCode": "AU", "securityType": "Common Stock",
                   "compositeFIGI": "BBG000D0D35X", "shareClassFIGI": "BBG001S5N8V8"}]},
        {"data": [{"figi": "BBG000BN2JD8", "name": "TAIWAN SEMICONDUCTOR",
                   "exchCode": "TT", "securityType": "Common Stock",
                   "compositeFIGI": None, "shareClassFIGI": None}]},
    ]
    got = parse_figi_response(blocks, sent)
    assert got[("au", "BHP")]["figi"] == "BBG000D0D358"
    assert got[("tw", "2330")]["name"] == "TAIWAN SEMICONDUCTOR"
    assert got[("au", "BHP")]["resolution_source"] == "openfigi"

def test_parse_figi_response_skips_misses():
    sent = [("au", "NOPE")]
    blocks = [{"warning": "No identifier found."}]
    assert parse_figi_response(blocks, sent) == {}

def test_parse_figi_response_tolerates_short_block_list():
    """A truncated response must not raise or misalign."""
    sent = [("au", "A"), ("au", "B")]
    blocks = [{"data": [{"figi": "F1", "name": "A LTD"}]}]
    got = parse_figi_response(blocks, sent)
    assert list(got) == [("au", "A")]

def test_resolve_falls_back_to_surrogate_when_unresolved():
    def fetcher(batch):
        return [{"warning": "No identifier found."} for _ in batch]
    got = resolve([("au", "OBSCURE")], fetcher=fetcher)
    entry = got[("au", "OBSCURE")]
    assert entry["spine_key"] == "AU:OBSCURE"
    assert entry["figi"] is None
    assert entry["resolution_source"] == "surrogate"

def test_resolve_uses_figi_as_spine_key_when_available():
    def fetcher(batch):
        return [{"data": [{"figi": "BBG1", "name": "X LTD", "exchCode": "AU"}]}]
    got = resolve([("au", "X")], fetcher=fetcher)
    assert got[("au", "X")]["spine_key"] == "BBG1"

def test_resolve_never_calls_fetcher_for_cached_identifiers():
    calls = []
    def fetcher(batch):
        calls.append(batch)
        return [{"warning": "no"} for _ in batch]
    cache = {("au", "BHP"): {"spine_key": "BBG000D0D358", "figi": "BBG000D0D358",
                             "resolution_source": "openfigi"}}
    got = resolve([("au", "BHP")], fetcher=fetcher, cache=cache)
    assert calls == []
    assert got[("au", "BHP")]["figi"] == "BBG000D0D358"

def test_resolve_batches_at_100():
    sizes = []
    def fetcher(batch):
        sizes.append(len(batch))
        return [{"warning": "no"} for _ in batch]
    resolve([("au", f"T{i}") for i in range(250)], fetcher=fetcher)
    assert sizes == [100, 100, 50]

def test_resolve_degrades_to_surrogate_when_fetcher_raises():
    """OpenFIGI being unavailable must not fail the build."""
    def fetcher(batch):
        raise RuntimeError("openfigi down")
    got = resolve([("au", "BHP")], fetcher=fetcher)
    assert got[("au", "BHP")]["resolution_source"] == "surrogate"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/panel/test_spine.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'panel.spine'`

- [ ] **Step 3: Write minimal implementation**

```python
# panel/spine.py
"""Securities master: market-local identifier -> FIGI, with surrogate fallback."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Callable, Dict, Iterable, List, Optional, Tuple

import requests

OPENFIGI_URL = "https://api.openfigi.com/v3/mapping"
BATCH_SIZE = 100

# OpenFIGI exchange codes per market.
EXCH_CODES = {
    "au": "AU", "tw": "TT", "ph": "PM", "kr": "KS",
    "cn": "CH", "in_bse": "IS", "hk": "HK", "jp": "JT",
}

Key = Tuple[str, str]


def surrogate_key(exchange: str, local_id: str) -> str:
    return f"{str(exchange).strip().upper()}:{str(local_id).strip().upper()}"


def figi_request(market: str, local_id: str) -> Dict[str, str]:
    return {
        "idType": "TICKER",
        "idValue": str(local_id).strip(),
        "exchCode": EXCH_CODES.get(market, market.upper()),
    }


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def parse_figi_response(blocks: List[dict], requests_sent: List[Key]) -> Dict[Key, dict]:
    out: Dict[Key, dict] = {}
    for key, block in zip(requests_sent, blocks or []):
        data = (block or {}).get("data") or []
        if not data:
            continue
        first = data[0]
        out[key] = {
            "figi": first.get("figi"),
            "name": first.get("name"),
            "exchange": first.get("exchCode"),
            "security_type": first.get("securityType"),
            "composite_figi": first.get("compositeFIGI"),
            "share_class_figi": first.get("shareClassFIGI"),
            "resolution_source": "openfigi",
            "resolved_at": _utc_now(),
        }
    return out


def _http_fetcher(batch: List[Key]) -> List[dict]:
    payload = [figi_request(m, i) for m, i in batch]
    resp = requests.post(
        OPENFIGI_URL, json=payload,
        headers={"Content-Type": "application/json"}, timeout=60,
    )
    resp.raise_for_status()
    return resp.json()


def resolve(
    identifiers: Iterable[Key],
    fetcher: Optional[Callable[[List[Key]], List[dict]]] = None,
    cache: Optional[Dict[Key, dict]] = None,
) -> Dict[Key, dict]:
    """Resolve identifiers, preferring cache, degrading to surrogate keys."""
    fetch = fetcher or _http_fetcher
    cache = dict(cache or {})
    out: Dict[Key, dict] = {}
    todo: List[Key] = []
    for key in identifiers:
        if key in cache:
            out[key] = cache[key]
        elif key not in todo:
            todo.append(key)

    for start in range(0, len(todo), BATCH_SIZE):
        batch = todo[start : start + BATCH_SIZE]
        try:
            blocks = fetch(batch)
            out.update(parse_figi_response(blocks, batch))
        except Exception:
            # OpenFIGI unavailable: fall through to surrogate keys below.
            pass

    for market, local_id in todo:
        key = (market, local_id)
        entry = out.get(key)
        if entry and entry.get("figi"):
            entry["spine_key"] = entry["figi"]
        else:
            out[key] = {
                "figi": None, "name": None,
                "exchange": EXCH_CODES.get(market, market.upper()),
                "security_type": None, "composite_figi": None,
                "share_class_figi": None,
                "resolution_source": "surrogate",
                "resolved_at": _utc_now(),
                "spine_key": surrogate_key(EXCH_CODES.get(market, market), local_id),
            }
    for key, entry in out.items():
        entry.setdefault("spine_key", surrogate_key(EXCH_CODES.get(key[0], key[0]), key[1]))
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/panel/test_spine.py -q`
Expected: PASS (11 tests)

- [ ] **Step 5: Commit**

```bash
git add panel/spine.py tests/panel/test_spine.py
git commit -m "feat(panel): OpenFIGI spine with cache and surrogate fallback"
```

---

### Task 3: Screening-input adapter (AU, TW, PH, KR)

**Files:**
- Create: `panel/adapters/__init__.py`, `panel/adapters/screening_input.py`
- Test: `tests/panel/test_adapter_screening_input.py`

**Interfaces:**
- Consumes: `panel.schema.METRIC_COLUMNS`, `normalize_period_end`, `period_type_for`, `is_plausible_fiscal_year`
- Produces: `SCREENING_COLUMN_MAP: dict[str, str]`, `rows_from_screening_csv(path, market, cadence="annual") -> list[dict]`

- [ ] **Step 1: Write the failing test**

```python
# tests/panel/test_adapter_screening_input.py
from panel.adapters.screening_input import (
    SCREENING_COLUMN_MAP, rows_from_screening_csv,
)

HEADER = ("Ticker,Symbol,Company Name,Exchange,Sector,Industry Group,Stock Style,"
          "Currency,bsns_year,Total Assets,Total Liabilities,Total Equity,"
          "Current Assets,Current Liabilities,Cash (Balance Sheet),Revenue,"
          "Operating Profit,Net Income\n")

def _write(tmp_path, body):
    p = tmp_path / "s.csv"
    p.write_text(HEADER + body)
    return p

def test_column_map_targets_canonical_metric_names():
    assert SCREENING_COLUMN_MAP["Total Assets"] == "total_assets"
    assert SCREENING_COLUMN_MAP["Cash (Balance Sheet)"] == "cash_and_equivalents"
    assert SCREENING_COLUMN_MAP["Revenue"] == "revenue"
    assert SCREENING_COLUMN_MAP["Net Income"] == "net_income"

def test_rows_carry_identity_and_metrics(tmp_path):
    p = _write(tmp_path, "BHP,BHP,BHP GROUP,ASX,Materials,,,USD,2024,100,40,60,50,20,10,200,30,25\n")
    rows = rows_from_screening_csv(p, market="au")
    assert len(rows) == 1
    r = rows[0]
    assert r["market"] == "au" and r["local_id"] == "BHP"
    assert r["company_name"] == "BHP GROUP"
    assert r["currency"] == "USD"
    assert r["fiscal_year"] == 2024
    assert r["period_end"] == "2024-12-31"
    assert r["period_type"] == "A"
    assert r["total_assets"] == 100.0
    assert r["cash_and_equivalents"] == 10.0
    assert r["net_income"] == 25.0

def test_implausible_fiscal_year_is_dropped(tmp_path):
    """PSE emitted bsns_year=430 on 2 rows."""
    p = _write(tmp_path, "FS,FS,X,PSE,,,,PHP,430,1,1,1,1,1,1,1,1,1\n")
    assert rows_from_screening_csv(p, market="ph") == []

def test_row_without_ticker_is_dropped(tmp_path):
    p = _write(tmp_path, ",,,PSE,,,,PHP,2024,1,1,1,1,1,1,1,1,1\n")
    assert rows_from_screening_csv(p, market="ph") == []

def test_blank_metric_becomes_none_not_zero(tmp_path):
    p = _write(tmp_path, "AAA,AAA,X,ASX,,,,AUD,2024,,40,60,50,20,10,200,30,25\n")
    assert rows_from_screening_csv(p, market="au")[0]["total_assets"] is None

def test_unparseable_metric_becomes_none(tmp_path):
    p = _write(tmp_path, "AAA,AAA,X,ASX,,,,AUD,2024,n/a,40,60,50,20,10,200,30,25\n")
    assert rows_from_screening_csv(p, market="au")[0]["total_assets"] is None

def test_source_artifact_records_provenance(tmp_path):
    p = _write(tmp_path, "AAA,AAA,X,ASX,,,,AUD,2024,1,1,1,1,1,1,1,1,1\n")
    assert str(p) in rows_from_screening_csv(p, market="au")[0]["source_artifact"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/panel/test_adapter_screening_input.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'panel.adapters'`

- [ ] **Step 3: Write minimal implementation**

```python
# panel/adapters/__init__.py
```

```python
# panel/adapters/screening_input.py
"""AU/TW/PH/KR adapter.

These four markets already share an identical *_screening_input.csv header, so
this adapter is a column mapping rather than extraction.
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path
from typing import Dict, List, Optional

from panel.schema import is_plausible_fiscal_year, period_type_for

SCREENING_COLUMN_MAP: Dict[str, str] = {
    "Total Assets": "total_assets",
    "Total Liabilities": "total_liabilities",
    "Total Equity": "total_equity",
    "Current Assets": "current_assets",
    "Current Liabilities": "current_liabilities",
    "Cash (Balance Sheet)": "cash_and_equivalents",
    "Revenue": "revenue",
    "Net Income": "net_income",
    "Accounts Receivable": "accounts_receivable",
    "Inventory": "inventories",
}


def _number(value) -> Optional[float]:
    text = str(value or "").strip().replace(",", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def rows_from_screening_csv(path, market: str, cadence: str = "annual") -> List[dict]:
    csv.field_size_limit(sys.maxsize)
    out: List[dict] = []
    with open(path, newline="", encoding="utf-8-sig", errors="replace") as fin:
        for raw in csv.DictReader(fin):
            ticker = str(raw.get("Ticker") or "").strip()
            year = raw.get("bsns_year")
            if not ticker or not is_plausible_fiscal_year(year):
                continue
            fiscal_year = int(year)
            period_end = f"{fiscal_year}-12-31"
            row = {
                "market": market,
                "local_id": ticker,
                "company_name": str(raw.get("Company Name") or "").strip() or None,
                "currency": str(raw.get("Currency") or "").strip() or None,
                "fiscal_year": fiscal_year,
                "period_end": period_end,
                "period_type": period_type_for(period_end, cadence=cadence),
                "source_artifact": str(Path(path)),
            }
            for src, dst in SCREENING_COLUMN_MAP.items():
                if src in raw:
                    row[dst] = _number(raw.get(src))
            out.append(row)
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/panel/test_adapter_screening_input.py -q`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add panel/adapters/__init__.py panel/adapters/screening_input.py tests/panel/test_adapter_screening_input.py
git commit -m "feat(panel): screening-input adapter for AU/TW/PH/KR"
```

---

### Task 4: CN adapter, including restated OPEN rows

**Files:**
- Create: `panel/adapters/cn.py`
- Test: `tests/panel/test_adapter_cn.py`

**Interfaces:**
- Consumes: `panel.schema.normalize_period_end`, `period_type_for`
- Produces: `CN_FIELD_MAP: dict[str, str]`, `rows_from_cn_frames(balance_sheet, income_statement=None, cash_flow=None) -> list[dict]`

CSMAR field codes used (verified present in `balance_sheet.parquet`): `A001000000` total assets, `A001100000` current assets, `A001101000` cash and equivalents. Income-statement and cash-flow codes are mapped the same way once their frames are passed; only fields present in the frame are emitted.

- [ ] **Step 1: Write the failing test**

```python
# tests/panel/test_adapter_cn.py
import pandas as pd
from panel.adapters.cn import CN_FIELD_MAP, rows_from_cn_frames

def _bs():
    return pd.DataFrame([
        {"Stkcd": "000001", "ShortName": "深发展A", "Accper": "2024-12-31",
         "Typrep": "A", "A001000000": 100.0, "A001100000": 60.0, "A001101000": 10.0},
        {"Stkcd": "000001", "ShortName": "深发展A", "Accper": "2024-03-31",
         "Typrep": "A", "A001000000": 90.0, "A001100000": 55.0, "A001101000": 9.0},
        {"Stkcd": "000001", "ShortName": "深发展A", "Accper": "2024-01-01",
         "Typrep": "A", "A001000000": 88.0, "A001100000": 50.0, "A001101000": 8.0},
    ])

def test_field_map_targets_canonical_names():
    assert CN_FIELD_MAP["A001000000"] == "total_assets"
    assert CN_FIELD_MAP["A001101000"] == "cash_and_equivalents"

def test_quarterly_and_annual_periods_are_typed():
    rows = {r["period_end"]: r for r in rows_from_cn_frames(_bs())}
    assert rows["2024-12-31"]["period_type"] == "A"
    assert rows["2024-03-31"]["period_type"] == "Q"

def test_january_first_rows_are_open_not_quarterly():
    """Restated opening balances: 149,269 such rows exist in the live corpus."""
    rows = {r["period_end"]: r for r in rows_from_cn_frames(_bs())}
    assert rows["2024-01-01"]["period_type"] == "OPEN"

def test_open_rows_are_retained_not_dropped():
    assert len(rows_from_cn_frames(_bs())) == 3

def test_identity_and_metrics_are_mapped():
    row = [r for r in rows_from_cn_frames(_bs()) if r["period_end"] == "2024-12-31"][0]
    assert row["market"] == "cn"
    assert row["local_id"] == "000001"
    assert row["company_name"] == "深发展A"
    assert row["currency"] == "CNY"
    assert row["fiscal_year"] == 2024
    assert row["total_assets"] == 100.0
    assert row["cash_and_equivalents"] == 10.0

def test_typrep_is_recorded_so_consolidated_and_parent_are_distinguishable():
    row = rows_from_cn_frames(_bs())[0]
    assert row["cn_typrep"] == "A"

def test_unusable_accper_is_dropped():
    bad = pd.DataFrame([{"Stkcd": "1", "ShortName": "x", "Accper": "junk",
                         "Typrep": "A", "A001000000": 1.0}])
    assert rows_from_cn_frames(bad) == []

def test_missing_optional_frames_are_fine():
    assert len(rows_from_cn_frames(_bs(), income_statement=None, cash_flow=None)) == 3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/panel/test_adapter_cn.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'panel.adapters.cn'`

- [ ] **Step 3: Write minimal implementation**

```python
# panel/adapters/cn.py
"""CN adapter over the CSMAR-style parquet trio.

CN is already period-level (`Accper`), including restated year-start opening
balances which are typed OPEN rather than mapped to a quarter.
"""
from __future__ import annotations

from typing import List, Optional

import pandas as pd

from panel.schema import normalize_period_end, period_type_for

CN_FIELD_MAP = {
    "A001000000": "total_assets",
    "A001100000": "current_assets",
    "A001101000": "cash_and_equivalents",
    "A001111000": "inventories",
    "A001110000": "accounts_receivable",
    "A002000000": "total_liabilities",
    "A002100000": "current_liabilities",
    "A003000000": "total_equity",
    "B001100000": "revenue",
    "B001200000": "cost_of_revenue",
    "B001000000": "profit_before_tax",
    "B002000000": "net_income",
    "C001000000": "operating_cash_flow",
    "C002000000": "investing_cash_flow",
    "C003000000": "financing_cash_flow",
}


def _merge(frames: List[Optional[pd.DataFrame]]) -> pd.DataFrame:
    present = [f for f in frames if f is not None and len(f)]
    if not present:
        return pd.DataFrame()
    merged = present[0]
    for extra in present[1:]:
        keys = [k for k in ("Stkcd", "Accper", "Typrep") if k in extra.columns]
        merged = merged.merge(extra, on=keys, how="outer", suffixes=("", "_dup"))
    return merged


def rows_from_cn_frames(
    balance_sheet: pd.DataFrame,
    income_statement: Optional[pd.DataFrame] = None,
    cash_flow: Optional[pd.DataFrame] = None,
) -> List[dict]:
    merged = _merge([balance_sheet, income_statement, cash_flow])
    out: List[dict] = []
    for record in merged.to_dict("records"):
        period_end = normalize_period_end(record.get("Accper"))
        if period_end is None:
            continue
        row = {
            "market": "cn",
            "local_id": str(record.get("Stkcd") or "").strip(),
            "company_name": record.get("ShortName") or None,
            "currency": "CNY",
            "fiscal_year": int(period_end[:4]),
            "period_end": period_end,
            "period_type": period_type_for(period_end, cadence="quarterly"),
            "cn_typrep": record.get("Typrep"),
            "source_artifact": "markets/cn/02_structured",
        }
        for code, name in CN_FIELD_MAP.items():
            if code in record:
                value = record.get(code)
                row[name] = None if pd.isna(value) else float(value)
        out.append(row)
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/panel/test_adapter_cn.py -q`
Expected: PASS (8 tests)

- [ ] **Step 5: Commit**

```bash
git add panel/adapters/cn.py tests/panel/test_adapter_cn.py
git commit -m "feat(panel): CN adapter with restated OPEN rows retained"
```

---

### Task 5: IN_BSE adapter

**Files:**
- Create: `panel/adapters/in_bse.py`
- Test: `tests/panel/test_adapter_in_bse.py`

**Interfaces:**
- Consumes: `panel.schema.METRIC_COLUMNS`, `is_plausible_fiscal_year`, `period_type_for`
- Produces: `rows_from_canonical_metrics(df, market, currency) -> list[dict]`

- [ ] **Step 1: Write the failing test**

```python
# tests/panel/test_adapter_in_bse.py
import pandas as pd
from panel.adapters.in_bse import rows_from_canonical_metrics

def _df():
    return pd.DataFrame([
        {"ticker": "533089", "filing_date": "2013", "fiscal_year": 2013,
         "doc_id": "5330890313", "n_metrics": 9, "revenue": 100.0,
         "total_assets": 500.0, "net_income": 12.0},
        {"ticker": "533089", "filing_date": "2014", "fiscal_year": 430,
         "doc_id": "5330890314", "n_metrics": 3, "revenue": 1.0},
    ])

def test_rows_map_identity_and_metrics():
    rows = rows_from_canonical_metrics(_df(), market="in_bse", currency="INR")
    assert len(rows) == 1
    r = rows[0]
    assert r["market"] == "in_bse" and r["local_id"] == "533089"
    assert r["currency"] == "INR"
    assert r["fiscal_year"] == 2013
    assert r["period_end"] == "2013-12-31"
    assert r["period_type"] == "A"
    assert r["revenue"] == 100.0 and r["total_assets"] == 500.0

def test_implausible_fiscal_year_is_dropped():
    rows = rows_from_canonical_metrics(_df(), market="in_bse", currency="INR")
    assert all(r["fiscal_year"] != 430 for r in rows)

def test_doc_id_is_recorded_for_traceability():
    rows = rows_from_canonical_metrics(_df(), market="in_bse", currency="INR")
    assert rows[0]["source_doc_id"] == "5330890313"

def test_duplicate_doc_ids_raise():
    """doc_id collisions would silently skip work at panel level."""
    df = pd.DataFrame([
        {"ticker": "1", "fiscal_year": 2013, "doc_id": "D", "revenue": 1.0},
        {"ticker": "1", "fiscal_year": 2014, "doc_id": "D", "revenue": 2.0},
    ])
    import pytest
    with pytest.raises(ValueError, match="duplicate doc_id"):
        rows_from_canonical_metrics(df, market="in_bse", currency="INR")

def test_missing_metric_columns_are_omitted_not_zeroed():
    rows = rows_from_canonical_metrics(_df(), market="in_bse", currency="INR")
    assert rows[0].get("inventories") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/panel/test_adapter_in_bse.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'panel.adapters.in_bse'`

- [ ] **Step 3: Write minimal implementation**

```python
# panel/adapters/in_bse.py
"""IN_BSE adapter over canonical_metrics_wide.parquet (annual only)."""
from __future__ import annotations

from typing import List

import pandas as pd

from panel.schema import METRIC_COLUMNS, is_plausible_fiscal_year, period_type_for


def rows_from_canonical_metrics(df: pd.DataFrame, market: str, currency: str) -> List[dict]:
    if "doc_id" in df.columns:
        ids = df["doc_id"].dropna()
        if ids.duplicated().any():
            dupes = sorted(set(ids[ids.duplicated()]))[:5]
            raise ValueError(f"duplicate doc_id in {market}: {dupes}")

    out: List[dict] = []
    for record in df.to_dict("records"):
        year = record.get("fiscal_year")
        ticker = str(record.get("ticker") or "").strip()
        if not ticker or not is_plausible_fiscal_year(year):
            continue
        fiscal_year = int(year)
        period_end = f"{fiscal_year}-12-31"
        row = {
            "market": market,
            "local_id": ticker,
            "company_name": None,
            "currency": currency,
            "fiscal_year": fiscal_year,
            "period_end": period_end,
            "period_type": period_type_for(period_end, cadence="annual"),
            "source_doc_id": record.get("doc_id"),
            "source_artifact": f"derived/{market.upper()}_FINANCIALS/canonical_metrics_wide.parquet",
        }
        for metric in METRIC_COLUMNS:
            if metric in record:
                value = record.get(metric)
                row[metric] = None if pd.isna(value) else float(value)
        out.append(row)
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/panel/test_adapter_in_bse.py -q`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add panel/adapters/in_bse.py tests/panel/test_adapter_in_bse.py
git commit -m "feat(panel): IN_BSE adapter with doc_id uniqueness assertion"
```

---

### Task 6: HK adapter — long facts to wide rows

**Files:**
- Create: `panel/adapters/hk.py`
- Test: `tests/panel/test_adapter_hk.py`

**Interfaces:**
- Consumes: `panel.schema.METRIC_COLUMNS`, `is_plausible_fiscal_year`, `period_type_for`
- Produces: `HK_METRIC_ALIASES: dict[str, str]`, `rows_from_hk_facts(records) -> list[dict]`, `iter_hk_fact_records(facts_root, limit=0) -> Iterator[dict]`

HK facts are tidy JSONL.gz records with keys `market, filing_id, filing_date, company_name, stock_code, title, document_url, section, metric, raw_label, value, value_index, fiscal_year, unit_text`. Observed metric vocabulary includes canonical names (`revenue`, `profit_before_tax`, `income_tax_expense`, `total_assets`, `total_equity`, `cash_and_equivalents`, `accounts_receivable`, `accounts_payable`) plus HK-specific ones (`profit_for_year`, `net_assets`, `finance_costs`, `operating_profit`).

- [ ] **Step 1: Write the failing test**

```python
# tests/panel/test_adapter_hk.py
import gzip, json
from panel.adapters.hk import (
    HK_METRIC_ALIASES, iter_hk_fact_records, rows_from_hk_facts,
)

def _rec(metric, value, fiscal_year=2012, code="00158", vi=0):
    return {"market": "HKEX", "filing_id": "1562416", "filing_date": "2013-01-02",
            "company_name": "MELBOURNE ENT", "stock_code": code,
            "title": "Annual Report 2012", "section": "income_statement",
            "metric": metric, "raw_label": metric, "value": value,
            "value_index": vi, "fiscal_year": fiscal_year, "unit_text": "HKD'000"}

def test_aliases_map_hk_names_onto_canonical():
    assert HK_METRIC_ALIASES["profit_for_year"] == "net_income"

def test_aliases_do_not_claim_net_assets_is_total_equity():
    """net_assets and total_equity are not interchangeable; do not alias."""
    assert HK_METRIC_ALIASES.get("net_assets") is None

def test_long_records_pivot_to_one_row_per_security_year():
    rows = rows_from_hk_facts([
        _rec("revenue", 100.0), _rec("total_assets", 500.0), _rec("profit_for_year", 12.0),
    ])
    assert len(rows) == 1
    r = rows[0]
    assert r["market"] == "hk" and r["local_id"] == "00158"
    assert r["fiscal_year"] == 2012
    assert r["period_end"] == "2012-12-31"
    assert r["period_type"] == "A"
    assert r["revenue"] == 100.0
    assert r["total_assets"] == 500.0
    assert r["net_income"] == 12.0

def test_separate_years_become_separate_rows():
    rows = rows_from_hk_facts([_rec("revenue", 1.0, 2012), _rec("revenue", 2.0, 2013)])
    assert {r["fiscal_year"] for r in rows} == {2012, 2013}

def test_first_value_index_wins_on_duplicates():
    """value_index orders repeated extractions; index 0 is the primary."""
    rows = rows_from_hk_facts([
        _rec("revenue", 99.0, vi=1), _rec("revenue", 100.0, vi=0),
    ])
    assert rows[0]["revenue"] == 100.0

def test_unknown_metrics_are_ignored_not_crashing():
    rows = rows_from_hk_facts([_rec("some_hk_only_line", 5.0), _rec("revenue", 1.0)])
    assert rows[0]["revenue"] == 1.0
    assert "some_hk_only_line" not in rows[0]

def test_implausible_fiscal_year_is_dropped():
    assert rows_from_hk_facts([_rec("revenue", 1.0, fiscal_year=430)]) == []

def test_non_numeric_values_are_skipped():
    rows = rows_from_hk_facts([_rec("revenue", "n/a"), _rec("total_assets", 5.0)])
    assert rows[0].get("revenue") is None
    assert rows[0]["total_assets"] == 5.0

def test_iter_reads_gzipped_jsonl(tmp_path):
    d = tmp_path / "20130102"
    d.mkdir()
    with gzip.open(d / "1562416.jsonl.gz", "wt", encoding="utf-8") as f:
        f.write(json.dumps(_rec("revenue", 1.0)) + "\n")
    got = list(iter_hk_fact_records(tmp_path))
    assert got[0]["metric"] == "revenue"

def test_iter_skips_unreadable_lines(tmp_path):
    d = tmp_path / "20130103"
    d.mkdir()
    with gzip.open(d / "x.jsonl.gz", "wt", encoding="utf-8") as f:
        f.write("{not json}\n")
        f.write(json.dumps(_rec("revenue", 2.0)) + "\n")
    got = list(iter_hk_fact_records(tmp_path))
    assert len(got) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/panel/test_adapter_hk.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'panel.adapters.hk'`

- [ ] **Step 3: Write minimal implementation**

```python
# panel/adapters/hk.py
"""HK adapter: tidy long facts -> wide panel rows.

HK stage-02 emits one JSONL.gz record per extracted line item, so this is a
pivot rather than extraction. `net_assets` is deliberately NOT aliased to
`total_equity` -- they are not interchangeable.
"""
from __future__ import annotations

import gzip
import json
from pathlib import Path
from typing import Dict, Iterator, List, Optional

from panel.schema import METRIC_COLUMNS, is_plausible_fiscal_year, period_type_for

HK_METRIC_ALIASES: Dict[str, str] = {
    "profit_for_year": "net_income",
    "profit_for_the_year": "net_income",
}


def _number(value) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def iter_hk_fact_records(facts_root, limit: int = 0) -> Iterator[dict]:
    count = 0
    for path in sorted(Path(facts_root).rglob("*.jsonl.gz")):
        try:
            with gzip.open(path, "rt", encoding="utf-8", errors="replace") as fin:
                for line in fin:
                    try:
                        yield json.loads(line)
                    except ValueError:
                        continue
                    count += 1
                    if limit and count >= limit:
                        return
        except OSError:
            continue


def rows_from_hk_facts(records) -> List[dict]:
    grouped: Dict[tuple, dict] = {}
    seen_index: Dict[tuple, int] = {}
    for rec in records:
        year = rec.get("fiscal_year")
        code = str(rec.get("stock_code") or "").strip()
        if not code or not is_plausible_fiscal_year(year):
            continue
        fiscal_year = int(year)
        key = (code, fiscal_year)
        row = grouped.get(key)
        if row is None:
            period_end = f"{fiscal_year}-12-31"
            row = {
                "market": "hk",
                "local_id": code,
                "company_name": rec.get("company_name") or None,
                "currency": "HKD",
                "fiscal_year": fiscal_year,
                "period_end": period_end,
                "period_type": period_type_for(period_end, cadence="annual"),
                "source_artifact": "markets/hk/02_structured/hkex_financials/facts",
            }
            grouped[key] = row

        metric = HK_METRIC_ALIASES.get(rec.get("metric"), rec.get("metric"))
        if metric not in METRIC_COLUMNS:
            continue
        value = _number(rec.get("value"))
        if value is None:
            row.setdefault(metric, None)
            continue
        index = int(rec.get("value_index") or 0)
        prior = seen_index.get((key, metric))
        if prior is None or index < prior:
            row[metric] = value
            seen_index[(key, metric)] = index
    return list(grouped.values())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/panel/test_adapter_hk.py -q`
Expected: PASS (10 tests)

- [ ] **Step 5: Commit**

```bash
git add panel/adapters/hk.py tests/panel/test_adapter_hk.py
git commit -m "feat(panel): HK adapter pivoting long facts to wide rows"
```

---

### Task 7: JP adapter over the EDINET duckdb

**Files:**
- Create: `panel/adapters/jp.py`
- Test: `tests/panel/test_adapter_jp.py`
- Modify: `requirements.txt` (add `duckdb`)

**Interfaces:**
- Consumes: `panel.schema.METRIC_COLUMNS`, `is_plausible_fiscal_year`, `normalize_period_end`, `period_type_for`
- Produces: `JP_COLUMN_MAP: dict[str, str]`, `rows_from_jp_frame(df) -> list[dict]`, `read_jp_wide(duckdb_path, limit=0) -> pandas.DataFrame`

`annual_metrics_wide` columns confirmed present: `doc_id, filing_date, fiscal_year, metric_period_end, company_name, stock_code, security_code, edinet_code, accounting_standards, has_consolidated_statements, assets, basic_eps, ...`

- [ ] **Step 1: Write the failing test**

```python
# tests/panel/test_adapter_jp.py
import pandas as pd
import pytest
from panel.adapters.jp import JP_COLUMN_MAP, rows_from_jp_frame

def _df():
    return pd.DataFrame([
        {"doc_id": "S10075EH", "filing_date": "2016-06-24", "fiscal_year": 2016,
         "metric_period_end": "2016-03-31", "company_name": "SUBARU CORP",
         "stock_code": "7270", "security_code": "72700", "edinet_code": "E02144",
         "accounting_standards": "JPGAAP", "has_consolidated_statements": True,
         "assets": 3000.0, "basic_eps": 55.0, "net_sales": 3300.0},
        {"doc_id": "BAD", "filing_date": "2016-06-24", "fiscal_year": 430,
         "metric_period_end": "junk", "company_name": "X", "stock_code": "1",
         "security_code": "1", "edinet_code": "E1", "accounting_standards": "JPGAAP",
         "has_consolidated_statements": True, "assets": 1.0},
    ])

def test_column_map_targets_canonical_names():
    assert JP_COLUMN_MAP["assets"] == "total_assets"
    assert JP_COLUMN_MAP["net_sales"] == "revenue"
    assert JP_COLUMN_MAP["basic_eps"] == "basic_eps"

def test_period_end_comes_from_metric_period_end_not_fiscal_year():
    """Japanese fiscal years commonly end 31 March; assuming 12-31 is wrong."""
    rows = rows_from_jp_frame(_df())
    assert rows[0]["period_end"] == "2016-03-31"

def test_march_year_end_is_typed_annual_not_quarterly():
    rows = rows_from_jp_frame(_df())
    assert rows[0]["period_type"] == "A"

def test_identity_and_metrics_are_mapped():
    r = rows_from_jp_frame(_df())[0]
    assert r["market"] == "jp"
    assert r["local_id"] == "7270"
    assert r["company_name"] == "SUBARU CORP"
    assert r["currency"] == "JPY"
    assert r["total_assets"] == 3000.0
    assert r["revenue"] == 3300.0
    assert r["basic_eps"] == 55.0

def test_edinet_and_standards_are_recorded():
    r = rows_from_jp_frame(_df())[0]
    assert r["jp_edinet_code"] == "E02144"
    assert r["jp_accounting_standards"] == "JPGAAP"

def test_rows_with_unusable_period_end_are_dropped():
    rows = rows_from_jp_frame(_df())
    assert len(rows) == 1
    assert all(r["local_id"] != "1" for r in rows)

def test_missing_stock_code_is_dropped():
    df = pd.DataFrame([{"doc_id": "D", "fiscal_year": 2016,
                        "metric_period_end": "2016-03-31", "stock_code": None,
                        "company_name": "X", "assets": 1.0}])
    assert rows_from_jp_frame(df) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/panel/test_adapter_jp.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'panel.adapters.jp'`

- [ ] **Step 3: Write minimal implementation**

```python
# panel/adapters/jp.py
"""JP adapter over markets/jp/02_structured/processed/edinet_xbrl/edinet_xbrl.duckdb.

`annual_metrics_wide` already holds one row per filing with a real
`metric_period_end`, so this is a column mapping. Japanese fiscal years commonly
end 31 March, so period_end must come from the data, never assumed as 12-31.
"""
from __future__ import annotations

from typing import Dict, List, Optional

import pandas as pd

from panel.schema import (
    METRIC_COLUMNS, is_plausible_fiscal_year, normalize_period_end, period_type_for,
)

JP_COLUMN_MAP: Dict[str, str] = {
    "assets": "total_assets",
    "liabilities": "total_liabilities",
    "net_assets": "total_equity",
    "net_sales": "revenue",
    "cost_of_sales": "cost_of_revenue",
    "gross_profit": "gross_profit",
    "operating_income": "profit_before_tax",
    "income_taxes": "income_tax_expense",
    "profit_loss": "net_income",
    "basic_eps": "basic_eps",
    "cash_and_deposits": "cash_and_equivalents",
    "inventories": "inventories",
    "current_assets": "current_assets",
    "current_liabilities": "current_liabilities",
    "operating_cash_flow": "operating_cash_flow",
    "investing_cash_flow": "investing_cash_flow",
    "financing_cash_flow": "financing_cash_flow",
}


def read_jp_wide(duckdb_path, limit: int = 0) -> pd.DataFrame:
    import duckdb

    query = "select * from annual_metrics_wide"
    if limit:
        query += f" limit {int(limit)}"
    con = duckdb.connect(str(duckdb_path), read_only=True)
    try:
        return con.execute(query).fetchdf()
    finally:
        con.close()


def _number(value) -> Optional[float]:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def rows_from_jp_frame(df: pd.DataFrame) -> List[dict]:
    out: List[dict] = []
    for record in df.to_dict("records"):
        code = str(record.get("stock_code") or "").strip()
        period_end = normalize_period_end(record.get("metric_period_end"))
        year = record.get("fiscal_year")
        if not code or code == "None" or period_end is None:
            continue
        if not is_plausible_fiscal_year(year):
            continue
        row = {
            "market": "jp",
            "local_id": code,
            "company_name": record.get("company_name") or None,
            "currency": "JPY",
            "fiscal_year": int(year),
            "period_end": period_end,
            "period_type": period_type_for(period_end, cadence="annual"),
            "jp_edinet_code": record.get("edinet_code"),
            "jp_accounting_standards": record.get("accounting_standards"),
            "source_doc_id": record.get("doc_id"),
            "source_artifact": "markets/jp/02_structured/processed/edinet_xbrl",
        }
        for src, dst in JP_COLUMN_MAP.items():
            if src in record and dst in METRIC_COLUMNS:
                row[dst] = _number(record.get(src))
        out.append(row)
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/panel/test_adapter_jp.py -q`
Expected: PASS (7 tests)

- [ ] **Step 5: Install duckdb into the venv and record it**

```bash
.venv/bin/python -m pip install duckdb
echo "duckdb" >> requirements.txt
.venv/bin/python -c "import duckdb; print(duckdb.__version__)"
```

- [ ] **Step 6: Verify against the live database**

```bash
.venv/bin/python -c "
from panel.adapters.jp import read_jp_wide, rows_from_jp_frame
p='/Volumes/OWC Express 1M2/datasets/markets/jp/02_structured/processed/edinet_xbrl/edinet_xbrl.duckdb'
df=read_jp_wide(p, limit=50)
rows=rows_from_jp_frame(df)
print('rows:', len(rows))
print('sample:', {k:v for k,v in rows[0].items() if v is not None})
"
```
Expected: non-zero row count and a sample carrying `period_end` and at least one metric. If `JP_COLUMN_MAP` keys do not match the real columns, print `list(df.columns)` and correct the map, then re-run Step 4.

- [ ] **Step 7: Commit**

```bash
git add panel/adapters/jp.py tests/panel/test_adapter_jp.py requirements.txt
git commit -m "feat(panel): JP adapter over EDINET duckdb annual_metrics_wide"
```

---

### Task 8: FX and USD conversion

**Files:**
- Create: `panel/fx.py`
- Test: `tests/panel/test_fx.py`

**Interfaces:**
- Consumes: `panel.schema.METRIC_COLUMNS`
- Produces: `FX_TICKERS: dict[str, str]`, `fetch_rates(currencies, fetcher=None) -> dict[str, float]`, `to_usd(row, rates, asof) -> dict`

- [ ] **Step 1: Write the failing test**

```python
# tests/panel/test_fx.py
import pytest
from panel.fx import FX_TICKERS, fetch_rates, to_usd

def test_fx_tickers_cover_panel_currencies():
    for cur in ("AUD", "TWD", "PHP", "KRW", "CNY", "INR", "HKD", "JPY"):
        assert cur in FX_TICKERS

def test_usd_is_identity_and_needs_no_lookup():
    rates = fetch_rates(["USD"], fetcher=lambda t: {})
    assert rates["USD"] == 1.0

def test_to_usd_multiplies_and_records_rate_and_asof():
    row = {"currency": "JPY", "total_assets": 1000.0, "revenue": None}
    out = to_usd(row, rates={"JPY": 0.0064}, asof="2026-08-17T00:00:00+00:00")
    assert out["total_assets_usd"] == pytest.approx(6.4)
    assert out["revenue_usd"] is None
    assert out["fx_rate"] == 0.0064
    assert out["fx_asof"] == "2026-08-17T00:00:00+00:00"

def test_to_usd_leaves_native_values_untouched():
    row = {"currency": "JPY", "total_assets": 1000.0}
    out = to_usd(row, rates={"JPY": 0.0064}, asof="x")
    assert out["total_assets"] == 1000.0

def test_missing_rate_yields_null_usd_and_null_rate_not_a_guess():
    row = {"currency": "XYZ", "total_assets": 10.0}
    out = to_usd(row, rates={}, asof="x")
    assert out["total_assets_usd"] is None
    assert out["fx_rate"] is None

def test_fetch_rates_skips_currencies_the_fetcher_cannot_price():
    rates = fetch_rates(["JPY", "XYZ"], fetcher=lambda t: {"JPY=X": 155.0})
    assert rates["JPY"] == pytest.approx(1 / 155.0)
    assert "XYZ" not in rates
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/panel/test_fx.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'panel.fx'`

- [ ] **Step 3: Write minimal implementation**

```python
# panel/fx.py
"""Live FX for panel snapshots.

Rates are fetched at snapshot time and recorded on every row, so a snapshot is
reproducible rather than silently re-rated on a later read.
"""
from __future__ import annotations

from typing import Callable, Dict, Iterable, Optional

from panel.schema import METRIC_COLUMNS

# yfinance-style tickers quote units of local currency per USD.
FX_TICKERS = {
    "AUD": "AUD=X", "TWD": "TWD=X", "PHP": "PHP=X", "KRW": "KRW=X",
    "CNY": "CNY=X", "INR": "INR=X", "HKD": "HKD=X", "JPY": "JPY=X",
    "EUR": "EUR=X", "GBP": "GBP=X",
}


def _yf_fetcher(tickers) -> Dict[str, float]:
    import yfinance as yf

    out: Dict[str, float] = {}
    for ticker in tickers:
        try:
            hist = yf.Ticker(ticker).history(period="1d")
            if len(hist):
                out[ticker] = float(hist["Close"].iloc[-1])
        except Exception:
            continue
    return out


def fetch_rates(
    currencies: Iterable[str],
    fetcher: Optional[Callable[[Iterable[str]], Dict[str, float]]] = None,
) -> Dict[str, float]:
    """currency -> USD per unit of that currency."""
    wanted = {c for c in currencies if c}
    rates: Dict[str, float] = {}
    if "USD" in wanted:
        rates["USD"] = 1.0
        wanted.discard("USD")
    tickers = {FX_TICKERS[c]: c for c in wanted if c in FX_TICKERS}
    if not tickers:
        return rates
    quotes = (fetcher or _yf_fetcher)(list(tickers))
    for ticker, value in (quotes or {}).items():
        currency = tickers.get(ticker)
        if currency and value:
            rates[currency] = 1.0 / float(value)
    return rates


def to_usd(row: dict, rates: Dict[str, float], asof: str) -> dict:
    out = dict(row)
    rate = rates.get(row.get("currency"))
    out["fx_rate"] = rate
    out["fx_asof"] = asof
    for metric in METRIC_COLUMNS:
        value = row.get(metric)
        out[f"{metric}_usd"] = (
            None if (rate is None or value is None) else float(value) * rate
        )
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/panel/test_fx.py -q`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add panel/fx.py tests/panel/test_fx.py
git commit -m "feat(panel): live FX with rate and asof recorded per row"
```

---

### Task 9: Quarterly view and panel invariants

**Files:**
- Create: `panel/views.py`
- Test: `tests/panel/test_views.py`, `tests/panel/test_invariants.py`

**Interfaces:**
- Consumes: `panel.schema.period_type_for`
- Produces: `quarterly_view(df, forward_fill=False) -> pandas.DataFrame`, `check_invariants(df) -> list[str]`

- [ ] **Step 1: Write the failing test**

```python
# tests/panel/test_views.py
import pandas as pd
from panel.views import quarterly_view

def _panel():
    return pd.DataFrame([
        {"spine_key": "B1", "period_end": "2024-03-31", "period_type": "Q", "revenue": 1.0},
        {"spine_key": "B1", "period_end": "2024-12-31", "period_type": "A", "revenue": 4.0},
        {"spine_key": "B1", "period_end": "2024-01-01", "period_type": "OPEN", "revenue": 9.0},
        {"spine_key": "B2", "period_end": "2024-06-30", "period_type": "H", "revenue": 2.0},
    ])

def test_open_rows_are_excluded_from_the_quarterly_view():
    """Opening balances would double-count against the prior close."""
    out = quarterly_view(_panel())
    assert "OPEN" not in set(out["period_type"])
    assert 9.0 not in set(out["revenue"])

def test_view_keeps_q_h_and_a_rows():
    out = quarterly_view(_panel())
    assert set(out["period_type"]) == {"Q", "A", "H"}

def test_view_does_not_forward_fill_by_default():
    out = quarterly_view(_panel())
    assert len(out) == 3

def test_view_is_sorted_by_security_then_period():
    out = quarterly_view(_panel())
    first = out.iloc[0]
    assert first["spine_key"] == "B1" and first["period_end"] == "2024-03-31"

def test_forward_fill_is_opt_in_and_flags_filled_rows():
    out = quarterly_view(_panel(), forward_fill=True)
    assert "is_filled" in out.columns
    assert out["is_filled"].any()
```

```python
# tests/panel/test_invariants.py
import pandas as pd
from panel.views import check_invariants

def _ok():
    return pd.DataFrame([
        {"spine_key": "B1", "period_end": "2024-12-31", "period_type": "A",
         "fiscal_year": 2024, "currency": "JPY", "fx_rate": 0.0064,
         "fx_asof": "2026-08-17T00:00:00+00:00", "revenue_usd": 1.0},
    ])

def test_clean_panel_reports_no_violations():
    assert check_invariants(_ok()) == []

def test_duplicate_security_period_is_flagged():
    df = pd.concat([_ok(), _ok()], ignore_index=True)
    assert any("duplicate" in v for v in check_invariants(df))

def test_period_type_inconsistent_with_period_end_is_flagged():
    df = _ok()
    df.loc[0, "period_type"] = "Q"   # 12-31 must be A
    assert any("period_type" in v for v in check_invariants(df))

def test_usd_value_without_fx_rate_is_flagged():
    df = _ok()
    df.loc[0, "fx_rate"] = None
    assert any("fx_rate" in v for v in check_invariants(df))

def test_implausible_fiscal_year_is_flagged():
    df = _ok()
    df.loc[0, "fiscal_year"] = 430
    assert any("fiscal_year" in v for v in check_invariants(df))

def test_open_period_type_is_allowed():
    df = _ok()
    df.loc[0, "period_end"] = "2024-01-01"
    df.loc[0, "period_type"] = "OPEN"
    assert check_invariants(df) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/panel/test_views.py tests/panel/test_invariants.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'panel.views'`

- [ ] **Step 3: Write minimal implementation**

```python
# panel/views.py
"""Views over the panel, plus invariant checks."""
from __future__ import annotations

from typing import List

import pandas as pd

from panel.schema import is_plausible_fiscal_year, period_type_for


def quarterly_view(df: pd.DataFrame, forward_fill: bool = False) -> pd.DataFrame:
    """Reporting-period view with opening balances removed.

    OPEN rows are restated opening balances and would double-count against the
    prior close, so they never appear here. Forward-filling is opt-in and
    flagged, never the default.
    """
    out = df[df["period_type"] != "OPEN"].copy()
    out = out.sort_values(["spine_key", "period_end"]).reset_index(drop=True)
    if not forward_fill:
        return out
    out["is_filled"] = False
    filled = []
    for _, group in out.groupby("spine_key", sort=False):
        g = group.sort_values("period_end").ffill()
        g["is_filled"] = group.isna().any(axis=1).values
        filled.append(g)
    return pd.concat(filled, ignore_index=True) if filled else out


def check_invariants(df: pd.DataFrame) -> List[str]:
    violations: List[str] = []
    if df.empty:
        return violations

    keys = ["spine_key", "period_end", "period_type"]
    if all(k in df.columns for k in keys):
        dupes = df.duplicated(subset=keys).sum()
        if dupes:
            violations.append(f"{dupes} duplicate (spine_key, period_end, period_type) rows")

    for _, row in df.iterrows():
        pe, pt = row.get("period_end"), row.get("period_type")
        if pe and pt:
            cadence = "quarterly" if pt in ("Q", "OPEN") else (
                "semiannual" if pt == "H" else "annual"
            )
            try:
                expected = period_type_for(pe, cadence=cadence)
            except ValueError:
                violations.append(f"unusable period_end {pe!r}")
                continue
            if expected != pt:
                violations.append(f"period_type {pt} inconsistent with period_end {pe}")
        if not is_plausible_fiscal_year(row.get("fiscal_year")):
            violations.append(f"implausible fiscal_year {row.get('fiscal_year')!r}")
        usd_cols = [c for c in df.columns if c.endswith("_usd")]
        if any(row.get(c) is not None and not pd.isna(row.get(c)) for c in usd_cols):
            if row.get("fx_rate") is None or pd.isna(row.get("fx_rate")):
                violations.append("USD value present without fx_rate")
    return violations
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/panel/test_views.py tests/panel/test_invariants.py -q`
Expected: PASS (11 tests)

- [ ] **Step 5: Commit**

```bash
git add panel/views.py tests/panel/test_views.py tests/panel/test_invariants.py
git commit -m "feat(panel): quarterly view excluding OPEN rows, plus invariants"
```

---

### Task 10: Build orchestrator and CLI

**Files:**
- Create: `panel/build.py`
- Test: `tests/panel/test_build.py`

**Interfaces:**
- Consumes: every adapter, `panel.spine.resolve`, `panel.fx.fetch_rates`, `panel.fx.to_usd`, `panel.views.check_invariants`, `panel.schema.PANEL_COLUMNS`
- Produces: `MARKET_SOURCES: dict[str, dict]`, `attach_spine(rows, resolved) -> list[dict]`, `write_panel(rows, out_root) -> pathlib.Path`, `main() -> int`

- [ ] **Step 1: Write the failing test**

```python
# tests/panel/test_build.py
import pandas as pd
from panel.build import MARKET_SOURCES, attach_spine, write_panel

def test_market_sources_cover_v1_adapters_and_exclude_us():
    assert set(MARKET_SOURCES) == {"au", "tw", "ph", "kr", "cn", "in_bse", "hk", "jp"}
    assert "us" not in MARKET_SOURCES

def test_attach_spine_sets_spine_key_from_resolution():
    rows = [{"market": "au", "local_id": "BHP", "period_end": "2024-12-31"}]
    resolved = {("au", "BHP"): {"spine_key": "BBG000D0D358", "figi": "BBG000D0D358",
                                "resolution_source": "openfigi", "name": "BHP GROUP LTD"}}
    out = attach_spine(rows, resolved)
    assert out[0]["spine_key"] == "BBG000D0D358"
    assert out[0]["figi"] == "BBG000D0D358"

def test_attach_spine_keeps_rows_whose_identifier_is_unresolved():
    """Dropping unresolved identifiers would silently shrink the panel."""
    rows = [{"market": "au", "local_id": "OBSCURE", "period_end": "2024-12-31"}]
    out = attach_spine(rows, {})
    assert len(out) == 1
    assert out[0]["spine_key"] == "AU:OBSCURE"
    assert out[0]["resolution_source"] == "unresolved"

def test_attach_spine_prefers_adapter_company_name_over_figi_name():
    rows = [{"market": "au", "local_id": "BHP", "company_name": "BHP GROUP"}]
    resolved = {("au", "BHP"): {"spine_key": "B1", "name": "BHP GROUP LTD",
                                "resolution_source": "openfigi"}}
    assert attach_spine(rows, resolved)[0]["company_name"] == "BHP GROUP"

def test_write_panel_creates_parquet_with_all_schema_columns(tmp_path):
    rows = [{"spine_key": "B1", "market": "au", "local_id": "BHP",
             "period_end": "2024-12-31", "period_type": "A", "fiscal_year": 2024,
             "currency": "AUD", "revenue": 1.0}]
    path = write_panel(rows, tmp_path)
    df = pd.read_parquet(path)
    assert len(df) == 1
    for col in ("spine_key", "period_type", "total_assets", "fx_rate"):
        assert col in df.columns

def test_write_panel_is_atomic_leaving_no_tmp_file(tmp_path):
    rows = [{"spine_key": "B1", "market": "au", "local_id": "X",
             "period_end": "2024-12-31", "period_type": "A", "fiscal_year": 2024}]
    write_panel(rows, tmp_path)
    assert list(tmp_path.rglob("*.tmp")) == []

def test_write_panel_on_empty_rows_returns_none(tmp_path):
    assert write_panel([], tmp_path) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/panel/test_build.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'panel.build'`

- [ ] **Step 3: Write minimal implementation**

```python
# panel/build.py
"""Assemble the panel from every v1 market adapter."""
from __future__ import annotations

import argparse
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

from panel.fx import fetch_rates, to_usd
from panel.schema import PANEL_COLUMNS
from panel.spine import EXCH_CODES, resolve, surrogate_key
from panel.views import check_invariants

DATA_ROOT = Path("/Volumes/OWC Express 1M2/datasets")
DEFAULT_OUT = Path("/Users/lichenyu/datasets/panel")

MARKET_SOURCES: Dict[str, dict] = {
    "au": {"kind": "screening", "currency": "AUD",
           "path": DATA_ROOT / "MARKET_FILINGS/derived/ASX_FINANCIALS/au_screening_input.csv"},
    "tw": {"kind": "screening", "currency": "TWD",
           "path": DATA_ROOT / "MARKET_FILINGS/derived/TWSE_FINANCIALS/tw_screening_input.csv"},
    "ph": {"kind": "screening", "currency": "PHP",
           "path": DATA_ROOT / "MARKET_FILINGS/derived/PSE_FINANCIALS/ph_screening_input.csv"},
    "kr": {"kind": "screening", "currency": "KRW",
           "path": DATA_ROOT / "MARKET_FILINGS/derived/DART_FINANCIALS/kr_screening_input.csv"},
    "cn": {"kind": "cn", "currency": "CNY",
           "path": DATA_ROOT / "markets/cn/02_structured"},
    "in_bse": {"kind": "canonical", "currency": "INR",
               "path": DATA_ROOT / "MARKET_FILINGS/derived/IN_BSE_FINANCIALS/canonical_metrics_wide.parquet"},
    "hk": {"kind": "hk", "currency": "HKD",
           "path": DATA_ROOT / "markets/hk/02_structured/hkex_financials/facts"},
    "jp": {"kind": "jp", "currency": "JPY",
           "path": DATA_ROOT / "markets/jp/02_structured/processed/edinet_xbrl/edinet_xbrl.duckdb"},
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def attach_spine(rows: List[dict], resolved: Dict[tuple, dict]) -> List[dict]:
    out = []
    for row in rows:
        key = (row.get("market"), row.get("local_id"))
        entry = resolved.get(key)
        merged = dict(row)
        if entry:
            merged["spine_key"] = entry.get("spine_key")
            merged["figi"] = entry.get("figi")
            merged["resolution_source"] = entry.get("resolution_source")
            if not merged.get("company_name"):
                merged["company_name"] = entry.get("name")
        else:
            merged["spine_key"] = surrogate_key(
                EXCH_CODES.get(key[0], key[0] or ""), key[1] or ""
            )
            merged["figi"] = None
            merged["resolution_source"] = "unresolved"
        out.append(merged)
    return out


def write_panel(rows: List[dict], out_root) -> Optional[Path]:
    if not rows:
        return None
    df = pd.DataFrame(rows)
    for col in PANEL_COLUMNS:
        if col not in df.columns:
            df[col] = None
    ordered = PANEL_COLUMNS + [c for c in df.columns if c not in PANEL_COLUMNS]
    df = df[ordered]
    out_root = Path(out_root)
    out_root.mkdir(parents=True, exist_ok=True)
    final = out_root / "panel.parquet"
    tmp = out_root / "panel.parquet.tmp"
    df.to_parquet(tmp, index=False)
    os.replace(tmp, final)
    return final


def load_market(market: str, limit: int = 0) -> List[dict]:
    cfg = MARKET_SOURCES[market]
    kind, path, currency = cfg["kind"], cfg["path"], cfg["currency"]
    if kind == "screening":
        from panel.adapters.screening_input import rows_from_screening_csv
        return rows_from_screening_csv(path, market=market)
    if kind == "canonical":
        from panel.adapters.in_bse import rows_from_canonical_metrics
        return rows_from_canonical_metrics(pd.read_parquet(path), market, currency)
    if kind == "cn":
        from panel.adapters.cn import rows_from_cn_frames
        bs = pd.read_parquet(Path(path) / "balance_sheet.parquet")
        inc = pd.read_parquet(Path(path) / "income_statement.parquet")
        cf = pd.read_parquet(Path(path) / "cash_flow_direct.parquet")
        if limit:
            bs, inc, cf = bs.head(limit), inc.head(limit), cf.head(limit)
        return rows_from_cn_frames(bs, inc, cf)
    if kind == "hk":
        from panel.adapters.hk import iter_hk_fact_records, rows_from_hk_facts
        return rows_from_hk_facts(iter_hk_fact_records(path, limit=limit))
    if kind == "jp":
        from panel.adapters.jp import read_jp_wide, rows_from_jp_frame
        return rows_from_jp_frame(read_jp_wide(path, limit=limit))
    raise ValueError(f"unknown source kind: {kind}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--markets", nargs="*", default=sorted(MARKET_SOURCES))
    parser.add_argument("--out-root", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--no-fx", action="store_true")
    args = parser.parse_args()

    rows: List[dict] = []
    for market in args.markets:
        try:
            got = load_market(market, limit=args.limit)
            print(f"{market}: {len(got)} rows", flush=True)
            rows.extend(got)
        except Exception as exc:
            print(f"{market}: FAILED ({type(exc).__name__}: {exc})", flush=True)

    identifiers = sorted({(r["market"], r["local_id"]) for r in rows})
    print(f"resolving {len(identifiers)} identifiers", flush=True)
    resolved = resolve(identifiers)
    rows = attach_spine(rows, resolved)

    if not args.no_fx:
        asof = _utc_now()
        rates = fetch_rates({r.get("currency") for r in rows})
        print(f"fx rates: {rates}", flush=True)
        rows = [to_usd(r, rates, asof) for r in rows]

    path = write_panel(rows, args.out_root)
    print(f"wrote {path} ({len(rows)} rows)", flush=True)

    violations = check_invariants(pd.DataFrame(rows))
    if violations:
        print(f"INVARIANT VIOLATIONS ({len(violations)}):", flush=True)
        for v in violations[:20]:
            print(f"  - {v}", flush=True)
    else:
        print("invariants: clean", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/panel/test_build.py -q`
Expected: PASS (7 tests)

- [ ] **Step 5: Run the whole panel test suite**

Run: `.venv/bin/python -m pytest tests/panel/ -q`
Expected: PASS (≈72 tests)

- [ ] **Step 6: Smoke-build on limited data**

```bash
.venv/bin/python -m panel.build --markets au tw ph jp --limit 200 --no-fx --out-root /tmp/panel_smoke
```
Expected: per-market row counts, an identifier-resolution line, a written parquet, and an invariants verdict. Investigate any market printing `FAILED` before proceeding.

- [ ] **Step 7: Full build**

```bash
.venv/bin/python -m panel.build --out-root /Users/lichenyu/datasets/panel 2>&1 | tail -30
```
Expected: all eight markets reporting counts, FX rates printed, parquet written. CN dominates row count.

- [ ] **Step 8: Commit**

```bash
git add panel/build.py tests/panel/test_build.py
git commit -m "feat(panel): build orchestrator, CLI, and full-panel assembly"
```

---

## Self-Review

**Spec coverage:**
- §3.1 spine → Task 2
- §3.2 adapters (au/tw/ph/kr, cn, in_bse, hk, jp) → Tasks 3, 4, 5, 6, 7
- §3.3 panel, currency, quarterly view → Tasks 8, 9, 10
- §5.1 PSE fiscal_year rescue → Task 3 (`is_plausible_fiscal_year`), Task 1
- §5.2 CN OPEN rows → Tasks 1, 4, 9
- §5.3 IN_BSE doc_id uniqueness → Task 5
- §6 error handling → Task 2 (fetcher degradation), Task 10 (per-market failure isolation, atomic write)
- §7 testing → every task, plus Task 9 invariants
- §10 US excluded → Task 10 (`MARKET_SOURCES` test asserts absence)

**Placeholder scan:** none — every step carries runnable code or an exact command.

**Type consistency:** `spine_key` is a str everywhere; `resolve()` returns `dict[tuple[str,str], dict]` consumed by `attach_spine`; `period_type_for` is the single source of Q/H/A/OPEN and is reused by `check_invariants`; `METRIC_COLUMNS` is defined once in Task 1 and imported by Tasks 3, 5, 6, 7, 8.

**Known risk carried into execution:** `JP_COLUMN_MAP` and `CN_FIELD_MAP` are written from observed column names but only two CN codes (`A001000000`, `A001100000`, `A001101000`) and two JP columns (`assets`, `basic_eps`) were directly verified. Task 7 Step 6 verifies JP against the live database and corrects the map; do the equivalent for CN in Task 4 by printing `list(bs.columns)` if metric coverage looks sparse.
