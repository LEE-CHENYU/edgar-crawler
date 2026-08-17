import requests

from panel.spine import (
    BATCH_SIZE, EXCH_CODES, figi_request, load_spine_cache, normalize_identifier,
    parse_figi_response, resolve, save_spine_cache, surrogate_key,
)

def test_surrogate_key_is_stable_and_namespaced():
    assert surrogate_key("AU", "BHP") == "AU:BHP"
    assert surrogate_key("au", " bhp ") == "AU:BHP"

def test_figi_request_uses_ticker_not_base_ticker():
    """BASE_TICKER additionally requires securityType2 and errors without it."""
    req = figi_request("au", "BHP")
    assert req == {"idType": "TICKER", "idValue": "BHP", "exchCode": "AU"}

def test_exch_codes_cover_v1_markets():
    for market in ("au", "tw", "ph", "kr", "cn", "in_bse", "hk", "jp"):
        assert market in EXCH_CODES

def test_parse_figi_response_maps_hits_to_requests():
    sent = [("au", "BHP"), ("tw", "2330")]
    blocks = [
        {"data": [{"figi": "BBG000D0D358", "name": "BHP GROUP LTD",
                   "exchCode": "AU", "securityType": "Common Stock",
                   "compositeFIGI": "BBG000D0D35X", "shareClassFIGI": "BBG001S5N8V8"}]},
        {"data": [{"figi": "BBG000BN2JD8", "name": "TAIWAN SEMICONDUCTOR",
                   "exchCode": "TT", "securityType": "Common Stock",
                   "compositeFIGI": None, "shareClassFIGI": None}]},
    ]
    got = parse_figi_response(blocks, sent)
    assert got[("au", "BHP")]["figi"] == "BBG000D0D358"
    assert got[("tw", "2330")]["name"] == "TAIWAN SEMICONDUCTOR"
    assert got[("au", "BHP")]["resolution_source"] == "openfigi"

def test_parse_figi_response_skips_misses():
    sent = [("au", "NOPE")]
    blocks = [{"warning": "No identifier found."}]
    assert parse_figi_response(blocks, sent) == {}

def test_parse_figi_response_tolerates_short_block_list():
    """A truncated response must not raise or misalign."""
    sent = [("au", "A"), ("au", "B")]
    blocks = [{"data": [{"figi": "F1", "name": "A LTD"}]}]
    got = parse_figi_response(blocks, sent)
    assert list(got) == [("au", "A")]

def test_resolve_falls_back_to_surrogate_when_unresolved():
    def fetcher(batch):
        return [{"warning": "No identifier found."} for _ in batch]
    got = resolve([("au", "OBSCURE")], fetcher=fetcher)
    entry = got[("au", "OBSCURE")]
    assert entry["spine_key"] == "AU:OBSCURE"
    assert entry["figi"] is None
    assert entry["resolution_source"] == "surrogate"

def test_resolve_uses_figi_as_spine_key_when_available():
    def fetcher(batch):
        return [{"data": [{"figi": "BBG1", "name": "X LTD", "exchCode": "AU"}]}]
    got = resolve([("au", "X")], fetcher=fetcher)
    assert got[("au", "X")]["spine_key"] == "BBG1"

def test_resolve_never_calls_fetcher_for_cached_identifiers():
    calls = []
    def fetcher(batch):
        calls.append(batch)
        return [{"warning": "no"} for _ in batch]
    cache = {("au", "BHP"): {"spine_key": "BBG000D0D358", "figi": "BBG000D0D358",
                             "resolution_source": "openfigi"}}
    got = resolve([("au", "BHP")], fetcher=fetcher, cache=cache)
    assert calls == []
    assert got[("au", "BHP")]["figi"] == "BBG000D0D358"

