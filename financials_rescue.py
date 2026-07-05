"""Local-LLM financial rescue for the PDF markets (AU/TW/PH).

The pdftotext+regex extractor gets balance-sheet totals right but grabs
note-refs/wrong columns for revenue/income on messy layouts. This re-extracts
ONLY the suspect rows via a local Qwen3-4B endpoint on the tailnet — free, no
API/subscription cost. Proven: rescued MIN revenue 40 -> 4,779.

Usage:
    python3 financials_rescue.py au --limit 20        # proof batch
    python3 financials_rescue.py tw --endpoint http://<host>:8080
"""
from __future__ import annotations
import argparse, json, re, urllib.request
from pathlib import Path
from typing import Optional

DEFAULT_ENDPOINT = "http://000cd4a2d4fa9a24-362.tail27957c.ts.net:8080"
MODEL = "Qwen3-4B-4bit"
DERIVED = Path("/Volumes/OWC Express 1M2/datasets/MARKET_FILINGS/derived")
MARKETS = {"au": "ASX_FINANCIALS", "tw": "TWSE_FINANCIALS", "ph": "PSE_FINANCIALS"}

_EN_KW = ("revenue", "sales", "cost of", "gross profit", "profit for", "profit before",
          "operating profit", "income tax", "total assets", "total equity", "total liabilities",
          "net cash", "net profit", "npat")
_ZH_KW = ("營業收入", "營業成本", "營業毛利", "營業利益", "本期淨利", "稅前", "所得稅",
          "資產總", "負債總", "權益總", "營業活動", "投資活動", "籌資活動")
_RESCUE_FIELDS = ("revenue", "net_income", "operating_profit", "total_assets", "total_equity")


def is_suspect(rec: dict) -> bool:
    """Flag rows whose regex-extracted income-statement values look wrong."""
    rev = rec.get("revenue")
    if rev is None:
        return True
    try:
        rev = abs(float(rev))
    except (TypeError, ValueError):
        return True
    ta = rec.get("total_assets")
    try:
        ta = abs(float(ta)) if ta is not None else None
    except (TypeError, ValueError):
        ta = None
    if ta and rev < 1000 and ta > 100 * max(rev, 1):      # revenue implausibly tiny vs assets
        return True
    ni = rec.get("net_income")
    try:
        ni = abs(float(ni)) if ni is not None else None
    except (TypeError, ValueError):
        ni = None
    if ni and rev and ni > 2 * rev:                        # net income far exceeds revenue
        return True
    return False


def parse_model_json(content: Optional[str]) -> Optional[dict]:
    """Extract the JSON object from a Qwen3 reply (strip <think> + code fences)."""
    if not content:
        return None
    content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL)
    content = content.replace("```json", "").replace("```", "")
    for obj in reversed(re.findall(r"\{[^{}]*\}", content, flags=re.DOTALL)):
        try:
            return json.loads(obj)
        except json.JSONDecodeError:
            continue
    return None


def income_snippet(text: str, market: str, max_chars: int = 3500) -> str:
    """Focused statement region: keep label-bearing lines (with their numbers)."""
    kws = _ZH_KW if market == "tw" else _EN_KW
    keep = []
    for ln in text.splitlines():
        hay = ln if market == "tw" else ln.lower()
        if any(k in hay for k in kws):
            keep.append(ln.strip())
    return "\n".join(keep)[:max_chars]


def call_model(snippet: str, market: str, endpoint: str) -> Optional[dict]:
    lang = "Traditional-Chinese" if market == "tw" else "English"
    yr = "leftmost" if market == "tw" else "rightmost/latest"
    prompt = (f"/no_think Extract CURRENT-year values from this {lang} financial statement "
              f"as strict JSON with numeric keys: revenue, net_income, operating_profit, "
              f"total_assets, total_equity. Use the {yr} year column; ignore prior years and "
              f"note-reference numbers. Values exactly as shown. Output ONLY the JSON.\n\n{snippet}")
    body = json.dumps({"model": MODEL, "messages": [{"role": "user", "content": prompt}],
                       "max_tokens": 250, "temperature": 0}).encode()
    req = urllib.request.Request(endpoint + "/v1/chat/completions", body,
                                 {"Content-Type": "application/json"})
    try:
        resp = json.load(urllib.request.urlopen(req, timeout=120))
        return parse_model_json(resp["choices"][0]["message"]["content"])
    except Exception:
        return None


def _row_key(rec: dict, market: str) -> str:
    """Join key from a metrics row to its pdf_path. PSE's parquet has no doc_id,
    so fall back to ticker|filing_date (au/tw carry doc_id)."""
    if market == "ph":
        return f"{rec.get('ticker')}|{rec.get('filing_date')}"
    return str(rec.get("doc_id"))


def _pdf_path_by_doc(market: str) -> dict:
    """row-key -> pdf_path, rebuilt from the market's sweep build_targets (the
    parquet drops pdf_path in consolidate()). Key matches _row_key()."""
    if market == "au":
        from asx_financials_sweep import build_targets, RAW_ROOT
        return {t["doc_id"]: t["pdf_path"] for t in build_targets(RAW_ROOT, None)}
    if market == "tw":
        from twse_financials_sweep import build_targets, RAW_ROOT
        return {t["doc_id"]: t["pdf_path"] for t in build_targets(RAW_ROOT, None)}
    from pse_financials_sweep import build_targets, RAW_ROOT
    return {f"{t['ticker']}|{t['filing_date']}": t["pdf_path"] for t in build_targets(RAW_ROOT)}


def rescue_market(market: str, endpoint: str, limit: int = 0) -> dict:
    import pandas as pd
    from asx_financials_extract import pdf_to_text
    d = DERIVED / MARKETS[market]
    df = pd.read_parquet(d / "canonical_metrics_wide.parquet")
    path_by_doc = _pdf_path_by_doc(market)
    out_jsonl = d / "rescue_results.jsonl"
    done = set()
    if out_jsonl.exists():
        for ln in out_jsonl.read_text().splitlines():
            try:
                done.add(json.loads(ln)["pdf_path"])
            except (json.JSONDecodeError, KeyError):
                pass
    recs = df.to_dict("records")
    suspects = []
    for r in recs:
        if not is_suspect(r):
            continue
        r["pdf_path"] = path_by_doc.get(_row_key(r, market))
        if r["pdf_path"] and r["pdf_path"] not in done:
            suspects.append(r)
    if limit:
        suspects = suspects[:limit]
    stats = {"suspect_total": sum(1 for r in recs if is_suspect(r)),
             "attempted": 0, "rescued": 0}
    for r in suspects:
        text = pdf_to_text(r["pdf_path"])
        got = call_model(income_snippet(text, market), market, endpoint)
        stats["attempted"] += 1
        if not got:                      # transient endpoint failure -> don't mark done, retry next run
            stats.setdefault("failed", 0)
            stats["failed"] += 1
            continue
        stats["rescued"] += 1
        rec = {"pdf_path": r["pdf_path"], "ticker": r.get("ticker"),
               "before": {k: r.get(k) for k in _RESCUE_FIELDS}, "rescued": got}
        with out_jsonl.open("a") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return stats


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("market", choices=list(MARKETS))
    p.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    p.add_argument("--limit", type=int, default=0)
    a = p.parse_args()
    print(rescue_market(a.market, a.endpoint, a.limit))
