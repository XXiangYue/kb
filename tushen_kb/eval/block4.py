"""Block 4 helpers for the pre-registered plan reports/plan_block4_zh.md (9073cdd).

  python -m tushen_kb.eval.block4 a2     evidence-only (no LLM) 口径 A checks
        - S1 (min_coverage 0, frozen t=0.94) with coverage_strip_doc_refs=False vs the 7818948 records
          (refusal / n_cit / n_cit_ok / S1 per question); chunk-id sets are compared between
          strip=True and strip=False in this same rerun, since 7818948 did not store chunk ids.
        - current comparator (coverage gate 0.5) with strip=False -> 口径 A values.
  python -m tushen_kb.eval.block4 pair-a1  only the a1 pairing -> reports/block4_paired_a1.json
  python -m tushen_kb.eval.block4 pair   paired stats for the two e2e runs (a1, b), no model calls.
No model judges anything here; all numbers are program checks against the frozen gold labels.
"""
from __future__ import annotations

from .. import stdref  # resolves standards-text references in committed artifacts

import json
import sys
import time

from ..ask import AskConfig, ask
from ..search import SearchConfig
from .metrics import bootstrap_ratio_diff_ci, mcnemar_exact, rate
from .refusal_b3 import MODE, REPORTS, TEST_PQ, load_sets, setup

T = 0.94  # frozen in 081a449
A2_OUT = REPORTS / "block4_a2_evidence_only_A.json"
PAIR_OUT = REPORTS / "block4_paired.json"


def _rec(store, q, cfg):
    r = ask(store, q["question"], cfg)
    acc = set(q.get("acceptable") or [])
    cits = [] if r["refused"] else r["citations"]
    ev = r.get("evidence") or []
    lg = [e["score"] for e in ev if e.get("route") == "rerank"]
    import math
    s1 = 1 / (1 + math.exp(-max(lg))) if lg else 0.0
    return {"refused": r["refused"], "reason": r["refusal_reason"], "cit_ids": [c["chunk_id"] for c in cits],
            "n_cit": len(cits), "n_cit_ok": sum(c["chunk_id"] in acc for c in cits), "s1": s1}


def a2():
    store = setup()
    sets = load_sets("test")
    old = {}
    for l in open(TEST_PQ, encoding="utf-8"):
        r = stdref.loads(l)
        old[(r["set"], r["qid"])] = r
    # S1 refusal = base refusal (min_cov 0) OR S1 < t, as in 7818948
    cA = AskConfig(search=SearchConfig(mode=MODE), min_coverage=0.0, coverage_strip_doc_refs=False)
    cB = AskConfig(search=SearchConfig(mode=MODE), min_coverage=0.0, coverage_strip_doc_refs=True)
    curA = AskConfig(search=SearchConfig(mode=MODE), min_coverage=0.5, coverage_strip_doc_refs=False)
    res, pq = {}, []
    for k, L in sets.items():
        agree = agree_ids = 0
        S, C = [], []
        for q in L:
            a, b, c = _rec(store, q, cA), _rec(store, q, cB), _rec(store, q, curA)
            o = old[(k, q["qid"])]
            nr = a["refused"] or a["s1"] < T
            same = (nr == o["new_refused"] and (nr or (a["n_cit"], a["n_cit_ok"]) == (o["n_cit"], o["n_cit_ok"]))
                    and abs(a["s1"] - o["S1_rerank_top"]) < 1e-6)
            agree += same
            agree_ids += (a["refused"], a["cit_ids"]) == (b["refused"], b["cit_ids"])
            S.append({"refused": nr, "n_cit": 0 if nr else a["n_cit"], "n_cit_ok": 0 if nr else a["n_cit_ok"]})
            C.append(c)
            pq.append({"set": k, "qid": q["qid"], "S1_A_refused": nr, "S1_A_cit_ids": [] if nr else a["cit_ids"],
                       "same_as_7818948": same, "cur05_A_refused": c["refused"], "cur05_A_cit_ids": c["cit_ids"]})
        d = {"n": len(L), "S1_A_vs_7818948_一致(refusal,n_cit,n_cit_ok,S1)": rate(agree, len(L))["str"],
             "S1_strip_A_vs_B_same_chunk_ids_and_refusal(this rerun)": rate(agree_ids, len(L))["str"],
             "S1_refused": rate(sum(s["refused"] for s in S), len(L))["str"],
             "current0.5_口径A_refused": rate(sum(c["refused"] for c in C), len(L))["str"],
             "mcnemar_refused(a=current0.5_A,b=S1)": mcnemar_exact([c["refused"] for c in C], [s["refused"] for s in S])}
        if L and L[0]["answerable"]:
            d["citation_precision_S1(口径A)"] = rate(sum(s["n_cit_ok"] for s in S), sum(s["n_cit"] for s in S))["str"]
            d["citation_precision_current0.5(口径A)"] = rate(sum(c["n_cit_ok"] for c in C), sum(c["n_cit"] for c in C))["str"]
            d["citation_precision_diff_ci(b=S1 - a=current0.5, 口径A)"] = bootstrap_ratio_diff_ci(
                [c["n_cit_ok"] for c in C], [c["n_cit"] for c in C], [s["n_cit_ok"] for s in S], [s["n_cit"] for s in S])
            d["ge1_correct_current0.5(口径A)"] = rate(sum(c["n_cit_ok"] > 0 for c in C), len(L))["str"]
        res[k] = d
    rep = {"time_local": time.strftime("%Y-%m-%d %H:%M %Z"), "mode": MODE, "S1_t": T, "results": res}
    A2_OUT.write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    with open(A2_OUT.with_name(A2_OUT.stem + "_per_question.jsonl"), "w", encoding="utf-8") as f:
        for r in pq:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(json.dumps(rep, ensure_ascii=False, indent=1))


