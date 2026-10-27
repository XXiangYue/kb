"""Measurement only (plan: reports/plan_block2_pool_coverage_zh.md).

Gold coverage of the candidate pool the cross-encoder actually sees, on the frozen block-2 test
set (214 answerable).  Reproduces search() up to the _rerank() call with the default SearchConfig
(k=10 as in run_block2): BM25 pool head[:rerank_n] and RRF(BM25, vector) head[:rerank_n].
No reranker / LLM calls.  Usage: python -m tushen_kb.eval.pool_coverage
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from ..ingest import ROOT
from ..search import CLAUSE_IN_QUERY, SearchConfig, _bm25_pool, _rrf, _vector_pool, resolve_docs
from ..store import Store

REPORTS = ROOT / "reports"


def first_rank(rows, acc):
    for i, r in enumerate(rows):
        if r["chunk_id"] in acc:
            return i + 1
    return None


def cov(ranks, k):
    n = sum(r is not None and r <= k for r in ranks)
    return f"{n}/{len(ranks)} = {n / len(ranks):.3f}"


def main():
    t0 = time.time()
    store = Store(str(ROOT / "data" / "tushen.sqlite"))
    lines = [json.loads(l) for l in open(REPORTS / "questions_block2.jsonl", encoding="utf-8")]
    ans = [q for q in lines[1:] if q["split"] == "test" and q["answerable"]]
    cfg = SearchConfig(mode="hybrid_rerank", k=10)  # same cfg run_block2 builds; pool/rerank_n/rrf_k default
    K = cfg.rerank_n
    rr = {}
    for l in open(REPORTS / "eval_block2_bm25_rerank_per_question.jsonl", encoding="utf-8"):
        d = json.loads(l)
        rr[d["qid"]] = d
    per, rb, rh = [], [], []
    for q in ans:
        acc = set(q["acceptable"])
        doc_ids = resolve_docs(store, q["question"])[0] if cfg.std_filter else []
        bm = _bm25_pool(store, q["question"], cfg, doc_ids)
        ve = _vector_pool(store, q["question"], cfg, doc_ids)
        hy = _rrf(bm, ve, cfg.rrf_k)
        b, h = first_rank(bm, acc), first_rank(hy, acc)
        rb.append(b)
        rh.append(h)
        p = rr[q["qid"]]
        per.append({"qid": q["qid"], "type": q["type"], "len_bm25_pool": len(bm), "len_hybrid_pool": len(hy),
                    "gold_rank_in_bm25_pool": b, "gold_rank_in_hybrid_pool": h,
                    "routing_triggered": bool(CLAUSE_IN_QUERY.search(q["question"])), "std_filter_docs": doc_ids,
                    "committed_rank_bm25_rerank": p["rank_bm25_rerank"],
                    "committed_rank_hybrid_rerank": p["rank_hybrid_rerank"]})
    n = len(ans)

    def misses(mode, key):
        out = []
        for x in per:
            r = x[f"committed_rank_{mode}"]
            if r is None or r > 5:
                g = x[key]
                out.append({"qid": x["qid"], "type": x["type"], "committed_rank": r, "gold_rank_in_pool": g,
                            "gold_in_reranked_head(K)": g is not None and g <= K})
        return out

    mb, mh = misses("bm25_rerank", "gold_rank_in_bm25_pool"), misses("hybrid_rerank", "gold_rank_in_hybrid_pool")
    inb = [r is not None and r <= K for r in rb]
    inh = [r is not None and r <= K for r in rh]
    rep = {
        "time_local": time.strftime("%Y-%m-%d %H:%M %Z"), "plan": "reports/plan_block2_pool_coverage_zh.md",
        "question_file": "reports/questions_block2.jsonl", "n_test_answerable": n,
        "K_rerank_head": K, "K_source": "tushen_kb/search.py:34 rerank_n=20; :141 head = rows[:n]",
        "per_retriever_pool": max(cfg.k * 5, cfg.pool), "per_retriever_pool_source": "search.py:32,92,119 max(k*5, pool)",
        "rrf_k": cfg.rrf_k, "routing_triggered": f"{sum(x['routing_triggered'] for x in per)}/{n}",
        "std_filter_applied": f"{sum(bool(x['std_filter_docs']) for x in per)}/{n}",
        "coverage": {
            "bm25_pool": {"@5(selfcheck=keyword Recall@5)": cov(rb, 5), f"@{K}(reranker input)": cov(rb, K),
                          "@50(full pool)": cov(rb, 50)},
            "hybrid_pool": {"@5(selfcheck=hybrid Recall@5)": cov(rh, 5), f"@{K}(reranker input)": cov(rh, K),
                            "@full(union, up to 100)": cov(rh, 10**9)},
        },
        "paired_coverage_at_K": {"both": sum(a and b for a, b in zip(inb, inh)),
                                 "only_bm25": sum(a and not b for a, b in zip(inb, inh)),
                                 "only_hybrid": sum(b and not a for a, b in zip(inb, inh)),
                                 "neither": sum(not a and not b for a, b in zip(inb, inh))},
        "rerank_top5_misses(committed, not re-run)": {"bm25_rerank": mb, "hybrid_rerank": mh},
        "wall_s": round(time.time() - t0, 1),
    }
    (REPORTS / "eval_block2_pool_coverage.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    with open(REPORTS / "eval_block2_pool_coverage_per_question.jsonl", "w", encoding="utf-8") as f:
        for x in per:
            f.write(json.dumps(x, ensure_ascii=False) + "\n")
    print(json.dumps(rep, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
