"""Agent-level end-to-end eval harness for ``ask`` (pre-registered plan: reports/plan_agent_eval_zh.md).

One command runs the whole regression and writes an eval report (md + json):

    python -m tushen_kb.eval.agent_eval --suite agent      # full: 414 test questions x 2 configs + stability
    python -m tushen_kb.eval.agent_eval --suite smoke      # CI-style smoke subset (20 questions, no judge2)
    make eval / make eval-smoke

Design (all fixed BEFORE the run, nothing tuned on the test split):
* system under test = frozen Block 3/4 config (hybrid_rerank + rerank_top gate 0.94) + DeepSeek generator;
  baseline = the repo's default config (BM25 keyword, coverage gate 0.5, no rerank) + the same generator.
* every question is one OTel trace: eval.item -> kb.ask -> step spans (existence_check / retrieve
  {bm25, vector, rrf, rerank, clause_route} / evidence_gate / rerank_gate / cite / generate{chat} /
  verify_quotes) -> eval.judge.* {chat}.  Spans are written to <out>/traces.jsonl.
* LLM judges only give binary inputs (answer correctness, evidence support).  Success and failure
  attribution are decided by ``attribute()`` - deterministic code that reads the item's spans and the
  frozen gold chunk ids.  The same function is re-run on the trace file at report time and must agree.
* judges: judge1 = Xiaomi MiMo (all answered items), judge2 = a Google model via OpenRouter on a seeded
  sample.  Neither shares provider/family with the DeepSeek generator (checked by llm.validate).
* resumable: each (config, run) has a checkpoint jsonl; finished items are skipped.
"""
from __future__ import annotations

from .. import stdref  # resolves standards-text references in committed artifacts

import argparse
import contextvars
import gzip
import json
import random
import re
import statistics
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .. import __version__, obs
from ..ask import AskConfig, ask
from ..ingest import ROOT
from ..llm import ChatModel, ModelSpec, load_models, validate
from ..search import SearchConfig
from ..store import Store
from .judges import FAITH_PROMPT, _parse_binary
from .metrics import bootstrap_diff_ci, bootstrap_mean_ci, cohen_kappa, mcnemar_exact, rate

REPORTS = ROOT / "reports"
QFILES = {"block2": "questions_block2.jsonl", "lowoverlap": "questions_lowoverlap.jsonl",
          "ooc_b3": "questions_ooc_b3.jsonl"}
CONFIGS = {
    # system under test: frozen Block 3/4 S1 config (same as 7fdc378 e2e_s1_deepseek)
    "system": dict(search=dict(mode="hybrid_rerank"), gate="rerank_top", gate_threshold=0.94),
    # pre-declared baseline: the repo default (BM25 keyword retrieval, coverage gate 0.5, no rerank)
    "baseline_bm25": dict(search=dict(mode="keyword"), gate="coverage", min_coverage=0.5),
}
PREV_VERSION_RUN = REPORTS / "e2e_s1_deepseek_per_question.jsonl"  # 7fdc378, same config as "system"
# judge2 (amendment A1, plan §10, committed before the judge2 phase ran): OpenRouter Gemini flash has no quota
# (key limit $0 -> HTTP 403) -> fallback the owner specified: MiMo.  A different, larger MiMo model than judge1;
# same provider/family as judge1 (weaker independence, reported), still different from the DeepSeek generator.
JUDGE2_SPEC = dict(role="judge2", provider="xiaomi-mimo", family="xiaomi-mimo",
                   base_url="https://token-plan-cn.xiaomimimo.com/v1", model="mimo-v2.6-pro",
                   api_key_env="MIMO_API_KEY", max_retries=3, backoff_base_s=10.0, timeout_s=120.0)
SEEDS = {"judge2_sample": 20261027, "stability_subset": 20261028, "bootstrap": 0}
SUITES = {
    "agent": dict(out="agent_eval", judge2_sample=60, judge2_cap=45, stability_n=30, stability_k=3,
                  overhead=True, smoke=False),
    "smoke": dict(out="agent_eval_smoke", judge2_sample=0, judge2_cap=0, stability_n=5, stability_k=3,
                  overhead=False, smoke=True),
    # harness debugging on the DEV split only (never reported as a result)
    "dev_smoke": dict(out="agent_eval_dev_smoke", judge2_sample=2, judge2_cap=2, stability_n=3, stability_k=2,
                      overhead=False, smoke=True, split="dev"),
}
CORRECT_PROMPT = (
    "你是严格的评测员。下面给出【问题】、【参考条文】（标准原文，包含该问题的正确答案）和【系统回答】。"
    "判断【系统回答】的核心结论是否与【参考条文】一致并回答了【问题】：涉及的数值、条件、对象必须与参考条文一致，"
    "不得与参考条文矛盾；回答可以比参考条文简短。只输出一个词：是 或 否。\n\n"
    "【问题】\n{question}\n\n【参考条文】\n{reference}\n\n【系统回答】\n{answer}"
)
TAXONOMY = ["检索未召回", "召回但排序靠后未进上下文", "生成幻觉", "引用错误", "拒答误判", "该拒未拒", "工具或步骤错误", "其他"]
STEP_SPANS = ["kb.existence_check", "kb.retrieve", "kb.evidence_gate", "kb.rerank_gate", "kb.cite", "kb.generate",
              "kb.verify_quotes"]


# ------------------------------------------------------------------ question set
def load_questions(smoke: bool = False, split: str = "test") -> list[dict]:
    qs = []
    for key, f in QFILES.items():
        for line in list(open(REPORTS / f, encoding="utf-8"))[1:]:
            q = stdref.loads(line)
            if q.get("split") == split:
                q["set"] = key if key != "block2" else ("block2_U2" if q["type"] == "U2" else "block2_answerable")
                qs.append(q)
    qs.sort(key=lambda q: (q["set"], q["qid"]))
    if smoke:  # pre-declared smoke subset: first 5 qids (sorted) of each of the 4 sets
        by = defaultdict(list)
        for q in qs:
            by[q["set"]].append(q)
        qs = [q for s in sorted(by) for q in by[s][:5]]
    return qs


def make_cfg(name: str) -> AskConfig:
    c = dict(CONFIGS[name])
    return AskConfig(search=SearchConfig(**c.pop("search")), **c)


# ------------------------------------------------------------------ attribution (code, from the trace)
def _by_name(spans):
    out = {}
    for s in sorted(spans, key=lambda s: s["start_time_unix_nano"]):
        out[s["name"]] = s
    return out


