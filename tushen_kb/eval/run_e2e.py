"""Step 5: end-to-end ask() (the function behind HTTP /ask) WITH the DeepSeek generator.

DeepSeek is the GENERATOR only; nothing here asks any model to judge.  All metrics are program
checks against the frozen gold labels:
  refusal / false refusal   (U2 test / answerable test)
  citation verbatim pass    「」 quotes in generated answers that verify verbatim against the
                            retrieved evidence  /  all 「」 quotes in generated answers
                            (counted on every answer the generator produced, incl. those then refused)
  citation precision        program citations (口径 B) in the acceptable set / all, non-refused only
  quote precision           verified quotes whose evidence chunk is in the acceptable set / verified quotes
Small CPU batches (--batch, default 20) with a resumable checkpoint file; per-call log has
status, tokens, latency, error class - never prompt secrets or keys.
"""
from __future__ import annotations

import argparse
import json
import re
import resource
import time
from pathlib import Path

from ..ask import AskConfig, ask
from ..cite import verify_quote
from ..ingest import ROOT
from ..llm import ChatModel, load_models, validate
from ..search import SearchConfig
from ..store import Store
from .metrics import rate
from .run_block2 import _memo

REPORTS = ROOT / "reports"


class Recorder:
    """Keeps the raw generator output: ask() drops the answer text when it refuses afterwards."""
    def __init__(self, m):
        self.m, self.last = m, None

    def complete(self, *a, **k):
        self.last = self.m.complete(*a, **k)
        return self.last


def one(store, q, cfg, llm):
    llm.last = None
    r = ask(store, q["question"], cfg, llm=llm)
    st = {s["stage"]: s for s in r["trace"]["stages"]}
    call = r["trace"].get("llm_call")
    acc = set(q["acceptable"])
    rec = {"qid": q["qid"], "type": q["type"], "split": q["split"], "answerable": q["answerable"],
           "refused": r["refused"], "refusal_reason": r["refusal_reason"], "llm_called": call is not None,
           "llm": call, "answer": r["answer"]}
    if call and call.get("status") == "ok":
        ans = llm.last["text"] or ""
        rec["raw_answer"] = ans
        quotes = re.findall(r"「([^」]{4,})」", ans or "")
        ev = r["evidence"]
        qv = []
        for qt in quotes:
            src = [e["chunk_id"] for e in ev if verify_quote(qt, e["text"])[0]]
            qv.append({"ok": bool(src), "in_gold": any(s in acc for s in src)})
        rec["quotes"] = qv
        rec["n_quotes_stage"] = st.get("llm", {}).get("n_quotes")
        rec["n_bad_quotes_stage"] = st.get("llm", {}).get("n_bad_quotes")
    cits = [] if r["refused"] else r["citations"]
    rec["n_cit"], rec["n_cit_ok"] = len(cits), sum(c["chunk_id"] in acc for c in cits)
    return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", type=int, default=20)
    ap.add_argument("--out", default="e2e_deepseek")
    ap.add_argument("--summarize-only", action="store_true")
    a = ap.parse_args()
    if a.summarize_only:
        prev = json.loads((REPORTS / f"{a.out}.json").read_text(encoding="utf-8"))
        # keep the threshold the run actually used, not the current default
        summarize(REPORTS / f"{a.out}_per_question.jsonl", a.out, AskConfig(min_coverage=prev["min_coverage"]))
        return
    gen, judges = load_models()
    validate(gen, judges)
    assert gen.family == "deepseek" and gen.status() == "ready", gen.status()
    llm = Recorder(ChatModel(gen))
    store = Store(str(ROOT / "data" / "tushen.sqlite"))
    from .. import dense
    _memo(dense)
    cfg = AskConfig(search=SearchConfig(mode="hybrid_rerank"))
    qs = []
    for f in ("questions_block2.jsonl", "questions_lowoverlap.jsonl"):
        qs += [dict(json.loads(l), _file=f) for l in open(REPORTS / f, encoding="utf-8")][1:]
    qs = [q for q in qs if q.get("split") == "test"]
    ck = REPORTS / f"{a.out}_per_question.jsonl"
    done = {json.loads(l)["qid"] for l in open(ck, encoding="utf-8")} if ck.exists() else set()
    todo = [q for q in qs if q["qid"] not in done]
    for i in range(0, len(todo), a.batch):
        b = todo[i:i + a.batch]
        t0 = time.time()
        recs = [dict(one(store, q, cfg, llm), file=q["_file"]) for q in b]
        with open(ck, "a", encoding="utf-8") as f:
            for r in recs:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        print(f"batch {i // a.batch + 1}: {len(b)} q, {time.time() - t0:.0f}s, "
              f"rss {resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024}MB", flush=True)
    summarize(ck, a.out, cfg)


