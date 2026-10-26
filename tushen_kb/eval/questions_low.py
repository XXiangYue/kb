"""Block 2 / step 3: a larger LOW-OVERLAP stratum (overlap_rate < 0.3), frozen before any run.

Type "L": the generator (DeepSeek, generator role only) is asked to describe a concrete
drawing-review situation in everyday words, avoiding the clause's own technical terms.
Program filters, in order (counts recorded in _meta.filters):
  call failed -> reference/copy guard (has_ref, >=8 common chars) -> program gold validity
  -> overlap_rate >= 0.3 removed -> back-translation (gold clause answers it?) -> closed-book leak
  -> dedup against block-2 questions.
Source clauses exclude every src_chunk already used in questions_block2.jsonl.
"""
from __future__ import annotations

import argparse
import json
import random
import re
import time
from concurrent.futures import ThreadPoolExecutor

from ..ingest import ROOT
from ..llm import ChatModel, load_models
from ..store import Store
from . import questions_b2 as b2

QFILE = b2.REPORTS / "questions_lowoverlap.jsonl"
CALLS = b2.REPORTS / "questions_lowoverlap_generator_calls.jsonl"
LOW = 0.3
L_PROMPT = ("以下是一条建筑消防技术标准的条文。请设想一位不熟悉规范术语的建设单位人员，在看施工图时遇到了"
            "这条条文所管的具体情形，用日常口语向审图工程师提一个问题，且该条文能够直接回答这个问题。"
            "要求：尽量不用条文里的专业术语，改用通俗说法描述场景；不得照抄条文中连续4个及以上的字；"
            "不要出现条文编号、标准编号或标准名称；只输出问题本身。\n条文：{text}")


def build(store: Store, n: int, seed: int = 2027):
    gen, _ = load_models()
    if gen.status() != "ready":
        raise SystemExit(f"generator {gen.status()}")
    m = ChatModel(gen)
    g = b2.Gold(store)
    _, old = b2.load()
    used = {q["src_chunk"] for q in old if q.get("src_chunk")}
    old_q = {re.sub(r"\s", "", q["question"]) for q in old}
    cs = [c for c in g.chunks if c["kind"] == "clause" and len(b2.body(c)) >= 20 and c["chunk_id"] not in used]
    sample = random.Random(seed).sample(cs, min(n, len(cs)))
    calls: list = []
    with ThreadPoolExecutor(4) as ex:
        res = list(ex.map(lambda c: m.complete([{"role": "user", "content": L_PROMPT.format(text=c["text"][:1200])}],
                                               max_tokens=120), sample))
    cnt = {"source_pool": len(cs), "generated_calls": len(sample), "removed_call_failed": 0,
           "removed_copy_or_ref": 0, "removed_validity": 0, "removed_overlap_ge_0.3": 0}
    out = []
    for c, r in zip(sample, res):
        calls.append(b2._log(r, "L_generate", c["chunk_id"]))
        if r["status"] != "ok":
            cnt["removed_call_failed"] += 1
            continue
        q = r["text"].strip().splitlines()[-1].strip()
        if b2.has_ref(q) or b2.lcs_len(q, c["text"]) >= b2.COPY_MAX:
            cnt["removed_copy_or_ref"] += 1
            continue
        d = g.rec("L", c["chunk_id"], q, c, [b2.body(c)[:60]], {"generator_model": r["model_version"]})
        if d is None:
            cnt["removed_validity"] += 1
            continue
        if d["overlap"] >= LOW:
            cnt["removed_overlap_ge_0.3"] += 1
            continue
        out.append(d)
    out, cnt["back_translation"] = b2.back_translate(g, m, out, calls)
    out, cnt["closed_book"] = b2.closed_book(g, m, out, calls)
    n0 = len(out)
    out = [q for q in out if re.sub(r"\s", "", q["question"]) not in old_q]
    out, dd = b2.dedup(out)
    cnt["dedup_removed"] = n0 - len(out)
    by = {"test": sum(q["split"] == "test" for q in out), "dev": sum(q["split"] == "dev" for q in out)}
    meta = {"built_local": time.strftime("%Y-%m-%d %H:%M %Z"), "seed": seed, "low_threshold": LOW,
            "prompts": {"L": L_PROMPT, "back_translation": b2.BT_PROMPT, "closed_book": b2.CB_PROMPT},
            "filters": cnt, "counts_by_split": by, "generator": {"model": gen.model, "family": gen.family},
            "generator_calls": {"n": len(calls), "ok": sum(c["status"] == "ok" for c in calls),
                                "tokens_total": sum((c["usage"] or {}).get("total_tokens", 0) for c in calls)}}
    with open(QFILE, "w", encoding="utf-8") as f:
        f.write(json.dumps({"_meta": meta}, ensure_ascii=False) + "\n")
        for q in out:
            f.write(json.dumps(q, ensure_ascii=False) + "\n")
    CALLS.write_text("".join(json.dumps(c, ensure_ascii=False) + "\n" for c in calls), encoding="utf-8")
    print(json.dumps(meta, ensure_ascii=False, indent=1))


def load() -> tuple[dict, list]:
    lines = [json.loads(l) for l in open(QFILE, encoding="utf-8")]
    return lines[0]["_meta"], lines[1:]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=260)
    ap.add_argument("--db", default=str(ROOT / "data" / "tushen.sqlite"))
    a = ap.parse_args()
    build(Store(a.db), a.n)


if __name__ == "__main__":
    main()