def trace_checks(spans: list[dict], gold: set) -> dict:
    """Per-step facts read only from span attributes + gold chunk ids."""
    S = _by_name(spans)
    A = S["kb.ask"]["attributes"]
    hits = S.get("kb.retrieve", {}).get("attributes", {}).get("kb.hit_chunk_ids", [])
    rr = S.get("kb.search.rerank", {}).get("attributes", {})
    pool = list(rr.get("kb.input_chunk_ids", [])) if rr else list(hits)
    for h in hits:  # clause_route can add chunks after rerank
        if h not in pool:
            pool.append(h)
    ev = S.get("kb.evidence_gate", {}).get("attributes", {}).get("kb.evidence_chunk_ids", [])
    cits = A.get("kb.citation_chunk_ids", [])
    vq = S.get("kb.verify_quotes", {}).get("attributes", {})
    gen = S.get("kb.generate")
    chat = next((s for s in spans if s["name"].startswith("chat ") and gen and s["parent_span_id"] == gen["span_id"]), None)
    errors = [s for s in spans if s["status"]["code"] == "ERROR" and (s["name"].startswith("kb.") or s is chat)]
    rank = lambda ids: next((i + 1 for i, c in enumerate(ids) if c in gold), None)
    return {
        "refused": bool(A.get("kb.refused")), "refusal_step": A.get("kb.refusal_step"),
        "refusal_reason": A.get("kb.refusal_reason"), "mode": A.get("kb.mode"),
        "reached": [n for n in STEP_SPANS if n in S],
        "error_spans": [{"name": s["name"], "error.type": s["attributes"].get("error.type"),
                         "description": s["status"]["description"]} for s in errors],
        "n_pool": len(pool), "gold_rank_pool": rank(pool), "gold_rank_hits": rank(hits),
        "gold_rank_pre_rerank": rank(rr.get("kb.input_chunk_ids", [])) if rr else None,
        "gold_in_pool": rank(pool) is not None, "gold_in_context": rank(ev) is not None,
        "s1": S.get("kb.rerank_gate", {}).get("attributes", {}).get("kb.s1"),
        "n_cit": len(cits) if not A.get("kb.refused") else 0,
        "n_cit_gold": sum(c in gold for c in cits) if not A.get("kb.refused") else 0,
        "n_cit_verified_any": S.get("kb.cite", {}).get("attributes", {}).get("kb.n_verified"),
        "llm_status": gen["attributes"].get("kb.llm_status") if gen else None,
        "n_quotes": vq.get("kb.n_quotes"), "n_bad_quotes": vq.get("kb.n_bad_quotes"),
        "quote_from_gold": bool(set(vq.get("kb.quote_source_chunk_ids", [])) & gold) if vq else None,
        "gen_tokens_in": chat["attributes"].get("gen_ai.usage.input_tokens") if chat else None,
        "gen_tokens_out": chat["attributes"].get("gen_ai.usage.output_tokens") if chat else None,
        "gen_cost_usd": chat["attributes"].get("tushen.cost_usd") if chat else None,
        "ask_ms": S["kb.ask"]["duration_ms"],
    }


def attribute(spans: list[dict], gold: set, answerable: bool, verdicts: dict) -> dict:
    """Pre-registered, priority-ordered rule (plan §5).  ``verdicts`` = {"correct": bool|None, "support": bool|None}
    from the primary judge; used only for answered answerable items."""
    c = trace_checks(spans, gold)
    answered = not c["refused"] and c["mode"] == "llm_answer"
    out = lambda cat, step, why: {"success": cat is None, "category": cat, "failed_step": step, "reason": why,
                                  "checks": c}
    if c["error_spans"]:
        e = c["error_spans"][0]
        return out("工具或步骤错误", e["name"], f"span status ERROR: {e['error.type']} {e['description'] or ''}".strip())
    if c["mode"] == "evidence_only" and not c["refused"]:
        return out("工具或步骤错误", "kb.generate", f"LLM 未返回答案（status={c['llm_status']}），退化为仅证据")
    if not answerable:
        if c["refused"]:
            return out(None, None, f"正确拒答 @ {c['refusal_step']}")
        gate = "kb.rerank_gate" if "kb.rerank_gate" in c["reached"] else "kb.evidence_gate"
        return out("该拒未拒", gate, "无答案问题未被任何步骤拦截，生成了答案")
    if answered and verdicts.get("correct") is True:
        return out(None, None, "已回答且裁判判定正确")
    if c["refusal_step"] == "kb.existence_check" or c["refusal_step"] == "existence_check":
        return out("拒答误判", "kb.existence_check", c["refusal_reason"])
    if not c["gold_in_pool"]:
        return out("检索未召回", "kb.retrieve", f"gold 不在候选池（{c['n_pool']} 个候选）")
    if not c["gold_in_context"]:
        step = "kb.search.rerank" if c["gold_rank_pre_rerank"] is not None else "kb.retrieve"
        return out("召回但排序靠后未进上下文", step,
                   (f"gold 在 rerank 输入（rerank 前排名 {c['gold_rank_pre_rerank']}），rerank 后 top10 排名 "
                    f"{c['gold_rank_hits']}，未进前 3 条证据" if c["gold_rank_pre_rerank"] is not None else
                    f"gold 在 top10 第 {c['gold_rank_hits']} 位，未进前 3 条证据"))
    rs = c["refusal_step"]
    if rs in ("evidence_gate", "rerank_gate"):
        return out("拒答误判", f"kb.{rs}", c["refusal_reason"])
    if rs == "cite":
        return out("工具或步骤错误", "kb.cite", c["refusal_reason"])
    if rs == "generate":
        return out("拒答误判", "kb.generate", c["refusal_reason"])
    if rs == "verify_quotes":
        return out("引用错误", "kb.verify_quotes", c["refusal_reason"])
    if answered:
        if verdicts.get("correct") is None:
            return out("其他", "eval.judge", "裁判调用失败/不可解析，按未通过计")
        if verdicts.get("support") is False:
            return out("生成幻觉", "kb.generate", "答案判错且裁判判定答案含证据不支持的陈述")
        return out("其他", "kb.generate", "答案判错但证据支持（答非所问/不完整）" if verdicts.get("support") else
                   "答案判错，支持度裁判缺失")
    return out("其他", rs and f"kb.{rs}", c["refusal_reason"] or "未分类")


