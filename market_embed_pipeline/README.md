# Cloud embedding pipeline (AU/TW/PH → multilingual-e5-large)

Embeds `chunk_financials.py` output (`{ASX,TWSE,PSE}_FINANCIALS/chunks_v1/`) into
1024-dim fp16 `intfloat/multilingual-e5-large` vectors on a rented GPU, moving all
bulk data through GCS (the dev uplink is a flaky hotspot-class link, so SSH is used
only to launch/monitor — never for GB transfers).

## Flow
1. Tar each market's `chunks_v1/` → `gs://<bucket>/chunks/<m>_chunks.tar` (gsutil, resumable).
2. Provision a GPU (brev). Run `l40s_setup.sh` over held-open ssh: installs torch
   **matched to the driver CUDA** (`pip install torch --index-url .../whl/cu128` for a
   12.8 driver — the default wheel is cu13x and fails), plus `transformers pandas
   pyarrow numpy`, and pre-downloads the model.
3. Generate short-lived signed GET (chunks) + PUT (embeddings) URLs locally with a
   temp bucket-scoped service-account key (`gsutil signurl`). The instance needs no
   gcloud/auth — only `curl`.
4. `run_pipeline_l40.sh` drives `instance_pipeline_curl.sh` on the GPU: `curl -C -`
   pull → `embed_chunks.py` (length-sorted batching + SDPA) → tar → `curl -T` push.
5. `embed_completion.sh` (local nohup) pulls each `<m>_emb.tar` from GCS as it lands,
   extracts to `<m>_FINANCIALS/embeddings_v1/`, **verifies vector-count == chunk-count**,
   and only then terminates the instance.

`gcs_xfer.py` is an alternate transfer path (google-cloud-storage client) if you can
install it on the instance; `bench.py` measures forward throughput (e5-large ≈ 250/s
at seq-512 on an L40, ~110/s on the densest filings).

## Lessons baked in
- **Verify the tar before trusting it** (`tar -tf | grep -c emb_part` == expected parts).
  Overlapping pipeline restarts writing the same `asx_emb.tar` concurrently produced a
  corrupt 7 GB archive with only 3 readable entries — never terminate on a bad verify.
- **Match torch to the driver CUDA**, install pandas/pyarrow/numpy (else the embed
  crashes silently and pushes empty tars), and force SDPA attention.

## STATUS / TODO (2026-07-05)
PSE ✅ (164,232) and TWSE ✅ (339,192) fully embedded + verified on OWC. **ASX is 57%:
`emb_part_00000–00039` (2,130,612 vectors) present; parts 40–70 (~1.63M) still to
re-embed** — the L40 crashed/was auto-terminated mid-run and its GCS backup was corrupt.
Chunks are intact (OWC `chunks_v1` + regenerable). To finish: embed only ASX input
parts 40–70 (drop the 40 done `emb_part_*` into `out/asx` so the embedder skips them),
push per-part (not one big tar), pull + merge.
