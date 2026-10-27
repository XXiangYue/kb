"""Block 3: a new refusal signal, with the rule PRE-REGISTERED here and committed before any run.

Candidates (exactly two; both replace the coverage gate; everything else in /ask is unchanged):
  S1  rerank_top  = sigmoid(top bge-reranker-base logit among the hybrid_rerank hits)
  S2  cov_rerank  = 0.5 * top_coverage(口径 B, top-3 evidence) + 0.5 * S1
      (equal fixed weights, both terms in [0,1]; no weight is fitted, to avoid overfitting a small dev)
  Refuse iff S < t.  Implementation: one evidence-only /ask per question with min_coverage = 0.0
  (so the coverage gate and the per-citation coverage filter are off), then apply S < t on top.
  The other refusals of /ask (missing standard / clause, no hits, no verified citation) still apply.
Grid: t = 0.00, 0.02, ..., 1.00, for each candidate (51 x 2 settings).
Objective (dev only), false answer costs MORE than false refusal, ratio fixed at 5 : 1:
  cost = 5 * FA_rate(dev out-of-corpus, not refused) + 1 * FR_rate(dev answerable, refused)
  Rates, not counts: dev has more answerable than out-of-corpus questions, and counting errors
  equally is what made Step 4 pick 0.05.  Using rates means assuming equal class priors, because
  the real deployment mix is unknown.
  Why 5:1: in drawing review, a false answer gives the reviewer an unsupported provision, and they
  may accept a non-compliant drawing. A false refusal only costs a manual lookup in the code.
  5 is a fixed round number, not derived from data.  Costs at 3:1 and 10:1 are reported for
  information only; they are not used for selection.
Ties: lower dev FA rate, then higher t (prefer refusing), then S1 before S2 (simpler).
Dev  = block-2 dev answerable + low-overlap dev (answerable), plus OOC dev (new, >= 30) + U2 dev.
Test = run ONCE, chosen (signal, t) only, on: block-2 test answerable, low-overlap test,
       OOC test (new, frozen in its own commit before any run), and U2 test, which is reported
       separately and labelled 「已见过」 (it was used in Steps 2-5).
Paired vs current (default /ask: coverage gate min_coverage = 0.5), on the same test questions:
  McNemar exact on refusal booleans per set; citation precision via cluster bootstrap.
  The archived 0.05 is shown for information only.

OOC questions: the U2 procedure (questions_b2.build_u2) with NEW topics, which are disjoint from
U2's topics and from each other.  Dev topics give split=dev, test topics give split=test.
Kept only if: parsable, term in q, no standard/clause reference, the key term occurs 0 times in the
whole stored corpus text, not equal to / not sharing the key term with / no >= 8-char common
substring with any existing U2 question (all 40, including the 31 U2 test), and no such overlap
between OOC dev and OOC test.

Usage:
  python -m tushen_kb.eval.refusal_b3 build-ooc   -> reports/questions_ooc_b3.jsonl (then commit = freeze)
  python -m tushen_kb.eval.refusal_b3 calibrate   -> reports/refusal_b3_dev.json  (dev only)
  python -m tushen_kb.eval.refusal_b3 test        -> reports/refusal_b3_test*.json (refuses to run twice)
"""
from __future__ import annotations

from .. import stdref  # resolves standards-text references in committed artifacts

import argparse
import json
import math
import re
import subprocess
import time

from ..ask import AskConfig, ask
from ..ingest import ROOT
from ..normalize import squash
from ..search import SearchConfig
from ..store import Store
from .metrics import bootstrap_ratio_diff_ci, mcnemar_exact, rate
from . import questions_b2 as b2

REPORTS = ROOT / "reports"
OOC = REPORTS / "questions_ooc_b3.jsonl"
DEV_OUT = REPORTS / "refusal_b3_dev.json"
TEST_OUT = REPORTS / "refusal_b3_test.json"
TEST_PQ = REPORTS / "refusal_b3_test_per_question.jsonl"
GRID = [round(0.02 * i, 2) for i in range(51)]
SIGNALS = ("S1_rerank_top", "S2_cov_rerank")
C_FA, C_FR = 5, 1
MODE = "hybrid_rerank"
CURRENT_MIN_COV = 0.5
OOC_TOPICS = {"dev": ["建筑电气照明设计", "暖通空调负荷计算", "室外道路与停车场设计",
                      "装配式混凝土构件连接", "基坑支护与降水"],
              "test": ["建筑幕墙设计", "无障碍设计", "人民防空工程", "电梯安装与验收",
                       "市政排水管网", "太阳能光伏建筑一体化"]}
