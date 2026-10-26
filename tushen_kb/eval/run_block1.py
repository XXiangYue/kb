"""Block-1 measurement: keyword mode, clause-direct lookup (T1) + unanswerable (U).

Primary metrics are all program-judged.  Two configs that differ in exactly
ONE variable (clause_route) are compared with a paired McNemar test and a
paired bootstrap CI.  Model-based filter passes run on a small random sample
only when --llm is given and the generator key is present; otherwise they are
recorded as "skipped".  Model judging (faithfulness) is optional and never
feeds primary metrics.

Usage: python -m tushen_kb.eval.run_block1 [--llm] [--llm-sample 8]
"""
from __future__ import annotations

import argparse
import json
import platform
import random
import time
from collections import defaultdict
from dataclasses import asdict
from pathlib import Path

from .. import CHUNKER_VERSION, PARSER_VERSION, TOKENIZER_VERSION
from ..ask import AskConfig, ask
from ..ingest import ROOT
from ..llm import ChatModel, load_models
from ..search import SearchConfig, search
from ..store import Store
from . import judges as J
from .metrics import (bootstrap_diff_ci, bootstrap_mean_ci, first_hit_rank, mcnemar_exact, mrr_at_k, rate,
                      recall_at_k, significance_label)
from .questions import build_t1, build_u, closed_book_leak, dedup, stratum

REPORTS = ROOT / "reports"


def hit_rank(store_chunks: dict, hits: list[dict], q: dict, k: int) -> int | None:
    """Hit = chunk in acceptable set, OR a chunk of the same doc whose span covers the gold span."""
    acc = set(q["acceptable"])
    for i, h in enumerate(hits[:k]):
        if h["chunk_id"] in acc:
            return i + 1
        gs = q.get("gold_span")
        if gs and h["doc_id"] == gs[0] and h["char_start"] <= gs[1] and h["char_end"] >= gs[2]:
            return i + 1
    return None


def run_config(store, qs, cfg: SearchConfig) -> list[int | None]:
    return [hit_rank(None, search(store, q["question"], cfg), q, 10) for q in qs]


def summarize(ranks, qs) -> dict:
    out = {"Recall@5": recall_at_k(ranks, 5), "MRR@10": mrr_at_k(ranks, 10)}
    by = defaultdict(list)
    for r, q in zip(ranks, qs):
        by[stratum(q["overlap"])].append(r)
    out["by_overlap_stratum"] = {s: {"Recall@5": recall_at_k(v, 5)["str"], "MRR@10": mrr_at_k(v, 10)["str"]}
                                 for s, v in sorted(by.items())}
    by_doc = defaultdict(list)
    for r, q in zip(ranks, qs):
        by_doc[q["doc_id"]].append(r)
    out["by_doc"] = {d: recall_at_k(v, 5)["str"] for d, v in sorted(by_doc.items())}
    hits5 = [1.0 if (r is not None and r <= 5) else 0.0 for r in ranks]
    out["Recall@5_bootstrap95"] = bootstrap_mean_ci(hits5)
    return out


