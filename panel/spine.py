"""Securities master: market-local identifier -> FIGI, with surrogate fallback."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable, Dict, Iterable, List, Optional, Tuple

import requests

OPENFIGI_URL = "https://api.openfigi.com/v3/mapping"
BATCH_SIZE = 100

# OpenFIGI exchange codes per market.
#
# NOTE (verified against the live OpenFIGI API on 2026-08-17): "in_bse" is
# known NOT to resolve via this path. OpenFIGI does not map BSE numeric scrip
# codes (idValue="500325" misses under both exchCode "IB" and "IN") — only
# ticker symbols like "RELIANCE" resolve, and the corpus stores only numeric
# scrip codes (no ISIN, no symbol). Per human-partner ruling, v1 accepts
# surrogate keys for in_bse rather than inventing a scrip->symbol crosswalk.
EXCH_CODES = {
    "au": "AU", "tw": "TT", "ph": "PM", "kr": "KS",
    "cn": "CH", "in_bse": "IS", "hk": "HK", "jp": "JT",
}

Key = Tuple[str, str]


def surrogate_key(exchange: str, local_id: str) -> str:
    return f"{str(exchange).strip().upper()}:{str(local_id).strip().upper()}"


def normalize_identifier(market: str, local_id: str) -> str:
    """Market-specific cleanup before OpenFIGI lookup.

    HK codes are stored zero-padded (00700) but OpenFIGI only matches the
    unpadded form (700); verified against the live API 2026-08-17. Other
    markets (notably CN, which keeps significant leading zeros, e.g.
    000001) are passed through unchanged.
    """
    local_id = str(local_id).strip()
    if market == "hk":
        stripped = local_id.lstrip("0")
        return stripped or "0"
    return local_id


def figi_request(market: str, local_id: str) -> Dict[str, str]:
    return {
        "idType": "TICKER",
        "idValue": normalize_identifier(market, local_id),
        "exchCode": EXCH_CODES.get(market, market.upper()),
    }


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def parse_figi_response(blocks: List[dict], requests_sent: List[Key]) -> Dict[Key, dict]:
    out: Dict[Key, dict] = {}
    for key, block in zip(requests_sent, blocks or []):
        data = (block or {}).get("data") or []
        if not data:
            continue
        first = data[0]
        out[key] = {
            "figi": first.get("figi"),
            "name": first.get("name"),
            "exchange": first.get("exchCode"),
            "security_type": first.get("securityType"),
            "composite_figi": first.get("compositeFIGI"),
            "share_class_figi": first.get("shareClassFIGI"),
            "resolution_source": "openfigi",
            "resolved_at": _utc_now(),
        }
    return out


def _http_fetcher(batch: List[Key]) -> List[dict]:
    payload = [figi_request(m, i) for m, i in batch]
    resp = requests.post(
        OPENFIGI_URL, json=payload,
        headers={"Content-Type": "application/json"}, timeout=60,
    )
    resp.raise_for_status()
    return resp.json()


def resolve(
    identifiers: Iterable[Key],
    fetcher: Optional[Callable[[List[Key]], List[dict]]] = None,
    cache: Optional[Dict[Key, dict]] = None,
) -> Dict[Key, dict]:
    """Resolve identifiers, preferring cache, degrading to surrogate keys."""
    fetch = fetcher or _http_fetcher
    cache = dict(cache or {})
    out: Dict[Key, dict] = {}
    todo: List[Key] = []
    for key in identifiers:
        if key in cache:
            out[key] = dict(cache[key])
        elif key not in todo:
            todo.append(key)

    for start in range(0, len(todo), BATCH_SIZE):
        batch = todo[start : start + BATCH_SIZE]
        try:
            blocks = fetch(batch)
            out.update(parse_figi_response(blocks, batch))
        except Exception:
            # OpenFIGI unavailable: fall through to surrogate keys below.
            pass

    for market, local_id in todo:
        key = (market, local_id)
        entry = out.get(key)
        if entry and entry.get("figi"):
            entry["spine_key"] = entry["figi"]
        else:
            out[key] = {
                "figi": None, "name": None,
                "exchange": EXCH_CODES.get(market, market.upper()),
                "security_type": None, "composite_figi": None,
                "share_class_figi": None,
                "resolution_source": "surrogate",
                "resolved_at": _utc_now(),
                "spine_key": surrogate_key(EXCH_CODES.get(market, market), local_id),
            }
    for key, entry in out.items():
        entry.setdefault("spine_key", surrogate_key(EXCH_CODES.get(key[0], key[0]), key[1]))
    return out
