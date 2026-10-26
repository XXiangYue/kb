"""/ask pipeline: retrieve -> evidence gate -> citations (verbatim-verified) ->
[optional LLM] -> answer or refusal.

The pipeline is a list of explicit stages so later blocks can plug in:
  * ``context_hooks``: callables (query, state) -> list[ContextBlock].  This is
    where future question-type Skills (SKILL.md, progressive disclosure) inject
    method text; every injected block is logged with its token count.
  * ``llm``: any object implementing tushen_kb.llm.LLM; None = evidence only.
Every call returns a ``trace`` with stage timings, config and context-token log.
"""
from __future__ import annotations

import math
import re
import time
from dataclasses import asdict, dataclass, field
from typing import Callable

from .cite import format_ref, locate, pick_quote, verify_citation, verify_quote
from .llm import count_tokens
from .search import STD_IN_QUERY, SearchConfig, resolve_docs, search
from .store import Store
from .tokenize import content_tokens

GATES = ("coverage", "rerank_top")
EXPLICIT_CLAUSE = re.compile(r"第\s*((?:[A-Z]|\d{1,2})(?:\.\d{1,3}){1,4})\s*条|((?:[A-Z]|\d{1,2})(?:\.\d{1,3}){1,4})\s*条")


@dataclass
class AskConfig:
    search: SearchConfig = field(default_factory=SearchConfig)
    n_evidence: int = 3
    # default restored to 0.5.  The dev-only calibration (rule 807a93d, result
    # reports/threshold_calibration_dev.json, frozen in 72a8b5a) picked 0.05, but on test that
    # gives U2 correct refusal 4/31, so 0.05 is NOT a safe default.  At 0.5 the low-overlap
    # test set is falsely refused 118/118.  Refusal is an unsolved limitation, not tuned here.
    min_coverage: float = 0.5
    # chunk kinds allowed as CITED evidence.  条文说明 (commentary) is explanatory, not the
    # normative text, so by default it may appear in evidence but is never cited.
    cite_kinds: tuple = ("clause", "table", "text")
    # compute evidence coverage on the query with document references removed (《title》, corpus titles,
    # standard codes, 第x条): otherwise neighbours match only the title words and get cited.
    coverage_strip_doc_refs: bool = True
    # refusal gate.  "coverage" (default, unchanged behaviour): refuse if top coverage < min_coverage.
    # "rerank_top" (Block 3 S1, frozen t=0.94 in 081a449): coverage gate AND per-citation coverage
    # filter are off (min_coverage treated as 0); refuse if sigmoid(top reranker logit) < gate_threshold.
    # Needs a rerank search mode.  Not the default.
    gate: str = "coverage"
    gate_threshold: float = 0.0


@dataclass
class ContextBlock:
    source: str        # e.g. "skill:clause_lookup" / "system_prompt"
    text: str
    loaded_reason: str = ""


ContextHook = Callable[[str, dict], list[ContextBlock]]


def strip_doc_refs(query: str, titles: list[str]) -> str:
    q = re.sub(r"《[^》]*》", " ", query)
    for t in sorted((t for t in titles if t), key=len, reverse=True):
        q = q.replace(t, " ")
    q = STD_IN_QUERY.sub(" ", q)
    return EXPLICIT_CLAUSE.sub(" ", q)


def _coverage(query: str, text: str) -> float:
    qt = set(content_tokens(query))
    if not qt:
        return 0.0
    flat = text.replace("\n", "").lower()
    return sum(1 for t in qt if t in flat) / len(qt)


