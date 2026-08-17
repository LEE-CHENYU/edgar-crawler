"""Securities master: market-local identifier -> FIGI, with surrogate fallback."""
from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Tuple

import requests

OPENFIGI_URL = "https://api.openfigi.com/v3/mapping"

# HARD LIMIT, not a tuning knob. The unauthenticated OpenFIGI /v3/mapping
# endpoint accepts at most 10 mapping jobs per request; 11 returns
# "413 Payload Too Large: Request may only contain 10 mapping jobs"
# (probed live 2026-08-17: n=1 200, n=10 200, n=11 413, n=100 413).
# This was 100 for the whole of v1, so EVERY batch 413'd and, because
# resolve() swallowed the exception, all 767,541 rows of the first full build
# came out resolution_source='surrogate' with figi=None while the build log
# cheerfully reported "resolving 17,801 identifiers" (a count SUBMITTED, not
# resolved). Do not raise this without an API key and a re-probe.
BATCH_SIZE = 10

# Retry policy: 429 (rate limited) is transient and worth backing off on;
# 413 is a configuration error -- the batch is too big and will be too big
# forever, so retrying it at the same size is pointless and is exactly how
# the original bug stayed invisible.
MAX_RETRIES = 3
BACKOFF_SECONDS = 2.0

# Unauthenticated OpenFIGI allows ~25 requests/minute. At BATCH_SIZE=10 that
# is ~250 identifiers/minute, so pace batches rather than earning a wall of
# 429s. A full first build (~18k identifiers) therefore takes ~75 min ONCE;
# the spine cache (see below) makes every later build near-instant.
PACE_SECONDS = 2.5

# A full first resolution is ~1,800 batches / ~75 min. Checkpoint the cache
# periodically so an interrupted run keeps what it already resolved instead of
# starting over.
CHECKPOINT_BATCHES = 100

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


def status_code_of(exc: BaseException) -> Optional[int]:
    """HTTP status carried by an exception, if any (requests.HTTPError)."""
    response = getattr(exc, "response", None)
    code = getattr(response, "status_code", None)
    try:
        return int(code) if code is not None else None
    except (TypeError, ValueError):
        return None


def _fetch_batch_with_retry(
    fetch: Callable[[List[Key]], List[dict]],
    batch: List[Key],
    stats: Dict[str, int],
    sleep: Callable[[float], None],
) -> Optional[List[dict]]:
    """One batch, with backoff on 429 and a loud hard stop on 413.

    Returns the response blocks, or None when the batch could not be fetched
    (the caller then falls through to surrogate keys). Never raises: a dead
    OpenFIGI must degrade the panel, not fail the build -- but it must be
    COUNTED, which is what stats is for.
    """
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return fetch(batch)
        except Exception as exc:  # noqa: BLE001 - deliberately broad, counted below
            code = status_code_of(exc)
            if code == 413:
                stats["oversize_batch_errors"] += 1
                stats["batch_failures"] += 1
                print(
                    "SPINE CONFIG ERROR: OpenFIGI rejected a batch of "
                    f"{len(batch)} as 413 Payload Too Large. The unauthenticated "
                    "limit is 10 mapping jobs per request; BATCH_SIZE="
                    f"{BATCH_SIZE} is wrong. Not retrying (an oversized batch "
                    "stays oversized). Resolution will degrade to surrogate keys.",
                    flush=True,
                )
                return None
            if code == 429 and attempt < MAX_RETRIES:
                stats["rate_limited"] += 1
                sleep(BACKOFF_SECONDS * (2 ** (attempt - 1)))
                continue
            if attempt < MAX_RETRIES and code is None:
                # Network-level flake (timeout, connection reset): worth one
                # more try. A definite non-429 HTTP status is not.
                sleep(BACKOFF_SECONDS * (2 ** (attempt - 1)))
                continue
            stats["batch_failures"] += 1
            print(
                f"SPINE: batch of {len(batch)} failed after {attempt} attempt(s): "
                f"{type(exc).__name__}: {exc}",
                flush=True,
            )
            return None
    return None


def new_stats() -> Dict[str, int]:
    return {
        "identifiers": 0, "cache_hits": 0, "batches": 0, "batch_failures": 0,
        "oversize_batch_errors": 0, "rate_limited": 0,
        "resolved": 0, "surrogate": 0,
    }


