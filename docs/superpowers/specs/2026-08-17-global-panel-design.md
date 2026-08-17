# Global Listing Spine + Fundamentals Panel — Design

Date: 2026-08-17
Status: design approved; implementation plan not yet written
Scope: a global securities master ("spine") and a fundamentals panel assembled
from every market corpus that has structured (stage-02) output.

## 1. Why this exists

Everything built so far acquires *documents* — PDFs, zips, iXBRL packages, one
market at a time. What is missing is a **panel**: one row per security per
reporting period, globally, with fundamentals attached, so a question like
"show me every listing whose gross margin fell three periods running" can be
answered without re-deriving anything.

The raw material largely exists. What does not exist is a **spine** to join it.

### Current state, measured 2026-08-17

Structured outputs and their identifiers:

| Market | Artifact | Identifier | Rows | Notes |
|---|---|---|---|---|
| AU (ASX) | `canonical_metrics_wide.parquet` | `ticker` (e.g. `SM1`) | 27,665 | 1,760 distinct tickers, FY2007-2025 |
| TW (TWSE) | same | numeric code (`8227`) | 1,304 | 918 tickers |
| PH (PSE) | same | `ticker` (`FS`) | 447 | 149 tickers |
| IN (BSE) | same | scrip code (`533089`) | growing | sweep running, 67,993 targets |
| KR (DART) | `three_statement_parquet` + facts | `Ticker` | 9,335 filings | stage-02 complete |
| CN | `balance_sheet/income_statement/cash_flow_direct.parquet` | `Stkcd` + `Accper` | 691,480 | 5,956 companies, 1990-2026, genuinely quarterly |
| HK | `hkex_financials/{facts,summaries,text}` | per-filing | 25,658 docs | needs adapter |
| JP | `edinet_xbrl` | per-filing | 37,668 docs | needs adapter |
| US | `processed/market_data` | — | 13 GB | **not fundamentals** — land/lodging/real_estate market data; out of v1 (§10) |
| EU (ESEF) | packages only | **LEI** | 15,875 | no stage-02 yet |
| IE/DE (EDGAR) | documents only | **CIK** | 407 + running | no stage-02 yet |

**The useful discovery:** AU, TW, PH and KR already share an *identical* header
in their `*_screening_input.csv` files:

```
Ticker,Symbol,Company Name,Exchange,Sector,Industry Group,Stock Style,
Currency,bsns_year,Total Assets,Total Liabilities,...
```

So four adapters are thin mappings rather than new extraction work.

**Identity problem:** every corpus keys on a market-local identifier and there
is no crosswalk. ESEF keys on LEI, EDGAR on CIK, everything else on a local
ticker or scrip code.

## 2. Decisions taken

**Identity backbone: OpenFIGI.** Verified working 2026-08-17 with no API key:
`BHP`/`AU` → `BBG000D0D358 BHP GROUP LTD`, `2330`/`TT` → TSMC. FIGI is built for
exactly this mapping, covers every exchange in scope, and returns name,
security type and a share-class grouping that lets a dual listing or ADR be
tied to its home line. Rejected alternatives: an `(exchange, ticker)` surrogate
(cannot unify dual listings) and GLEIF LEI (thin coverage for Asian small-caps,
which is where most of the documents are).

**Frequency: native, with a period label.** One row per
`(spine_key, period_end, period_type)` where `period_type ∈ {Q, H, A}`. No
forward-filling in storage; a quarterly grid is a *view function* over the
panel. Rejected a forced forward-filled quarterly grid because it fabricates
data points for semi-annual European filers and makes as-of correctness easy to
get wrong.

**Scope: every market with structured output** — AU, TW, PH, KR, CN, IN, plus
HK and JP behind per-market adapters. US was investigated and dropped (§10):
its `02_structured` output is real-estate market data, not fundamentals.

## 3. Architecture

Three layers, each independently testable and independently runnable.

### 3.1 Spine — securities master

`(market, local_id)` → `figi, name, exchange, currency, security_type,
share_class_figi, resolved_at, resolution_source`.

- Distinct identifiers are collected from every adapter's output, resolved
  through OpenFIGI **once**, and cached to parquet + SQLite. Resolution is
  never repeated for a known identifier, so the build is offline-repeatable.
- **Unresolved identifiers keep a surrogate key `{EXCH}:{TICKER}` and are
  flagged.** With 2,829 tickers today and small-caps throughout, partial
  resolution is certain; losing those rows would silently shrink the panel.
- OpenFIGI quirk to handle: `BASE_TICKER` requires `securityType2`, while
  `TICKER` does not. Use `TICKER` with `exchCode` as the primary path.
- Rate limits: batch 100 identifiers per request, respect the documented
  unauthenticated limit, and cache aggressively.

### 3.2 Adapters — one per market

Signature: stage-02 artifact → iterable of normalized rows
`(market, local_id, period_end, period_type, fiscal_year, currency, <metrics>)`.

| Adapter | Source | Effort |
|---|---|---|
| `au`, `tw`, `ph`, `kr` | `*_screening_input.csv` (shared header) | thin mapping |
| `cn` | `balance_sheet` / `income_statement` / `cash_flow_direct` parquet, keyed `Stkcd` + `Accper` | moderate; already period-level |
| `in_bse` | `canonical_metrics_wide.parquet` | thin, annual only |
| `hk` | `hkex_financials/facts` | real work |
| `jp` | `edinet_xbrl` | real work |

Each adapter is runnable alone so a broken market never blocks the panel.

### 3.3 Panel — the snapshot

