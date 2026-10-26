import pytest

from tushen_kb.ask import AskConfig, ContextBlock, ask
from tushen_kb.search import SearchConfig, search
from tushen_kb.tokenize import fts_query, tokens


def test_tokenizer_protects_clause_numbers():
    t = tokens("GB 55037-2022 第4.1.2条 防火分区")
    assert "4.1.2" in t and "55037-2022" in t and "防火" in t


def test_fts_query_nonempty():
    assert fts_query("防火分区的面积") and fts_query("的") == ""


def test_keyword_search_bm25(store):
    hits = search(store, "消防电梯底坑排水井", SearchConfig(mode="keyword", clause_route=False))
    assert hits and hits[0]["clause_no"] == "3.1.2"


def test_clause_route(store):
    hits = search(store, "第3.2.1条是什么", SearchConfig(mode="keyword"))
    assert hits[0]["clause_no"] == "3.2.1"


def test_other_modes_not_implemented(store):
    with pytest.raises(NotImplementedError):
        search(store, "x", SearchConfig(mode="vector"))


def test_ask_answers_with_verified_citation(store):
    r = ask(store, "消防电梯底坑排水井有效容量")
    assert not r["refused"] and r["citations"]
    c = r["citations"][0]
    assert c["verified"] and store.doc_text("TEST1")[c["char_start"]:c["char_end"]] == c["quote"]


@pytest.mark.parametrize("q", ["GB 50016-2014 第5.5.17条", "第9.9.9条规定了什么", "火星基地的氧气浓度要求"])
def test_ask_refusals(store, q):
    r = ask(store, q)
    assert r["refused"] and r["refusal_reason"] and not r["citations"]


def test_context_hook_logged(store):
    hook = lambda q, st: [ContextBlock("skill:test", "按条文号检索。", "unit-test")]
    r = ask(store, "第3.1.1条", context_hooks=[hook])
    assert any(x["source"] == "skill:test" and x["tokens"] > 0 for x in r["trace"]["context_log"])


class FakeLLM:
    def __init__(self, text):
        self.text = text

    def complete(self, messages, max_tokens=400):
        return {"role": "generator", "provider": "fake", "family": "fake", "model_name": "fake-1",
                "model_version": "fake-1", "status": "ok", "text": self.text, "usage": None, "ms": 0, "error": None}


def test_llm_answer_quotes_verified(store):
    r = ask(store, "消防电梯底坑排水井有效容量", llm=FakeLLM("依据「排水井有效容量不应小于2m³」。"))
    assert not r["refused"] and r["mode"] == "llm_answer" and r["trace"]["llm_call"]["model_name"] == "fake-1"


def test_llm_fabricated_quote_refused(store):
    r = ask(store, "消防电梯底坑排水井有效容量", llm=FakeLLM("依据「排水井有效容量不应小于5m³」。"))
    assert r["refused"] and "校验" in r["refusal_reason"]


def test_llm_skipped_falls_back(store):
    class Skip(FakeLLM):
        def complete(self, messages, max_tokens=400):
            d = super().complete(messages)
            d.update(status="skipped:no_key(X)", text=None)
            return d
    r = ask(store, "消防电梯底坑排水井有效容量", llm=Skip(""))
    assert not r["refused"] and r["mode"] == "evidence_only" and r["trace"]["llm_call"]["status"].startswith("skipped")