def summarize(ck: Path, out: str, cfg):
    R = [json.loads(l) for l in open(ck, encoding="utf-8")]
    res = {}
    for name, sel in (("block2_test", lambda r: r["file"] == "questions_block2.jsonl"),
                      ("lowoverlap_test", lambda r: r["file"] == "questions_lowoverlap.jsonl")):
        S = [r for r in R if sel(r)]
        ans = [r for r in S if r["answerable"]]
        u2 = [r for r in S if r["type"] == "U2"]
        calls = [r["llm"] for r in S if r["llm"]]
        ok = [c for c in calls if c["status"] == "ok"]
        quotes = [x for r in S for x in r.get("quotes", [])]
        vq = [x for x in quotes if x["ok"]]
        reasons = {}
        for r in ans:
            if r["refused"]:
                k = re.sub(r"[（(].*", "", r["refusal_reason"]).strip()
                reasons[k] = reasons.get(k, 0) + 1
        lat = sorted(c["ms"] for c in ok if c.get("ms") is not None)
        res[name] = {
            "n_answerable": len(ans), "n_U2": len(u2),
            "correct_refusal_U2": rate(sum(r["refused"] for r in u2), len(u2))["str"],
            "false_refusal_answerable": rate(sum(r["refused"] for r in ans), len(ans))["str"],
            "false_refusal_reasons": reasons,
            "llm_calls": len(calls), "llm_ok": len(ok),
            "llm_failures": {s: sum(c["status"] == s for c in calls) for s in {c["status"] for c in calls}},
            "tokens_total": sum((c.get("usage") or {}).get("total_tokens", 0) for c in ok),
            "latency_ms_mean": round(sum(lat) / len(lat)) if lat else None,
            "latency_ms_p50": lat[len(lat) // 2] if lat else None,
            "latency_ms_p95": lat[int(len(lat) * 0.95) - 1] if lat else None,
            "answers_with_no_quote": sum(1 for r in S if r["llm"] and r["llm"]["status"] == "ok" and not r.get("quotes")),
            "citation_verbatim_pass(quotes)": rate(len(vq), len(quotes))["str"],
            "quote_precision(verified quotes from gold chunk)": rate(sum(x["in_gold"] for x in vq), len(vq))["str"],
            "citation_precision(program citations, non-refused answerable, 口径B)":
                rate(sum(r["n_cit_ok"] for r in ans), sum(r["n_cit"] for r in ans))["str"],
            "citation_precision_口径A": "待测 (needs rerun with coverage_strip_doc_refs=False; not run)",
            "answerable_with_ge1_correct_citation":
                rate(sum(1 for r in ans if not r["refused"] and r["n_cit_ok"] > 0), len(ans))["str"],
        }
    rep = {"time_local": time.strftime("%Y-%m-%d %H:%M %Z"), "search_mode": "hybrid_rerank",
           "min_coverage": cfg.min_coverage, "generator": "deepseek-flash (generator role only; no model judging)",
           "results": res}
    (REPORTS / f"{out}.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(rep, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
