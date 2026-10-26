"""Block-2 measurement on the frozen question file reports/questions_block2.jsonl
(committed before this module was run).  Test split only; dev is never used for tuning here.

Retrieval questions contain no clause numbers / standard codes (filtered at build time), so
these numbers measure retrieval, not clause-number routing.

Four modes, adjacent comparisons differ in ONE component:
  keyword -> hybrid      (add the vector pool + RRF)
  vector  -> hybrid      (add the BM25 pool + RRF)
  hybrid  -> hybrid_rerank (re-order hybrid top-20 by the cross-encoder)
Program-judged only: Recall@5, MRR@10, refusal on U2, false refusal, citation precision
(/ask evidence-only, no generator).  Paired exact McNemar + paired bootstrap CI.

Failure attribution (per mode, per test question that misses top-5): every flag is recorded,
the primary category is the first applicable in this order:
  1 parse_error     gold chunk text looks garbled ((cid:, U+FFFD, mostly 1-2 char lines)
  2 label_error     a top-5 chunk outside the acceptable set contains every gold span
  3 chunk_boundary  gold chunk is a continuation part, or [vector-using modes] > 512 model tokens
                    (bge input truncated)
  4 tokenization    query words occur as substrings of the gold chunk far more often than as its
                    jieba tokens (gap >= 0.25); keyword mode, or hybrid modes when BOTH retrievers miss
  5 vector_miss     vector mode; or hybrid modes when BOTH BM25 and vector miss top-5
  6 fusion_loss     [hybrid modes] gold in BM25 or vector top-5 but not in hybrid top-5
  7 rerank_error    [hybrid_rerank] gold in hybrid top-5, pushed out by the reranker
  (for keyword mode a miss without a tokenization gap is "unattributed" = vocabulary gap)
  unattributed      none of the above (reported, not hidden)
Usage: python -m tushen_kb.eval.run_block2
"""
from __future__ import annotations

import argparse
import json
import resource
import time
from collections import Counter, defaultdict
from dataclasses import replace

from ..ask import AskConfig, _coverage, ask
from ..ingest import ROOT
from ..search import CLAUSE_IN_QUERY, MODES, SearchConfig, _bm25_pool, _vector_pool, search
from ..store import Store
from ..tokenize import content_tokens, tokens
from . import questions_b2 as QB
from .metrics import (bootstrap_diff_ci, bootstrap_ratio_diff_ci, mcnemar_exact, mrr_at_k, rate, recall_at_k,
                      significance_label)
from .questions import stratum

REPORTS = ROOT / "reports"
QFILE = REPORTS / "questions_block2.jsonl"
PAIRS = [("keyword", "hybrid"), ("vector", "hybrid"), ("hybrid", "hybrid_rerank"), ("keyword", "vector")]
CATS = ["parse_error", "label_error", "chunk_boundary", "tokenization", "vector_miss", "fusion_loss",
        "rerank_error", "unattributed"]


def _memo(dense):
    """Deterministic functions: memoise so search() and ask() do not recompute the same query."""
    eq, rr = dense.embed_query, dense.rerank_scores
    c1, c2 = {}, {}
    dense.embed_query = lambda q: c1[q] if q in c1 else c1.setdefault(q, eq(q))
    dense.rerank_scores = lambda q, ps, batch=8: (c2[(q, tuple(ps))] if (q, tuple(ps)) in c2
                                                  else c2.setdefault((q, tuple(ps)), rr(q, ps, batch)))


def rank_of(rows, acc):
    for i, r in enumerate(rows):
        if r["chunk_id"] in acc:
            return i + 1
    return None