def resolve(
    identifiers: Iterable[Key],
    fetcher: Optional[Callable[[List[Key]], List[dict]]] = None,
    cache: Optional[Dict[Key, dict]] = None,
    stats: Optional[Dict[str, int]] = None,
    sleep: Callable[[float], None] = time.sleep,
    pace: float = PACE_SECONDS,
    checkpoint: Optional[Callable[[Dict[Key, dict]], None]] = None,
) -> Dict[Key, dict]:
    """Resolve identifiers, preferring cache, degrading to surrogate keys.

    Failures are never silent. Pass `stats` (any dict; missing keys are
    initialised) to receive counts of batches attempted, batch failures,
    oversize (413) config errors, 429 backoffs, and resolved-vs-surrogate
    identifiers. The caller is expected to report them -- a 0%-resolution
    build must be impossible to mistake for a good one.
    """
    fetch = fetcher or _http_fetcher
    cache = dict(cache or {})
    if stats is None:
        stats = new_stats()
    else:
        for key, value in new_stats().items():
            stats.setdefault(key, value)
    out: Dict[Key, dict] = {}
    todo: List[Key] = []
    for key in identifiers:
        stats["identifiers"] += 1
        if key in cache:
            out[key] = dict(cache[key])
            stats["cache_hits"] += 1
        elif key not in todo:
            todo.append(key)

    for start in range(0, len(todo), BATCH_SIZE):
        batch = todo[start : start + BATCH_SIZE]
        if start and pace:
            sleep(pace)
        stats["batches"] += 1
        blocks = _fetch_batch_with_retry(fetch, batch, stats, sleep)
        if blocks is not None:
            out.update(parse_figi_response(blocks, batch))
        if checkpoint is not None and stats["batches"] % CHECKPOINT_BATCHES == 0:
            checkpoint(out)
            print(
                f"spine: {stats['batches']} batches done, "
                f"{sum(1 for e in out.values() if e.get('figi'))} resolved so far",
                flush=True,
            )

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
        if entry.get("figi"):
            stats["resolved"] += 1
        else:
            stats["surrogate"] += 1
    return out


# --- Spine cache (spec Sec 3.1: "resolution is never repeated for a known
# identifier"; "the build is offline-repeatable"). ---
#
# Only SUCCESSFUL resolutions are persisted. Caching surrogate fallbacks would
# make a transient outage (or the BATCH_SIZE=100 413 bug) permanent: the second
# build would "hit cache" for 767k identifiers and never retry them, which is
# the opposite of what a cache is for here.
SPINE_CACHE_NAME = "spine_cache.parquet"
_CACHE_FIELDS = (
    "figi", "name", "exchange", "security_type", "composite_figi",
    "share_class_figi", "resolution_source", "resolved_at", "spine_key",
)


def spine_cache_path(out_root) -> Path:
    return Path(out_root) / SPINE_CACHE_NAME


def load_spine_cache(out_root) -> Dict[Key, dict]:
    """Load persisted resolutions; an absent/unreadable cache is simply empty."""
    import pandas as pd

    path = spine_cache_path(out_root)
    if not path.exists():
        return {}
    try:
        df = pd.read_parquet(path)
    except Exception as exc:  # noqa: BLE001
        print(f"SPINE: ignoring unreadable cache {path}: {exc}", flush=True)
        return {}
    cache: Dict[Key, dict] = {}
    for record in df.to_dict("records"):
        market = str(record.get("market") or "")
        local_id = str(record.get("local_id") or "")
        if not market or not local_id or not record.get("figi"):
            continue
        cache[(market, local_id)] = {
            field: (record.get(field) if record.get(field) == record.get(field) else None)
            for field in _CACHE_FIELDS
        }
    return cache


def save_spine_cache(out_root, resolved: Dict[Key, dict]) -> Optional[Path]:
    """Persist every openfigi-resolved entry, merged over any existing cache."""
    import pandas as pd

    merged = load_spine_cache(out_root)
    for (market, local_id), entry in resolved.items():
        if entry.get("figi"):
            merged[(market, local_id)] = {f: entry.get(f) for f in _CACHE_FIELDS}
    if not merged:
        return None
    rows = [
        dict(market=market, local_id=local_id, **entry)
        for (market, local_id), entry in sorted(merged.items())
    ]
    out_root = Path(out_root)
    out_root.mkdir(parents=True, exist_ok=True)
    final = spine_cache_path(out_root)
    tmp = final.with_suffix(".parquet.tmp")
    pd.DataFrame(rows).to_parquet(tmp, index=False)
    os.replace(tmp, final)
    return final
