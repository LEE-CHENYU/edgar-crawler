# Japan EDINET Digestion and Embedding Plan

## Goal

Build a Japanese annual-report corpus that supports both semantic retrieval and
structured financial analysis. The English 10-K pipeline can guide the document
flow, but Japan needs a second structured-financial pipeline because we do not
already have SEC-style standardized financial tables.

## Current State

- Corpus scope: EDINET annual securities reports only (`有価証券報告書`).
- Current raw files: PDF downloads from EDINET `documents/{doc_id}?type=2`.
- Missing raw files: EDINET XBRL ZIP packages from `documents/{doc_id}?type=1`.
- Local annual-only metadata after cleanup:
  - `EDINET`: 37,669 rows, 37,668 local PDFs.
  - `HKEX`: 3,614 rows, 3,460 local PDFs.
  - `DART`: 348 rows, 72 local files.
  - `TWSE_REPORTS`: 155 rows, 6 local files.

## Bake-Test Findings

Test filing: Toyota Motor Corporation, EDINET `S1007UWT`.

- PDF text extraction with `pdftotext -layout` is usable.
- Japanese annual-report headings are detectable from extracted text:
  - `第１ 【企業の概況】`
  - `第２ 【事業の状況】`
  - `４ 【事業等のリスク】`
  - `７ 【財政状態、経営成績及びキャッシュ・フローの状況の分析】`
  - `第５ 【経理の状況】`
- EDINET XBRL ZIP for the same filing was small, about 709 KB.
- The XBRL ZIP contained 25 iXBRL HTML files.
- Parsed iXBRL facts:
  - 759 facts.
  - 268 unique concepts.
  - 106 contexts.
  - 4 units.
- The sample exposed clean current/prior context IDs, including:
  - `CurrentYearDuration`
  - `CurrentYearInstant`
  - `Prior1YearDuration`
  - `Prior1YearInstant`
- Key facts were directly available, including revenue, profit before tax,
  total assets, operating cash flow, net sales, operating income, profit/loss,
  liabilities, net assets, and per-share values.

## Product Split

Create two derived products from the same filings.

### 1. Narrative Corpus

Purpose: retrieval, summaries, LLM context, semantic search, reranking, and
cross-market comparison.

Primary source:

1. Prefer iXBRL HTML text when the XBRL ZIP exists.
2. Fall back to `pdftotext -layout` for filings without usable iXBRL text.

Output:

- `edinet_sections.jsonl`
- `edinet_chunks.jsonl`
- embedding vectors in a vector store or parquet shard.

Do not embed:

- full raw PDFs;
- dense numeric tables;
- repeated EDINET headers/footers;
- raw XBRL fact dumps.

### 2. Structured Financial Facts

Purpose: the Japan equivalent of structured 10-K financial data.

Primary source:

- EDINET XBRL ZIP (`type=1`), not the PDF.

Output:

- raw long fact table first;
- canonical financial tables second.

Recommended raw fact schema:

```text
doc_id
edinet_code
security_code
company_name
filing_date
fiscal_year
period_start
period_end
period_type
context_id
concept_qname
concept_namespace
label_ja
label_en
value
raw_value
unit
decimals
scale
accounting_standard
consolidated_or_nonconsolidated
dimensions_json
source_ixbrl_file
```

Recommended canonical annual table fields:

```text
doc_id
security_code
fiscal_year
accounting_standard
is_consolidated
revenue
net_sales
operating_income
ordinary_income
profit_before_tax
net_income
total_assets
total_liabilities
net_assets
equity
operating_cash_flow
investing_cash_flow
financing_cash_flow
cash_and_equivalents
eps
bps
roe
equity_ratio
employee_count
```

Keep the raw long table even after building canonical fields. The mapping will
need iteration across J-GAAP, IFRS, US GAAP, banks, insurance, and REITs.

## Section Extraction

Use Japanese annual-report headings as the equivalent of English 10-K item
sections.

Important section mappings:

```text
【企業の概況】                                  company overview
【事業の状況】                                  business overview
【事業等のリスク】                              risk factors
【対処すべき課題】                              business issues / strategy
【研究開発活動】                                R&D
【財政状態、経営成績及びキャッシュ・フローの状況の分析】  MD&A
【設備の状況】                                  facilities / capex
【提出会社の状況】                              issuer / shares / governance
【コーポレート・ガバナンスの状況等】             governance
【経理の状況】                                  financial statements
【連結財務諸表等】                              consolidated statements
【注記事項】                                    notes
```

Extraction rules:

- Remove repeated headers: `EDINET提出書類`, company name, document type.
- Remove page counters such as `1/177`.
- Preserve section hierarchy: part, chapter, numbered subsection, bracket title.
- Store character offsets and page spans where possible.
- Classify sections as:
  - narrative;
  - table-heavy;
  - financial statement;
  - note;
  - governance.

