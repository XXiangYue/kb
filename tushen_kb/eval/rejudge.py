"""Frozen judge-item set + dual-judge run (OPTIONAL model-judged study; never a primary metric).

  build : regenerate the block-1 judge items (6 generator answers on the first 6
          T1-test questions + program corruptions + 4 two-choice items) and
          FREEZE them to reports/judge_items_block1.jsonl together with the exact
          prompts.  Commit that file before judging.
  judge : every configured judge judges every frozen item (two-choice items in
          both orders).  Records keep model name/version, status, attempts,
          token usage and latency; failures stay in all denominators.
          Agreement x/N + kappa are reported only at >= 0.8 coverage.

Block-1 run2 generator answers were not persisted, so the items are regenerated
here (same questions, same corruption rule, same two-choice items).
"""
from __future__ import annotations

import argparse
import json
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict

from ..ask import ask
from ..ingest import ROOT
from ..llm import ChatModel, load_models
from ..store import Store
from . import judges as J

REPORTS = ROOT / "reports"
ITEMS = REPORTS / "judge_items_block1.jsonl"


def build(store, n=6, n_choice=4):
    gen, _ = load_models()
    if gen.status() != "ready":
        raise SystemExit(f"generator {gen.status()}")
    gm = ChatModel(gen)
    qs = [json.loads(l) for l in open(REPORTS / "questions_block1.jsonl", encoding="utf-8")]
    test = [q for q in qs if q["type"] != "U" and q["split"] == "test"]
    items, gen_calls = [], []
    for q in test[:n]:
        r = ask(store, q["question"], llm=gm)
        gen_calls.append({"qid": q["qid"], **(r["trace"].get("llm_call") or {}), "refused": r["refused"],
                          "refusal_reason": r["refusal_reason"]})
        if r["refused"] or not r["answer"]:
            continue
        ev = "\n".join(e["text"] for e in r["evidence"])
        items.append({"item_id": q["qid"] + ":orig", "task": "faithfulness", "evidence": ev, "answer": r["answer"],
                      "label": True, "change": None})
        c = J.corrupt(r["answer"])
        if c:
            items.append({"item_id": q["qid"] + ":corrupt", "task": "faithfulness", "evidence": ev, "answer": c[0],
                          "label": False, "change": c[1]})
    for q in test[n:n + 3 * n_choice]:
        if sum(i["task"] == "choice" for i in items) >= n_choice:
            break
        ch = store.chunks("chunk_id=?", (q["acceptable"][0],))[0]
        c = J.corrupt(ch["text"])
        if c:
            items.append({"item_id": q["qid"] + ":choice", "task": "choice", "question": q["question"],
                          "evidence": ch["text"], "correct": ch["text"], "wrong": c[0], "change": c[1]})
    with open(ITEMS, "w", encoding="utf-8") as f:
        f.write(json.dumps({"_meta": {"built_local": time.strftime("%Y-%m-%d %H:%M %Z"),
                                      "faith_prompt": J.FAITH_PROMPT, "choice_prompt": J.CHOICE_PROMPT,
                                      "generator_calls": gen_calls}}, ensure_ascii=False) + "\n")
        for it in items:
            f.write(json.dumps(it, ensure_ascii=False) + "\n")
    print(f"froze {len(items)} items ({sum(i['task'] == 'choice' for i in items)} choice) -> {ITEMS}")


def _judge_item(j: J.Judge, it: dict) -> list[J.Judgment]:
    if it["task"] == "faithfulness":
        return [j.faithfulness(it["item_id"], it["evidence"], it["answer"])]
    return j.choice(it["item_id"], it["question"], it["evidence"], it["correct"], it["wrong"])


def judge(roles: list[str], workers: int, tag: str):
    lines = [json.loads(l) for l in open(ITEMS, encoding="utf-8")]
    meta, items = lines[0]["_meta"], lines[1:]
    assert meta["faith_prompt"] == J.FAITH_PROMPT and meta["choice_prompt"] == J.CHOICE_PROMPT, "prompts changed"
    judges = [j for j in J.load_judges() if j.spec.role in roles]
    recs: dict[str, list] = {}
    for j in judges:
        t0 = time.time()
        if j.spec.status() != "ready":
            recs[j.spec.role] = [J.Judgment(it["item_id"], j.spec.role, j.spec.model, None, j.spec.family, "model",
                                            it["task"], "-", j.spec.status(), None) for it in items]
            continue
        with ThreadPoolExecutor(max_workers=workers) as ex:
            res = list(ex.map(lambda it: _judge_item(j, it), items))
        recs[j.spec.role] = [x for r in res for x in r]
        print(j.spec.role, j.spec.model, "done in", round(time.time() - t0), "s", flush=True)
    _write(recs, items, tag, retry_log=[])


