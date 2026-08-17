# Corpus Backlog Program — Design

Date: 2026-08-14
Status: approved (design), pending implementation plans
Scope: four workstreams closing download and processing gaps in `MARKET_FILINGS` /
`datasets/markets`, plus one new market (NZX).

## 1. Why this exists

The annual-financials supervisor reports all five implemented markets `complete=true`
and has been idling in a restart loop since 2026-07-17. "Complete" in that queue means
*discovery* reached its floor — not that documents were downloaded, and not that any
downstream stage ran. Verification on 2026-08-12/14 found gaps at both levels.

### Verified current state

Download coverage (from `metadata/MARKET_FILINGS_METADATA.csv`):

| Market | Metadata rows | Files on disk | Coverage |
|---|---|---|---|
| IN_BSE_ANNUAL_REPORTS | 68,091 | 68,091 | 100% |
| EDINET | 37,669 | 37,668 | 100% |
| ASX | 28,202 | 27,665 | 98% |
| HKEX | 26,573 | 25,658 | 97% |
| TWSE_REPORTS | 25,978 | 4,044 | **16%** |
| DART | 14,049 | 9,299 | **66%** |
| ESEF (27 countries) | 15,875 | 15,875 | 100% of what was pulled |
| NO_OSLO_NEWSWEB | 8,134 | 8,134 | 100% |
| PSE_EDGE | 606 | 606 | 100% |

Downstream stage coverage:

| Corpus | Docs | 02_structured | 03_gates | 04_nlp | 05_ner |
|---|---|---|---|---|---|
| HKEX | 25,658 | yes | yes | yes (88 GB) | yes (2.3 GB) |
| ASX | 27,665 | yes (27,665) | durability only | yes (6.4 GB) | no |
| TWSE | 4,044 | 1,304 (32%) | durability only | partial | no |
| PSE | 606 | 447 (74%) | durability only | yes | no |
| DART | 9,299 | 866 (6%) | durability only | no | no |
| IN_BSE | 67,994 | no | no | no | no |
| ESEF (all) | 15,875 | no | no | no | no |
| NO_OSLO | 8,139 | no | no | no | no |

Two independent observations follow. First, running stage 02 on TWSE or DART today
would extract from a 16% / 66% corpus and need redoing. Second, `03_gates` produced
only `durability/` for every non-HK market — none of the `veto` / `price` / `two_track`
outputs that `two_track_latest.csv` consumers expect.

## 2. Decisions taken

**Storage target.** New corpora land on the boot volume (`/`, ExternalMind, disk6,
536 GB free). The OWC drive is at 94% (116 GB free) and the external Mac volume
(disk3, `Macintosh HD` + `Data`, one 245 GB container) has 33 GB free — neither fits
the program. Corpora therefore span two physical volumes; the split is recorded via
per-stage `live_root` in `markets/_meta/inventory.json`, the same mechanism the HK
Phase 4b migration used. The logical canonical layout is unchanged.

**NZX robots posture.** `api.nzx.com/robots.txt` is `Disallow: /` and serves the
attachment PDFs; `www.nzx.com` allows `/announcements/`. Decision: sweep metadata from
the allowed host and fetch linked PDFs from `api.nzx.com` at low rate (<=1 req/s) with
a descriptive User-Agent carrying a contact string. This is a deliberate choice, not an
oversight — recorded here so it is reviewable.

**Europe scope.** Both: complete the `filings.xbrl.org` pull *and* track additional
national sources for pre-2020 history.

## 3. Workstream A — download rescue (TWSE + DART)

**TWSE — 21,934 missing, all diagnosed and recoverable.** The doc server is healthy:
a 2008 annual report downloaded in under a second (1,086,164 bytes, valid PDF v1.2) on
2026-08-14. The two-step flow works as implemented — `t57sb01?step=9` returns a
big5 HTML page carrying a one-shot `/pdf/{stem}_{YYYYMMDD}_{HHMMSS}.pdf` link, and
`fetch_twse_report_pdf_url` already parses it.

Failure split from `raw_metadata` on rows lacking `local_path`:

- 18,735 — resolved a `pdf_url`, binary download then failed (matches the historical
  "doc server timed out from this environment" note)
- 3,199 — `pdf_url_error: missing generated PDF URL` (link resolution failed)

Design: a **rescue driver**, not a rewrite. Read metadata rows where
`market='TWSE_REPORTS'` and `local_path` is empty, re-drive the existing
resolve-then-download path, write files, and update the row in place. The one-shot
links are timestamped and expire, so resolution must happen per attempt — never cache
a `pdf_url` across runs. Resumable via its own state file; safe to interleave with the
existing metadata writer, which already takes an `fcntl` lock and preserves a previous
`local_path` when a new row arrives without one.