## Chunking Rules

Chunk by section, then paragraph/sentence boundaries.

Defaults:

- target: 1,000-1,800 Japanese characters;
- overlap: 100-200 characters;
- max hard limit: model-dependent token budget;
- keep all chunks inside one logical section;
- do not merge risk and MD&A sections;
- route financial-statement tables to structured extraction rather than semantic
  embedding.

Chunk ID format:

```text
edinet:{doc_id}:section:{section_slug}:chunk:{chunk_index}
```

Chunk metadata:

```text
market=EDINET
language=ja
doc_id
edinet_code
security_code
company_name
filing_date
fiscal_year
section_name_ja
section_name_en
section_path
page_start
page_end
char_start
char_end
accounting_standard
has_xbrl
has_structured_facts
source=text|ixbrl|pdf
```

## Embedding Strategy

Use embeddings only for narrative and selected note text. Do not embed every
numeric table. The structured facts should be queried directly and joined to
chunks by `doc_id` and fiscal year.

Initial embedding candidates:

- `事業等のリスク`
- `対処すべき課題`
- `財政状態、経営成績及びキャッシュ・フローの状況の分析`
- `研究開発活動`
- `コーポレート・ガバナンスの状況等`
- selected notes if they are not table-dense.

Evaluation set:

- Toyota (`72030`)
- Sony (`67580`)
- SoftBank Group (`99840`)
- NTT (`94320`)
- MUFG (`83060`)
- one regional bank
- one insurance company
- one REIT
- one IFRS filer
- one US GAAP filer

## RunPod Acceleration Path

Use RunPod for compute while Nebius is unavailable. Structured XBRL parsing is
CPU-bound and deterministic, so it can run on a cheap CPU/Jupyter pod. Embedding
can later use a GPU pod or a remote embedding endpoint hosted from RunPod.

Operational rules:

- Store provider API keys only in the process environment or local private env
  files, never in the repository.
- Prefer persistent volumes for long jobs. If only container disk is available,
  use `--delete-zip-after-parse --gzip-facts` and monitor disk usage.
- Batch chunks by token budget, not by file count.
- Persist embeddings incrementally after each successful batch.
- Use stable chunk IDs so failed batches can resume without duplicate vectors.
- Record the embedding backend, model, dimension, and timestamp in metadata.
- Keep provider-specific vectors in separate namespaces. Do not mix vectors from
  different embedding models or providers in the same vector index.

Suggested output vector metadata:

```text
chunk_id
embedding_provider=runpod|openai|local
embedding_model=<model-name>
embedding_dim=<dimension>
embedded_at
source_hash
```

## Implementation Phases

### Phase 1: XBRL Downloader

- Add an EDINET XBRL download mode using `document_type=1`.
- Store ZIPs beside PDFs:

```text
raw/EDINET_XBRL/{YYYYMMDD}/{doc_id}/{doc_id}.zip
```

- Resume from annual-only metadata.
- Keep PDF and XBRL paths in a derived manifest.

### Phase 2: XBRL Parser

- Parse all iXBRL HTML files in each ZIP.
- Extract contexts, units, dimensions, numeric facts, and text facts.
- Emit long facts as JSONL/parquet.
- Build a first canonical mapping for core metrics.
- Validate canonical values on the bake-test companies.

### Phase 3: Section Parser

- Extract text from iXBRL HTML where available.
- Fall back to `pdftotext -layout`.
- Detect Japanese section headings.
- Emit section JSONL with offsets/page spans.
- Mark table-heavy sections for exclusion from embeddings.

### Phase 4: Chunker

- Produce stable chunk IDs.
- Chunk narrative sections by paragraph/sentence boundaries.
- Attach fiscal and structured-fact availability metadata.

### Phase 5: Embedding Bakeoff

- Run 50 filings through the chunker.
- Embed with the selected RunPod-hosted or external embedding backend.
- Measure:
  - chunks/sec;
  - tokens/sec;
  - failure rate;
  - cost per 1,000 filings;
  - retrieval quality on Japanese queries.

### Phase 6: Full Run

- Download XBRL ZIPs for all annual filings.
- Parse facts.
- Parse sections/chunks.
- Embed narrative chunks.
- Build retrieval index and structured fact store.

## Open Decisions

- Whether to store vectors in parquet first or directly in a vector database.
- Whether to use iXBRL text or PDF text as the default narrative source.
- Final canonical metric mapping across J-GAAP, IFRS, US GAAP, banks, insurance,
  and REITs.
- Whether Japanese-only embeddings are enough, or whether we need multilingual
  alignment with English filings.
