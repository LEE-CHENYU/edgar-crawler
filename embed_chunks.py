"""Embed chunks_v1 parquet -> multilingual-e5-large vectors (transformers direct).

e5 average-pool + L2 normalize, 'passage: ' prefix. CUDA/MPS/CPU. Output is a
DIRECTORY of per-input-part parquets (emb_<stem>.parquet), resumable (skip done).

Throughput: within each part, chunks are LENGTH-SORTED before batching so each
batch pads to ~its own max (not a global 512), then results are scattered back to
original order. This cuts wasted padding compute massively on varied-length text.

Usage: python embed_chunks.py <chunks_v1_dir> <out_dir> [batch]
"""
import sys, glob, time, os
import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import torch
import torch.nn.functional as F
from transformers import AutoTokenizer, AutoModel

IN_DIR, OUT_DIR = sys.argv[1], sys.argv[2]
device = ("cuda" if torch.cuda.is_available()
          else "mps" if torch.backends.mps.is_available() else "cpu")
BATCH = int(sys.argv[3]) if len(sys.argv) > 3 else (384 if device == "cuda" else 32)
MODEL = os.environ.get("E5_MODEL", "/Users/lichenyu/models/multilingual-e5-large")

os.makedirs(OUT_DIR, exist_ok=True)
tok = AutoTokenizer.from_pretrained(MODEL, use_fast=True)
try:
    model = AutoModel.from_pretrained(MODEL, attn_implementation="sdpa").to(device).eval()
except Exception:
    model = AutoModel.from_pretrained(MODEL).to(device).eval()
if device == "cuda":
    model = model.half()
print(f"[embed] device={device} batch={BATCH} fast_tok={tok.is_fast} model={MODEL}", flush=True)


def _avg_pool(last_hidden, mask):
    last_hidden = last_hidden.masked_fill(~mask[..., None].bool(), 0.0)
    return last_hidden.sum(dim=1) / mask.sum(dim=1)[..., None]


@torch.inference_mode()
def embed_batch(texts):
    b = tok(texts, max_length=512, padding=True, truncation=True, return_tensors="pt").to(device)
    emb = _avg_pool(model(**b).last_hidden_state, b["attention_mask"])
    return F.normalize(emb, p=2, dim=1).cpu().float().numpy()


def embed_all(texts):
    """Length-sorted batching; returns embeddings in ORIGINAL order."""
    n = len(texts)
    order = sorted(range(n), key=lambda i: len(texts[i]))       # short -> long
    out = np.zeros((n, 1024), dtype=np.float32)
    for j in range(0, n, BATCH):
        idx = order[j:j + BATCH]
        vecs = embed_batch(["passage: " + texts[i] for i in idx])
        for k, i in enumerate(idx):
            out[i] = vecs[k]
    return out


files = sorted(glob.glob(IN_DIR + "/**/*.parquet", recursive=True))
print(f"[embed] {len(files)} input files -> {OUT_DIR}", flush=True)
total, t0 = 0, time.time()
for i, f in enumerate(files):
    stem = os.path.splitext(os.path.basename(f))[0]
    out_path = os.path.join(OUT_DIR, f"emb_{stem}.parquet")
    if os.path.exists(out_path):
        print(f"[embed] {i+1}/{len(files)} skip {stem} (done)", flush=True)
        continue
    df = pd.read_parquet(f, columns=["chunk_id", "text"])
    texts = df["text"].fillna("").tolist()
    tp = time.time()
    emb = embed_all(texts) if texts else np.zeros((0, 1024), dtype=np.float32)
    out = pd.DataFrame({"chunk_id": df["chunk_id"].values})
    out["embedding"] = list(emb.astype("float16"))
    tmp = out_path + ".tmp"
    pq.write_table(pa.Table.from_pandas(out, preserve_index=False), tmp)
    os.replace(tmp, out_path)
    total += len(out)
    dt = time.time() - tp
    print(f"[embed] {i+1}/{len(files)} {stem} n={len(out)} {len(out)/max(1e-9,dt):.0f}/s "
          f"total={total:,} avg={total/max(1e-9,time.time()-t0):.0f}/s", flush=True)
print(f"[embed] DONE {total:,} vectors in {(time.time()-t0)/60:.1f} min -> {OUT_DIR}", flush=True)