Estimated ~9 hours at 1.5 s/doc, ~20-25 GB.

**DART — 4,750 missing.** `DART_API_KEY` is present (40 chars) and
`opendart.fss.or.kr/api/document.xml?rcept_no=...` returned a valid ZIP for a 2008
filing on 2026-08-14. Same rescue shape. Respect DART's 20,000 calls/day cap — the
rescue must count calls and stop cleanly at the ceiling, resuming next run. ~2 GB.

### Source behaviour: doc.twse.com.tw (measured 2026-08-14)

Everything below is from direct measurement, not inference. It supersedes the
"the document PDF server path timed out from this environment" note in
`docs/market_filing_targets.md`, which almost certainly described this same
limiter during the original backfill.

- **The limiter is IP-level, not per-session and not per-document.** A fresh
  `requests.Session` per request failed 20/20 while a block was active, so
  recycling connections buys nothing. The same document that had just
  downloaded successfully failed moments later, so it is not per-document.
- **Allowance is roughly 29 requests**, and it barely moves with pacing:
  trips came at ~50-75 requests (0.5s pacing), ~30 (2s) and ~29 (5s). Slowing
  down therefore trades working time for the same number of cooldowns.
- **Recovery is fast — about 7 minutes.** While blocked, `/server-java/t57sb01`
  refuses connections for both GET and POST while `https://doc.twse.com.tw/`
  keeps returning 200; that pair is the cheap way to tell "blocked" from
  "down". After recovery the endpoint served a full 1,086,164-byte PDF again.
- **Symptom signature**: `('Connection aborted.', BadStatusLine('<!DOCTYPE HTML
  PUBLIC ...'))` on the link-resolution step, i.e. a malformed HTTP response,
  not an HTTP error status.

Operating rules that follow:

1. **One worker at a time against this host.** The budget is shared per IP, so
   parallel workers just trip each other.
2. **Never probe the endpoint while a job is running against it.** Doing so on
   2026-08-14 consumed the shared allowance, invalidated the probe's own
   result, and pushed the live rescue into an extra 15-minute cooldown. Read
   the running job's logs instead.
3. **Cool down and resume; do not stop.** 480s matches the observed recovery.
4. Expect ~29 documents per cooldown cycle: with a 480s cooldown that is
   roughly **6-7 days** for the 21,849 outstanding documents.

## 4. Workstream D1 — complete filings.xbrl.org

`filings.xbrl.org` reports **25,675** filings; the corpus holds **15,875**. ~9,800
(38%) were never pulled, across all 27 countries including UK, Poland, and Germany
(currently zero rows).

No script in `edgar-crawler` or `securities_selection` references `filings.xbrl.org` —
the original pull was ad-hoc and left no state file. This needs a real worker.

Design: `scripts/xbrl_org_discovery.py`, following the `asx_annual_discovery.py`
convention (self-contained, appends to the shared metadata CSV under lock). Enumerate
via the JSON:API (`/api/filings`, `page[size]` / `page[number]`), diff against existing
metadata rows by filing key, download only the missing package zips into
`markets/{cc}/01_raw/europe_annual_reports/xbrl_org/{CC}/...` — the layout the existing
15,875 already use — on the boot volume. Cursor + per-country counters in
`backfill_state_xbrl_org.json`.

### CORRECTION 2026-08-17: the gap was ~200 filings, not ~9,800

The original sizing below was wrong, and the error is worth recording because it
was an inference presented as a measurement.

**What was claimed:** the catalogue holds 25,675 filings and metadata holds
15,875, therefore ~9,800 (38%) were never pulled, at ~16 MB each = ~160 GB,
"the single largest item in the program".

**What the worker's own catalogue walk found:** pages 1-124 contained **zero**
genuinely missing filings. Per-page breakdown (200 filings each):

| Page | Already in metadata | No package | Genuinely missing |
|---|---|---|---|
| 79 | 166 | 34 | 0 |
| 90 | 124 | 76 | 0 |
| 100 | 80 | 120 | 0 |
| 110 | 0 | 200 | 0 |
| 124 | 184 | 16 | 0 |

The ~9,800 delta is almost entirely catalogue entries whose `package_url` is
`"None"` — viewer-only or JSON-only filings that can never be downloaded. The
no-package rate is ~16% early in the catalogue and reaches **100% by page 110**.
Pagination was verified as non-repeating (zero fxo_id overlap between pages 1,
50 and 120), so this is not a scanning artifact.

**Final tally from the completed run (2026-08-17):**

```
done: 127 downloaded, 0 failed, 16378 already had, 9770 no package
```

