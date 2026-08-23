"""Text extraction writes to the BOOT volume, not the full external drive.

2026-08-21: OWC reached 6.6 GB free on 1.8 TB -- 100% -- after the TWSE rescue
added ~27 GB of PDFs and this extractor added ~10.5 GB of text. A 100%-full APFS
volume is also a credible factor in the two disconnects that killed jobs that
day. The boot volume had 361 GB free the whole time.

The rule: NEW derived output goes to the boot volume. OWC holds the existing
1.4 TB corpus and is read-mostly.
"""

import importlib.util
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "_tx", Path(__file__).resolve().parents[1] / "scripts" / "market_text_extract.py")
tx = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tx)


def test_no_market_writes_output_to_the_full_external_drive():
    for name, cfg in tx.MARKETS.items():
        assert "/Volumes/OWC" not in cfg["out"], f"{name} writes to OWC: {cfg['out']}"


def test_every_market_writes_under_the_boot_datasets_root():
    for name, cfg in tx.MARKETS.items():
        assert cfg["out"].startswith("/Users/lichenyu/datasets"), name


def test_raw_inputs_may_still_be_read_from_the_external_drive():
    """OWC stays readable -- it holds the source PDFs. Only writes move."""
    assert any("/Volumes/OWC" in cfg["raw"] for cfg in tx.MARKETS.values())


def test_out_path_mirrors_the_source_tree(tmp_path):
    got = tx.out_path(tmp_path / "a" / "b" / "c.pdf", tmp_path, tmp_path / "out")
    assert got == tmp_path / "out" / "a" / "b" / "c.txt.gz"