def test_resolve_batches_at_openfigi_limit_of_10():
    """INVERTED from the original assertion of [100, 100, 50], which pinned the
    bug that made the entire v1 build 100% surrogate: the unauthenticated
    OpenFIGI limit is 10 mapping jobs per request and 11 returns 413 (probed
    live 2026-08-17). A batch of 100 can never resolve anything."""
    sizes = []
    def fetcher(batch):
        sizes.append(len(batch))
        return [{"warning": "no"} for _ in batch]
    resolve([("au", f"T{i}") for i in range(25)], fetcher=fetcher)
    assert sizes == [10, 10, 5]
    assert max(sizes) <= 10

def test_resolve_degrades_to_surrogate_when_fetcher_raises():
    """OpenFIGI being unavailable must not fail the build."""
    def fetcher(batch):
        raise RuntimeError("openfigi down")
    got = resolve([("au", "BHP")], fetcher=fetcher)
    assert got[("au", "BHP")]["resolution_source"] == "surrogate"


# --- Fix round 1 ---

def test_resolve_does_not_mutate_callers_cache_dict():
    """A caller who loads a persisted cache and re-serializes it later must
    not see resolve() silently mutate the entries it passed in."""
    original_entry = {"figi": "BBG000D0D358", "resolution_source": "openfigi"}
    cache = {("au", "BHP"): original_entry}

    def fetcher(batch):
        return [{"warning": "no"} for _ in batch]

    resolve([("au", "BHP")], fetcher=fetcher, cache=cache)

    assert "spine_key" not in original_entry
    assert cache[("au", "BHP")] is original_entry
    assert "spine_key" not in cache[("au", "BHP")]


def test_normalize_identifier_strips_hk_zero_padding():
    assert normalize_identifier("hk", "00700") == "700"
    assert normalize_identifier("hk", "00158") == "158"


def test_normalize_identifier_leaves_non_hk_untouched():
    assert normalize_identifier("au", "BHP") == "BHP"


def test_normalize_identifier_keeps_cn_leading_zeros():
    assert normalize_identifier("cn", "000001") == "000001"


def test_normalize_identifier_is_idempotent_for_hk():
    assert normalize_identifier("hk", "700") == "700"


def test_figi_request_normalizes_hk_identifier():
    assert figi_request("hk", "00700")["idValue"] == "700"


def test_resolve_surrogate_key_keeps_original_unpadded_hk_local_id():
    """The surrogate key must match the corpus's original (padded) local_id,
    even though the OpenFIGI request itself uses the normalized form."""
    def fetcher(batch):
        return [{"warning": "No identifier found."} for _ in batch]
    got = resolve([("hk", "00700")], fetcher=fetcher)
    assert got[("hk", "00700")]["spine_key"] == "HK:00700"


# --- Final fix wave: FIX 1 (batch size, counted failures, retry policy) ---


def _http_error(status: int) -> requests.HTTPError:
    resp = requests.Response()
    resp.status_code = status
    return requests.HTTPError(f"{status}", response=resp)


def test_batch_size_never_exceeds_openfigi_unauthenticated_limit():
    assert BATCH_SIZE <= 10


def test_resolve_reports_nonzero_failure_count_when_every_batch_raises():
    """The v1 failure mode: 100% surrogate with nothing reported. Both halves
    must now be visible -- the surrogate outcome AND the failure count."""
    def fetcher(batch):
        raise RuntimeError("openfigi down")

    stats = {}
    got = resolve([("au", f"T{i}") for i in range(15)], fetcher=fetcher,
                  stats=stats, sleep=lambda _s: None)

    assert len(got) == 15
    assert all(e["resolution_source"] == "surrogate" for e in got.values())
    assert stats["surrogate"] == 15
    assert stats["resolved"] == 0
    assert stats["batch_failures"] > 0
    assert stats["batches"] == 2


def test_resolve_counts_resolved_and_surrogate_separately():
    def fetcher(batch):
        return [
            {"data": [{"figi": "BBG1", "name": "X"}]} if m_i[1] == "HIT" else {"warning": "no"}
            for m_i in batch
        ]

    stats = {}
    resolve([("au", "HIT"), ("au", "MISS")], fetcher=fetcher, stats=stats,
            sleep=lambda _s: None)
    assert stats["resolved"] == 1
    assert stats["surrogate"] == 1


