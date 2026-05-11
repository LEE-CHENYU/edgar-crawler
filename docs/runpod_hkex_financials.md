# RunPod HKEX Financials Worker

Use RunPod CPU pods as plain PDF extraction workers for HKEX annual reports.
The worker downloads each HKEX PDF from `document_url`, extracts text with
Poppler `pdftotext -layout`, and writes compressed financial fact candidates,
the raw plain text, and the source PDF.

The PDF and extracted text are persisted by default so a downstream
section/chunk pipeline can re-read them without re-running `pdftotext` (and
without re-downloading from HKEX). Pass `--no-keep-pdf` and/or
`--no-keep-text` to fall back to the old temp-dir behaviour for disk-
constrained pods.

## Local Prep

From the repo root:

```bash
./.venv/bin/python hkex_financials_pipeline.py \
  --metadata "/Volumes/OWC Express 1M2/datasets/MARKET_FILINGS/metadata/MARKET_FILINGS_METADATA.csv" \
  --write-manifest /tmp/hkex_annual_manifest.csv \
  --newest-first

tar -czf /tmp/hkex_financials_runpod_bundle.tgz \
  hkex_financials_pipeline.py requirements.txt
```

Current manifest:

```text
26,573 HKEX annual-report rows
```

## Jupyter CPU Pod Path

RunPod SSH may allocate a forwarded port before the container actually serves
an SSH banner. The working path is a CPU pod running:

```text
quay.io/jupyter/base-notebook:python-3.11
port: 8888/http
working directory: /home/jovyan
```

Upload `hkex_financials_pipeline.py` and `hkex_annual_manifest.csv` through the
Jupyter Contents API or the JupyterLab file browser.

Install Poppler in the notebook environment:

```bash
mamba install -y -c conda-forge poppler
python -m pip install --quiet requests
```

Run a smoke test:

```bash
python hkex_financials_pipeline.py \
  --manifest hkex_annual_manifest.csv \
  --output-dir hkex_financials_smoke_pdftotext \
  --download \
  --gzip-facts \
  --pdf-text-engine pdftotext \
  --limit 3 \
  --timeout 180 \
  --log-every 1
```

Start two detached shard processes from separate pods:

```bash
PYTHONUNBUFFERED=1 python hkex_financials_pipeline.py \
  --manifest hkex_annual_manifest.csv \
  --output-dir hkex_financials \
  --download \
  --gzip-facts \
  --pdf-text-engine pdftotext \
  --shard-index 0 \
  --shard-count 2 \
  --timeout 180 \
  --sleep-seconds 0.1 \
  --log-every 25 \
  >> hkex_financials/logs/stdout_shard_000.log 2>&1 &
```

Use `--shard-index 1` and `stdout_shard_001.log` on the second pod.

## Monitor

From a notebook terminal or kernel:

```bash
pgrep -af hkex_financials_pipeline.py
tail -n 20 hkex_financials/logs/stdout_shard_000.log
tail -n 20 hkex_financials/logs/hkex_financials_shard_000.jsonl
find hkex_financials/facts -name '*.jsonl.gz' | wc -l
du -sh hkex_financials
df -h .
```

## Download Results

For the Jupyter fallback, package each pod's output before terminating it:

```bash
tar -czf hkex_financials_shard_000.tgz hkex_financials
```

Download the tarball through the JupyterLab file browser or the Jupyter
Contents API, then unpack into the local derived dataset directory:

```bash
mkdir -p "/Volumes/OWC Express 1M2/datasets/MARKET_FILINGS/derived/HKEX_FINANCIALS"
tar -xzf hkex_financials_shard_000.tgz \
  -C "/Volumes/OWC Express 1M2/datasets/MARKET_FILINGS/derived/HKEX_FINANCIALS"
```

## Output Layout

```text
hkex_financials/
  facts/YYYYMMDD/FILING_ID.jsonl.gz
  summaries/YYYYMMDD/FILING_ID.json
  pdfs/YYYYMMDD/FILING_ID.pdf          # written when --keep-pdf (default)
  text/YYYYMMDD/FILING_ID.txt[.gz]     # written when --keep-text (default)
  logs/stdout_shard_000.log
  logs/hkex_financials_shard_000.jsonl
```

The worker is resumable. If a summary and facts file already exist, that filing
is skipped on restart. If a PDF already exists at `pdfs/YYYYMMDD/FILING_ID.pdf`
(from a prior partial run), the worker reuses it instead of downloading
again, even on rows that failed before producing a summary.

The summary JSON now includes `pdf_path` and `text_path` fields so downstream
jobs can locate the persisted artefacts without re-deriving paths.
