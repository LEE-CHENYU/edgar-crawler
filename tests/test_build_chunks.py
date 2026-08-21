"""Chunking must match the HK contract exactly, or the embeddings are useless.

CLAUDE.md's cross-model trap: US 04_nlp uses e5-large-v2, HK/CN/JP use
multilingual-e5-large, and cosine-comparing across the two is INVALID. The same
applies to chunk geometry -- vectors built from 1200-char windows are not
comparable to vectors built from 512-char ones, even with an identical model.

The authoritative values come from the HK run's own config snapshot:
    chunk_chars 1200 | overlap_chars 150 | min_chunk_chars 180
    intfloat/multilingual-e5-large | dim 1024 | normalize true
"""

import pytest

from scripts.build_chunks import CONTRACT, chunk_text


def test_the_contract_matches_the_hk_snapshot_exactly():
    """These are not tunable. Changing one silently makes six new corpora
    incomparable with the three already embedded."""
    assert CONTRACT["chunk_chars"] == 1200
    assert CONTRACT["overlap_chars"] == 150
    assert CONTRACT["min_chunk_chars"] == 180
    assert CONTRACT["embedding_model_id"] == "intfloat/multilingual-e5-large"
    assert CONTRACT["embedding_dim"] == 1024
    assert CONTRACT["normalize_embeddings"] is True


def test_a_short_document_yields_one_chunk():
    text = "x" * 500
    chunks = chunk_text(text)
    assert len(chunks) == 1 and chunks[0] == text


def test_a_document_below_the_minimum_yields_nothing():
    """A 20-char fragment embeds to noise."""
    assert chunk_text("x" * 20) == []


def test_windows_overlap_by_the_contract_amount():
    text = "".join(str(i % 10) for i in range(3000))
    chunks = chunk_text(text)
    assert len(chunks) > 1
    tail = chunks[0][-150:]
    assert chunks[1].startswith(tail)


def test_chunks_never_exceed_the_window():
    chunks = chunk_text("y" * 10_000)
    assert all(len(c) <= 1200 for c in chunks)


def test_a_trailing_fragment_below_the_minimum_is_dropped():
    """Otherwise the last chunk of every document is a sliver of noise."""
    text = "z" * (1200 + 150 + 10)
    for c in chunk_text(text):
        assert len(c) >= 180


def test_whitespace_only_text_yields_nothing():
    assert chunk_text("   \n\n\t  ") == []


def test_chunking_is_deterministic():
    text = "abcdefghij" * 400
    assert chunk_text(text) == chunk_text(text)