def model_filters(qs, store, n: int, seed: int = 3) -> dict:
    gen, _ = load_models()
    m = ChatModel(gen)
    res = {"generator": {"model_name": gen.model, "family": gen.family, "status": gen.status()},
           "sample_n": 0, "back_translation": {}, "closed_book": {}, "calls": []}
    if gen.status() != "ready":
        res["back_translation"] = res["closed_book"] = {"status": gen.status(), "removed": "skipped"}
        return res
    rng = random.Random(seed)
    sample = rng.sample(qs, min(n, len(qs)))
    res["sample_n"] = len(sample)
    bt_removed, cb_removed = [], []
    for q in sample:
        gold = "\n".join(c["text"] for c in store.chunks("chunk_id IN (%s)" % ",".join("?" * len(q["acceptable"])),
                                                         tuple(q["acceptable"])) if c["kind"] == "clause")
        c1 = m.complete([{"role": "user", "content": f"条文：{gold[:1500]}\n问题：{q['question']}\n"
                          "仅凭上述条文能否回答该问题？只输出：是 或 否。"}], max_tokens=4)
        c2 = m.complete([{"role": "user", "content": f"不查阅任何资料，直接回答：{q['question']} 请尽量给出条文原文。"}],
                        max_tokens=300)
        for c, task in ((c1, "back_translation"), (c2, "closed_book")):
            res["calls"].append({"qid": q["qid"], "task": task, "model_name": c["model_name"],
                                 "model_version": c["model_version"], "status": c["status"], "usage": c["usage"]})
        if c1["status"] == "ok" and not (c1["text"] or "").strip().startswith("是"):
            bt_removed.append(q["qid"])
        if c2["status"] == "ok" and closed_book_leak(c2["text"], gold):
            cb_removed.append(q["qid"])
    ok1 = sum(1 for c in res["calls"] if c["task"] == "back_translation" and c["status"] == "ok")
    ok2 = sum(1 for c in res["calls"] if c["task"] == "closed_book" and c["status"] == "ok")
    res["back_translation"] = {"removed": len(bt_removed), "of_ok_calls": ok1, "qids": bt_removed,
                               "rule": "model answers 否 → not answerable from its own source → remove"}
    res["closed_book"] = {"removed": len(cb_removed), "of_ok_calls": ok2, "qids": cb_removed,
                          "rule": "PROGRAM: closed-book answer contains >=15 consecutive chars of gold verbatim → remove"}
    return res


