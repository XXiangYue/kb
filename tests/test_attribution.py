from tushen_kb.eval.run_block2 import attribute

Q = {"acceptable": ["d:clause:1:0"], "gold_spans": ["不得设置"]}


def _d(**kw):
    d = {"garbled": False, "gold_tokens": 100, "continuation_part": False, "rank_bm25_top50": None,
         "rank_vector_top50": None, "substring_cov": 0.5, "token_cov": 0.5}
    d.update(kw)
    return d


def test_rerank_error_when_hybrid_had_it():
    p, f = attribute("hybrid_rerank", [], Q, _d(rank_bm25_top50=2), None, 3)
    assert p == "rerank_error"
    assert "rerank_error" in f


def test_vector_miss_and_order():
    p, f = attribute("vector", [], Q, _d(gold_tokens=700), None, None)
    assert p == "chunk_boundary" and "vector_miss" in f
    p, _ = attribute("keyword", [], Q, _d(gold_tokens=700), None, None)
    assert p == "unattributed"  # BM25 does not truncate


def test_label_error():
    top = [{"chunk_id": "d:commentary:1:0", "text": "此处不得设置", "table_title": None}]
    p, _ = attribute("keyword", top, Q, _d(), None, None)
    assert p == "label_error"


def test_fusion_loss_before_vector_miss():
    p, f = attribute("hybrid", [], Q, _d(rank_bm25_top50=1), None, 9)
    assert p == "fusion_loss" and "vector_miss" not in f