def _write(recs: dict, items: list, tag: str, retry_log: list):
    path = REPORTS / f"judgments_{tag}_records.jsonl"
    with open(path, "w", encoding="utf-8") as f:
        for rs in recs.values():
            for x in rs:
                d = asdict(x)
                d.pop("raw", None)
                f.write(json.dumps(d, ensure_ascii=False) + "\n")
    (REPORTS / "runs").mkdir(exist_ok=True)
    J.dump([x for rs in recs.values() for x in rs], REPORTS / "runs" / f"judgments_{tag}_raw.jsonl")
    if retry_log:
        with open(REPORTS / f"judgments_{tag}_superseded_failures.jsonl", "w", encoding="utf-8") as f:
            for d in retry_log:
                f.write(json.dumps(d, ensure_ascii=False) + "\n")
    labels = {it["item_id"]: it for it in items}
    summ = {"items_file": str(ITEMS.relative_to(ROOT)), "n_items": len(items), "per_judge": {}}
    for role, rs in recs.items():
        cv = J.coverage(rs)
        faith = [x for x in rs if x.task == "faithfulness"]
        chs = [x for x in rs if x.task == "choice"]
        prev = [d for d in retry_log if d["judge_role"] == role]
        pj = {"models_used": sorted({f"{x.judge_model} ({x.judge_family})" for x in rs}),
              "n_records": len(rs), "coverage(status ok)": cv["str"],
              "status_counts": _count(x.status for x in rs),
              "superseded_failed_calls(retried)": len(prev),
              "n_http_attempts(incl. superseded)": sum(x.n_attempts for x in rs) + sum(d.get("n_attempts") or 0 for d in prev),
              "tokens_total": sum((x.usage or {}).get("total_tokens", 0) for x in rs)
              + sum((d.get("usage") or {}).get("total_tokens", 0) for d in prev),
              "latency_ms_mean(final records)": round(sum(x.ms or 0 for x in rs) / len(rs)) if rs else None,
              "latency_ms_max(final records)": max((x.ms or 0) for x in rs) if rs else None}
        if cv["value"] and cv["value"] >= J.MIN_JUDGE_COVERAGE:
            pj["faith_correct_vs_program_label(over all faith records)"] = J.rate_str(
                sum(1 for x in faith if x.verdict is not None and x.verdict == labels[x.item_id]["label"]), len(faith))
            pj["choice_correct(over all order-judgments)"] = J.rate_str(sum(1 for x in chs if x.verdict is True), len(chs))
        else:
            pj["accuracy"] = f"待测（覆盖率 {cv['str']} < {J.MIN_JUDGE_COVERAGE}）"
        summ["per_judge"][role] = pj
    if len(recs) == 2:
        a, b = recs.values()
        summ["inter_judge_agreement"] = J.agreement(list(a), list(b))
    if retry_log:
        summ["retry_note"] = ("首轮失败的调用按原样重试（同模型、同 prompt），被替换的失败记录保存在 "
                              f"judgments_{tag}_superseded_failures.jsonl，其调用次数与 token 计入上方统计。")
    (REPORTS / f"judge_summary_{tag}.json").write_text(json.dumps(summ, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(summ, ensure_ascii=False, indent=1))


def _load_recs(tag: str) -> dict:
    fields = set(J.Judgment.__dataclass_fields__)
    recs: dict[str, list] = {}
    for l in open(REPORTS / f"judgments_{tag}_records.jsonl", encoding="utf-8"):
        d = json.loads(l)
        recs.setdefault(d["judge_role"], []).append(J.Judgment(**{k: v for k, v in d.items() if k in fields}))
    return recs


def retry(role: str, tag: str, rounds: int, wait_s: float):
    """Re-run ONLY failed judgments of one role with the same model + prompt.
    Never switches model (backup models are manual-only)."""
    lines = [json.loads(l) for l in open(ITEMS, encoding="utf-8")]
    meta, items = lines[0]["_meta"], lines[1:]
    assert meta["faith_prompt"] == J.FAITH_PROMPT and meta["choice_prompt"] == J.CHOICE_PROMPT, "prompts changed"
    by_id = {it["item_id"]: it for it in items}
    recs = _load_recs(tag)
    sup_path = REPORTS / f"judgments_{tag}_superseded_failures.jsonl"
    retry_log = [json.loads(l) for l in open(sup_path, encoding="utf-8")] if sup_path.exists() else []
    j = next(x for x in J.load_judges() if x.spec.role == role)
    for rnd in range(rounds):
        bad = sorted({x.item_id for x in recs[role] if x.status != "ok"})
        if not bad:
            break
        print(f"round {rnd + 1}: retrying {bad}", flush=True)
        for iid in bad:
            old = [x for x in recs[role] if x.item_id == iid]
            new = _judge_item(j, by_id[iid])
            if any(n.judge_model != j.spec.model for n in new):
                raise SystemExit("model changed during retry - refusing")
            for o in old:
                d = asdict(o)
                d.pop("raw", None)
                d["retry_round"] = rnd + 1
                retry_log.append(d)
            recs[role] = [x for x in recs[role] if x.item_id != iid] + new
            time.sleep(wait_s)
    order = {it["item_id"]: k for k, it in enumerate(items)}
    for r in recs:
        recs[r].sort(key=lambda x: (order[x.item_id], x.order))
    _write(recs, items, tag, retry_log)


def _count(it):
    d: dict = {}
    for x in it:
        d[x] = d.get(x, 0) + 1
    return d


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["build", "judge", "retry"])
    ap.add_argument("--db", default=str(ROOT / "data" / "tushen.sqlite"))
    ap.add_argument("--roles", default="judge1,judge2")
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--tag", default="block1_v2")
    ap.add_argument("--role", default="judge2")
    ap.add_argument("--rounds", type=int, default=3)
    ap.add_argument("--wait", type=float, default=20.0)
    a = ap.parse_args()
    if a.cmd == "build":
        build(Store(a.db))
    elif a.cmd == "retry":
        retry(a.role, a.tag, a.rounds, a.wait)
    else:
        judge(a.roles.split(","), a.workers, a.tag)


if __name__ == "__main__":
    main()
