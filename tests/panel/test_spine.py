from panel.spine import (
    EXCH_CODES, figi_request, parse_figi_response, resolve, surrogate_key,
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

def test_resolve_batches_at_100():
    sizes = []
    def fetcher(batch):
        sizes.append(len(batch))
        return [{"warning": "no"} for _ in batch]
    resolve([("au", f"T{i}") for i in range(250)], fetcher=fetcher)
    assert sizes == [100, 100, 50]

def test_resolve_degrades_to_surrogate_when_fetcher_raises():
    """OpenFIGI being unavailable must not fail the build."""
    def fetcher(batch):
        raise RuntimeError("openfigi down")
    got = resolve([("au", "BHP")], fetcher=fetcher)
    assert got[("au", "BHP")]["resolution_source"] == "surrogate"