def diag(store, q, gold_chunks, sq, tok_len):
    """Mode-independent evidence for attribution."""
    cfg = SearchConfig(pool=50)
    acc = set(q["acceptable"])
    bm = _bm25_pool(store, q["question"], cfg, [])
    ve = _vector_pool(store, q["question"], cfg, [])
    src = gold_chunks[q["src_chunk"]]
    t = src["text"]
    lines = [ln for ln in t.split("\n") if ln.strip()]
    garbled = "(cid:" in t or "\ufffd" in t or (len(lines) >= 4 and sum(len(ln.strip()) <= 2 for ln in lines) / len(lines) > 0.3)
    qw = content_tokens(q["question"])
    flat = t.replace("\n", "").lower()
    ctoks = set(tokens(t, bigrams=False))
    sub_cov = sum(w in flat for w in qw) / len(qw) if qw else 0.0
    tok_cov = sum(w in ctoks for w in qw) / len(qw) if qw else 0.0
    part = q["src_chunk"].rsplit(":", 1)[-1] != "0"
    return {"rank_bm25_top50": rank_of(bm, acc), "rank_vector_top50": rank_of(ve, acc),
            "garbled": garbled, "gold_tokens": tok_len(src), "continuation_part": part,
            "substring_cov": round(sub_cov, 3), "token_cov": round(tok_cov, 3),
            "routing_triggered": bool(CLAUSE_IN_QUERY.search(q["question"]))}


def attribute(mode, top, q, d, sq, rank_hybrid):
    acc = set(q["acceptable"])
    spans = [QB.squash(s)[0] for s in q["gold_spans"]]
    flags = []
    if d["garbled"]:
        flags.append("parse_error")
    if any(r["chunk_id"] not in acc and all(s in QB.squash((r.get("table_title") or "") + r["text"])[0] for s in spans)
           for r in top[:5]):
        flags.append("label_error")
    if d["continuation_part"] or (d["gold_tokens"] > 512 and mode != "keyword"):  # BM25 does not truncate
        flags.append("chunk_boundary")
    bm5 = d["rank_bm25_top50"] is not None and d["rank_bm25_top50"] <= 5
    ve5 = d["rank_vector_top50"] is not None and d["rank_vector_top50"] <= 5
    gap = d["substring_cov"] - d["token_cov"] >= 0.25
    hy5 = rank_hybrid is not None and rank_hybrid <= 5
    if mode == "keyword":
        if gap:
            flags.append("tokenization")
    elif mode == "vector":
        flags.append("vector_miss")
    elif mode == "hybrid_rerank" and hy5:
        flags.append("rerank_error")            # hybrid had it in top-5, the reranker pushed it out
    elif bm5 or ve5:
        flags.append("fusion_loss")             # one retriever had it in top-5, RRF lost it
    else:                                       # both retrievers missed top-5
        if gap:
            flags.append("tokenization")
        flags.append("vector_miss")
    primary = next((c for c in CATS if c in flags), "unattributed")
    return primary, flags


def summarize(ranks, qs):
    out = {"Recall@5": recall_at_k(ranks, 5)["str"], "MRR@10": mrr_at_k(ranks, 10)["str"]}
    for key, fn in (("by_type", lambda q: q["type"]), ("by_overlap_stratum", lambda q: stratum(q["overlap"]))):
        g = defaultdict(list)
        for r, q in zip(ranks, qs):
            g[fn(q)].append(r)
        out[key] = {k: {"Recall@5": recall_at_k(v, 5)["str"], "MRR@10": mrr_at_k(v, 10)["str"]}
                    for k, v in sorted(g.items())}
    return out