# ------------------------------------------------------------------ judges
class Judges:
    def __init__(self, store, use_judge2=True):
        gen, judges = load_models()
        j1 = next(j for j in judges if j.role == "judge1")
        j2 = ModelSpec(**JUDGE2_SPEC)
        validate(gen, [j1])  # family/provider separation from the generator under test (each judge)
        validate(gen, [j2])  # (judge1 and judge2 share MiMo family by amendment A1, so they are checked separately)
        assert gen.family == "deepseek" and j1.family != "deepseek" and j2.family != "deepseek"
        assert j1.model != j2.model, "judge2 must be a different model from judge1"
        self.gen_spec, self.j1_spec, self.j2_spec = gen, j1, j2
        self.j1 = ChatModel(j1)
        self.j2 = ChatModel(j2) if use_judge2 and j2.available() else None
        self.store = store
        self.j2_calls = 0
        self._chunks = {}
        self._lock = __import__("threading").Lock()

    def chunk(self, cid):
        with self._lock:
            return self._chunk(cid)

    def _chunk(self, cid):  # sqlite connection is shared across judge threads -> serialize
        if cid not in self._chunks:
            row = self.store.conn.execute("SELECT doc_id, clause_no, kind, text FROM chunks WHERE chunk_id=?",
                                          (cid,)).fetchone()
            self._chunks[cid] = row
        return self._chunks[cid]

    def reference(self, q):
        parts = []
        for cid in q["acceptable"][:3]:
            row = self.chunk(cid)
            if row:
                parts.append(f"[{row[0]} 第{row[1] or '-'}条]\n{row[3][:1500]}")
        return "\n\n".join(parts)

    def _call(self, model, name, task, prompt, qid):
        with obs.span(f"eval.judge.{task}", **{"eval.qid": qid, "eval.judge.role": name,
                                               "eval.judge.model": model.spec.model}) as sp:
            called_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            rec = model.complete([{"role": "user", "content": prompt}], max_tokens=16)
            v = _parse_binary(rec.get("text")) if rec["status"] == "ok" else None
            st = rec["status"] if rec["status"] != "ok" or v is not None else "unparsable"
            reason = None if st == "ok" else (rec.get("error") or ("unparsable: " + (rec.get("text") or "")[:40]
                                                                  if st == "unparsable" else st))
            # every judge call records the model id the API RETURNED, the call time and the failure reason
            obs.set_attrs(sp, **{"eval.judge.verdict": v, "eval.judge.status": st,
                                 "eval.judge.returned_model": rec.get("model_version"),
                                 "eval.judge.called_at_utc": called_at, "eval.judge.failure_reason": reason,
                                 "eval.judge.attempts": len(rec.get("attempts") or [])})
            return {"verdict": v, "status": st, "model": rec.get("model_name"), "model_version": rec.get("model_version"),
                    "usage": rec.get("usage"), "cost_usd": rec.get("cost_usd"), "ms": rec.get("ms"),
                    "raw": (rec.get("text") or "")[:40], "error": rec.get("error"), "called_at_utc": called_at,
                    "failure_reason": reason}

    def judge(self, q, answer, evidence_text, with_j2):
        cp = CORRECT_PROMPT.format(question=q["question"], reference=self.reference(q), answer=answer)
        fp = FAITH_PROMPT.format(evidence=evidence_text, answer=answer)
        jobs = {"j1_correct": (self.j1, "judge1", "correctness", cp), "j1_support": (self.j1, "judge1", "support", fp)}
        if with_j2 and self.j2 is not None:
            jobs["j2_correct"] = (self.j2, "judge2", "correctness", cp)
            self.j2_calls += 1
        with ThreadPoolExecutor(len(jobs)) as ex:  # contexts copied so judge spans stay in the item trace
            futs = {k: ex.submit(contextvars.copy_context().run, self._call, *v, q["qid"]) for k, v in jobs.items()}
            return {k: f.result() for k, f in futs.items()}


# ------------------------------------------------------------------ one item
class Recorder:
    def __init__(self, m):
        self.m, self.last = m, None
        self.spec = m.spec

    def complete(self, *a, **k):
        self.last = self.m.complete(*a, **k)
        return self.last


def run_item(store, q, cfg_name, cfg, llm, judges, run_idx, with_j2, trace_file):
    gold = set(q["acceptable"])
    with obs.span("eval.item", kind="INTERNAL", **{"eval.qid": q["qid"], "eval.set": q["set"], "eval.type": q["type"],
                                                   "eval.config": cfg_name, "eval.run": run_idx,
                                                   "eval.answerable": bool(q["answerable"]),
                                                   "eval.gold_chunk_ids": sorted(gold)}) as root:
        tid = obs.current_trace_id(root)
        llm.last = None
        r = ask(store, q["question"], cfg, llm=llm)
        raw = (llm.last or {}).get("text") if (llm.last or {}).get("status") == "ok" else None
        answered = not r["refused"] and r.get("mode") == "llm_answer"
        verdicts, jres = {}, {}
        if q["answerable"] and answered and judges is not None:
            ev_text = "\n\n".join(e["text"] for e in r["evidence"])
            jres = judges.judge(q, r["answer"], ev_text, with_j2)
            verdicts = {"correct": jres["j1_correct"]["verdict"], "support": jres["j1_support"]["verdict"]}
        spans = [s for s in obs.finished_spans(tid) if not s["name"].startswith("eval.judge")]
        att = attribute(spans, gold, bool(q["answerable"]), verdicts)
        c = att["checks"]
        obs.set_attrs(root, **{"eval.success": att["success"], "eval.failure_category": att["category"],
                               "eval.failed_step": att["failed_step"], "eval.failure_reason": att["reason"],
                               "eval.gold_in_pool": c["gold_in_pool"], "eval.gold_in_context": c["gold_in_context"],
                               "eval.gold_rank_hits": c["gold_rank_hits"],
                               "eval.judge.correct": verdicts.get("correct"), "eval.judge.support": verdicts.get("support")})
        if not att["success"]:
            obs.set_error(root, "eval_failure:" + att["category"], att["reason"] or "")
    return {"qid": q["qid"], "set": q["set"], "type": q["type"], "answerable": bool(q["answerable"]),
            "config": cfg_name, "run": run_idx, "trace_id": tid, "trace_file": str(trace_file.relative_to(ROOT)),
            "refused": r["refused"], "refusal_reason": r["refusal_reason"], "mode": r.get("mode"),
            "raw_answer": raw, "llm": {k: (llm.last or {}).get(k) for k in ("status", "model_name", "model_version",
                                                                             "usage", "cost_usd", "ms")} if llm.last else None,
            "judges": jres, "verdicts": verdicts, "success": att["success"], "category": att["category"],
            "failed_step": att["failed_step"], "reason": att["reason"], "checks": c}


