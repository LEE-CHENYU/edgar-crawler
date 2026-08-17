# Corpus Pipeline — Field Practices

Hard-won rules for acquiring, processing and panelising market-filing corpora.
Every entry below cost real debugging; each names the incident that produced it
so you can judge whether your situation matches.

Stages: `01_raw` (documents) → `02_structured` (facts/metrics) →
`03_gates` (screening) → `04_nlp` (chunks/embeddings) → `05_ner`.

---

## 1. Acquisition

### 1.1 "Complete" must mean acquired, not discovered

A backfill has two phases — discovery (find the filings) and acquisition
(download them). Define completion on the second.

> **Incident.** The annual-financials queue reported `complete=true` for TWSE
> for four weeks. It had discovered 25,978 filings and downloaded 4,044 — 16%.
> `try_download_binary` swallowed the exception, the row was written with an
> empty `local_path`, and the cursor advanced regardless. DART was at 66% for
> the same reason.

**Practice:** a market is complete when `rows with local_path == rows
discovered`. Surface that ratio in every status output. `scripts/corpus_ledger.py`
and `market_filings_snapshot.py` both do this now.

### 1.2 Every rate needs its denominator checked

> **Incident, twice in one session.** "17,801 identifiers resolved" was the
> count *submitted*, printed by a log line; the true figure was 0 resolved of
> 767,541. Later, "99.7% scale recovery" was 99.7% of the 18% of filings that
> had text — 18.0% overall.

**Practice:** before quoting a percentage, print numerator *and* denominator and
ask what population the denominator represents. A rate without its base is a
claim, not a measurement.

### 1.3 Size a gap by what is *fetchable*, not by catalogue arithmetic

> **Incident.** filings.xbrl.org listed 25,675 filings against 15,875 held, so
> ~9,800 looked missing (~160 GB). The real gap was 127. Of the delta, 9,770
> entries had `package_url: "None"` — viewer-only records that can never be
> downloaded. The no-package rate is ~16% early in the catalogue and reaches
> 100% by page 110.

**Practice:** count only entries that expose a downloadable artifact, and
re-measure that rate deeper into the catalogue rather than extrapolating from
the first page.

### 1.4 Rate limiters: cool down and resume; never count total cooldowns

> **Incident.** `doc.twse.com.tw` allows ~35 documents then refuses all
> connections for ~7 minutes while the site root still returns 200. A hard stop
> stranded the run; then a cap on *total* cooldowns (80) would have killed a
> healthy job at ~2,800 of 21,849 documents, because the backlog legitimately
> needs ~620 cooldowns.

**Practice:**
- Cool down and resume; do not abandon on a limiter that self-clears.
- Give up only on **consecutive cooldowns that recovered nothing**, never on a
  total count.
- Never persist a cursor past a failed row, or resume will skip it.
- Distinguish "blocked" from "down" cheaply: if the document endpoint refuses
  while the site root returns 200, you are limited, not offline.

### 1.5 One worker per rate-limited host, and never probe a host a job is using

> **Incident.** Running ~29 diagnostic probes against TWSE while the rescue was
> live consumed the shared per-IP allowance, invalidated the probe's own result,
> and forced an extra 15-minute cooldown on the production job.

**Practice:** diagnose from the running job's logs. If you must probe, stop the
job first.

### 1.6 A 200 on a landing page is not a validated source

> **Incident.** Ten European national sources returned 200 and were listed as
> "reachable". Bundesanzeiger turned out to be a stateful Wicket app whose
> document retrieval is captcha-guarded; CRO and Companies House return 403/401;
> ESAP is not live. Meanwhile `opendata.cro.ie` — never probed — served
> 1.39M rows under CC BY 4.0.

**Practice:** a source is validated when you have downloaded **one real document
end to end**. Until then it is a candidate. And probe the *open-data* subdomain
before concluding a registry is closed.

### 1.7 Prefer bulk endpoints; measure before building a scraper

Measured throughputs:

| Source | Auth | Result |
|---|---|---|
| Companies House bulk | none | 80 MB / **11,347 iXBRL docs in 5.6s** |
| EDGAR full-index | UA only | 55 MB/quarter, 10 req/s allowed |
| filings.xbrl.org | none | JSON:API, whole catalogue |
| Bundesanzeiger | none | search-by-name only, ~1.4 KB pointer notices |

**Practice:** check for a bulk product before writing a per-document crawler.

---

## 2. Processing (stage 02)

### 2.1 Path layouts differ per corpus, and a wrong builder fails *silently*