def paired(a_ranks, b_ranks):
    ha = [r is not None and r <= 5 for r in a_ranks]
    hb = [r is not None and r <= 5 for r in b_ranks]
    mc = mcnemar_exact(ha, hb)
    bs = bootstrap_diff_ci([float(x) for x in ha], [float(x) for x in hb])
    rra = [1.0 / r if r and r <= 10 else 0.0 for r in a_ranks]
    rrb = [1.0 / r if r and r <= 10 else 0.0 for r in b_ranks]
    bs2 = bootstrap_diff_ci(rra, rrb)
    return {"n": len(ha), "Recall@5_mcnemar": mc, "Recall@5_diff_B_minus_A": bs,
            "Recall@5_label(mcnemar p<0.05 and CI excludes 0)": significance_label(mc["p_value"], bs["ci"]),
            "MRR@10_diff_B_minus_A(paired bootstrap)": bs2,
            "MRR@10_label": "无显著差异" if bs2["ci"] and bs2["ci"][0] <= 0 <= bs2["ci"][1] else "显著"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "data" / "tushen.sqlite"))
    ap.add_argument("--split", default="test")
    a = ap.parse_args()
    t_start = time.time()
    store = Store(a.db)
    from .. import dense
    _memo(dense)
    lines = [json.loads(l) for l in open(QFILE, encoding="utf-8")]
    meta, qs = lines[0], lines[1:]
    test = [q for q in qs if q["split"] == a.split]
    ans = [q for q in test if q["answerable"]]
    u2 = [q for q in test if q["type"] == "U2"]
    g = QB.Gold(store)
    allc = {c["chunk_id"]: c for c in store.chunks()}
    tok, _, _ = dense.embedder()
    tl_cache = {}

    def tok_len(c):
        if c["chunk_id"] not in tl_cache:
            tl_cache[c["chunk_id"]] = len(tok(dense.passage_text(c))["input_ids"])
        return tl_cache[c["chunk_id"]]

    diags = {q["qid"]: diag(store, q, allc, g.sq, tok_len) for q in ans}
    ranks, tops, per_q = {}, {}, defaultdict(dict)
    timing = {}
    for mode in MODES:
        t0 = time.time()
        cfg = SearchConfig(mode=mode, k=10)
        rs, tp = [], []
        for q in ans:
            hits = search(store, q["question"], cfg)
            rs.append(rank_of(hits, set(q["acceptable"])))
            tp.append(hits)
        ranks[mode], tops[mode] = rs, tp
        timing[mode] = round((time.time() - t0) / max(1, len(ans)) * 1000, 1)
    # failure attribution
    attr = {}
    for mode in MODES:
        prim, flg = Counter(), Counter()
        for i, q in enumerate(ans):
            r = ranks[mode][i]
            if r is not None and r <= 5:
                continue
            p, f = attribute(mode, tops[mode][i], q, diags[q["qid"]], g.sq, ranks["hybrid"][i])
            prim[p] += 1
            flg.update(f)
            per_q[q["qid"]][mode] = {"rank": r, "primary": p, "flags": f}
        n_fail = sum(prim.values())
        attr[mode] = {"n_failures(top5 miss)": n_fail, "den_questions": len(ans),
                      "primary": {c: rate(prim[c], n_fail)["str"] for c in CATS},
                      "flags(any, multi-label)": {c: flg[c] for c in CATS if c != "unattributed"}}
    # /ask evidence-only: refusal + citation precision, same AskConfig except search mode
    askres = {}
    for mode in MODES:
        cfg = AskConfig(search=SearchConfig(mode=mode))
        per = []
        for q in ans:
            r = ask(store, q["question"], cfg)
            cits = [] if r["refused"] else r["citations"]
            per.append({"refused": r["refused"], "n_cit": len(cits),
                        "n_ok": sum(c["chunk_id"] in set(q["acceptable"]) for c in cits)})
        ur = [ask(store, q["question"], cfg)["refused"] for q in u2]
        askres[mode] = {"per": per, "u_ref": ur}
    ask_summary = {}
    for mode, d in askres.items():
        per = d["per"]
        ask_summary[mode] = {
            "correct_refusal_U2": rate(sum(d["u_ref"]), len(d["u_ref"]))["str"],
            "false_refusal_answerable": rate(sum(p["refused"] for p in per), len(per))["str"],
            "citation_precision(citations)": rate(sum(p["n_ok"] for p in per), sum(p["n_cit"] for p in per))["str"],
            "questions_with_>=1_correct_citation": rate(sum(p["n_ok"] > 0 for p in per), len(per))["str"]}
    ask_paired = {}
    for A, B in PAIRS:
        pa, pb = askres[A]["per"], askres[B]["per"]
        ya = [p["n_ok"] > 0 for p in pa]
        yb = [p["n_ok"] > 0 for p in pb]
        mc = mcnemar_exact(ya, yb)
        bs = bootstrap_ratio_diff_ci([p["n_ok"] for p in pa], [p["n_cit"] for p in pa],
                                     [p["n_ok"] for p in pb], [p["n_cit"] for p in pb])
        fr = mcnemar_exact([p["refused"] for p in pa], [p["refused"] for p in pb])
        ur = mcnemar_exact(askres[A]["u_ref"], askres[B]["u_ref"])
        ask_paired[f"{A}->{B}"] = {
            "mcnemar(question has >=1 correct citation)": mc,
            "citation_precision_diff_B_minus_A(cluster bootstrap)": bs,
            "label_question_level(mcnemar p<0.05)": "显著" if mc["p_value"] < 0.05 else "无显著差异",
            "label_citation_precision(CI excludes 0)": ("待测" if not bs["ci"] else
                                                        "无显著差异" if bs["ci"][0] <= 0 <= bs["ci"][1] else "显著"),
            "mcnemar(false refusal)": fr, "mcnemar(U2 correct refusal)": ur}
    # paraphrase robustness: P item vs its own source question, same mode
    idx = {q["qid"]: i for i, q in enumerate(ans)}
    pp = [(idx[q["src_qid"]], idx[q["qid"]]) for q in ans if q["type"] == "P" and q["src_qid"] in idx]
    para = {}
    for mode in MODES:
        src = [ranks[mode][i] for i, _ in pp]
        par = [ranks[mode][j] for _, j in pp]
        para[mode] = {"source_Recall@5": recall_at_k(src, 5)["str"], "paraphrase_Recall@5": recall_at_k(par, 5)["str"],
                      "paired(source->paraphrase)": paired(src, par)}
    rep = {
        "time_local": time.strftime("%Y-%m-%d %H:%M %Z"), "question_file": str(QFILE.relative_to(ROOT)),
        "question_file_built": meta.get("built_local"), "split": a.split,
        "n_test_answerable": len(ans), "n_test_U2": len(u2),
        "n_test_by_type": dict(Counter(q["type"] for q in test)),
        "routing_triggered_by_decimal_in_question": rate(sum(d["routing_triggered"] for d in diags.values()), len(ans))["str"],
        "hit_definition": "rank of first chunk in the frozen acceptable set (<=3 chunks containing all gold spans)",
        "retrieval": {m: summarize(ranks[m], ans) for m in MODES},
        "retrieval_paired": {f"{A}->{B}": paired(ranks[A], ranks[B]) for A, B in PAIRS},
        "ask_evidence_only": ask_summary, "ask_paired": ask_paired,
        "paraphrase_robustness": {"n_pairs": len(pp), **para},
        "failure_attribution": attr,
        "search_ms_per_query(after warm-up, memoised across ask)": timing,
        "max_rss_mb": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024),
        "wall_s": round(time.time() - t_start, 1),
        "config": {"SearchConfig": SearchConfig().__dict__, "AskConfig_min_coverage": AskConfig().min_coverage,
                   "cite_kinds": list(AskConfig().cite_kinds)},
    }
    (REPORTS / "eval_block2.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    with open(REPORTS / "eval_block2_per_question.jsonl", "w", encoding="utf-8") as f:
        for i, q in enumerate(ans):
            f.write(json.dumps({"qid": q["qid"], "type": q["type"], **{f"rank_{m}": ranks[m][i] for m in MODES},
                                "diag": diags[q["qid"]], "failures": per_q.get(q["qid"], {})},
                               ensure_ascii=False) + "\n")
    print(json.dumps({k: rep[k] for k in ("n_test_answerable", "n_test_U2", "retrieval_paired", "ask_evidence_only",
                                          "wall_s", "max_rss_mb")}, ensure_ascii=False, indent=1)[:6000])


if __name__ == "__main__":
    main()