# ------------------------------------------------------------------ phases
def run_phase(suite: str, out: Path, no_judge2=False):
    S = SUITES[suite]
    qs = load_questions(S["smoke"], S.get("split", "test"))
    trace_file = out / "traces.jsonl"
    obs.setup(trace_file, service_name="tushen-kb-eval", keep_in_memory=True)
    store = Store(str(ROOT / "data" / "tushen.sqlite"))
    gen, _ = load_models()
    llm = Recorder(ChatModel(gen))
    judges = Judges(store, use_judge2=not no_judge2 and S["judge2_sample"] > 0)
    ans_qids = sorted(q["qid"] for q in qs if q["answerable"])
    j2_sample = set(random.Random(SEEDS["judge2_sample"]).sample(ans_qids, min(S["judge2_sample"], len(ans_qids))))
    stab = sorted(random.Random(SEEDS["stability_subset"]).sample(sorted(q["qid"] for q in qs),
                                                                  min(S["stability_n"], len(qs))))
    if S["smoke"]:
        stab = sorted(q["qid"] for q in qs)[:S["stability_n"]]
    (out / "manifest.json").write_text(json.dumps({
        "suite": suite, "kb_version": __version__, "configs": CONFIGS, "n_questions": len(qs),
        "judge2_sample_qids": sorted(j2_sample), "judge2_cap": S["judge2_cap"], "stability_qids": stab,
        "stability_k": S["stability_k"], "seeds": SEEDS, "generator": gen.model, "judge1": judges.j1_spec.model,
        "judge2": judges.j2_spec.model if judges.j2 else None}, ensure_ascii=False, indent=1), encoding="utf-8")
    # warm-up (not traced, not counted): load embedding/rerank models and jieba
    obs_state = obs._state["tracer"]
    obs._state["tracer"] = None
    for q in qs[:3]:
        for name in CONFIGS:
            ask(store, q["question"], make_cfg(name))
    obs._state["tracer"] = obs_state
    plan = [(name, 1, qs) for name in CONFIGS]
    plan += [("system", k, [q for q in qs if q["qid"] in set(stab)]) for k in range(2, S["stability_k"] + 1)]
    for name, k, Q in plan:
        cfg = make_cfg(name)
        ck = out / f"items_{name}_run{k}.jsonl"
        done = {stdref.loads(l)["qid"] for l in open(ck, encoding="utf-8")} if ck.exists() else set()
        t0 = time.time()
        for i, q in enumerate([q for q in Q if q["qid"] not in done]):
            rec = run_item(store, q, name, cfg, llm, judges, k, False, trace_file)
            with open(ck, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            if (i + 1) % 25 == 0:
                print(f"{name} run{k}: {i + 1 + len(done)}/{len(Q)} {time.time() - t0:.0f}s", flush=True)
        print(f"{name} run{k}: done {len(Q)}", flush=True)
    obs.shutdown()


def judge2_phase(suite: str, out: Path):
    """judge2 (second-family cross judge) on the pre-declared seeded sample of system-run1 items that were
    answered.  Run after the main pass because the free endpoint is slow (queueing); its verdicts are only
    used for judge agreement / sensitivity, never for attribution.  Each call is its own trace (eval.judge2)."""
    S = SUITES[suite]
    man = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    sample = set(man["judge2_sample_qids"])
    items = [r for r in _load(out / "items_system_run1.jsonl")
             if r["qid"] in sample and r["answerable"] and not r["refused"] and r["mode"] == "llm_answer"]
    items = items[:S["judge2_cap"]]
    ck = out / "judge2.jsonl"
    obs.setup(out / "traces.jsonl", service_name="tushen-kb-eval")
    store = Store(str(ROOT / "data" / "tushen.sqlite"))
    J = Judges(store, use_judge2=True)
    assert J.j2 is not None, "judge2 key not available"
    qs = {q["qid"]: q for q in load_questions(S["smoke"], S.get("split", "test"))}
    lock = __import__("threading").Lock()

    def one(r):
        q = qs[r["qid"]]
        cp = CORRECT_PROMPT.format(question=q["question"], reference=J.reference(q), answer=r["raw_answer"])
        with obs.span("eval.judge2", **{"eval.qid": r["qid"], "eval.item_trace_id": r["trace_id"]}) as sp:
            res = J._call(J.j2, "judge2", "correctness", cp, r["qid"])
            tid = obs.current_trace_id(sp)
        rec = {"qid": r["qid"], "item_trace_id": r["trace_id"], "judge2_trace_id": tid, "j2_correct": res}
        with lock, open(ck, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    # sequential (free endpoint is rate-limited upstream); up to 3 passes, a pass retries items whose
    # last record is an error; the last record per qid counts (pre-declared, plan §6)
    for pass_no in range(3):
        last = {}
        if ck.exists():
            for l in open(ck, encoding="utf-8"):
                x = stdref.loads(l)
                last[x["qid"]] = x["j2_correct"]["status"]
        todo = [r for r in items if last.get(r["qid"]) not in ("ok", "unparsable")]
        if not todo:
            break
        if pass_no:
            time.sleep(60)
        for r in todo:
            one(r)
    obs.shutdown()


def overhead_phase(out: Path, reps: int = 2):
    """Tracing overhead on the cheapest path (keyword, no LLM): alternate off/on passes over the test set."""
    store = Store(str(ROOT / "data" / "tushen.sqlite"))
    qs = load_questions()
    cfg = AskConfig()
    tmp = out / "_overhead_traces.jsonl"
    res = {"off": [], "on": []}
    for q in qs[:3]:
        ask(store, q["question"], cfg)
    for rep in range(reps):
        for mode in ("off", "on"):
            if mode == "on":
                obs.setup(tmp)
            t = []
            for q in qs:
                t0 = time.perf_counter()
                ask(store, q["question"], cfg)
                t.append((time.perf_counter() - t0) * 1000)
            obs.shutdown()
            res[mode].append(t)
    n_spans = sum(1 for _ in open(tmp, encoding="utf-8"))
    tmp.unlink()
    off = [statistics.median(x) for x in zip(*res["off"])]
    on = [statistics.median(x) for x in zip(*res["on"])]
    r = {"path": "keyword BM25 + coverage gate, no LLM (cheapest path; overhead is the largest relative share here)",
         "n_questions": len(qs), "reps_per_mode": reps, "spans_written_on_passes": n_spans,
         "median_ms_off": round(statistics.median(off), 3), "median_ms_on": round(statistics.median(on), 3),
         "mean_ms_off": round(statistics.mean(off), 3), "mean_ms_on": round(statistics.mean(on), 3)}
    r["median_delta_ms"] = round(r["median_ms_on"] - r["median_ms_off"], 3)
    r["mean_delta_ms_per_question"] = round(statistics.mean(b - a for a, b in zip(off, on)), 3)
    (out / "overhead.json").write_text(json.dumps(r, ensure_ascii=False, indent=1), encoding="utf-8")
    return r


# ------------------------------------------------------------------ report
def _load(p):
    return [stdref.loads(l) for l in open(p, encoding="utf-8")] if p.exists() else []


def _rate_ci(xs):
    r = rate(sum(xs), len(xs))
    r["ci95"] = bootstrap_mean_ci([float(x) for x in xs], seed=SEEDS["bootstrap"])
    if r["ci95"]:
        r["str"] += f" [95% CI {r['ci95'][0]:.3f}, {r['ci95'][1]:.3f}]"
    return r


def _pct(xs):
    xs = [x for x in xs if x is not None]
    return {"n": len(xs), "p50": obs.percentile(xs, 50), "p95": obs.percentile(xs, 95),
            "mean": round(statistics.mean(xs), 1) if xs else None}


def _open_traces(out):
    p = out / "traces.jsonl"
    if p.exists():
        return obs.load_spans(p)
    with gzip.open(out / "traces.jsonl.gz", "rt", encoding="utf-8") as f:
        return [stdref.loads(l) for l in f if l.strip()]


def config_metrics(R, spans_by_trace):
    ans = [r for r in R if r["answerable"]]
    una = [r for r in R if not r["answerable"]]
    m = {"n": len(R), "n_answerable": len(ans), "n_unanswerable": len(una)}
    m["task_success"] = _rate_ci([r["success"] for r in R])
    m["task_success_answerable"] = _rate_ci([r["success"] for r in ans])
    m["task_success_unanswerable(correct_refusal)"] = _rate_ci([r["success"] for r in una])
    m["task_success_by_set"] = {s: rate(sum(r["success"] for r in R if r["set"] == s), sum(r["set"] == s for r in R))
                                for s in sorted({r["set"] for r in R})}
    answered = [r for r in ans if r["mode"] == "llm_answer" and not r["refused"]]
    m["answered_rate_answerable"] = rate(len(answered), len(ans))
    m["false_refusal_answerable"] = rate(sum(r["refused"] for r in ans), len(ans))
    m["answer_correct_among_answered(judge1)"] = rate(sum(r["verdicts"].get("correct") is True for r in answered),
                                                      len(answered))
    m["answer_supported_among_answered(judge1)"] = rate(sum(r["verdicts"].get("support") is True for r in answered),
                                                        len(answered))
    # step (tool-call) success
    reach = lambda n: [r for r in ans if n in r["checks"]["reached"]]
    st = {}
    st["existence_check_pass(answerable)"] = rate(sum("kb.retrieve" in r["checks"]["reached"] for r in ans), len(ans))
    rr = reach("kb.retrieve")
    st["retrieve_gold_in_pool"] = rate(sum(r["checks"]["gold_in_pool"] for r in rr), len(rr))
    st["context_gold_in_top3"] = rate(sum(r["checks"]["gold_in_context"] for r in rr), len(rr))
    gate_name = "kb.rerank_gate" if any("kb.rerank_gate" in r["checks"]["reached"] for r in R) else "kb.evidence_gate"
    g = [r for r in ans if gate_name in r["checks"]["reached"]]
    gr = lambda r: r["checks"]["refusal_step"] == gate_name[3:]
    st[f"gate_pass_answerable({gate_name})"] = rate(sum(not gr(r) for r in g), len(g))
    g_ctx = [r for r in g if r["checks"]["gold_in_context"]]
    st["gate_pass_answerable_gold_in_context"] = rate(sum(not gr(r) for r in g_ctx), len(g_ctx))
    st["refused_before_llm(unanswerable)"] = rate(sum(r["refused"] and r["checks"]["refusal_step"] not in
                                                      ("generate", "verify_quotes") for r in una), len(una))
    c = reach("kb.cite")
    st["cite_verified_any(answerable reaching cite)"] = rate(sum((r["checks"]["n_cit_verified_any"] or 0) > 0 for r in c), len(c))
    nonref = [r for r in ans if not r["refused"]]
    st["citation_precision(non-refused answerable)"] = rate(sum(r["checks"]["n_cit_gold"] for r in nonref),
                                                            sum(r["checks"]["n_cit"] for r in nonref))
    gen = [r for r in R if "kb.generate" in r["checks"]["reached"]]
    st["llm_call_ok(generate reached)"] = rate(sum(r["checks"]["llm_status"] == "ok" for r in gen), len(gen))
    vq = [r for r in R if "kb.verify_quotes" in r["checks"]["reached"]]
    st["quote_verify_pass(answers reaching verify)"] = rate(sum((r["checks"]["n_quotes"] or 0) > 0 and
                                                                not r["checks"]["n_bad_quotes"] for r in vq), len(vq))
    vqa = [r for r in vq if r["answerable"] and (r["checks"]["n_quotes"] or 0) > 0]
    st["quote_from_gold(answerable answers with quotes)"] = rate(sum(bool(r["checks"]["quote_from_gold"]) for r in vqa), len(vqa))
    # technical tool-call success: step spans that ended without ERROR status
    tot = ok = 0
    per = Counter(), Counter()
    for r in R:
        for s in spans_by_trace.get(r["trace_id"], []):
            if s["name"] in STEP_SPANS or s["name"].startswith("kb.search."):
                tot += 1
                per[0][s["name"]] += 1
                if s["status"]["code"] != "ERROR":
                    ok += 1
                    per[1][s["name"]] += 1
    st["step_spans_without_error(all steps)"] = rate(ok, tot)
    m["step_success"] = st
    m["failure_table"] = {k: sum(r["category"] == k for r in R) for k in TAXONOMY}
    m["failure_table_by_step"] = dict(Counter(f"{r['category']} @ {r['failed_step']}" for r in R if not r["success"]))
    # tokens / cost / latency (generator only = the system under test)
    calls = [r["llm"] for r in R if r["llm"]]
    tin = sum(((c.get("usage") or {}).get("prompt_tokens") or 0) for c in calls)
    tout = sum(((c.get("usage") or {}).get("completion_tokens") or 0) for c in calls)
    cost = sum((c.get("cost_usd") or 0) for c in calls)
    m["generator"] = {"calls": len(calls), "calls_ok": sum(c["status"] == "ok" for c in calls),
                      "prompt_tokens": tin, "completion_tokens": tout,
                      "tokens_per_question": round((tin + tout) / len(R), 1) if R else None,
                      "cost_usd_total": round(cost, 6), "cost_usd_per_question": round(cost / len(R), 8) if R else None,
                      "cost_usd_per_1k_questions": round(cost / len(R) * 1000, 4) if R else None,
                      "models": sorted({c.get("model_version") or c.get("model_name") or "?" for c in calls})}
    m["latency_ms"] = {}
    per_span = defaultdict(list)
    for r in R:
        T = spans_by_trace.get(r["trace_id"], [])
        gen_ids = {s["span_id"] for s in T if s["name"] == "kb.generate"}
        for s in T:
            if s["name"].startswith("kb.") or (s["name"].startswith("chat ") and s["parent_span_id"] in gen_ids):
                per_span[s["name"] if s["name"].startswith("kb.") else "chat (generator)"].append(s["duration_ms"])
    for n in sorted(per_span):
        m["latency_ms"][n] = _pct(per_span[n])
    return m


def judge_stats(R):
    rows = [r for r in R if r["judges"]]
    j = defaultdict(lambda: {"calls": 0, "ok": 0, "prompt_tokens": 0, "completion_tokens": 0, "cost_usd": 0.0,
                             "models": set()})
    for r in rows:
        for k, v in r["judges"].items():
            role = k.split("_")[0]
            j[role]["calls"] += 1
            j[role]["ok"] += v["status"] == "ok"
            j[role]["prompt_tokens"] += (v.get("usage") or {}).get("prompt_tokens") or 0
            j[role]["completion_tokens"] += (v.get("usage") or {}).get("completion_tokens") or 0
            j[role]["cost_usd"] += v.get("cost_usd") or 0
            j[role]["models"].add(v.get("model_version") or v.get("model") or "?")
    for v in j.values():
        v["models"] = sorted(v["models"])
        v["cost_usd"] = round(v["cost_usd"], 6)
    pairs = [(r["judges"]["j1_correct"]["verdict"], r["judges"]["j2_correct"]["verdict"], r["qid"])
             for r in rows if "j2_correct" in r["judges"]]
    both = [(a, b, q) for a, b, q in pairs if a is not None and b is not None]
    agree = {"j2_attempted": len(pairs), "both_valid": len(both),
             "raw_agreement": rate(sum(a == b for a, b, _ in both), len(both)),
             "cohen_kappa": cohen_kappa([a for a, _, _ in both], [b for _, b, _ in both]) if both else None,
             "j1_yes": sum(a for a, _, _ in both), "j2_yes": sum(b for _, b, _ in both),
             "disagreements": [{"qid": q, "judge1": a, "judge2": b} for a, b, q in both if a != b],
             "and_rule_correct": rate(sum(a and b for a, b, _ in both), len(both)),
             "judge1_only_correct": rate(sum(a for a, _, _ in both), len(both))}
    return dict(j), agree


def judge_ledger(spans):
    """One row per judge call, read from the trace file: role, qid, requested model, the model id the API
    returned (``gen_ai.response.model`` on the child chat span, or ``eval.judge.returned_model``), call time
    (span start, UTC) and failure reason.  Calls are then cut into *version batches*: per role, in time order,
    a new batch starts whenever the returned model id changes.  Batches are never pooled (plan §10 A2)."""
    by_parent = defaultdict(list)
    for s in spans:
        by_parent[s["parent_span_id"]].append(s)
    rows = []
    for s in spans:
        if not s["name"].startswith("eval.judge."):
            continue
        a = s["attributes"]
        chat = next((c for c in by_parent.get(s["span_id"], []) if c["name"].startswith("chat ")), None)
        ca = chat["attributes"] if chat else {}
        st = a.get("eval.judge.status")
        reason = a.get("eval.judge.failure_reason")
        if reason is None and st != "ok":
            reason = (chat and chat["status"].get("description")) or (
                "unparsable (verdict not 是/否)" if st == "unparsable" else st)
        t = s["start_time_unix_nano"] / 1e9
        rows.append({"role": a.get("eval.judge.role"), "task": s["name"].split(".")[-1], "qid": a.get("eval.qid"),
                     "trace_id": s["trace_id"], "span_id": s["span_id"], "request_model": a.get("eval.judge.model"),
                     "returned_model": a.get("eval.judge.returned_model") or ca.get("gen_ai.response.model"),
                     "called_at_utc": a.get("eval.judge.called_at_utc") or
                     time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t)), "_t": t,
                     "status": st, "verdict": a.get("eval.judge.verdict"), "failure_reason": reason,
                     "attempts": ca.get("tushen.attempts")})
    rows.sort(key=lambda r: (r["role"] or "", r["_t"]))
    batches, cur = [], {}
    for r in rows:
        if r["returned_model"] is None:  # failed call: no returned id, belongs to the running batch of its role
            r["batch"] = cur[r["role"]]["batch_id"] if r["role"] in cur else None
            if r["role"] in cur:
                cur[r["role"]]["n_failed"] += 1
            continue
        b = cur.get(r["role"])
        if b is None or b["returned_model"] != r["returned_model"]:
            b = {"batch_id": f"{r['role']}#{sum(x['role'] == r['role'] for x in batches) + 1}", "role": r["role"],
                 "returned_model": r["returned_model"], "first_utc": r["called_at_utc"], "n_calls": 0, "n_failed": 0}
            batches.append(b)
            cur[r["role"]] = b
        b["n_calls"] += 1
        b["last_utc"] = r["called_at_utc"]
        r["batch"] = b["batch_id"]
    for r in rows:
        r.pop("_t")
    roles = Counter(b["role"] for b in batches)
    return rows, {"batches": batches, "version_changed_roles": sorted(k for k, v in roles.items() if v > 1)}