> **Incident.** `no_oslo` was registered against the `in_bse` target builder on
> the assumption of a shared shape. IN_BSE is `{scrip}/{year}/{file}.pdf`; Oslo
> is `{year}/{month}/{id}.pdf`; Nordic is `{year}/{company}/{file}.pdf`. The
> builder found **0 targets** in both — a run would report success having
> processed nothing.

**Practice:** every corpus gets its own target builder plus a registry test
asserting it routes to that builder. Always verify a builder against the live
tree and compare the target count to the file count before running.

### 2.2 Never hardcode currency — derive it per row and check the distribution

> **Incident.** The HK adapter hardcoded `HKD`. A tally of 30,000 live records:
> HK$ 15,318 | **RMB 11,141** | US$ 1,806 | Renminbi 369 | USD 188. About 40% of
> HKEX filers report in RMB and ~7% in USD, so FX conversion would have been
> wrong by the HKD/CNY cross for a third of the market and ~7.8× for USD filers.

**Practice:** derive currency from the data, normalise aliases
(`HK$`/`HKD`/`Hong Kong dollars` → HKD), record an **auditable** fallback
carrying the raw text, and print the resulting distribution.

### 2.3 Currency and unit scale are different problems — check both

> **Incident.** After correctly establishing that `unit_text` is the currency,
> nobody asked where the *scale* went. It was never captured. Tencent's
> `total_assets` appears as 56,804,365 (thousands) and 17,506 (millions), both
> labelled `RMB`. Cross-market medians of `total_assets_usd` differed by 10^5.

**Practice:** capture the multiplier from the statement caption (`RMB'000`,
"expressed in thousands", 人民幣千元) as a `unit_scale` column. Record it; do
not silently multiply. Where no scale is recoverable, label `unknown` and
exclude from level comparisons.

### 2.4 Group facts by filing, not by (company, period)

> **Incident.** Grouping HK facts by `(stock_code, fiscal_year)` across the whole
> corpus merged a later annual report's comparative column with the original
> filing's figures — values 1000× apart arbitrated by directory iteration order.

**Practice:** group by `(filing_id, entity, period)`. One filing's figures must
stay internally consistent. Carry `source_doc_id` so any flagged row is
traceable.

### 2.5 `period_end` comes from the data, never from a fiscal-year label

> **Incident.** Japanese fiscal years commonly end 31 March — 3,157 of 5,000
> sampled rows versus 565 on 31 December. Assuming `{fiscal_year}-12-31` would
> have misdated 37,669 rows.

**Practice:** read the real period-end column. Fabricating `-12-31` is only
acceptable where the source genuinely has no period end, and then it should be
marked as derived.

### 2.6 Metric mapping is accounting, not string matching

> **Incident.** `operating_income → profit_before_tax` conflated 営業利益 with
> 税引前当期純利益 when `income_before_taxes` existed as its own column. One row
> moved from 4,386,564,000 to 3,864,139,000 — a 13.5% overstatement that would
> have flowed into every effective-tax-rate calculation.

**Practice:** map on the accounting definition, not the closest name. Two traps
worth knowing:
- `net_assets → total_equity` is **correct for JP** (純資產 = Assets − Liabilities
  incl. NCI) but **wrong for HK**, where the same label is free-text extracted
  with no guaranteed definition. Same words, different provenance.
- A column literally named `cash_and_equivalents` may be the cash-flow-statement
  figure; the balance-sheet line (`cash_and_deposits`, 現金及び預金) is usually
  what the panel wants.

Document *why* each ambiguous mapping is right, in the file. Otherwise someone
"fixes" it later by name-matching.

### 2.7 Retain every reporting basis, and put it in the key

> **Incident.** CN publishes consolidated (`Typrep=A`) and parent-only (`B`)
> statements for the same company-period; JP does the same via
> `has_consolidated_statements`. With basis absent from the panel key, 616,642
> rows collided. Separately, 5,308 JP parent-only rows were silently labelled
> consolidated by a default.

**Practice:** carry `reporting_basis` (consolidated / parent / unknown) and
include it in the uniqueness key. Never default an unknown to consolidated.

### 2.8 Restated openings are data, not noise

> **Incident.** 149,269 CN rows carry a 1 January period end. They are restated
> opening balances — 148,459 carry values, and comparing one to the prior
> December close yields a restatement delta.

**Practice:** retain them as `period_type='OPEN'`, exclude them from period
grids so they cannot double-count, and join restatement deltas on `period_end`
(OPEN `YYYY-01-01` ↔ A `(YYYY-1)-12-31`), never on `fiscal_year`.

---

## 3. Verification

### 3.1 A test double must match the real callee's signature

> **Incident.** `try_download_binary`'s 5th positional parameter is `params`, not
> `timeout`. A stub with a different signature let the tests pass while the live
> call blew up inside `requests`.