PER_CALL = 12


def sig(x: float) -> float:
    return 1 / (1 + math.exp(-x))


# ---------------------------------------------------------------- OOC build
def build_ooc():
    from ..llm import ChatModel, load_models
    gen, _ = load_models()
    assert gen.family == "deepseek" and gen.status() == "ready", gen.status()
    m = ChatModel(gen)
    store = Store(str(ROOT / "data" / "tushen.sqlite"))
    g = b2.Gold(store)
    assert not set(OOC_TOPICS["dev"]) & set(OOC_TOPICS["test"]) and not set(sum(OOC_TOPICS.values(), [])) & set(b2.U_TOPICS)
    u2 = [q for q in [stdref.loads(l) for l in open(b2.QFILE, encoding="utf-8")][1:] if q["type"] == "U2"]
    norm = lambda s: re.sub(r"\s", "", s)
    cnt = {s: {"proposed": 0, "removed_unparsable": 0, "removed_ref": 0, "removed_term_in_corpus": 0,
               "removed_overlap_U2": 0, "removed_overlap_dev_test": 0, "removed_dup": 0} for s in OOC_TOPICS}
    calls, out = [], {"dev": [], "test": []}
    for split in ("dev", "test"):
        for topic in OOC_TOPICS[split]:
            r = m.complete([{"role": "user", "content": b2.U_PROMPT.format(n=PER_CALL, topics=topic)}],
                           max_tokens=1500, temperature=0.7)
            calls.append(b2._log(r, f"OOC_{split}_generate", topic))
            if r["status"] != "ok":
                continue
            try:
                items = json.loads(re.search(r"\[.*\]", r["text"], re.S).group())
            except Exception:
                cnt[split]["removed_unparsable"] += 1
                continue
            for it in items:
                c = cnt[split]
                c["proposed"] += 1
                q, term = str(it.get("q", "")).strip(), str(it.get("term", "")).strip()
                if not q or not term or term not in q:
                    c["removed_unparsable"] += 1; continue
                if b2.has_ref(q):
                    c["removed_ref"] += 1; continue
                if squash(term)[0] in g.corpus:
                    c["removed_term_in_corpus"] += 1; continue
                if any(norm(q) == norm(x["question"]) or term == x["key_term"] or b2.lcs_len(q, x["question"]) >= 8
                       for x in u2):
                    c["removed_overlap_U2"] += 1; continue
                other = out["dev"] if split == "test" else []
                if any(term == x["key_term"] or b2.lcs_len(q, x["question"]) >= 8 for x in other):
                    c["removed_overlap_dev_test"] += 1; continue
                if any(norm(q) == norm(x["question"]) or term == x["key_term"] for x in out[split]):
                    c["removed_dup"] += 1; continue
                i = b2.qid("OOC", q)
                out[split].append({"qid": i, "type": "OOC", "question": q, "key_term": term, "topic": topic,
                                   "key_term_occurrences_in_corpus": 0, "acceptable": [], "gold_spans": [],
                                   "split": split, "answerable": False, "generator_model": r["model_version"]})
    allq = out["dev"] + out["test"]
    u2t = [x for x in u2 if x["split"] == "test"]
    verify = {
        "n_dev": len(out["dev"]), "n_test": len(out["test"]),
        "qid_overlap_with_U2_all40": len({q["qid"] for q in allq} & {x["qid"] for x in u2}),
        "text_overlap_with_U2_test31": len({norm(q["question"]) for q in allq} & {norm(x["question"]) for x in u2t}),
        "key_term_overlap_with_U2_test31": len({q["key_term"] for q in allq} & {x["key_term"] for x in u2t}),
        "max_common_substring_with_U2_test31": max((b2.lcs_len(q["question"], x["question"]) for q in allq for x in u2t), default=0),
        "dev_test_text_overlap": len({norm(q["question"]) for q in out["dev"]} & {norm(q["question"]) for q in out["test"]}),
        "dev_test_key_term_overlap": len({q["key_term"] for q in out["dev"]} & {q["key_term"] for q in out["test"]}),
        "key_terms_in_corpus": sum(squash(q["key_term"])[0] in g.corpus for q in allq),
    }
    meta = {"time_local": time.strftime("%Y-%m-%d %H:%M %Z"), "prompt": b2.U_PROMPT, "topics": OOC_TOPICS,
            "per_call": PER_CALL, "filters": cnt, "verify": verify,
            "generator": {"model": gen.model, "family": gen.family, "role": "generator only"},
            "generator_calls": {"n": len(calls), "ok": sum(c["status"] == "ok" for c in calls),
                                "tokens_total": sum((c["usage"] or {}).get("total_tokens", 0) for c in calls)},
            "calls": calls}
    with open(OOC, "w", encoding="utf-8") as f:
        f.write(json.dumps({"_meta": meta}, ensure_ascii=False) + "\n")
        for q in allq:
            f.write(json.dumps(q, ensure_ascii=False) + "\n")
    print(json.dumps({k: meta[k] for k in ("filters", "verify", "generator_calls")}, ensure_ascii=False, indent=1))


