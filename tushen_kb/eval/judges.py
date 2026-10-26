"""Model-judged metrics (e.g. faithfulness) — OPTIONAL, never a primary metric.

Rules enforced here:
* judges come from config/models.json; validation guarantees judge family !=
  generator family and the two judges differ in family;
* verdicts are binary (True/False) only; for two-choice items each judge is
  asked twice with the option order swapped and the item counts as consistent
  only if both orders agree;
* every record stores judge model name + version + family + status; records
  are labelled ``judge_type="model"`` — never "人工"/human;
* a judge without key/config yields status "skipped" records, never a verdict;
* agreement between two judges is reported as x/N plus Cohen's kappa and the
  disagreeing items are listed.  With one judge available, agreement is "待测".
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass

from ..llm import ChatModel, ModelSpec, load_models
from .metrics import cohen_kappa

FAITH_PROMPT = (
    "你是严格的事实核查员。下面给出【证据】和【回答】。判断【回答】中的每一个事实陈述是否都能在【证据】中找到直接依据。"
    "只输出一个词：是 或 否。\n\n【证据】\n{evidence}\n\n【回答】\n{answer}"
)
CHOICE_PROMPT = (
    "问题：{question}\n证据：{evidence}\n选项A：{a}\n选项B：{b}\n哪个选项被证据支持？只输出 A 或 B。"
)


@dataclass
class Judgment:
    item_id: str
    judge_role: str
    judge_model: str | None
    judge_model_version: str | None
    judge_family: str | None
    judge_type: str  # always "model"
    task: str
    order: str  # "-" | "AB" | "BA"
    status: str  # ok | skipped:* | error | unparsable
    verdict: bool | None
    raw: str | None = None
    fallback_used: bool = False
    n_attempts: int = 0
    error: str | None = None


def _final_line(text: str) -> str:
    """Verdict = the whole reply if short, else its LAST non-empty line (reasoning
    models may print their reasoning before the final answer)."""
    t = (text or "").strip()
    lines = [x.strip(" *`\"'：:.。") for x in t.splitlines() if x.strip()]
    return lines[-1] if lines else ""


def _parse_binary(text: str | None) -> bool | None:
    if not text:
        return None
    for t in (text.strip(), _final_line(text)):
        if re.fullmatch(r"(是|yes|true)[。.!]?", t, re.I) or (t is text.strip() and re.match(r"^是", t)):
            return True
        if re.fullmatch(r"(否|不是|no|false)[。.!]?", t, re.I) or (t is text.strip() and re.match(r"^(否|不是)", t)):
            return False
    return None


def _parse_choice(text: str | None) -> str | None:
    if not text:
        return None
    t = text.strip().upper()
    if t[:1] in ("A", "B") and (len(t) == 1 or not t[1:2].isalpha()):
        return t[:1]
    f = _final_line(text).upper()
    m = re.fullmatch(r"(?:答案|ANSWER|选项|OPTION)?\s*[:：]?\s*([AB])", f)
    return m.group(1) if m else None


class Judge:
    def __init__(self, spec: ModelSpec):
        self.spec = spec
        self.model = ChatModel(spec)

    def _rec(self, item_id, task, order, call, verdict) -> Judgment:
        status = call["status"]
        if status == "ok" and verdict is None:
            status = "unparsable"
        # name the model ACTUALLY used (fallback may differ from the configured primary)
        return Judgment(item_id, self.spec.role, call.get("model_name"), call.get("model_version"), call.get("family"),
                        "model", task, order, status, verdict if status == "ok" else None, call.get("text"),
                        bool(call.get("fallback_used")), len(call.get("attempts") or []), call.get("error"))

    def faithfulness(self, item_id: str, evidence: str, answer: str) -> Judgment:
        call = self.model.complete([{"role": "user", "content": FAITH_PROMPT.format(evidence=evidence, answer=answer)}],
                                   max_tokens=8)
        return self._rec(item_id, "faithfulness", "-", call, _parse_binary(call.get("text")))

    def choice(self, item_id: str, question: str, evidence: str, correct: str, wrong: str) -> list[Judgment]:
        """Two-choice item, asked in both orders.  verdict=True means the judge picked the correct option."""
        out = []
        for order, (a, b) in (("AB", (correct, wrong)), ("BA", (wrong, correct))):
            call = self.model.complete([{"role": "user", "content": CHOICE_PROMPT.format(
                question=question, evidence=evidence, a=a, b=b)}], max_tokens=4)
            t = _parse_choice(call.get("text"))
            picked_correct = None if t is None else ((t == "A") == (order == "AB"))
            out.append(self._rec(item_id, "choice", order, call, picked_correct))
        return out


def load_judges() -> list[Judge]:
    _, specs = load_models()
    return [Judge(s) for s in specs]


def agreement(j1: list[Judgment], j2: list[Judgment]) -> dict:
    """Inter-judge agreement.  Items where either judge's call failed are NOT dropped
    silently: they are counted in ``n_total``, listed in ``failed_items``, and an
    all-items agreement (failures counted as non-agreement) is reported too."""
    a = {(r.item_id, r.order): r for r in j1}
    b = {(r.item_id, r.order): r for r in j2}
    keys = sorted(set(a) | set(b))
    both = [k for k in keys if k in a and k in b and a[k].status == "ok" and b[k].status == "ok"]
    failed = [{"item_id": k[0], "order": k[1], "judge1_status": a[k].status if k in a else "missing",
               "judge2_status": b[k].status if k in b else "missing"} for k in keys if k not in both]
    if not both:
        return {"agree": "待测", "n_both_ok": 0, "n_total": len(keys), "kappa": "待测",
                "disagreements": [], "failed_items": failed}
    agree = sum(a[k].verdict == b[k].verdict for k in both)
    return {"agree": agree, "n_both_ok": len(both), "str": f"{agree}/{len(both)}",
            "agree_over_all_items(failures=non-agreement)": f"{agree}/{len(keys)}", "n_total": len(keys),
            "kappa": cohen_kappa([a[k].verdict for k in both], [b[k].verdict for k in both]),
            "disagreements": [{"item_id": k[0], "order": k[1], "judge1": a[k].verdict, "judge1_model": a[k].judge_model,
                               "judge2": b[k].verdict, "judge2_model": b[k].judge_model}
                              for k in both if a[k].verdict != b[k].verdict],
            "failed_items": failed}


def corrupt(text: str) -> tuple[str, str] | None:
    """Program-made unfaithful variant (label known): change a number, else flip a modal."""
    import re as _re
    m = _re.search(r"\d+(?:\.\d+)?", _re.sub(r"\d+(\.\d+){1,3}条", lambda x: "#" * len(x.group()), text))
    if m:
        v = m.group()
        nv = str(int(float(v)) + 7) if "." not in v else str(round(float(v) + 0.7, 2))
        return text[:m.start()] + nv + text[m.end():], f"number {v}->{nv}"
    for a, b in (("不应", "应"), ("不得", "可"), ("应", "不应"), ("必须", "可不")):
        if a in text:
            return text.replace(a, b, 1), f"modal {a}->{b}"
    return None


def dump(records: list[Judgment], path) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(asdict(r), ensure_ascii=False) + "\n")