def ask(store: Store, query: str, cfg: AskConfig | None = None, llm=None,
        context_hooks: list[ContextHook] | None = None) -> dict:
    cfg = cfg or AskConfig()
    if cfg.gate not in GATES:
        raise ValueError(f"unknown gate {cfg.gate}")
    if cfg.gate == "rerank_top" and cfg.search.mode not in ("hybrid_rerank", "bm25_rerank"):
        raise ValueError("gate rerank_top needs a rerank search mode")
    min_cov = cfg.min_coverage if cfg.gate == "coverage" else 0.0
    t0 = time.perf_counter()
    trace: dict = {"config": {"search": asdict(cfg.search), "n_evidence": cfg.n_evidence,
                              "min_coverage": cfg.min_coverage, "cite_kinds": list(cfg.cite_kinds),
                              "coverage_strip_doc_refs": cfg.coverage_strip_doc_refs,
                              "gate": cfg.gate, "gate_threshold": cfg.gate_threshold},
                   "stages": [], "context_log": [], "token_counter": "cjk_char+ascii_word estimate"}

    def stage(name, **kw):
        trace["stages"].append({"stage": name, "ms": round((time.perf_counter() - t0) * 1000, 1), **kw})

    def refuse(reason: str, evidence=None):
        stage("refuse", reason=reason)
        return {"query": query, "refused": True, "refusal_reason": reason, "answer": None,
                "evidence": evidence or [], "citations": [], "trace": trace}

    # 1. existence checks: standard code / clause number that the corpus does not contain
    matched_docs, missing_codes = resolve_docs(store, query)
    if missing_codes:
        return refuse(f"语料库中不存在标准 {', '.join(missing_codes)}")
    for m in EXPLICIT_CLAUSE.finditer(query):
        no = m.group(1) or m.group(2)
        exists = any(store.clause_exists(no, d) for d in matched_docs) if matched_docs else store.clause_exists(no)
        if not exists:
            return refuse(f"语料库中不存在第 {no} 条" + (f"（限定 {', '.join(matched_docs)}）" if matched_docs else ""))
    stage("existence_check", matched_docs=matched_docs)

    # 2. retrieve
    hits = search(store, query, cfg.search)
    stage("retrieve", n_hits=len(hits))
    if not hits:
        return refuse("未检索到相关条文")

    # 3. evidence gate
    docs = {d["doc_id"]: d for d in store.docs()}
    evidence = []
    cov_query = strip_doc_refs(query, [d["title"] for d in docs.values()]) if cfg.coverage_strip_doc_refs else query
    trace["coverage_query"] = cov_query
    for h in hits[:cfg.n_evidence]:
        cov = _coverage(cov_query, h["text"]) if h.get("route") != "clause_route" else 1.0
        evidence.append({"chunk_id": h["chunk_id"], "doc_id": h["doc_id"], "kind": h["kind"],
                         "clause_no": h["clause_no"], "page_start": h["page_start"], "page_end": h["page_end"],
                         "char_start": h["char_start"], "char_end": h["char_end"], "score": h["score"],
                         "route": h.get("route"), "coverage": round(cov, 3), "is_mandatory": bool(h["is_mandatory"]),
                         "text": h["text"]})
    top_cov = max(e["coverage"] for e in evidence)
    stage("evidence_gate", top_coverage=top_cov)
    if top_cov < min_cov:
        return refuse(f"证据不足（最高覆盖率 {top_cov:.2f} < 阈值 {cfg.min_coverage}）", evidence)
    if cfg.gate == "rerank_top":
        logits = [e["score"] for e in evidence if e.get("route") == "rerank"]
        s1 = 1 / (1 + math.exp(-max(logits))) if logits else 0.0
        stage("rerank_gate", s1=round(s1, 4), threshold=cfg.gate_threshold)
        if s1 < cfg.gate_threshold:
            return refuse(f"证据不足（rerank 最高分 {s1:.3f} < 阈值 {cfg.gate_threshold}）", evidence)

    # 4. citations, each verified verbatim against stored text
    citations = []
    texts = {}
    for e, h in zip(evidence, hits):
        if e["coverage"] < min_cov or h["kind"] not in cfg.cite_kinds:
            continue
        if h["doc_id"] not in texts:
            texts[h["doc_id"]] = store.doc_text(h["doc_id"])
        quote = pick_quote(query, h["text"])
        cit = {"chunk_id": h["chunk_id"], "quote": quote, "ref": format_ref(docs[h["doc_id"]], h),
               "doc_id": h["doc_id"], "clause_no": h["clause_no"], "page_start": h["page_start"]}
        cit["char_start"], cit["char_end"] = locate(cit, h)
        cit["verified"] = verify_citation(cit, h, texts[h["doc_id"]])
        if cit["verified"]:
            citations.append(cit)
    stage("cite", n_verified=len(citations))
    if not citations:
        return refuse("没有通过原文校验的引用", evidence)

    # 5. context hooks (future: Skills) + optional LLM
    state = {"evidence": evidence, "citations": citations}
    for hook in context_hooks or []:
        for blk in hook(query, state):
            trace["context_log"].append({"source": blk.source, "reason": blk.loaded_reason,
                                         "tokens": count_tokens(blk.text)})
            state.setdefault("context", []).append(blk)
    ev_tokens = sum(count_tokens(e["text"]) for e in evidence)
    trace["context_log"].append({"source": "evidence", "tokens": ev_tokens})
    answer, llm_call = None, None
    if llm is not None:
        prompt = build_prompt(query, evidence, state.get("context", []))
        trace["context_log"].append({"source": "prompt_total", "tokens": count_tokens(prompt)})
        llm_call = llm.complete([{"role": "user", "content": prompt}], max_tokens=400)
        trace["llm_call"] = {k: llm_call.get(k) for k in ("role", "provider", "family", "model_name",
                                                          "model_version", "status", "usage", "ms", "error")}
        if llm_call["status"] != "ok":
            stage("llm", status=llm_call["status"])  # skipped/error: fall back to evidence-only
        else:
            answer = llm_call["text"] or ""
            if "无法回答" in answer[:20]:
                return refuse("生成模型判定证据不足", evidence)
            quotes = re.findall(r"「([^」]{4,})」", answer)
            bad = [q for q in quotes if not any(verify_quote(q, e["text"])[0] for e in evidence)]
            stage("llm", status="ok", n_quotes=len(quotes), n_bad_quotes=len(bad))
            if not quotes or bad:
                return refuse("生成答案中的引文未通过原文校验" if bad else "生成答案未给出原文引文", evidence)
    mode = "llm_answer" if answer is not None else "evidence_only"
    stage("done", llm=(llm_call or {}).get("model_name") or "none")
    return {"query": query, "refused": False, "refusal_reason": None, "answer": answer,
            "mode": mode, "evidence": evidence, "citations": citations, "trace": trace}


def build_prompt(query: str, evidence: list[dict], context: list[ContextBlock]) -> str:
    ctx = "\n\n".join(b.text for b in context)
    ev = "\n\n".join(f"[证据{i + 1}] {e['doc_id']} 第{e['clause_no'] or '-'}条 ({e['kind']})\n{e['text']}"
                      for i, e in enumerate(evidence))
    return ((ctx + "\n\n") if ctx else "") + (
        "仅依据下列证据回答问题。凡引用原文，必须用「」逐字摘抄证据中的连续原文（不得改写）。"
        "若证据不足以回答，只输出：无法回答。\n\n" f"{ev}\n\n问题：{query}\n回答：")