# ---------------------------------------------------------------- evaluation
def _frozen(path):
    """The OOC file must be committed and unchanged (frozen before any run)."""
    rel = str(path.relative_to(ROOT))
    tracked = subprocess.run(["git", "ls-files", "--error-unmatch", rel], cwd=ROOT, capture_output=True).returncode == 0
    clean = subprocess.run(["git", "diff", "--quiet", "HEAD", "--", rel], cwd=ROOT).returncode == 0
    assert tracked and clean, f"{rel} must be committed (frozen) before running"


def load_sets(split):
    def rd(p):
        return [stdref.loads(l) for l in open(p, encoding="utf-8")][1:]
    b2q, low, ooc = rd(b2.QFILE), rd(REPORTS / "questions_lowoverlap.jsonl"), rd(OOC)
    s = lambda L: [q for q in L if q["split"] == split]
    return {"block2_answerable": [q for q in s(b2q) if q["answerable"]],
            "lowoverlap": [q for q in s(low) if q["answerable"]],
            "ooc_new": s(ooc),
            "U2" + ("_已见过" if split == "test" else ""): [q for q in s(b2q) if q["type"] == "U2"]}


def one(store, q, cfg):
    r = ask(store, q["question"], cfg)
    ev = r.get("evidence") or []
    logits = [e["score"] for e in ev if e.get("route") == "rerank"]
    top_cov = max((e["coverage"] for e in ev), default=0.0)
    s1 = sig(max(logits)) if logits else 0.0
    acc = set(q.get("acceptable") or [])
    cits = [] if r["refused"] else r["citations"]
    return {"qid": q["qid"], "answerable": q["answerable"], "base_refused": r["refused"],
            "base_reason": r["refusal_reason"], "top_cov": top_cov, "S1_rerank_top": s1,
            "S2_cov_rerank": 0.5 * top_cov + 0.5 * s1,
            "n_cit": len(cits), "n_cit_ok": sum(c["chunk_id"] in acc for c in cits)}


def current(store, q, mc):
    r = ask(store, q["question"], AskConfig(search=SearchConfig(mode=MODE), min_coverage=mc))
    acc = set(q.get("acceptable") or [])
    cits = [] if r["refused"] else r["citations"]
    return {"refused": r["refused"], "n_cit": len(cits), "n_cit_ok": sum(c["chunk_id"] in acc for c in cits)}


def refused(rec, sigl, t):
    return rec["base_refused"] or rec[sigl] < t


def run_records(store, sets):
    cfg = AskConfig(search=SearchConfig(mode=MODE), min_coverage=0.0)
    return {k: [one(store, q, cfg) for q in L] for k, L in sets.items()}


def setup():
    store = Store(str(ROOT / "data" / "tushen.sqlite"))
    from .. import dense
    from .run_block2 import _memo
    _memo(dense)
    return store


