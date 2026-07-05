"""TDD for the AU/TW/PH text-chunking pipeline (feeds the e5-large embedder).

Produces the embedding-critical subset of HK's chunks_v1 schema (same column
names) so the GPU embed step is identical across markets.
"""
import chunk_financials as c


def test_clean_text_collapses_whitespace_and_formfeed():
    assert c.clean_text("a\f\n\n\n\nb   c\t\td") == "a\n\nb c d"


def test_short_text_is_single_chunk():
    ch = list(c.chunk_text("hello world", max_chars=100, overlap=10))
    assert len(ch) == 1
    assert ch[0][2] == "hello world"


def test_long_text_chunks_respect_max_and_overlap_and_cover():
    txt = " ".join(f"w{i}" for i in range(1000))
    ch = list(c.chunk_text(txt, max_chars=200, overlap=40))
    assert len(ch) > 1
    assert all(len(t) <= 200 for _, _, t in ch)     # respects max_chars
    assert ch[0][0] == 0 and ch[-1][1] == len(txt)  # full coverage
    assert ch[1][0] < ch[0][1]                       # overlap: next starts before prev ends


def test_chunk_document_schema_sequence_and_ids():
    rows = c.chunk_document("x " * 1000, source_stem="ABC_2023_1", market="au",
                            year=2023, stock_code="ABC", company_name="Abc Ltd")
    assert len(rows) > 1
    assert rows[0]["market"] == "AU" and rows[0]["country"] == "Australia"
    assert rows[0]["language"] == "en"
    assert [r["chunk_idx"] for r in rows] == list(range(len(rows)))
    assert rows[0]["chunk_id"].startswith("AU_ABC_2023_1_0000_")
    assert all(len(r["content_hash"]) == 64 for r in rows)
    assert all(r["source_stem"] == "ABC_2023_1" for r in rows)


def test_tw_gets_chinese_metadata_and_denser_chunks():
    rows = c.chunk_document("營業收入淨額" * 400, source_stem="2330_2023", market="tw",
                            year=2023, stock_code="2330", company_name="TSMC")
    assert rows[0]["language"] == "zh" and rows[0]["country"] == "Taiwan"
    assert all(r["char_count"] <= c.MARKET_META["tw"]["max_chars"] for r in rows)


def test_header_footer_flag():
    assert c._is_header_footer("Page 12")
    assert not c._is_header_footer(
        "This is a substantial paragraph about the company's operations and outlook.")


def test_content_hash_is_stable():
    assert c._content_hash("abc") == c._content_hash("abc")
    assert c._content_hash("abc") != c._content_hash("abd")
