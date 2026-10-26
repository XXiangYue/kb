"""Paired A/B of ONE /ask citation variable on the FROZEN block-1 question set.

Reads reports/questions_block1.jsonl (never regenerates questions), runs /ask
under two AskConfigs that differ in exactly one field, and reports citation
precision (cited chunk in acceptable set) with denominators, false-refusal on
T1-test, correct refusal on U-test, an exact McNemar test on the per-question
outcome "all citations precise", and a cluster bootstrap CI for the precision
difference.  Program-judged only; no model calls.

Usage: python -m tushen_kb.eval.cite_ab --variable cite_kinds
"""
from __future__ import annotations

import argparse
import collections
import json
import time
from dataclasses import replace

from ..ask import AskConfig, ask
from ..ingest import ROOT
from ..store import Store
from .metrics import bootstrap_ratio_diff_ci, mcnemar_exact, rate, significance_label

REPORTS = ROOT / "reports"
QFILE = REPORTS / "questions_block1.jsonl"

# variable name -> (old value, new value).  Everything else stays at AskConfig defaults.
VARIABLES = {
    "cite_kinds": (("clause", "table", "text", "commentary"), ("clause", "table", "text")),
    "coverage_strip_doc_refs": (False, True),
}


def run(store, cfg: AskConfig, test, u_test):
    per = []
    kinds_bad = collections.Counter()
    for q in test:
        r = ask(store, q["question"], cfg)
        acc = set(q["acceptable"])
        cits = [] if r["refused"] else r["citations"]
        ok = sum(1 for c in cits if c["chunk_id"] in acc)
        for c in cits:
            if c["chunk_id"] not in acc:
                kinds_bad[c["chunk_id"].split(":")[1]] += 1
        per.append({"qid": q["qid"], "refused": r["refused"], "n_cit": len(cits), "n_ok": ok})
    u_ref = sum(ask(store, q["question"], cfg)["refused"] for q in u_test)
    n_ok, n_cit = sum(p["n_ok"] for p in per), sum(p["n_cit"] for p in per)
    return per, {
        "citation_precision_T1": rate(n_ok, n_cit)["str"],
        "false_refusal_T1_test": rate(sum(p["refused"] for p in per), len(per))["str"],
        "correct_refusal_U_test": rate(u_ref, len(u_test))["str"],
        "questions_all_citations_precise": rate(sum(1 for p in per if p["n_cit"] and p["n_ok"] == p["n_cit"]),
                                                len(per))["str"],
        "imprecise_citations_by_kind": dict(kinds_bad),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "data" / "tushen.sqlite"))
    ap.add_argument("--variable", required=True, choices=sorted(VARIABLES))
    ap.add_argument("--base", default="{}", help="JSON AskConfig overrides shared by A and B (earlier fixes)")
    a = ap.parse_args()
    store = Store(a.db)
    qs = [json.loads(l) for l in open(QFILE, encoding="utf-8")]
    test = [q for q in qs if q["type"] != "U" and q["split"] == "test"]
    u_test = [q for q in qs if q["type"] == "U" and q["split"] == "test"]
    base = AskConfig(**{k: tuple(v) if isinstance(v, list) else v for k, v in json.loads(a.base).items()})
    old, new = VARIABLES[a.variable]
    cfg_a, cfg_b = replace(base, **{a.variable: old}), replace(base, **{a.variable: new})
    pa, sa = run(store, cfg_a, test, u_test)
    pb, sb = run(store, cfg_b, test, u_test)
    ya = [p["n_cit"] > 0 and p["n_ok"] == p["n_cit"] for p in pa]
    yb = [p["n_cit"] > 0 and p["n_ok"] == p["n_cit"] for p in pb]
    mc = mcnemar_exact(ya, yb)
    bs = bootstrap_ratio_diff_ci([p["n_ok"] for p in pa], [p["n_cit"] for p in pa],
                                 [p["n_ok"] for p in pb], [p["n_cit"] for p in pb])
    rep = {"time_local": time.strftime("%Y-%m-%d %H:%M %Z"), "question_file": str(QFILE.relative_to(ROOT)),
           "n_T1_test": len(test), "n_U_test": len(u_test), "variable": a.variable,
           "A_old": {"value": old, **sa}, "B_new": {"value": new, **sb},
           "paired": {"mcnemar_exact(per-question all-citations-precise)": mc,
                      "bootstrap_precision_diff_B_minus_A": bs,
                      "label": significance_label(mc["p_value"], bs["ci"])},
           "shared_base_overrides": json.loads(a.base)}
    out = REPORTS / f"cite_ab_{a.variable}.json"
    out.write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(rep, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