**9,770 entries have no downloadable package** — almost exactly the "~9,800
missing" that was claimed. The entire supposed gap was undownloadable catalogue
entries. Actual outstanding work was 127 filings in the recent tail (pages
125-129), all fetched with zero failures.

**Root cause of the error:** a catalogue count was compared against a metadata
count without checking whether the catalogue entries were *fetchable*. The 16%
no-package rate was measured early and then not re-checked deeper in the
catalogue, where it rises steeply.

**Consequences for the program:** D1 is not the largest item and does not
require ~160 GB. UK and Poland were never missing. Germany's zero rows are real
but Germany does not file to this collector at all, so only D2 can address it.

Historical sizing (retained to show what was superseded): 258 GB for 15,875
filings measured on disk (~16 MB/filing; UK alone is 44 GB across 2,794 zips),
with the ~9,800 assumed missing therefore estimated at ~160 GB.

Coverage ceiling: ESEF was mandated from FY2020, so this source cannot yield pre-2020
history for any country. That is the entire motivation for D2.

## 5. Workstream D2 — additional EU sources

Reachability probed 2026-08-14. **These are landing-page probes only.** The repo's bar
for "implementable" is a validated real document download; nothing below has cleared it
yet, and each needs a validation pass before a worker is written.

| Source | Probe | Next step |
|---|---|---|
| DE Bundesanzeiger | 200 | Validate search + document flow. Highest value — DE is the zero-row gap |
| DE Unternehmensregister | 200 (580 KB) | Validate; likely the annual-accounts surface |
| FR BALO | 200 | Validate document flow |
| NL AFM | 200 | Validate |
| BE NBB Central Balance Sheet | 200 | Validate |
| ES CNMV | 200 | Validate |
| CH SIX SER | 200 | Validate |
| Nordic Nasdaq | 200 | Validate |
| NO Oslo Newsweb | 200 | Already yields 8,139 docs; extend rather than start |
| PL KNF RSS / PAP ESPI | 200 | Validate; RSS is current-only, history path unclear |
| UK FCA NSM | 403 auth | Needs token; investigate access terms |
| UK Companies House | 401 | Free API key available on request |
| AT Wiener Börse | 403 | Blocked from this environment |
| IT Borsa Italiana | 404 | Wrong path; find correct announcement surface |
| FR AMF | 404 | Wrong path; find correct search surface |
| PL GPW | no connection | Blocked from this environment |

Deliverable for D2 is a validation report updating `docs/market_filing_targets.md` with
each source moved to validated / queued / blocked, plus workers for whichever validate.
Sizing is unknown until validation — deliberately not estimated here.

## 6. Workstream B — downstream stages