def judge_study(store, qs, n: int, n_choice: int = 4) -> dict:
    """OPTIONAL model-judged study; never feeds primary metrics.

    Items: (a) n real generator answers, (b) their program-corrupted variants
    (label known = unfaithful), (c) n_choice two-choice items (true quote vs
    corrupted quote), asked in both option orders.  Every judge judges every
    item independently; failed calls stay in the record and the denominators."""
    gen, specs = load_models()
    out = {"generator": {"model": gen.model, "family": gen.family, "status": gen.status()},
           "judges": [{"role": s.role, "model": s.model, "family": s.family,
                       "fallback": [f["model"] for f in (s.fallback_models or [])], "status": s.status()} for s in specs],
           "judge_type": "model (not human)", "items": []}
    if gen.status() != "ready":
        out["status"] = gen.status()
        return out
    gm = ChatModel(gen)
    judges = J.load_judges()
    recs: dict[str, list] = {j.spec.role: [] for j in judges}

    def judge_all(fn):
        for j in judges:
            got = fn(j)
            recs[j.spec.role].extend(got if isinstance(got, list) else [got])

    faith_items = []
    for q in qs[:n]:
        r = ask(store, q["question"], llm=gm)
        item = {"qid": q["qid"], "refused": r["refused"], "reason": r["refusal_reason"],
                "llm_call": r["trace"].get("llm_call")}
        out["items"].append(item)
        if r["refused"] or not r["answer"]:
            continue
        ev = "\n".join(e["text"] for e in r["evidence"])
        faith_items.append((q["qid"] + ":orig", ev, r["answer"], None))
        c = J.corrupt(r["answer"])
        if c:
            faith_items.append((q["qid"] + ":corrupt", ev, c[0], c[1]))
    for iid, ev, ans, change in faith_items:
        judge_all(lambda j: j.faithfulness(iid, ev, ans))
    choice_items = []
    for q in qs[n:n + 3 * n_choice]:
        if len(choice_items) >= n_choice:
            break
        ch = [c for c in store.chunks("chunk_id=?", (q["acceptable"][0],))][0]
        c = J.corrupt(ch["text"])
        if c:
            choice_items.append((q["qid"] + ":choice", q["question"], ch["text"], c[1]))
            judge_all(lambda j, q=q, ch=ch, c=c: j.choice(q["qid"] + ":choice", q["question"], ch["text"],
                                                           ch["text"], c[0]))
    out["n_faithfulness_items"] = len(faith_items)
    out["n_corrupted_items"] = sum(1 for x in faith_items if x[3])
    out["n_choice_items"] = len(choice_items)
    out["n_choice_judgments_per_judge"] = 2 * len(choice_items)
    per = {}
    for role, rs in recs.items():
        f = [x for x in rs if x.task == "faithfulness"]
        corr = [x for x in f if x.item_id.endswith(":corrupt")]
        orig = [x for x in f if x.item_id.endswith(":orig")]
        chs = [x for x in rs if x.task == "choice"]
        by_item = {}
        for x in chs:
            by_item.setdefault(x.item_id, []).append(x)
        per[role] = {
            "models_used": sorted({f"{x.judge_model} ({x.judge_family})" for x in rs if x.judge_model}),
            "status_counts": _count(x.status for x in rs),
            "fallback_used": sum(x.fallback_used for x in rs),
            "orig_judged_faithful(over all orig items)": rate(sum(1 for x in orig if x.verdict is True), len(orig))["str"],
            "corrupt_detected(over all corrupted items)": rate(sum(1 for x in corr if x.verdict is False), len(corr))["str"],
            "choice_correct(over all order-judgments)": rate(sum(1 for x in chs if x.verdict is True), len(chs))["str"],
            "choice_order_consistent(both orders correct, over items)":
                rate(sum(1 for v in by_item.values() if len(v) == 2 and all(x.verdict is True for x in v)),
                     len(by_item))["str"],
        }
    out["per_judge"] = per
    roles = list(recs)
    out["inter_judge_agreement"] = J.agreement(recs[roles[0]], recs[roles[1]]) if len(roles) > 1 else "待测"
    out["corruptions"] = [{"item_id": i, "change": c} for i, _, _, c in faith_items if c] + \
                         [{"item_id": i, "change": c} for i, _, _, c in choice_items]
    REPORTS.joinpath("runs").mkdir(parents=True, exist_ok=True)
    J.dump([x for v in recs.values() for x in v], REPORTS / "runs" / "judgments_block1.jsonl")
    # committed summary of judgment records (no raw text): model name/version/status per record
    with open(REPORTS / "judgments_block1_records.jsonl", "w", encoding="utf-8") as fh:
        for v in recs.values():
            for x in v:
                d = asdict(x)
                d.pop("raw", None)
                fh.write(json.dumps(d, ensure_ascii=False) + "\n")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "data" / "tushen.sqlite"))
    ap.add_argument("--llm", action="store_true", help="run small model-based filter/judge samples if keys exist")
    ap.add_argument("--llm-sample", type=int, default=8)
    a = ap.parse_args()
    store = Store(a.db)
    t0 = time.time()

    t1, counts = build_t1(store)
    t1, n_dup = dedup(t1)
    u = build_u(store)
    filters = {**counts, "removed_dedup": n_dup, "after_dedup": len(t1)}
    test = [q for q in t1 if q["split"] == "test"]
    dev = [q for q in t1 if q["split"] == "dev"]
    u_test = [q for q in u if q["split"] == "test"]

    # freeze question set before running
    REPORTS.mkdir(exist_ok=True)
    qpath = REPORTS / "questions_block1.jsonl"
    with open(qpath, "w", encoding="utf-8") as f:
        for q in t1 + u:
            f.write(json.dumps(q, ensure_ascii=False) + "\n")

    # one variable at a time: A = pure BM25; B = A + clause_route
    cfg_a = SearchConfig(mode="keyword", k=10, clause_route=False)
    cfg_b = SearchConfig(mode="keyword", k=10, clause_route=True)
    ra, rb = run_config(store, test, cfg_a), run_config(store, test, cfg_b)
    ha = [r is not None and r <= 5 for r in ra]
    hb = [r is not None and r <= 5 for r in rb]
    mc = mcnemar_exact(ha, hb)
    bs = bootstrap_diff_ci([float(x) for x in ha], [float(x) for x in hb])

    # /ask behaviour (default config = B), program-judged
    t1_ans = [ask(store, q["question"]) for q in test]
    u_ans = [ask(store, q["question"]) for q in u_test]
    false_ref = sum(r["refused"] for r in t1_ans)
    correct_ref = sum(r["refused"] for r in u_ans)
    cits = [(c, q) for r, q in zip(t1_ans, test) if not r["refused"] for c in r["citations"]]
    cit_ok = sum(1 for c, q in cits if c["chunk_id"] in set(q["acceptable"]))
    cit_verified = sum(1 for c, _ in cits if c["verified"])
    # failure list for attribution (block 1: only categories decidable by program)
    failures = [{"qid": q["qid"], "question": q["question"], "rank_A": r1, "rank_B": r2,
                 "attribution": "待测（block 2 自动归因）"} for q, r1, r2 in zip(test, ra, rb) if r2 is None or r2 > 5]

    report = {
        "run": {"time_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "python": platform.python_version(),
                "parser": PARSER_VERSION, "chunker": CHUNKER_VERSION, "tokenizer": TOKENIZER_VERSION,
                "docs": [{"doc_id": d["doc_id"], "sha256": d["sha256"]} for d in store.docs()],
                "questions_file": str(qpath.relative_to(ROOT)), "seconds": None},
        "question_construction": {
            "T1_filters": filters, "T1_dev": len(dev), "T1_test": len(test), "U_total": len(u), "U_test": len(u_test),
            "model_filter_passes": "skipped (run with --llm)"},
        "retrieval_T1_test": {
            "A_keyword_bm25_only": {"config": asdict(cfg_a), **summarize(ra, test)},
            "B_keyword_plus_clause_route": {"config": asdict(cfg_b), **summarize(rb, test)},
            "paired_A_vs_B_hit@5": {"mcnemar_exact": mc, "bootstrap_diff_B_minus_A": bs,
                                     "label": significance_label(mc["p_value"], bs["ci"]),
                                     "variable_changed": "clause_route"}},
        "ask_program_judged": {
            "false_refusal_rate_T1_test": rate(false_ref, len(test))["str"],
            "refusal_rate_U_test(correct_refusals)": rate(correct_ref, len(u_test))["str"],
            "citation_precision_T1(cited chunk in acceptable set)": rate(cit_ok, len(cits))["str"],
            "citations_verbatim_verified": rate(cit_verified, len(cits))["str"],
            "refusal_reasons_T1": _count(r["refusal_reason"] for r in t1_ans if r["refused"]),
        },
        "not_measured_yet": ["T2/T3/T4/T5 题型", "vector/hybrid/rerank 模式", "改写题集复测", "自动失败归因"],
        "failures_B": failures[:50], "n_failures_B": len(failures),
    }
    if a.llm:
        report["question_construction"]["model_filter_passes"] = model_filters(test, store, a.llm_sample)
        report["optional_model_judged_study"] = judge_study(store, test, a.llm_sample)
    else:
        report["optional_model_judged_study"] = "skipped (run with --llm)"
    report["run"]["seconds"] = round(time.time() - t0, 1)
    out = REPORTS / "eval_block1.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("question_construction", "ask_program_judged")}, ensure_ascii=False, indent=1))
    for name in ("A_keyword_bm25_only", "B_keyword_plus_clause_route"):
        r = report["retrieval_T1_test"][name]
        print(name, "Recall@5", r["Recall@5"]["str"], "MRR@10", r["MRR@10"]["str"], r["by_overlap_stratum"])
    print("paired", report["retrieval_T1_test"]["paired_A_vs_B_hit@5"])


def _count(it):
    d: dict = {}
    for x in it:
        k = (x or "")[:20]
        d[k] = d.get(k, 0) + 1
    return d


if __name__ == "__main__":
    main()
