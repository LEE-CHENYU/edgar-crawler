#!/bin/bash
# Autonomous completion. For TWSE then ASX: wait for emb tar in GCS, pull to OWC,
# extract, VERIFY LOCALLY (vector count == chunk count AND part count). Only after
# BOTH verify does it terminate the L40 instance, then CONFIRM it is actually gone.
# Idempotent: skips a market already verified on OWC (cheap restart). Survives via nohup.
BREV=/opt/homebrew/bin/brev
GSUTIL=/opt/homebrew/bin/gsutil
EMB=gs://happyhunting-10604-markets-transfer-usw1/embeddings
DERIVED="/Volumes/OWC Express 1M2/datasets/MARKET_FILINGS/derived"
PY=/Users/lichenyu/miniconda3/envs/python310/bin/python
LOG=/Users/lichenyu/stock_journal/logs/embed_completion.log
log(){ echo "$(date '+%m-%d %H:%M:%S') $*" >> "$LOG"; }
count(){ $PY -c "import pyarrow.parquet as pq,glob; fs=glob.glob('$1/*.parquet'); print(sum(pq.ParquetFile(f).metadata.num_rows for f in fs))" 2>>"$LOG"; }

log "=== completion watcher (hardened) start ==="
names=(twse asx); upper=(TWSE ASX); expect=(339192 3764759); eparts=(4 71)
allok=1
for i in 0 1; do
  m=${names[$i]}; U=${upper[$i]}; EXP=${expect[$i]}; EP=${eparts[$i]}
  D="$DERIVED/${U}_FINANCIALS/embeddings_v1"; mkdir -p "$D"
  # skip if already verified on OWC
  got=$(count "$D"); parts=$(ls "$D"/*.parquet 2>/dev/null | wc -l | tr -d ' ')
  if [ "$got" = "$EXP" ] && [ "$parts" = "$EP" ]; then log "$m already on OWC & VERIFIED (skip)"; continue; fi
  log "waiting for ${m}_emb.tar in GCS ..."
  while true; do
    sz=$($GSUTIL ls -l "$EMB/${m}_emb.tar" 2>/dev/null | awk 'NR==1{print $1}')
    [ -n "$sz" ] && [ "$sz" != "0" ] && { log "$m landed: $sz bytes"; break; }
    sleep 120
  done
  TAR="$DERIVED/${U}_FINANCIALS/${m}_emb.tar"
  log "pulling $m to OWC ..."
  $GSUTIL cp "$EMB/${m}_emb.tar" "$TAR" >> "$LOG" 2>&1
  rm -f "$D"/*.parquet; tar -xf "$TAR" -C "$D" && rm -f "$TAR"
  got=$(count "$D"); parts=$(ls "$D"/*.parquet 2>/dev/null | wc -l | tr -d ' ')
  log "$m verify: vectors=$got (expect $EXP)  parts=$parts (expect $EP)"
  if [ "$got" = "$EXP" ] && [ "$parts" = "$EP" ]; then log "$m VERIFIED_OK"; else log "$m VERIFY_FAILED"; allok=0; fi
done

if [ "$allok" != "1" ]; then log "NEEDS_ATTENTION: verify failed; instance NOT terminated"; exit 1; fi

log "ALL_VERIFIED_ON_OWC -> terminating instance embed-l40"
for k in $(seq 1 8); do
  $BREV delete embed-l40 >> "$LOG" 2>&1
  sleep 20
  st=$($BREV ls 2>/dev/null | grep "embed-l40 ")
  if [ -z "$st" ] || echo "$st" | grep -qi "DELETING\|DELETED"; then log "instance terminated (try $k): ${st:-absent}"; break; fi
  log "delete not confirmed (try $k): $st"
done
final=$($BREV ls 2>/dev/null | grep "embed-l40 ")
if [ -z "$final" ] || echo "$final" | grep -qi "DELETING\|DELETED"; then
  log "ALL_DONE instance gone. (Left for manual audit: temp SA + GCS staging tars)"
else
  log "WARN instance still present after retries: $final -- terminate manually: brev delete embed-l40"
fi
