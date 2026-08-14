# RunPod HKEX NER Worker

Use RunPod CPU Jupyter pods to run named-entity extraction over HKEX annual
reports. The worker downloads each PDF from `document_url`, extracts text with
Poppler `pdftotext -layout`, runs spaCy NER, and writes per-filing aggregated
entity JSONL files.

## Local Prep

```bash
./.venv/bin/python hkex_ner_pipeline.py \
  --metadata "/Volumes/OWC Express 1M2/datasets/MARKET_FILINGS/metadata/MARKET_FILINGS_METADATA.csv" \
  --write-manifest /tmp/hkex_ner_manifest.csv \
  --newest-first
```

Current manifest:

```text
26,573 HKEX annual-report rows
```

## Jupyter CPU Pod Setup

Use:

```text
image: quay.io/jupyter/base-notebook:python-3.11
port: 8888/http
working directory: /home/jovyan
```

Upload:

```text
hkex_ner_pipeline.py
hkex_ner_manifest.csv
```

Install runtime:

```bash
mamba install -y -q -c conda-forge poppler
python -m pip install --quiet requests spacy
python -m spacy download en_core_web_sm
```

Smoke test:

```bash
python hkex_ner_pipeline.py \
  --manifest hkex_ner_manifest.csv \
  --output-dir hkex_ner_smoke \
  --download \
  --gzip-entities \
  --shard-index 0 \
  --shard-count 2 \
  --limit 2 \
  --timeout 180 \
  --log-every 1 \
  --max-chars 200000 \
  --chunk-chars 3500
```

Start one shard per pod:

```bash
PYTHONUNBUFFERED=1 python hkex_ner_pipeline.py \
  --manifest hkex_ner_manifest.csv \
  --output-dir hkex_ner \
  --download \
  --gzip-entities \
  --shard-index 0 \
  --shard-count 2 \
  --timeout 180 \
  --sleep-seconds 0.05 \
  --log-every 25 \
  --max-chars 200000 \
  --chunk-chars 3500 \
  >> hkex_ner/logs/stdout_shard_000.log 2>&1 &
```

Use `--shard-index 1` and `stdout_shard_001.log` on the second pod.

## Monitor

```bash
pgrep -af hkex_ner_pipeline.py
tail -n 20 hkex_ner/logs/stdout_shard_000.log
tail -n 20 hkex_ner/logs/hkex_ner_shard_000.jsonl
find hkex_ner/entities -name '*.jsonl.gz' | wc -l
du -sh hkex_ner
df -h .
```

## Local Watchdog

Run from the repo with the Jupyter token in the environment:

```bash
HKEX_JUPYTER_TOKEN='<token>' python3 scripts/hkex_ner_runpod_watchdog.py \
  --pods POD0:000,POD1:001 \
  --dest "/Volumes/OWC Express 1M2/datasets/MARKET_FILINGS/derived/HKEX_NER" \
  --download-dir /tmp/hkex_ner_runpod_downloads \
  --poll-seconds 300 \
  --delete-pods
```

The watchdog waits for every shard row to finish, packages each pod's
`hkex_ner` directory, verifies archive SHA256 and local event counts, writes
`hkex_ner_runpod_download_manifest.json`, then deletes the pods.

## Output Layout

```text
hkex_ner/
  entities/YYYYMMDD/FILING_ID.jsonl.gz
  summaries/YYYYMMDD/FILING_ID.json
  logs/stdout_shard_000.log
  logs/hkex_ner_shard_000.jsonl
```

