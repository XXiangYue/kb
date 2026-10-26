"""False-refusal breakdown of the S1 t=0.94 + DeepSeek e2e run (7fdc378).  Analysis only: it reads
reports/e2e_s1_deepseek_per_question.jsonl, makes no model calls and runs no retrieval.

Rule (deterministic, on each answerable record with refused == True):
  the refusal_reason string is one of the fixed strings that tushen_kb/ask.py emits, and the
  record fields must agree with the stage that emits it:
    1 模型自判证据不足   reason == "生成模型判定证据不足"            and llm_called
    2 引文没过逐字校验   reason == "生成答案中的引文未通过原文校验"    and llm_called and n_bad_quotes_stage >= 1
    3 没给引文           reason == "生成答案未给出原文引文"            and llm_called and n_quotes_stage == 0
    4 其他, sub-counts:
        S1闸门           reason starts "证据不足（rerank 最高分"      and not llm_called
        无通过校验的引用  reason == "没有通过原文校验的引用"          and not llm_called
        其他原因          any other reason string
        未能分类          a known reason whose record fields disagree with the rule above
Examples: the first 3 records of each (set, category / sub-category) sorted by qid.
Description only, for category 2: how many of the rejected answers contain an ellipsis
("……" or "...") inside a 「」 quote (i.e. the model spliced a quote).
"""
from __future__ import annotations

import json
import re
import time

from ..ingest import ROOT

REPORTS = ROOT / "reports"
SRC = REPORTS / "e2e_s1_deepseek_per_question.jsonl"
OUT = REPORTS / "false_refusal_breakdown_s1_deepseek.json"
CATS = ("1_模型自判证据不足", "2_引文没过逐字校验", "3_没给引文", "4_其他")
SUBS = ("S1闸门", "无通过校验的引用", "其他原因", "未能分类")
SETS = {"block2_answerable": "questions_block2.jsonl", "lowoverlap": "questions_lowoverlap.jsonl"}


def classify(r: dict) -> tuple[str, str | None]:
    reason, llm = r.get("refusal_reason") or "", bool(r.get("llm_called"))
    if reason == "生成模型判定证据不足":
        return (CATS[0], None) if llm else (CATS[3], "未能分类")
    if reason == "生成答案中的引文未通过原文校验":
        return (CATS[1], None) if llm and (r.get("n_bad_quotes_stage") or 0) >= 1 else (CATS[3], "未能分类")
    if reason == "生成答案未给出原文引文":
        return (CATS[2], None) if llm and r.get("n_quotes_stage") == 0 else (CATS[3], "未能分类")
    if reason.startswith("证据不足（rerank 最高分"):
        return CATS[3], "S1闸门" if not llm else "未能分类"
    if reason == "没有通过原文校验的引用":
        return CATS[3], "无通过校验的引用" if not llm else "未能分类"
    return CATS[3], "其他原因"


def spliced(ans: str) -> bool:
    return any("……" in q or "..." in q for q in re.findall(r"「([^」]*)」", ans or ""))


def main():
    R = [json.loads(l) for l in open(SRC, encoding="utf-8")]
    qtext = {}
    for f in SETS.values():
        for l in list(open(REPORTS / f, encoding="utf-8"))[1:]:
            q = json.loads(l)
            qtext[q["qid"]] = q["question"]
    res = {}
    for name, f in SETS.items():
        ans = [r for r in R if r["file"] == f and r["answerable"]]
        fr = sorted((r for r in ans if r["refused"]), key=lambda r: r["qid"])
        groups = {}
        for r in fr:
            c, s = classify(r)
            groups.setdefault(c, []).append(r)
            if s:
                groups.setdefault(f"{c}/{s}", []).append(r)
        d = {"n_answerable": len(ans), "n_false_refusal": len(fr), "counts": {}, "examples": {}}
        for c in CATS:
            d["counts"][c] = f"{len(groups.get(c, []))}/{len(fr)}"
        for s in SUBS:
            d["counts"][f"{CATS[3]}/{s}"] = f"{len(groups.get(f'{CATS[3]}/{s}', []))}/{len(fr)}"
        assert sum(len(groups.get(c, [])) for c in CATS) == len(fr)
        assert sum(len(groups.get(f"{CATS[3]}/{s}", [])) for s in SUBS) == len(groups.get(CATS[3], []))
        c2 = groups.get(CATS[1], [])
        d["cat2_spliced_quote_with_ellipsis(description)"] = f"{sum(spliced(r.get('raw_answer')) for r in c2)}/{len(c2)}"
        for k, L in groups.items():
            if k == CATS[3]:
                continue
            d["examples"][k] = [{"qid": r["qid"], "question": qtext.get(r["qid"]), "refusal_reason": r["refusal_reason"],
                                 "s1": r.get("s1"), "model_output": (r.get("raw_answer") or "")[:240] or None}
                                for r in L[:3]]
        res[name] = d
    rep = {"time_local": time.strftime("%Y-%m-%d %H:%M %Z"), "source": str(SRC.relative_to(ROOT)) + " (7fdc378)",
           "rule": __doc__, "results": res}
    OUT.write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(res, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