def per_batch_judge_metrics(rows, sysR):
    """Only computed when some judge's returned model id changed mid-run: answer correctness and judge1/judge2
    agreement inside each batch (or batch pair) separately, never pooled."""
    item_trace = {r["trace_id"]: r for r in sysR}
    j1b = {(x["trace_id"]): x["batch"] for x in rows if x["role"] == "judge1" and x["task"] == "correctness"}
    out = {}
    for b in sorted({v for v in j1b.values() if v}):
        R = [item_trace[t] for t, bb in j1b.items() if bb == b and t in item_trace]
        out[b] = {"answer_correct_among_judged(system run1)": rate(sum(r["verdicts"].get("correct") is True for r in R), len(R))}
    return out


def stability(runs, k):
    by = defaultdict(dict)
    for i, R in enumerate(runs, 1):
        for r in R:
            by[r["qid"]][i] = r
    full = {q: v for q, v in by.items() if len(v) == k}
    norm = lambda s: re.sub(r"\s+", "", s or "")
    per_q = []
    for q, v in sorted(full.items()):
        rs = [v[i] for i in range(1, k + 1)]
        per_q.append({"qid": q, "set": rs[0]["set"], "success": [r["success"] for r in rs],
                      "refused": [r["refused"] for r in rs], "category": [r["category"] for r in rs],
                      "trace_ids": [r["trace_id"] for r in rs],
                      "decision_consistent": len({r["refused"] for r in rs}) == 1,
                      "success_consistent": len({r["success"] for r in rs}) == 1,
                      "answers_identical": len({norm(r["raw_answer"]) for r in rs}) == 1})
    n = len(per_q)
    ans_all = [p for p in per_q if not any(p["refused"])]
    return {"n_questions": n, "k": k,
            "pass^k(all k runs succeed)": rate(sum(all(p["success"]) for p in per_q), n),
            "pass@k(any run succeeds)": rate(sum(any(p["success"]) for p in per_q), n),
            "mean_single_run_success": rate(sum(sum(p["success"]) for p in per_q), n * k),
            "success_consistency(same outcome in all k)": rate(sum(p["success_consistent"] for p in per_q), n),
            "decision_consistency(refuse/answer same in all k)": rate(sum(p["decision_consistent"] for p in per_q), n),
            "answer_agreement(identical text, answered in all k)": rate(sum(p["answers_identical"] for p in ans_all), len(ans_all)),
            "per_question": per_q}


