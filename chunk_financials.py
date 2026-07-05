"""Text-chunking pipeline for the PDF markets (AU / TW / PH) — stage 4 prep.

Turns each filing's pdftotext output into overlapping ~450-token chunks and
writes a chunks_v1 parquet dataset (embedding-critical subset of HK's schema,
same column names). CPU-only and resumable — runs alongside the rescue; the
GPU only touches the resulting `text` column.

Usage:
    python3 chunk_financials.py au --limit 20        # smoke
    python3 chunk_financials.py au                    # full (parallel)
"""
from __future__ import annotations
import argparse, hashlib, re
from pathlib import Path

DERIVED = Path("/Volumes/OWC Express 1M2/datasets/MARKET_FILINGS/derived")
MARKETS = {"au": "ASX_FINANCIALS", "tw": "TWSE_FINANCIALS", "ph": "PSE_FINANCIALS"}
# CJK chunks smaller: ~1 char ≈ 1 token, vs English ~1 token ≈ 4 chars. Both target ~<450 tokens.
MARKET_META = {
    "au": {"market": "AU", "country": "Australia", "language": "en", "max_chars": 1600, "overlap": 160},
    "tw": {"market": "TW", "country": "Taiwan", "language": "zh", "max_chars": 800, "overlap": 80},
    "ph": {"market": "PH", "country": "Philippines", "language": "en", "max_chars": 1600, "overlap": 160},
}

_WS = re.compile(r"[ \t]+")
_NL = re.compile(r"\n{3,}")
_HEADER_FOOTER = re.compile(r"^\s*(page\s*)?\d{1,4}\s*(of\s*\d{1,4})?\s*$", re.I)
_TABLE = re.compile(r"(\d[\d,.\s]{6,}\d)")
_TOC = re.compile(r"\.{4,}\s*\d+")


def clean_text(text: str) -> str:
    text = text.replace("\f", "\n")
    text = _WS.sub(" ", text)
    text = _NL.sub("\n\n", text)
    return "\n".join(ln.rstrip() for ln in text.split("\n")).strip()


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _approx_tokens(text: str, language: str) -> int:
    return len(text) if language == "zh" else len(text.split())


def _is_header_footer(chunk: str) -> bool:
    s = chunk.strip()
    return len(s) < 60 and bool(_HEADER_FOOTER.match(s))


def _is_toc_junk(chunk: str) -> bool:
    return len(_TOC.findall(chunk)) >= 3


def _has_table_pattern(chunk: str) -> bool:
    return len(_TABLE.findall(chunk)) >= 3


def chunk_text(text: str, max_chars: int, overlap: int):
    """Yield (start_char, end_char, chunk_str); breaks on paragraph/sentence
    boundaries near max_chars, overlaps by `overlap`, covers the whole text."""
    n = len(text)
    if n <= max_chars:
        if text.strip():
            yield (0, n, text)
        return
    start = 0
    while start < n:
        end = min(start + max_chars, n)
        if end < n:
            window = text[start:end]
            brk = max(window.rfind("\n\n"), window.rfind(". "), window.rfind("。"))
            if brk > max_chars * 0.5:
                end = start + brk + 1
        chunk = text[start:end]
        if chunk.strip():
            yield (start, end, chunk)
        if end >= n:
            break
        start = max(end - overlap, start + 1)


def chunk_document(text: str, source_stem: str, market: str, year, stock_code, company_name) -> list[dict]:
    meta = MARKET_META[market]
    cleaned = clean_text(text)
    doc_id = f"{meta['market'].lower()}_doc_" + hashlib.sha1(source_stem.encode()).hexdigest()[:22]
    rows = []
    for idx, (s, e, chunk) in enumerate(chunk_text(cleaned, meta["max_chars"], meta["overlap"])):
        h = _content_hash(chunk)
        rows.append({
            "chunk_id": f"{meta['market']}_{source_stem}_{idx:04d}_{h[:10]}",
            "doc_id": doc_id,
            "source_stem": source_stem,
            "year": int(year) if year is not None else None,
            "market": meta["market"], "country": meta["country"], "language": meta["language"],
            "chunk_idx": idx, "start_char": s, "end_char": e, "text": chunk,
            "char_count": len(chunk), "token_count": _approx_tokens(chunk, meta["language"]),
            "content_hash": h,
            "is_toc_junk": _is_toc_junk(chunk),
            "is_header_footer": _is_header_footer(chunk),
            "has_table_pattern": _has_table_pattern(chunk),
            "stock_code": str(stock_code) if stock_code is not None else None,
            "company_name": company_name or "",
        })
    return rows


# ---- pipeline ----
def _pdf_targets(market: str):
    if market == "au":
        from asx_financials_sweep import build_targets, RAW_ROOT
        return build_targets(RAW_ROOT, None)
    if market == "tw":
        from twse_financials_sweep import build_targets, RAW_ROOT
        return build_targets(RAW_ROOT, None)
    from pse_financials_sweep import build_targets, RAW_ROOT
    return build_targets(RAW_ROOT)


def _process_one(t: dict, market: str):
    from asx_financials_extract import pdf_to_text
    try:
        text = pdf_to_text(t["pdf_path"])
        if not text or len(text) < 200:
            return []
        stem = str(t.get("doc_id") or Path(t["pdf_path"]).stem)
        return chunk_document(text, stem, market, t.get("fiscal_year"),
                              t.get("ticker"), t.get("company_name", ""))
    except Exception:
        return []


def chunk_market(market: str, limit: int = 0, workers: int = 8, batch_docs: int = 400) -> dict:
    import pandas as pd
    from concurrent.futures import ProcessPoolExecutor, as_completed
    out_dir = DERIVED / MARKETS[market] / "chunks_v1"
    out_dir.mkdir(parents=True, exist_ok=True)
    existing = sorted(out_dir.glob("part_*.parquet"))
    done_stems = set()                                   # resume: skip docs already chunked
    for p in existing:
        try:
            done_stems |= set(pd.read_parquet(p, columns=["source_stem"])["source_stem"].astype(str).unique())
        except Exception:
            pass
    targets = _pdf_targets(market)
    if limit:
        targets = targets[:limit]
    targets = [t for t in targets
               if str(t.get("doc_id") or Path(t["pdf_path"]).stem) not in done_stems]
    stats = {"docs": len(targets), "already_done": len(done_stems),
             "chunked_docs": 0, "chunks": 0, "batches": len(existing)}
    buf, part = [], len(existing)
    with ProcessPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(_process_one, t, market): i for i, t in enumerate(targets)}
        pending_docs = 0
        for fut in as_completed(futs):
            rows = fut.result() or []
            if rows:
                buf.extend(rows)
                stats["chunked_docs"] += 1
                stats["chunks"] += len(rows)
            pending_docs += 1
            if pending_docs >= batch_docs and buf:
                pd.DataFrame(buf).to_parquet(out_dir / f"part_{part:05d}.parquet", index=False)
                part += 1; buf = []; pending_docs = 0
    if buf:
        pd.DataFrame(buf).to_parquet(out_dir / f"part_{part:05d}.parquet", index=False)
    return stats


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("market", choices=list(MARKETS))
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--workers", type=int, default=8)
    a = p.parse_args()
    print(chunk_market(a.market, a.limit, a.workers))
