import time, torch, pandas as pd, glob
import torch.nn.functional as F
from transformers import AutoTokenizer, AutoModel
M = "intfloat/multilingual-e5-large"
tok = AutoTokenizer.from_pretrained(M, use_fast=True)
for impl in ["sdpa", "eager"]:
    try:
        model = AutoModel.from_pretrained(M, attn_implementation=impl).to("cuda").half().eval()
        used = getattr(model.config, "_attn_implementation", "?")
        df = pd.read_parquet(sorted(glob.glob("/home/shadeform/embed/pse/chunks_v1/*.parquet"))[0], columns=["text"])
        texts = ["passage: " + t for t in df["text"].fillna("").tolist()[:512]]
        t0 = time.time(); b = tok(texts, max_length=512, padding=True, truncation=True, return_tensors="pt"); t1 = time.time()
        seq = b["input_ids"].shape[1]; b = {k: v.to("cuda") for k, v in b.items()}
        with torch.inference_mode():
            model(**b); torch.cuda.synchronize(); t2 = time.time()
            for _ in range(3): o = model(**b).last_hidden_state
            torch.cuda.synchronize(); t3 = time.time()
        fwd = (t3 - t2) / 3
        print(f"impl_req={impl} impl_used={used} tok512={t1-t0:.2f}s seq={seq} fwd512={fwd:.2f}s => {512/fwd:.0f}/s gpu-only", flush=True)
    except Exception as e:
        print(f"impl_req={impl} FAILED {type(e).__name__}: {e}", flush=True)
print("BENCH_DONE", flush=True)