def test_resolve_retries_on_429_then_succeeds():
    calls = []

    def fetcher(batch):
        calls.append(batch)
        if len(calls) == 1:
            raise _http_error(429)
        return [{"data": [{"figi": "BBG1", "name": "X"}]} for _ in batch]

    slept = []
    stats = {}
    got = resolve([("au", "X")], fetcher=fetcher, stats=stats, sleep=slept.append)
    assert got[("au", "X")]["figi"] == "BBG1"
    assert len(calls) == 2
    assert slept and slept[0] > 0
    assert stats["rate_limited"] == 1
    assert stats["batch_failures"] == 0


def test_resolve_does_not_retry_413_and_counts_it_as_config_error(capsys):
    """413 means the batch is oversized; retrying it at the same size forever
    is exactly how the original bug hid. One attempt, loud log, counted."""
    calls = []

    def fetcher(batch):
        calls.append(batch)
        raise _http_error(413)

    stats = {}
    resolve([("au", "X")], fetcher=fetcher, stats=stats, sleep=lambda _s: None)
    assert len(calls) == 1
    assert stats["oversize_batch_errors"] == 1
    assert stats["batch_failures"] == 1
    assert "413" in capsys.readouterr().out


# --- FIX 6: spine cache (spec Sec 3.1) ---


def test_spine_cache_round_trips_resolved_entries(tmp_path):
    resolved = {("au", "BHP"): {"figi": "BBG000D0D358", "name": "BHP GROUP LTD",
                                "spine_key": "BBG000D0D358",
                                "resolution_source": "openfigi",
                                "resolved_at": "2026-08-17T00:00:00+00:00"}}
    save_spine_cache(tmp_path, resolved)
    loaded = load_spine_cache(tmp_path)
    assert loaded[("au", "BHP")]["figi"] == "BBG000D0D358"
    assert loaded[("au", "BHP")]["spine_key"] == "BBG000D0D358"


def test_second_resolve_with_cache_present_makes_no_fetcher_calls(tmp_path):
    """The whole point of Sec 3.1: an identifier resolved once is never
    re-resolved, so a rebuild is offline-repeatable."""
    calls = []

    def fetcher(batch):
        calls.append(batch)
        return [{"data": [{"figi": "BBG1", "name": "X LTD", "exchCode": "AU"}]}
                for _ in batch]

    first = resolve([("au", "X")], fetcher=fetcher, sleep=lambda _s: None)
    save_spine_cache(tmp_path, first)
    assert len(calls) == 1

    calls.clear()
    stats = {}
    second = resolve([("au", "X")], fetcher=fetcher,
                     cache=load_spine_cache(tmp_path), stats=stats,
                     sleep=lambda _s: None)
    assert calls == []
    assert second[("au", "X")]["figi"] == "BBG1"
    assert stats["cache_hits"] == 1
    assert stats["batches"] == 0


def test_spine_cache_never_persists_surrogate_fallbacks(tmp_path):
    """Caching a surrogate would make a transient OpenFIGI outage permanent."""
    resolved = {("au", "MISS"): {"figi": None, "spine_key": "AU:MISS",
                                 "resolution_source": "surrogate"}}
    save_spine_cache(tmp_path, resolved)
    assert load_spine_cache(tmp_path) == {}


def test_load_spine_cache_on_missing_file_is_empty(tmp_path):
    assert load_spine_cache(tmp_path / "nope") == {}


def test_resolve_paces_between_batches_to_respect_rate_limit():
    slept = []

    def fetcher(batch):
        return [{"warning": "no"} for _ in batch]

    resolve([("au", f"T{i}") for i in range(25)], fetcher=fetcher,
            sleep=slept.append, pace=2.5)
    # Two gaps between three batches, no sleep before the first.
    assert slept == [2.5, 2.5]