def calibrate():
    _frozen(OOC)
    store = setup()
    sets = load_sets("dev")
    R = run_records(store, sets)
    ans = R["block2_answerable"] + R["lowoverlap"]
    ooc = R["ooc_new"] + R["U2"]
    grid = []
    for sigl in SIGNALS:
        for t in GRID:
            fr = sum(refused(r, sigl, t) for r in ans)
            fa = sum(not refused(r, sigl, t) for r in ooc)
            fr_r, fa_r = fr / len(ans), fa / len(ooc)
            grid.append({"signal": sigl, "t": t, "false_refusal": rate(fr, len(ans))["str"],
                         "false_answer_ooc": rate(fa, len(ooc))["str"],
                         "cost_5to1": round(C_FA * fa_r + C_FR * fr_r, 4),
                         "cost_3to1(info)": round(3 * fa_r + fr_r, 4), "cost_10to1(info)": round(10 * fa_r + fr_r, 4),
                         "_fa": fa_r})
    best = sorted(grid, key=lambda g: (g["cost_5to1"], g["_fa"], -g["t"], SIGNALS.index(g["signal"])))[0]
    cur = {}
    for mc in (CURRENT_MIN_COV, 0.05):
        fr = sum(current(store, q, mc)["refused"] for q in sets["block2_answerable"] + sets["lowoverlap"])
        fa = sum(not current(store, q, mc)["refused"] for q in sets["ooc_new"] + sets["U2"])
        cur[f"coverage_gate_{mc}"] = {"false_refusal": rate(fr, len(ans))["str"],
                                      "false_answer_ooc": rate(fa, len(ooc))["str"],
                                      "cost_5to1": round(C_FA * fa / len(ooc) + C_FR * fr / len(ans), 4)}
    rep = {"time_local": time.strftime("%Y-%m-%d %H:%M %Z"), "mode": MODE, "rule": __doc__,
           "n_dev": {k: len(v) for k, v in sets.items()},
           "dev_breakdown_at_choice": {k: rate(sum(refused(r, best["signal"], best["t"]) for r in v), len(v))["str"]
                                       + (" refused") for k, v in R.items()},
           "current_on_dev": cur,
           "grid": [{k: v for k, v in g.items() if k != "_fa"} for g in grid],
           "chosen": {"signal": best["signal"], "t": best["t"]}}
    DEV_OUT.write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: rep[k] for k in ("n_dev", "dev_breakdown_at_choice", "current_on_dev", "chosen")},
                     ensure_ascii=False, indent=1))


def test():
    _frozen(OOC)
    _frozen(DEV_OUT)
    assert not TEST_OUT.exists() and not TEST_PQ.exists(), "test already run once"
    ch = json.loads(DEV_OUT.read_text(encoding="utf-8"))["chosen"]
    store = setup()
    sets = load_sets("test")
    R = run_records(store, sets)
    res, pq = {}, []
    for k, L in sets.items():
        new = [refused(r, ch["signal"], ch["t"]) for r in R[k]]
        cu = [current(store, q, CURRENT_MIN_COV) for q in L]
        old = [c["refused"] for c in cu]
        o05 = [current(store, q, 0.05)["refused"] for q in L]
        for q, r, n, c in zip(L, R[k], new, cu):
            pq.append({"set": k, **r, "new_refused": n, "current_refused": c["refused"]})
        what = "false_refusal" if L and L[0]["answerable"] else "correct_refusal"
        d = {"n": len(L), f"{what}_new": rate(sum(new), len(L))["str"],
             f"{what}_current_0.5": rate(sum(old), len(L))["str"],
             f"{what}_archived_0.05(info)": rate(sum(o05), len(L))["str"],
             "mcnemar_refused(a=current,b=new; b01=current not refused,new refused)": mcnemar_exact(old, new)}
        if L and L[0]["answerable"]:
            nn = [0 if x else r["n_cit_ok"] for r, x in zip(R[k], new)]
            nd = [0 if x else r["n_cit"] for r, x in zip(R[k], new)]
            d["citation_precision_new(口径B)"] = rate(sum(nn), sum(nd))["str"]
            d["citation_precision_current(口径B)"] = rate(sum(c["n_cit_ok"] for c in cu), sum(c["n_cit"] for c in cu))["str"]
            d["citation_precision_diff_ci"] = bootstrap_ratio_diff_ci([c["n_cit_ok"] for c in cu], [c["n_cit"] for c in cu], nn, nd)
            d["ge1_correct_new"] = rate(sum(x > 0 for x in nn), len(L))["str"]
            d["ge1_correct_current"] = rate(sum(c["n_cit_ok"] > 0 for c in cu), len(L))["str"]
        res[k] = d
    rep = {"time_local": time.strftime("%Y-%m-%d %H:%M %Z"), "mode": MODE, "chosen_on_dev": ch,
           "current": f"coverage gate min_coverage={CURRENT_MIN_COV}", "results": res,
           "note": "U2_已见过 was used in Steps 2-5; only ooc_new is an unseen out-of-corpus set."}
    TEST_OUT.write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    TEST_PQ.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in pq), encoding="utf-8")
    print(json.dumps(rep, ensure_ascii=False, indent=1))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["build-ooc", "calibrate", "test"])
    a = ap.parse_args()
    {"build-ooc": build_ooc, "calibrate": calibrate, "test": test}[a.cmd]()


if __name__ == "__main__":
    main()