def _rd(name):
    return {r["qid"]: r for r in (stdref.loads(l) for l in open(REPORTS / name, encoding="utf-8"))}


def _cmp(qids, A, B, refused_a, refused_b, prec):
    a = [refused_a(A[q]) for q in qids]
    b = [refused_b(B[q]) for q in qids]
    d = {"n": len(qids), "refused_a": rate(sum(a), len(qids))["str"], "refused_b": rate(sum(b), len(qids))["str"],
         "mcnemar_refused(b01=a not refused, b refused)": mcnemar_exact(a, b)}
    if prec:
        na = [0 if x else A[q]["n_cit_ok"] for q, x in zip(qids, a)]
        da = [0 if x else A[q]["n_cit"] for q, x in zip(qids, a)]
        nb = [0 if x else B[q]["n_cit_ok"] for q, x in zip(qids, b)]
        db = [0 if x else B[q]["n_cit"] for q, x in zip(qids, b)]
        d["citation_precision_a"] = rate(sum(na), sum(da))["str"]
        d["citation_precision_b"] = rate(sum(nb), sum(db))["str"]
        d["citation_precision_diff_ci(b-a, cluster bootstrap)"] = bootstrap_ratio_diff_ci(na, da, nb, db)
        d["ge1_correct_a"] = rate(sum(x > 0 for x in na), len(qids))["str"]
        d["ge1_correct_b"] = rate(sum(x > 0 for x in nb), len(qids))["str"]
    return d


def pair(only_a1=False):
    s5 = _rd("e2e_deepseek_per_question.jsonl")          # 14d729f, 口径 B
    groups = {"block2_answerable": lambda r: r["file"] == "questions_block2.jsonl" and r["answerable"],
              "lowoverlap": lambda r: r["file"] == "questions_lowoverlap.jsonl",
              "U2_已见过": lambda r: r["type"] == "U2"}
    out = {"time_local": time.strftime("%Y-%m-%d %H:%M %Z")}
    fr = lambda r: r["refused"]
    if (REPORTS / "e2e_deepseek_A_per_question.jsonl").exists():
        A = _rd("e2e_deepseek_A_per_question.jsonl")
        out["a1: a=14d729f(口径B) b=e2e_deepseek_A(口径A), archive only"] = {
            g: _cmp([q for q in s5 if f(s5[q]) and q in A], s5, A, fr, fr, g != "U2_已见过") for g, f in groups.items()}
    if not only_a1 and (REPORTS / "e2e_s1_deepseek.json").exists():  # summary exists only after the run finished
        B = _rd("e2e_s1_deepseek_per_question.jsonl")
        out["b vs 14d729f: a=Step5 0.05+DeepSeek b=S1 0.94+DeepSeek (reference only: gate and threshold both differ)"] = {
            g: _cmp([q for q in s5 if f(s5[q]) and q in B], s5, B, fr, fr, g != "U2_已见过") for g, f in groups.items()}
        ev = {}
        for l in open(TEST_PQ, encoding="utf-8"):
            r = stdref.loads(l)
            ev.setdefault(r["set"], {})[r["qid"]] = r
        out["b vs 7818948: a=S1 evidence-only b=S1+DeepSeek (effect of adding the LLM stage)"] = {
            k: _cmp([q for q in E if q in B], E, B, lambda r: r["new_refused"], fr, k in ("block2_answerable", "lowoverlap"))
            for k, E in ev.items()}
    (PAIR_OUT.with_name("block4_paired_a1.json") if only_a1 else PAIR_OUT).write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    {"a2": a2, "pair": pair, "pair-a1": lambda: pair(True)}[sys.argv[1]]()