def prev_version_agreement(R):
    if not PREV_VERSION_RUN.exists():
        return None
    prev = {stdref.loads(l)["qid"]: stdref.loads(l) for l in open(PREV_VERSION_RUN, encoding="utf-8")}
    both = [(r, prev[r["qid"]]) for r in R if r["qid"] in prev]
    ans = [(a, b) for a, b in both if a["answerable"]]
    return {"source": "reports/e2e_s1_deepseek_per_question.jsonl (commit 7fdc378, same config, DeepSeek)",
            "n_overlap": len(both),
            "refusal_decision_agreement": rate(sum(a["refused"] == b["refused"] for a, b in both), len(both)),
            "false_refusal_now": rate(sum(a["refused"] for a, _ in ans), len(ans)),
            "false_refusal_prev": rate(sum(b["refused"] for _, b in ans), len(ans)),
            "mcnemar_refusal(prev->now)": mcnemar_exact([b["refused"] for _, b in ans], [a["refused"] for a, _ in ans])}


def report_phase(suite: str, out: Path):
    S = SUITES[suite]
    man = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    spans = _open_traces(out)
    by_trace = defaultdict(list)
    for s in spans:
        by_trace[s["trace_id"]].append(s)
    runs = {(name, k): _load(out / f"items_{name}_run{k}.jsonl") for name in CONFIGS for k in range(1, S["stability_k"] + 1)}
    # re-derive attribution from the trace FILE and check it matches the inline decision
    mismatch = []
    for R in runs.values():
        for r in R:
            sp = [s for s in by_trace.get(r["trace_id"], []) if s["name"].startswith(("kb.", "chat ")) and
                  not any(p["name"].startswith("eval.judge") for p in by_trace[r["trace_id"]]
                          if p["span_id"] == s["parent_span_id"])]
            gold = set(next(s for s in by_trace[r["trace_id"]] if s["name"] == "eval.item")["attributes"]
                       .get("eval.gold_chunk_ids", []))
            a = attribute(sp, gold, r["answerable"], r["verdicts"])
            if (a["category"], a["failed_step"]) != (r["category"], r["failed_step"]):
                mismatch.append(r["qid"])
    res = {"suite": suite, "manifest": man, "generated_local": time.strftime("%Y-%m-%d %H:%M %Z"),
           "attribution_rederived_from_trace_file": {"items": sum(len(R) for R in runs.values()),
                                                      "mismatches": mismatch}, "configs": {}}
    for name in CONFIGS:
        R = runs[(name, 1)]
        if R:
            res["configs"][name] = config_metrics(R, by_trace)
    sysR, baseR = runs[("system", 1)], runs[("baseline_bm25", 1)]
    if sysR and baseR:
        bm = {r["qid"]: r for r in baseR}
        pair = [(bm[r["qid"]]["success"], r["success"]) for r in sysR if r["qid"] in bm]
        res["system_vs_baseline"] = {"n_paired": len(pair),
                                     "task_success_diff(system-baseline)": bootstrap_diff_ci(
                                         [float(a) for a, _ in pair], [float(b) for _, b in pair]),
                                     "mcnemar": mcnemar_exact([a for a, _ in pair], [b for _, b in pair])}
    j2 = {r["qid"]: r["j2_correct"] for r in _load(out / "judge2.jsonl")}  # last record per qid wins
    jr = [dict(r, judges=dict(r["judges"], **({"j2_correct": j2[r["qid"]]} if r["qid"] in j2 and r["run"] == 1
                                              and r["config"] == "system" else {})))
          for R in runs.values() for r in R]
    res["judges"], res["judge_agreement"] = judge_stats(jr)
    res["judge_agreement"]["scope"] = "system run1, judge2 seeded sample (answered answerable items only)"
    j2_all = _load(out / "judge2.jsonl")
    res["judge_agreement"]["j2_http_calls_incl_retry_passes"] = len(j2_all)
    res["judge_agreement"]["j2_status_counts_all_calls"] = dict(Counter(x["j2_correct"]["status"] for x in j2_all))
    # judge-call ledger with returned model ids / timestamps / failure reasons; version batches (A2)
    ledger, vb = judge_ledger(spans)
    j2_by_qid = {x["qid"]: x for x in j2_all}
    for x in ledger:  # judge2 calls live in their own traces; link them to the item via judge2.jsonl
        if x["role"] == "judge2" and x["qid"] in j2_by_qid:
            x["item_trace_id"] = j2_by_qid[x["qid"]]["item_trace_id"]
    with open(out / "judge_calls.jsonl", "w", encoding="utf-8") as f:
        for x in ledger:
            f.write(json.dumps(x, ensure_ascii=False) + "\n")
    vb["n_calls_in_trace"] = len(ledger)
    vb["n_calls_without_returned_model"] = sum(x["returned_model"] is None for x in ledger)
    vb["failure_reasons"] = dict(Counter(x["failure_reason"] for x in ledger if x["failure_reason"]))
    vb["ledger_path"] = str((out / "judge_calls.jsonl").relative_to(ROOT))
    if vb["version_changed_roles"]:
        vb["per_batch"] = per_batch_judge_metrics(ledger, sysR)
        vb["note"] = "returned model id changed mid-run: batches reported separately, pooled judge metrics invalid"
    res["judge_version_batches"] = vb
    stab_runs = [[r for r in runs[("system", k)] if r["qid"] in set(man["stability_qids"])] for k in range(1, S["stability_k"] + 1)]
    res["stability"] = stability(stab_runs, S["stability_k"]) if all(stab_runs) else None
    res["prev_version"] = prev_version_agreement(sysR) if not S["smoke"] else None
    res["split"] = S.get("split", "test")
    if (out / "overhead.json").exists():
        res["tracing_overhead"] = json.loads((out / "overhead.json").read_text(encoding="utf-8"))
    # badcases: every failed item of run1 linked to its trace
    bad = [{"config": r["config"], "qid": r["qid"], "set": r["set"], "category": r["category"],
            "failed_step": r["failed_step"], "reason": r["reason"], "trace_id": r["trace_id"],
            "trace_file": r["trace_file"]} for name in CONFIGS for r in runs[(name, 1)] if not r["success"]]
    with open(out / "badcases.jsonl", "w", encoding="utf-8") as f:
        for b in bad:
            f.write(json.dumps(b, ensure_ascii=False) + "\n")
    # one complete example trace: a system-run failure with the full step tree, judges included
    ex = next((r for r in sysR if r["category"] == "生成幻觉"), None) or next((r for r in sysR if not r["success"]
                                                                                and r["judges"]), None) or (sysR or [None])[0]
    if ex:
        tree = obs.trace_tree(by_trace[ex["trace_id"]], ex["trace_id"])
        (out / "trace_example.json").write_text(json.dumps(tree, ensure_ascii=False, indent=1), encoding="utf-8")
        res["trace_example"] = {"qid": ex["qid"], "trace_id": ex["trace_id"], "category": ex["category"],
                                "path": str((out / "trace_example.json").relative_to(ROOT))}
    (out / "report.json").write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    (out / "report.md").write_text(render_md(res, bad), encoding="utf-8")
    return res


