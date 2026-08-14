#!/bin/bash
# Robust local-MPS embed pipeline: resumably download e5-large, then embed AU/TW/PH.
# HF anonymous rate-limit keeps dropping the big safetensors mid-transfer, so we
# loop `curl -C -` (resume) until the file reaches its full Content-Length.
set -u
MD=/Users/lichenyu/models/multilingual-e5-large
URL="https://huggingface.co/intfloat/multilingual-e5-large/resolve/main/model.safetensors"
mkdir -p "$MD"

# exact expected size from the CDN (follows redirects)
EXPECTED=$(curl -sIL "$URL" | awk 'tolower($1)=="content-length:"{v=$2} END{printf "%d", v}' | tr -d '\r ')
[ "${EXPECTED:-0}" -lt 2000000000 ] && EXPECTED=2239607176   # fallback if HEAD flaky
echo "=== target safetensors size: $EXPECTED bytes ($(date)) ==="

n=0
while true; do
  SZ=$(stat -f%z "$MD/model.safetensors" 2>/dev/null || echo 0)
  [ "$SZ" -ge "$EXPECTED" ] && { echo "=== download complete: $SZ bytes ($(date)) ==="; break; }
  n=$((n+1))
  echo "=== dl attempt $n — have $SZ / $EXPECTED ($(date)) ==="
  curl -L -C - --connect-timeout 30 --speed-limit 15000 --speed-time 30 \
       -o "$MD/model.safetensors" "$URL" || true
  [ "$n" -gt 200 ] && { echo "=== GAVE UP after $n attempts ==="; exit 1; }
  sleep 4
done

cd /Users/lichenyu/GitHub/edgar-crawler
for m in PSE TWSE ASX; do
  D="/Volumes/OWC Express 1M2/datasets/MARKET_FILINGS/derived/${m}_FINANCIALS"
  echo "=== embed $m START $(date) ==="
  ~/miniconda3/envs/embed-e5/bin/python embed_chunks.py "$D/chunks_v1" "$D/embeddings_v1.parquet"
  echo "=== embed $m DONE $(date) ==="
done
echo "=== ALL EMBED DONE $(date) ==="
