"""forward_refresh.py --data-root: stage downloads outside the default OWC root."""
import importlib.util
import sys
import types
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location("forward_refresh_t", REPO / "scripts" / "forward_refresh.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_default_root_unchanged():
    fr = _load()
    assert str(fr.DATA_ROOT) == "/Volumes/OWC Express 1M2/datasets/MARKET_FILINGS"
    assert fr.META == fr.DATA_ROOT / "metadata" / "MARKET_FILINGS_METADATA.csv"


def test_set_data_root_moves_meta(tmp_path):
    fr = _load()
    fr.set_data_root(tmp_path)
    assert fr.DATA_ROOT == tmp_path
    assert fr.META == tmp_path / "metadata" / "MARKET_FILINGS_METADATA.csv"


def test_known_ids_missing_meta_is_empty(tmp_path):
    fr = _load()
    fr.set_data_root(tmp_path)
    assert fr.known_ids("dart") == set()


def test_main_uses_data_root_for_raw_and_meta(tmp_path, monkeypatch):
    fr = _load()
    seen = {}
    fake = types.ModuleType("download_market_filings")
    fake.load_config = lambda p: {"markets": {"dart": {}}}

    def collect_market(market, mc, raw_dir, download_documents, timeout):
        seen["raw_dir"] = raw_dir
        return []

    fake.collect_market = collect_market
    fake.write_metadata = lambda *a, **k: None
    monkeypatch.setitem(sys.modules, "download_market_filings", fake)
    monkeypatch.setattr(sys, "argv", ["x", "--market", "dart", "--start", "2026-03-01",
                                      "--end", "2026-03-01", "--data-root", str(tmp_path)])
    fr.main()
    assert seen["raw_dir"] == str(tmp_path / "raw")