Run 02 → 03 → 04 over corpora that are download-complete. Priority by volume:
IN_BSE (67,994 docs), ESEF (15,875 + D1's ~9,800), NO_OSLO (8,139); then TWSE and DART
*after* Workstream A, and the DART 02 backlog (866 of 9,299).

Sizing anchor: ASX gives 27,665 docs → 6.4 GB of chunks + embeddings (~230 KB/doc), so
IN_BSE lands near 16-20 GB. (CN's 107 GB `04_nlp` reflects a much larger per-document
corpus and is not the right anchor.)

Two constraints. Embeddings require cloud GPU — the no-local-models rule means Brev or
RunPod as with HK and CN, so this workstream carries a dollar cost the others don't.
And the embedding model must be recorded: the ASX / TWSE / PSE `embeddings_v1` parquets
have no manifest, so **the model that produced them is currently unknown**. Establish it
before any cross-market cosine comparison (see the cross-model trap in CLAUDE.md), and
write an `embedding_model` field into inventory for every stage this workstream touches.

The `03_gates` gap (durability-only) is tracked here but is a distinct piece of work
from stage 02 extraction.

## 7. Workstream C — NZX

New market. Full design in section 8 below; summarized here for sequencing.

`api.nzx.com/public` JSON now returns `403 Access denied`, so the "validated" note in
`docs/market_filing_targets.md` is stale — no date- or company-filtered query survives.
Enumeration is therefore an ID sweep over `www.nzx.com`.

- Archive floor: ID 292474 = 2016-11-11 (everything below 404s). **Not 2008** — the
  queue's `DEFAULT_START_DATE` must not define completion for this job.
- Current max ID ≈ 477911 → ~185,000 IDs, 17-26 h at 2-3 req/s, ~6 GB transient JSON.
- Expected yield ~4,000-6,000 annual PDFs, 12-20 GB.

## 8. NZX worker design

`scripts/nzx_annual_discovery.py`, self-contained, matching `asx_annual_discovery.py`.
Rejected alternatives: adding `collect_nzx` to `download_market_filings.py` (already
2,026 lines, and its driver is date-chunked which NZX cannot be), and waiting to unlock
the `api.nzx.com` JSON (403 anonymous plus `Disallow: /` — a dead end, not a delay).

Loop, descending newest → oldest:

1. Resolve `buildId` from `https://www.nzx.com/` (`__NEXT_DATA__`).
2. Discover current max ID by probing upward from last known max.
3. Per ID: `GET /_next/data/{buildId}/announcements/{id}.json`, parse
   `pageProps.announcement`.
4. Every announcement → one metadata row (`market="NZX"`), empty `local_path`.
5. Annual matches → download each PDF attachment from `api.nzx.com` with
   `Referer: https://www.nzx.com/`, one row per attachment
   (`filing_id = "{id}-{attachmentId}"`), into `raw/NZX/{YYYYMMDD}/{id}/{file}.pdf`.
6. Checkpoint the cursor every N IDs.

**Annual classification** — `is_annual_document(summary)`, pure and unit-tested: type
allowlist (`FLLYR`, plus `GENERAL`/`MEETING` when title matches), include keywords
(`annual report`, `annual results`, `full year results`, `full-year results`,
`integrated report`, `annual financial statements`), minus exclusions (`annual meeting`,
`annual shareholders meeting`, `notice of annual`, `results of annual meeting`). The
exclusions carry real weight: in the recent-200 sample, 8 of 11 "annual" title hits were
shareholder-meeting notices, not reports.

**buildId fragility** — the main operational risk. An NZX deploy changes `buildId`, after
which *every* ID 404s, indistinguishable from "ID does not exist"; unhandled, the worker
would burn the whole range recording nothing. Mitigation: after 25 consecutive 404s,
re-probe sentinel ID 292474. If the sentinel also 404s the buildId is stale → refresh and
retry; if the sentinel is fine the 404s are real gaps. Fallback if `_next/data` is
withdrawn: parse `__NEXT_DATA__` from the `/announcements/{id}` HTML, same payload.

**State** — `backfill_state_nzx.json`: `nzx_next_id`, `nzx_min_id` (292474), `nzx_max_id`,
`nzx_status`, `nzx_rows`, `nzx_pdfs`, `nzx_updated_at`. Completion is
`nzx_next_id < 292474`, and the queue status line reports `covers_from=2016-11-11` so the
pre-2016 absence stays visible rather than implied-complete.

**Queue registration** — `Job(key="nzx", priority=45, script_name="scripts/nzx_annual_discovery.py")`
with `nzx_args` / `nzx_complete` / `nzx_status`; drop the `nzx` `BacklogTarget`.

**Politeness** — descriptive User-Agent naming the project plus a contact string,
1.5 req/s on www.nzx.com, 1 req/s on api.nzx.com, existing `Retry` backoff on 429/5xx,
and a hard stop to metadata-only if api.nzx.com begins returning 403.

## 9. Sequencing and totals

Order: **A → D1 → B → C**. A is cheapest, needs no new worker, unblocks B for TWSE and
DART, and un-idles the supervisor. D1 is the largest win and covers the UK/Poland ask.
C is independent of B and D1 except for disk.

| Workstream | New data |
|---|---|
| A — TWSE + DART rescue | ~27 GB |
| D1 — complete xbrl.org | ~160 GB |
| B — stages 02-04 | ~20 GB + GPU cost |
| C — NZX | ~12-20 GB |
| D2 — EU national sources | unknown until validation |

Total ≈ 220 GB against 536 GB free on the boot volume.

## 10. Risks and open items

1. **Production code is untracked.** `scripts/` in its entirety — including
   `annual_financials_queue.py`, `asx_annual_discovery.py`, and the supervisor shell
   script running for four weeks — is untracked in git, alongside 255 uncommitted lines
   in `download_market_filings.py`. The pipeline behind a ~500 GB corpus has no version
   history. Commit before modifying any of it.
2. **The supervisor restart-loop.** It exits `rc=0`, sleeps 60 s, restarts, roughly every
   80 s since 2026-07-17 — 38,286 progress snapshots and a 116 MB log, plus a write to
   the external drive every cycle. Workstream A makes the jobs incomplete again, which
   masks rather than fixes it. Per hook hygiene, the job should exit or be unloaded when
   all work is genuinely done.
3. **Unknown embedding model** for ASX / TWSE / PSE `embeddings_v1` (section 6).
4. **Disk pressure.** OWC at 94%. Anything landing there needs tarring and a
   Standard→Archive lifecycle transition per the global GCS discipline, never a direct
   Archive write.
5. **D2 sizing is unestimated** by design, pending validation.
