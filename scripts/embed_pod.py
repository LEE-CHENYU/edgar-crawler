#!/usr/bin/env python3
"""Embed chunk parquets on a rented GPU. RUNS ON THE POD, not this machine.

Local ML models are barred on the laptop (8 GB RAM, and a standing rule against
installing them), so this is the one step that genuinely needs rented hardware.
Everything before it -- PDF text extraction and chunking -- runs locally because
the data is local and moving 471 GB costs more than the compute saves.

Sizing, from the HK run's actual invoice rather than a rate card: 16,222,033
chunks cost ~$10-12 of GPU. This job is ~22.1M chunks (127,674 documents x ~173
chunks), so ~1.36x that. On a Prime Intellect A6000 48GB at $0.54/hr it fits
inside the existing $18.65 credit balance.

CONTRACT. intfloat/multilingual-e5-large, 1024 dims, normalized, matching
contract.json shipped alongside the chunks. e5 REQUIRES the "passage: " prefix
on documents and "query: " on queries -- omitting it degrades retrieval badly
and silently, and would make these vectors subtly incompatible with the HK/CN/JP
layers they are meant to be searched beside.

Checkpoints every --checkpoint-every batches: a rented pod that dies at hour 18
must not cost the whole run.

Usage (on the pod):
    python embed_pod.py --chunks /data/chunks --out /data/embeddings
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

PASSAGE_PREFIX = "passage: "   # e5 requirement; see module docstring


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--chunks", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--batch-size", type=int, default=256)
    ap.add_argument("--checkpoint-every", type=int, default=200)
    ap.add_argument("--model", default=None, help="override; defaults to contract.json")
    args = ap.parse_args()

    import numpy as np
    import pandas as pd
    import torch
    from sentence_transformers import SentenceTransformer

    contract = json.loads((args.chunks / "contract.json").read_text())
    model_id = args.model or contract["embedding_model_id"]
    dim = int(contract["embedding_dim"])
    normalize = bool(contract["normalize_embeddings"])
    print(f"contract: {model_id} dim={dim} normalize={normalize}", flush=True)

    device = "cuda" if torch.cuda.is_available() else "cpu"
    if device == "cpu":
        print("WARNING: no GPU visible -- this will take days, not hours", flush=True)
    model = SentenceTransformer(model_id, device=device)
    args.out.mkdir(parents=True, exist_ok=True)

    for parquet in sorted(args.chunks.glob("*_chunks.parquet")):
        corpus = parquet.stem.replace("_chunks", "")
        dest = args.out / f"{corpus}_embeddings.npy"
        meta_dest = args.out / f"{corpus}_index.parquet"
        if dest.exists() and meta_dest.exists():
            print(f"{corpus}: already embedded, skipping", flush=True)
            continue

        df = pd.read_parquet(parquet)
        texts = [PASSAGE_PREFIX + t for t in df["text"].astype(str)]
        print(f"{corpus}: {len(texts):,} chunks", flush=True)

        vectors = np.zeros((len(texts), dim), dtype=np.float16)
        start = time.time()
        for i in range(0, len(texts), args.batch_size):
            batch = texts[i:i + args.batch_size]
            emb = model.encode(batch, batch_size=len(batch),
                               normalize_embeddings=normalize,
                               show_progress_bar=False)
            vectors[i:i + len(batch)] = emb.astype(np.float16)
            done = i + len(batch)
            if (i // args.batch_size) % args.checkpoint_every == 0 and i:
                np.save(dest.with_suffix(".partial.npy"), vectors[:done])
                rate = done / max(time.time() - start, 1e-9)
                left = (len(texts) - done) / max(rate, 1e-9) / 3600
                print(f"  {done:,}/{len(texts):,} {rate:.0f} chunks/s "
                      f"~{left:.1f}h left", flush=True)

        np.save(dest, vectors)
        df.drop(columns=["text"]).to_parquet(meta_dest, index=False)
        partial = dest.with_suffix(".partial.npy")
        if partial.exists():
            partial.unlink()
        print(f"{corpus}: wrote {dest} {vectors.shape}", flush=True)

    (args.out / "contract.json").write_text(json.dumps(contract, indent=1))
    print("done", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
