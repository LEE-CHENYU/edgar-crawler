"""TDD for the local-LLM financial rescue (Qwen3-4B on the tailnet).

Targets only suspect extractions (regex got revenue/income wrong), feeds a
focused income-statement snippet, parses the model's JSON.
"""
import financials_rescue as r


# ---- suspect flagging (which regex rows to rescue) ----
def test_missing_revenue_is_suspect():
    assert r.is_suspect({"net_income": 100, "total_assets": 50000})

def test_tiny_revenue_vs_assets_is_suspect():
    # regex grabbed a note-ref: revenue 40 while assets 8,000,000
    assert r.is_suspect({"revenue": 40, "total_assets": 8_000_000})

def test_net_income_far_exceeds_revenue_is_suspect():
    assert r.is_suspect({"revenue": 100, "net_income": 5000, "total_assets": 9000})

def test_plausible_row_not_suspect():
    assert not r.is_suspect({"revenue": 4779, "net_income": 244, "total_assets": 8395})


# ---- model JSON parsing (Qwen3 emits <think> + maybe code fences) ----
def test_parse_strips_think_block():
    c = '<think>\n\n</think>\n\n{"revenue": 4779, "net_income": 244}'
    assert r.parse_model_json(c) == {"revenue": 4779, "net_income": 244}

def test_parse_strips_code_fence():
    c = '```json\n{"revenue": 100, "net_income": 5}\n```'
    assert r.parse_model_json(c) == {"revenue": 100, "net_income": 5}

def test_parse_takes_last_json_object():
    c = 'reasoning {"foo":1} final {"revenue": 9, "net_income": 2}'
    assert r.parse_model_json(c)["revenue"] == 9

def test_parse_garbage_returns_none():
    assert r.parse_model_json("no json here") is None