**Practice:** assert keyword-only invocation, or bind your call to the real
function's signature in a test.

### 3.2 Green tests plus an unchecked denominator equals false confidence

> **Incident.** All 5 IN_BSE tests passed while the adapter used bare `float()`,
> because none fed a non-numeric value. The panel built 767,541 rows and 146
> tests passed while the spine had resolved **nothing**.

**Practice:** for every headline number, verify it against the *artifact*, not
the log. Add the test that would have made the bug visible, not just the fix.

### 3.3 Invariants flag; they never correct

Working invariants for this panel: no duplicate `(spine_key, period_end,
period_type, reporting_basis)`; `period_type` consistent with `period_end`
(assert independently — deriving cadence from the type under test is a
tautology); every USD figure carries `fx_rate` and `fx_asof`; plausible
`fiscal_year`; and `total_liabilities + total_equity ≈ total_assets` at **1%
relative** tolerance (a fixed epsilon is useless across tiny caps to trillions),
evaluated only when all three are present.

That last one surfaced 9,211 genuine upstream corruptions, e.g. `AU:AAC` with
`total_assets=1.584` against liabilities of 584,276. Surface them; do not
suppress.

Note it is **scale-invariant** and therefore cannot catch §2.3.

### 3.4 Job trackers go stale in both directions

> **Incident.** A Nordic backfill displayed "running" for 34 days after its
> process died. Later, two finished jobs still displayed "running" because their
> scripts had no finish hook.

**Practice:** treat a job as running only if its last update is recent **and**
its pid is alive. Every long job needs register/update/finish. Never write job
state to `/tmp` — macOS clears it on reboot.

### 3.5 Count every dropped row, with a reason

Silent `continue` in an adapter is how a market ends up with 1,759 rows from a
file that could have yielded 27,665 without anyone noticing. Emit
`{reason: count}` per market and reconcile against source counts.

---

## 4. Choosing a method

### 4.1 Measure the cheap baseline before commissioning models

> **Incident.** A bake-off was planned across Claude CLI, Codex CLI and a local
> model to recover HK unit scales. A regex over statement captions achieved
> **99.7% where text exists**, validated against the two diagnostic filings. The
> bake-off was cancelled: the residual was ~1%, and for those the honest answer
> is `unknown` rather than a model's guess.

**Practice:** always include a no-LLM baseline arm. Run it first — it may make
the comparison unnecessary. A confidently wrong answer is worse than an
abstention.

### 4.2 Prefer a maintained library to a hand-rolled scraper

`deutschland.bundesanzeiger` solves a captcha-guarded flow that would have been
fragile to hand-roll. It still returned only ~1.4 KB pointer notices, so
*validate what a library actually yields* before adopting it.

### 4.3 Storage: check the volume before committing

OWC sits at 94% (116 GB free); the external Mac volume has 33 GB; the boot
volume has 536 GB. New corpora land on the boot volume, with `live_roots`
recorded per stage in `markets/_meta/inventory.json` so the logical layout stays
canonical while physically split.

Never bulk-write many small files straight to Archive-class cloud storage — tar
first, land in Standard, and transition by lifecycle rule (see the global
CLAUDE.md for the incident that produced this rule).

---

## 5. Tooling map

| Job | Tool |
|---|---|
| Where do we stand / what next | `scripts/corpus_ledger.py` |
| Per-market acquisition coverage | `scripts/market_filings_snapshot.py` |
| Re-drive failed downloads | `scripts/rescue_missing_documents.py --market …` |
| Stage 02 for a PDF market | `scripts/pdf_market_sweep.py --market …` |
| Screening input (stage 03 feed) | `build_pdf_market_screening_input.py <market>` |
| Country pull via EDGAR | `scripts/edgar_country_pull.py --country IE\|DE` |
| Complete filings.xbrl.org | `scripts/xbrl_org_discovery.py` |
| HK text for scale recovery | `scripts/hk_text_backfill.py` |
| HK unit-scale table | `scripts/hk_scale_recovery.py` |
| Build the panel | `python -m panel.build` |

## 6. Known open items

- `extract_items.py` (EDGAR 10-K/20-F item extraction) is blocked by an
  unresolved dependency chain — `click`, `cssutils`, `pathos`, and a
  `ResolutionImpossible` on the full manifest. Needed before the Ireland (407)
  and Germany (309) EDGAR documents can reach stage 02.
- No confirmed parser for the 16,002 ESEF iXBRL packages.
- `03_gates` produces only `durability/` for every non-HK market; the
  `veto`/`price`/`two_track` outputs that `two_track_latest.csv` consumers
  expect have never been built outside HK.
- The annual-financials supervisor restarts every ~80s with nothing to do.
