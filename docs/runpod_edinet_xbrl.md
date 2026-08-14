# RunPod EDINET XBRL Worker

Use RunPod as plain compute for EDINET structured-financial extraction. The
worker is deterministic and provider-neutral; it downloads EDINET `type=1`
XBRL ZIPs, parses iXBRL facts, and writes JSONL/JSON outputs.

## Local Prep

From the repo root:

```bash
./.venv/bin/python edinet_xbrl_pipeline.py \
  --metadata "/Volumes/OWC Express 1M2/datasets/MARKET_FILINGS/metadata/MARKET_FILINGS_METADATA.csv" \
  --write-manifest /tmp/edinet_annual_manifest.csv \
  --newest-first \
  --dry-run

tar -czf /tmp/edinet_runpod_bundle.tgz edinet_xbrl_pipeline.py requirements.txt
```

Expected manifest size:

```text
37670 /tmp/edinet_annual_manifest.csv
```

## RunPod Pod

Recommended pod shape:

- Image: a Python/Jupyter image works. SSH is optional; in this session the SSH
  port did not produce a banner, but the Jupyter HTTP proxy worked.
- Disk: use a persistent volume when available. A 20 GB container disk is enough
  for a run that uses `--delete-zip-after-parse`, but monitor it closely.
- GPU: not required for XBRL parsing; use CPU-only or cheap GPU unless the same
  pod will also run embedding/model jobs.
- Working directory: `/workspace` for SSH images, or `/home/jovyan` for Jupyter
  base images.

Copy files to the pod:

```bash
scp -P <ssh_port> /tmp/edinet_runpod_bundle.tgz /tmp/edinet_annual_manifest.csv \
  root@<runpod_host>:/workspace/
```

Initialize the environment on the pod:

```bash
ssh -p <ssh_port> root@<runpod_host>
cd /workspace
tar -xzf edinet_runpod_bundle.tgz
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install requests lxml beautifulsoup4 pandas
```

Run a 3-filing pilot:

```bash
export EDINET_API_KEY='<secret>'
python edinet_xbrl_pipeline.py \
  --manifest /workspace/edinet_annual_manifest.csv \
  --output-dir /workspace/edinet_xbrl \
  --newest-first \
  --limit 3 \
  --timeout 90 \
  --delete-zip-after-parse \
  --gzip-facts
```

Start the full resumable job:

```bash
tmux new-session -d -s edinet_xbrl "cd /workspace && . .venv/bin/activate && \
  EDINET_API_KEY='<secret>' stdbuf -oL -eL python edinet_xbrl_pipeline.py \
  --manifest /workspace/edinet_annual_manifest.csv \
  --output-dir /workspace/edinet_xbrl \
  --newest-first \
  --sleep-seconds 0.25 \
  --timeout 90 \
  --log-every 25 \
  --delete-zip-after-parse \
  --gzip-facts \
  >> /workspace/edinet_xbrl/logs/xbrl_pipeline_stdout.log 2>&1"
```

### Jupyter Proxy Fallback

If SSH is unavailable, upload `edinet_xbrl_pipeline.py`,
`edinet_annual_manifest.csv`, and an `edinet_env.sh` file to the Jupyter pod.
The current working RunPod setup uses:

```text
pod_id: z3iv8qlk5zqaqw
image: quay.io/jupyter/base-notebook:python-3.11
working_dir: /home/jovyan
proxy: https://z3iv8qlk5zqaqw-8888.proxy.runpod.net
```

Run commands through the Jupyter kernel API. The RunPod API/proxy may reject
non-browser requests, so include a normal `User-Agent` header. The current full
job command is:

```bash
python edinet_xbrl_pipeline.py \
  --manifest edinet_annual_manifest.csv \
  --output-dir edinet_xbrl \
  --newest-first \
  --sleep-seconds 0.25 \
  --timeout 90 \
  --log-every 25 \
  --delete-zip-after-parse \
  --gzip-facts
```

Monitor:

```bash
tmux ls
tail -f /workspace/edinet_xbrl/logs/xbrl_pipeline_stdout.log
tail -n 20 /workspace/edinet_xbrl/logs/xbrl_pipeline_events.jsonl
find /workspace/edinet_xbrl/facts -name '*.jsonl' | wc -l
```

For the Jupyter fallback, run the same commands from `/home/jovyan` without
`tmux`:

```bash
pgrep -af edinet_xbrl_pipeline.py
tail -n 20 edinet_xbrl/logs/xbrl_pipeline_events.jsonl
find edinet_xbrl/facts -name '*.jsonl' | wc -l
du -sh edinet_xbrl
df -h .
```

Sync results back:

```bash
rsync -avz -e "ssh -p <ssh_port>" \
  root@<runpod_host>:/workspace/edinet_xbrl/ \
  "/Volumes/OWC Express 1M2/datasets/MARKET_FILINGS/derived/EDINET_XBRL/"
```

## Output Layout

```text
edinet_xbrl/
  zips/YYYYMMDD/DOCID/DOCID.zip
  facts/YYYYMMDD/DOCID.jsonl or DOCID.jsonl.gz
  contexts/YYYYMMDD/DOCID.json
  units/YYYYMMDD/DOCID.json
  summaries/YYYYMMDD/DOCID.json
  logs/xbrl_pipeline_stdout.log
  logs/xbrl_pipeline_events.jsonl
```

The job is resumable. If `facts/YYYYMMDD/DOCID.jsonl` exists and is non-empty,
the worker skips that filing. Cached skips do not sleep, so restarted shard
workers can move through already-finished ranges quickly.