Parquet partitioned by `market` and period, one row per
`(spine_key, period_end, period_type)`. Metric columns follow the existing
`CANONICAL_METRICS` vocabulary so stage-02 semantics carry through unchanged.

**Currency:** store native values *and* a USD column computed with live FX at
snapshot time, recording the rate used and its timestamp. This follows the
live-FX guardrail in CLAUDE.md and makes a snapshot reproducible rather than
silently re-rated on later reads.

**Quarterly view:** a function over the panel, not a stored table.

## 4. Data flow

```
stage-02 artifacts
      |
      v
  adapter (per market)  -->  normalized rows
      |
      v
  spine resolve (cached OpenFIGI)  -->  spine_key
      |
      v
  panel parquet (market/period partitions)
      |
      +--> quarterly view function
      +--> panel invariants test suite
```

## 5. Data-quality findings — both investigated, both rescuable

### 5.1 PSE implausible fiscal_year — 2 rows, not a broken extractor

Investigated 2026-08-17. **Exactly 2 rows of 447** carry `fiscal_year=430`; both
have `filing_date=20250502` and one has `ticker=None`. An earlier
characterisation of this as "year extraction is broken for some filings" was
technically true but made two outliers sound systemic.

Rescue: re-derive `fiscal_year` from `filing_date`, which is present and valid.
Drop the `ticker=None` row, which is unusable regardless of its year. The
adapter still asserts plausibility (1990 ≤ year ≤ current+1) so a future
regression is caught rather than flowing into the panel.

### 5.2 CN `Accper` `01-01` rows — restated opening balances, and a capability

149,269 rows carry month-day `01-01`, spanning 1994-2025 across both `Typrep`
A and B, and **148,459 of them carry values**. They are not placeholders.

Comparing company `000001` year-start rows against the prior year's closing
balance sheet (`A001000000`, total assets):

| Opening | Prior close | Result |
|---|---|---|
| 1999-01-01: 39,008,042,523 | 1998-12-31: 39,399,858,617 | differs |
| 2000-01-01: 43,912,394,151 | 1999-12-31: 45,868,972,050 | differs |
| 2001-01-01: 66,006,167,607 | 2000-12-31: 67,227,499,769 | differs |
| 2002-01-01: 120,126,983,351 | 2001-12-31: 120,126,983,351 | identical |
| 2003-01-01: 166,166,379,400 | 2002-12-31: 166,166,379,400 | identical |
| 2004-01-01: 193,453,415,330 | 2003-12-31: 192,851,003,723 | differs |

Some match exactly, most differ slightly. That is the signature of
**restatement**: the opening balance for year Y carries the prior year's close
*as restated*, which diverges from what was originally reported.

Handling: emit these as `period_type='OPEN'` — retained, labelled, and
**excluded from the default period grid** so they never double-count against a
closing balance. Their real value is that comparing an `OPEN` row against the
prior `A` row yields a restatement delta.

This **moves point-in-time restatement history from out-of-scope into a
by-product for CN** (see §9), because the data was already there.

### 5.3 IN_BSE `doc_id` uniqueness

Depends on filename stems being unique per scrip/year; already covered by a
sweep test, but the adapter should assert it again at panel level.

## 6. Error handling

- An adapter row that fails parsing is dropped with a counted reason, never
  silently skipped.
- A market whose adapter raises is excluded from that build with a loud log; the
  panel still builds for the others.
- OpenFIGI unavailability degrades to surrogate keys rather than failing the
  build.
- Panel writes are atomic (temp then move), matching the existing download and
  state-file conventions.

## 7. Testing

- **Per-adapter fixture tests**: a handful of real rows per market, asserting
  identifier, period_end, period_type and at least two metric mappings.
- **Spine resolution**: stubbed OpenFIGI covering a hit, a miss (surrogate key
  path), a batch, and the `BASE_TICKER`/`securityType2` error.
- **Panel invariants suite**:
  - no duplicate `(spine_key, period_end, period_type)`
  - `period_type` consistent with `period_end` month
  - every USD figure carries an FX rate and timestamp
  - per-market row counts reconcile to adapter output
  - no implausible `fiscal_year` reaches the panel

## 8. Scale

~1–2M panel rows total (CN dominates at 691k balance-sheet rows alone). Trivial
in parquet. The cost of this project is adapter correctness, not volume.

## 9. Explicitly out of scope for v1 (YAGNI)

- Forward-filled quarterly storage
- Derived ratios beyond what stage-02 already computes
- ESEF and EDGAR fundamentals: those corpora have documents but no stage-02
  output, so they need extraction first — a separate project
- Point-in-time restatement history in general — **except** for CN, where the
  `OPEN` rows described in §5.2 give a restated-vs-originally-reported delta as a
  by-product. Retained and labelled in v1; a full cross-market restatement model
  is a separate project.
- Any UI

## 10. Resolved: US drops out of v1

Investigated 2026-08-17. `markets/us/02_structured/processed/market_data` holds
13 GB under `land/`, `lodging/`, `real_estate/` — alternative/real-estate market
data, **not company fundamentals**. The only US parquet artifacts are NLP-layer
outputs under `04_nlp/` (`cik_sector_mapping`, `reconciliation_v2`,
`operating_drivers_v2`), which are derived signals rather than 3-statement data.

So there is no US fundamentals artifact to adapt. The US enters the panel by the
same route as ESEF and EDGAR — extraction first — and is **out of v1**. The
`us` adapter is removed from the §3.2 table.

Revised v1 adapter set: `au`, `tw`, `ph`, `kr`, `cn`, `in_bse`, `hk`, `jp`.
