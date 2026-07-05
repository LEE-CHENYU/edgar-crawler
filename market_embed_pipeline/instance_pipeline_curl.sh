#!/bin/bash
# curl-only pipeline (no pip/gcs-lib/snap/auth). Pull chunk tar via signed GET,
# embed on CUDA, tar embeddings, push via signed PUT (streaming). Resumable:
# embed skips done parts; a re-run re-pulls (fast) + re-embeds(skip) + re-pushes.
# Portable: uses $HOME so it works as ubuntu (L4) or shadeform (L40).
set -x
H="$HOME"
source "$H/signed_urls.sh"
declare -A GET=( [pse]="$GET_pse" [twse]="$GET_twse" [asx]="$GET_asx" )
declare -A PUT=( [pse]="$PUT_pse" [twse]="$PUT_twse" [asx]="$PUT_asx" )
mkdir -p "$H/embed/out"
cd "$H/embed"
for m in pse twse asx; do
  echo "=== $m PULL $(date) ==="
  curl -fsS -C - -o "$H/embed/${m}_chunks.tar" "${GET[$m]}" || { echo "$m PULL_FAIL rc=$?"; continue; }
  rm -rf "$H/embed/$m" && mkdir -p "$H/embed/$m"
  tar -xf "$H/embed/${m}_chunks.tar" -C "$H/embed/$m"
  echo "=== $m EMBED $(date) parts=$(ls $H/embed/$m/chunks_v1/part_*.parquet 2>/dev/null | wc -l) ==="
  E5_MODEL=intfloat/multilingual-e5-large python3 "$H/embed_chunks.py" \
      "$H/embed/$m/chunks_v1" "$H/embed/out/$m" "${EMBED_BATCH:-512}"
  echo "=== $m TAR+PUSH $(date) emb_parts=$(ls $H/embed/out/$m/*.parquet 2>/dev/null | wc -l) ==="
  tar -cf "$H/embed/${m}_emb.tar" -C "$H/embed/out/$m" .
  curl -fsS -T "$H/embed/${m}_emb.tar" -H "Content-Type: application/octet-stream" "${PUT[$m]}" -w "put_http=%{http_code}\n"
  rm -f "$H/embed/${m}_chunks.tar" "$H/embed/${m}_emb.tar"
  echo "=== $m DONE $(date) ==="
done
echo "ALL_MARKETS_DONE $(date)"