def _fmt(x):
    if isinstance(x, dict) and "str" in x:
        return x["str"]
    return json.dumps(x, ensure_ascii=False) if isinstance(x, (dict, list)) else str(x)


def render_md(res, bad):
    C = res["configs"]
    names = list(C)
    L = [f"# Agent 端到端评测报告（suite={res['suite']}）", "",
         f"生成时间 {res['generated_local']}；预注册方案 reports/plan_agent_eval_zh.md；数字全部来自本次运行的 "
         f"`items_*.jsonl` 与 `traces.jsonl(.gz)`。被测系统 = `system`，预声明基线 = `baseline_bm25`。", "",
         "## 1. 任务成功率（分母见每格）", "", "| 指标 | " + " | ".join(names) + " |", "|---|" + "---|" * len(names)]
    for k in ["task_success", "task_success_answerable", "task_success_unanswerable(correct_refusal)",
              "answered_rate_answerable", "false_refusal_answerable", "answer_correct_among_answered(judge1)",
              "answer_supported_among_answered(judge1)"]:
        L.append(f"| {k} | " + " | ".join(_fmt(C[n].get(k)) for n in names) + " |")
    for s in sorted({s for n in names for s in C[n]["task_success_by_set"]}):
        L.append(f"| success[{s}] | " + " | ".join(_fmt(C[n]["task_success_by_set"].get(s, "-")) for n in names) + " |")
    if res.get("system_vs_baseline"):
        d = res["system_vs_baseline"]
        L += ["", f"配对比较（system − baseline，n={d['n_paired']}）：差值 {d['task_success_diff(system-baseline)']['diff']:.3f}，"
                  f"bootstrap 95% CI {[round(x, 3) for x in d['task_success_diff(system-baseline)']['ci']]}；"
                  f"McNemar b01={d['mcnemar']['b01']} b10={d['mcnemar']['b10']} p={d['mcnemar']['p_value']:.2g}"]
    L += ["", "## 2. 步骤（工具调用）成功率", "", "| 步骤指标 | " + " | ".join(names) + " |", "|---|" + "---|" * len(names)]
    keys = []
    for n in names:
        keys += [k for k in C[n]["step_success"] if k not in keys]
    for k in keys:
        L.append(f"| {k} | " + " | ".join(_fmt(C[n]["step_success"].get(k, "-")) for n in names) + " |")
    L += ["", "## 3. 失败归因（代码规则，从 trace 判定）", "", "| 类别 | " + " | ".join(names) + " |", "|---|" + "---|" * len(names)]
    for k in TAXONOMY:
        L.append(f"| {k} | " + " | ".join(str(C[n]["failure_table"][k]) for n in names) + " |")
    L.append("| 失败合计 / 总数 | " + " | ".join(f"{sum(C[n]['failure_table'].values())}/{C[n]['n']}" for n in names) + " |")
    for n in names:
        L += ["", f"`{n}` 失败按（类别 @ 定位步骤）：", ""]
        for k, v in sorted(C[n]["failure_table_by_step"].items(), key=lambda x: -x[1]):
            L.append(f"- {k}: {v}")
    a = res["attribution_rederived_from_trace_file"]
    L += ["", f"归因复算：从 trace 文件重新运行 `attribute()`，{a['items']} 条中不一致 {len(a['mismatches'])} 条。"]
    L += ["", "## 4. 稳定性", ""]
    st = res.get("stability")
    if st:
        for k, v in st.items():
            if k != "per_question":
                L.append(f"- {k}: {_fmt(v)}")
    pv = res.get("prev_version")
    if pv:
        L += [f"- 与上一版本已提交运行（{pv['source']}）的拒答决策一致率: {pv['refusal_decision_agreement']['str']}；"
              f"可答题误拒 现 {pv['false_refusal_now']['str']} / 前 {pv['false_refusal_prev']['str']}；"
              f"McNemar p={pv['mcnemar_refusal(prev->now)']['p_value']:.2g}"]
    L += ["", "## 5. 裁判", ""]
    for role, v in res["judges"].items():
        L.append(f"- {role}: {v['models']}，调用 {v['calls']}（ok {v['ok']}），tokens {v['prompt_tokens']}+{v['completion_tokens']}，"
                 f"标价折算 ${v['cost_usd']}")
    ja = res["judge_agreement"]
    L.append(f"- 一致性（{ja['scope']}）：尝试 {ja['j2_attempted']}，双方有效 {ja['both_valid']}，原始一致率 "
             f"{ja['raw_agreement']['str']}，Cohen's κ = {ja['cohen_kappa'] if ja['cohen_kappa'] is None else round(ja['cohen_kappa'], 3)}；"
             f"AND 规则正确率 {ja['and_rule_correct']['str']} vs judge1 单独 {ja['judge1_only_correct']['str']}")
    vb = res.get("judge_version_batches")
    if vb:
        L += ["", f"裁判调用台账（`{vb['ledger_path']}`，每次调用记录返回的模型 id、UTC 时间戳、失败原因）："
                  f"trace 中共 {vb['n_calls_in_trace']} 次调用，无返回模型 id（失败）{vb['n_calls_without_returned_model']} 次。", "",
              "| batch | 角色 | 返回模型 id | 调用数 | 失败数 | 首次 (UTC) | 末次 (UTC) |", "|---|---|---|---|---|---|---|"]
        for b in vb["batches"]:
            L.append(f"| {b['batch_id']} | {b['role']} | `{b['returned_model']}` | {b['n_calls']} | {b['n_failed']} | "
                     f"{b['first_utc']} | {b.get('last_utc')} |")
        L.append("")
        L.append("返回模型 id 运行中途变化的角色：" + (", ".join(vb["version_changed_roles"]) +
                 "（已分批单列，不合并）" if vb["version_changed_roles"] else "无（每个裁判在整个运行中只返回一个模型 id）"))
        if vb["failure_reasons"]:
            L.append(f"失败原因计数：{json.dumps(vb['failure_reasons'], ensure_ascii=False)}")
        for b, m in (vb.get("per_batch") or {}).items():
            L.append(f"- {b}: {_fmt(m)}")
    L += ["", "## 6. Token / 成本 / 延迟（生成模型，来自 span）", ""]
    for n in names:
        g = C[n]["generator"]
        L.append(f"- `{n}`: 调用 {g['calls']}（ok {g['calls_ok']}），prompt {g['prompt_tokens']} + completion "
                 f"{g['completion_tokens']} tokens，每题 {g['tokens_per_question']} tokens，标价成本 ${g['cost_usd_total']}"
                 f"（每题 ${g['cost_usd_per_question']}，每千题 ${g['cost_usd_per_1k_questions']}），模型 {g['models']}")
    L += ["", "| span | " + " | ".join(f"{n} p50 / p95 (n)" for n in names) + " |", "|---|" + "---|" * len(names)]
    spn = []
    for n in names:
        spn += [k for k in C[n]["latency_ms"] if k not in spn]
    for k in spn:
        L.append(f"| {k} | " + " | ".join(
            (lambda p: f"{p['p50']:.0f} / {p['p95']:.0f} ({p['n']})" if p and p["n"] else "-")(C[n]["latency_ms"].get(k))
            for n in names) + " |")
    if res.get("tracing_overhead"):
        o = res["tracing_overhead"]
        L += ["", f"Tracing 开销（{o['path']}，{o['n_questions']} 题 × {o['reps_per_mode']} 轮）：中位数 off {o['median_ms_off']} ms "
                  f"/ on {o['median_ms_on']} ms，每题平均增量 {o['mean_delta_ms_per_question']} ms。"]
    if res.get("trace_example"):
        t = res["trace_example"]
        L += ["", f"完整 trace 示例：`{t['path']}`（qid {t['qid']}，trace_id {t['trace_id']}，类别 {t['category']}）"]
    L += ["", "## 7. Badcase 列表（每条链接 trace_id，trace 文件见 trace_file 列）", "",
          "| config | qid | set | 类别 | 定位步骤 | 原因 | trace_id |", "|---|---|---|---|---|---|---|"]
    for b in bad:
        L.append(f"| {b['config']} | {b['qid']} | {b['set']} | {b['category']} | {b['failed_step']} | "
                 f"{(b['reason'] or '').replace('|', '/')[:60]} | `{b['trace_id']}` |")
    L.append("")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--suite", choices=list(SUITES), default="agent")
    ap.add_argument("--phase", choices=["all", "run", "judge2", "overhead", "report"], default="all")
    ap.add_argument("--no-judge2", action="store_true")
    a = ap.parse_args()
    out = REPORTS / SUITES[a.suite]["out"]
    out.mkdir(parents=True, exist_ok=True)
    if a.phase in ("all", "run"):
        run_phase(a.suite, out, a.no_judge2)
    if a.phase in ("all", "judge2") and SUITES[a.suite]["judge2_sample"] and not a.no_judge2:
        judge2_phase(a.suite, out)
    if a.phase in ("all", "overhead") and SUITES[a.suite]["overhead"]:
        overhead_phase(out)
    if a.phase in ("all", "report"):
        res = report_phase(a.suite, out)
        for n, m in res["configs"].items():
            print(n, "task_success", m["task_success"]["str"], flush=True)
        print("report:", out / "report.md")


if __name__ == "__main__":
    main()
