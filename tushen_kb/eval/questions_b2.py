"""Block-2 question sets (NO clause numbers / standard codes in any question).

  T2  content reverse lookup  PROGRAM: a verbatim sentence of a clause -> "which provision says ...?"
  T3  paraphrase-of-content   MODEL (generator) writes a question the clause answers, in its own words;
                              PROGRAM rejects copies (>= 8 consecutive chars shared with the clause)
  T4  table value             PROGRAM: (table name, row key, column header) -> cell value
  T5  negation / prohibition  PROGRAM: subject of a 不得/严禁/禁止/不应 sentence -> "what is prohibited?"
  P   paraphrase set          MODEL rewrites a T2/T4/T5 question (same gold); PROGRAM rejects copies
  U2  unanswerable (content)  MODEL proposes out-of-corpus questions + key term; PROGRAM keeps only
                              those whose key term has 0 occurrences in the whole stored corpus text

Acceptable answer sets are computed by PROGRAM: every clause/table/text chunk whose
text contains the gold span (whitespace-insensitive); questions with > 3 golds are
dropped.  Filters (each reports its removal count): validity, copy guard, back-
translation (model, T3/P only), closed-book leak (model + program check, T3 only),
dedup.  The frozen file starts with a _meta line holding the prompts, seeds,
generator call log (no texts of keys) and the filter counts.

Usage: python -m tushen_kb.eval.questions_b2 build   -> reports/questions_block2.jsonl
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
from ..normalize import squash
from ..search import CLAUSE_IN_QUERY, STD_IN_QUERY
from ..store import Store
from .questions import closed_book_leak, overlap_rate, qid, split_of

REPORTS = ROOT / "reports"
QFILE = REPORTS / "questions_block2.jsonl"
GOLD_KINDS = ("clause", "table", "text")
MAX_GOLDS = 3
COPY_MAX = 8  # a model-written question may not share >= 8 consecutive chars with its source
NEG = re.compile(r"(严禁|禁止|不得|不应)(?!小于|大于|低于|高于|超过|少于|多于)")

T3_PROMPT = ("以下是一条建筑消防技术标准的条文。请站在施工图审查工程师的角度，提出一个该条文能够直接回答的问题。"
             "要求：用自己的话表述，不得照抄条文中连续6个及以上的字；不要出现条文编号、标准编号或标准名称；"
             "只输出问题本身。\n条文：{text}")
P_PROMPT = ("请改写下面这个问题：保持所问内容与答案完全不变，但换一种说法（同义词替换、调整语序、口语化均可），"
            "不得照抄原问题中连续6个及以上的字；不要添加条文编号、标准编号或标准名称；只输出改写后的问题。\n原问题：{q}")
BT_PROMPT = "条文：{gold}\n问题：{q}\n仅凭上述条文能否回答该问题？只输出：是 或 否。"
CB_PROMPT = "不查阅任何资料，直接回答：{q} 请尽量给出条文原文。"
U_PROMPT = ("请生成{n}个建筑工程施工图审查中可能被问到的具体技术问题，主题只能来自：{topics}。"
            "每个问题必须围绕一个具体专业术语（3到8个汉字）。不要出现标准编号或条文编号。"
            "只输出 JSON 数组，每个元素形如 {{\"q\": \"问题\", \"term\": \"术语\"}}。")
U_TOPICS = ["结构抗震设计", "混凝土结构配筋", "地基基础与桩基", "建筑节能与热工", "建筑声学与隔声",
            "绿色建筑评价", "钢结构焊接", "屋面防水", "给水管道水压试验", "住宅日照间距"]


def lcs_len(a: str, b: str) -> int:
    """Longest common substring length (whitespace-insensitive)."""
    a, b = squash(a)[0], squash(b)[0]
    best, prev = 0, [0] * (len(b) + 1)
    for i in range(1, len(a) + 1):
        cur = [0] * (len(b) + 1)
        for j in range(1, len(b) + 1):
            if a[i - 1] == b[j - 1]:
                cur[j] = prev[j - 1] + 1
                best = max(best, cur[j])
        prev = cur
    return best


def has_ref(q: str) -> bool:
    return bool(CLAUSE_IN_QUERY.search(q) or STD_IN_QUERY.search(q) or re.search(r"第[\d.]+条|《", q))


LIST_MARK = re.compile(r"^(?:\d{1,2}|[a-z]\)|[（(]\d{1,2}[）)]|——|—)\s*")


def body(c: dict) -> str:
    """Clause text without its number, whitespace removed (PDF line breaks split sentences)."""
    t = squash(c["text"])[0]
    no = c["clause_no"] or ""
    if no and t.startswith(no):
        t = t[len(no):]
    return t


class Gold:
    def __init__(self, store: Store):
        self.store = store
        self.chunks = store.chunks("kind IN ('clause','table','text')")
        self.sq = {c["chunk_id"]: squash(c["text"] if c["kind"] != "table" else
                                         (c["table_title"] or "") + c["text"])[0] for c in self.chunks}
        self.by_id = {c["chunk_id"]: c for c in self.chunks}
        self.corpus = "".join(squash(store.doc_text(d["doc_id"]))[0] for d in store.docs())

    def acceptable(self, *spans: str) -> list[str]:
        ss = [squash(s)[0] for s in spans]
        return sorted(cid for cid, t in self.sq.items() if all(s in t for s in ss))

    def rec(self, typ, key, question, c, spans, extra=None):
        acc = self.acceptable(*spans)
        if not acc or len(acc) > MAX_GOLDS or c["chunk_id"] not in acc:
            return None
        i = qid(typ, key)
        gold_text = "\n".join(self.by_id[a]["text"] for a in acc)
        d = {"qid": i, "type": typ, "question": question, "doc_id": c["doc_id"], "src_chunk": c["chunk_id"],
             "gold_spans": list(spans), "acceptable": acc, "overlap": round(overlap_rate(question, gold_text), 3),
             "split": split_of(i), "answerable": True}
        d.update(extra or {})
        return d


def build_t2(g: Gold, n: int, rng) -> tuple[list, dict]:
    cs = [c for c in g.chunks if c["kind"] == "clause"]
    rng.shuffle(cs)
    out, cnt = [], {"candidates": 0, "removed_validity": 0}
    for c in cs:
        if len(out) >= n:
            break
        sents = [LIST_MARK.sub("", s.strip("，、：；。——")) for s in re.split(r"[。；：]|——", body(c))]
        sents = [s for s in sents if 12 <= len(squash(s)[0]) <= 50 and not has_ref(s)]
        if not sents:
            continue
        cnt["candidates"] += 1
        s = rng.choice(sents)
        r = g.rec("T2", c["chunk_id"], f"哪一条规定了“{s}”？", c, [s], {"template": "哪一条规定了“{span}”？"})
        if r is None:
            cnt["removed_validity"] += 1
            continue
        out.append(r)
    return out, cnt


def parse_table(text: str) -> list[list[str]]:
    return [[x.strip() for x in ln.split("|")] for ln in text.split("\n") if "|" in ln]


def build_t4(g: Gold, n: int, rng, per_table: int = 3) -> tuple[list, dict]:
    out, cnt = [], {"candidates": 0, "removed_validity": 0}
    tabs = [c for c in g.chunks if c["kind"] == "table"]
    rng.shuffle(tabs)
    for c in tabs:
        rows = parse_table(c["text"])
        if len(rows) < 2:
            continue
        head = rows[0]
        name = re.sub(r"^表[A-Z]?[\d.\-]*", "", squash(c["table_title"] or "")[0])
        name = re.sub(r"[（(]续[）)]$", "", name)
        cands = []
        for row in rows[1:]:
            key = row[0] if row and row[0] else None
            for j, v in enumerate(row[1:], start=1):
                h = head[j] if j < len(head) else ""
                if (key and h and v and 2 <= len(v) <= 40 and len(h) <= 20 and v != key and v != h
                        and not re.fullmatch(r"[\d.]+", key) and re.search(r"[\u4e00-\u9fff\d]", v) and "□" not in key + h + v and not has_ref(key + h)):
                    cands.append((key, h, v))
        rng.shuffle(cands)
        made = 0
        for key, h, v in cands:
            if made >= per_table or len(out) >= n:
                break
            cnt["candidates"] += 1
            q = f"{name}中，“{key}”对应的“{h}”是什么？" if name else f"表格中“{key}”对应的“{h}”是什么？"
            r = g.rec("T4", f"{c['chunk_id']}|{key}|{h}", q, c, [key, v], {"answer": v, "table_name": name})
            if r is None:
                cnt["removed_validity"] += 1
                continue
            out.append(r)
            made += 1
    return out, cnt


def build_t5(g: Gold, n: int, rng) -> tuple[list, dict]:
    out, cnt = [], {"candidates": 0, "removed_validity": 0}
    cs = [c for c in g.chunks if c["kind"] == "clause"]
    rng.shuffle(cs)
    for c in cs:
        if len(out) >= n:
            break
        made = 0
        for s in re.split(r"[。；]|——", body(c)):
            if made >= 2:
                break
            s = LIST_MARK.sub("", s)
            m = NEG.search(s)
            if not m:
                continue
            subj = LIST_MARK.sub("", re.split(r"[，、：,]", s[:m.start()])[-1].strip("—— "))
            pred = s[m.end():].strip("，。 ")
            if (not (4 <= len(subj) <= 24 and len(pred) >= 2) or has_ref(subj) or re.match(r"^[\d.]", subj)
                    or re.match(r"^(其|下列|该|上述|此|且|并|均)", subj)):
                continue
            cnt["candidates"] += 1
            q = f"关于“{subj}”，有哪些禁止性要求？"
            r = g.rec("T5", f"{c['chunk_id']}|{subj}", q, c, [subj + s[m.start():m.end()]],
                      {"neg_word": m.group(1)})
            if r is None:
                cnt["removed_validity"] += 1
                continue
            out.append(r)
            made += 1
    return out, cnt


def gold_text(g: Gold, q: dict) -> str:
    return "\n".join(g.by_id[a]["text"] for a in q["acceptable"])


def build_t3(g: Gold, m: ChatModel, n: int, rng, calls: list, exclude: set) -> tuple[list, dict]:
    cs = [c for c in g.chunks if c["kind"] == "clause" and len(body(c)) >= 20 and c["chunk_id"] not in exclude]
    sample = rng.sample(cs, min(n, len(cs)))
    with ThreadPoolExecutor(4) as ex:
        res = list(ex.map(lambda c: m.complete([{"role": "user", "content": T3_PROMPT.format(text=c["text"][:1200])}],
                                               max_tokens=120), sample))
    out, cnt = [], {"generated_calls": len(sample), "removed_call_failed": 0, "removed_copy_or_ref": 0,
                    "removed_validity": 0}
    for c, r in zip(sample, res):
        calls.append(_log(r, "T3_generate", c["chunk_id"]))
        if r["status"] != "ok":
            cnt["removed_call_failed"] += 1
            continue
        q = r["text"].strip().splitlines()[-1].strip()
        if has_ref(q) or lcs_len(q, c["text"]) >= COPY_MAX:
            cnt["removed_copy_or_ref"] += 1
            continue
        d = g.rec("T3", c["chunk_id"], q, c, [body(c)[:60]], {"generator_model": r["model_version"]})
        if d is None:  # gold = the source clause chunk(s): span = first 60 chars of its body
            cnt["removed_validity"] += 1
            continue
        out.append(d)
    return out, cnt


def build_p(g: Gold, m: ChatModel, src: list, n: int, rng, calls: list) -> tuple[list, dict]:
    sample = rng.sample(src, min(n, len(src)))
    with ThreadPoolExecutor(4) as ex:
        res = list(ex.map(lambda q: m.complete([{"role": "user", "content": P_PROMPT.format(q=q["question"])}],
                                               max_tokens=120), sample))
    out, cnt = [], {"generated_calls": len(sample), "removed_call_failed": 0, "removed_copy_or_ref": 0}
    for q, r in zip(sample, res):
        calls.append(_log(r, "P_generate", q["qid"]))
        if r["status"] != "ok":
            cnt["removed_call_failed"] += 1
            continue
        p = r["text"].strip().splitlines()[-1].strip()
        if has_ref(p) or lcs_len(p, q["question"]) >= COPY_MAX:
            cnt["removed_copy_or_ref"] += 1
            continue
        d = dict(q)
        d.update(qid=qid("P", q["qid"]), type="P", question=p, src_qid=q["qid"], src_type=q["type"],
                 overlap=round(overlap_rate(p, gold_text(g, q)), 3), generator_model=r["model_version"])
        d["split"] = q["split"]  # paired with its source question
        out.append(d)
    return out, cnt


def back_translate(g: Gold, m: ChatModel, qs: list, calls: list) -> tuple[list, dict]:
    with ThreadPoolExecutor(4) as ex:
        res = list(ex.map(lambda q: m.complete([{"role": "user", "content": BT_PROMPT.format(
            gold=gold_text(g, q)[:1500], q=q["question"])}], max_tokens=4), qs))
    kept, rem, failed = [], 0, 0
    for q, r in zip(qs, res):
        calls.append(_log(r, f"{q['type']}_back_translation", q["qid"]))
        if r["status"] != "ok":
            failed += 1
            kept.append(q)  # a failed check never removes a question; recorded
            continue
        if (r["text"] or "").strip().startswith("是"):
            kept.append(q)
        else:
            rem += 1
    return kept, {"removed": rem, "calls_failed(kept)": failed, "of": len(qs)}


def closed_book(g: Gold, m: ChatModel, qs: list, calls: list) -> tuple[list, dict]:
    with ThreadPoolExecutor(4) as ex:
        res = list(ex.map(lambda q: m.complete([{"role": "user", "content": CB_PROMPT.format(q=q["question"])}],
                                               max_tokens=300), qs))
    kept, rem = [], 0
    for q, r in zip(qs, res):
        calls.append(_log(r, f"{q['type']}_closed_book", q["qid"]))
        if r["status"] == "ok" and closed_book_leak(r["text"], gold_text(g, q)):
            rem += 1
        else:
            kept.append(q)
    return kept, {"removed": rem, "of": len(qs)}


def build_u2(g: Gold, m: ChatModel, n: int, calls: list) -> tuple[list, dict]:
    out, cnt = [], {"proposed": 0, "removed_unparsable": 0, "removed_term_in_corpus": 0, "removed_ref": 0}
    seen = set()
    for k in range(0, len(U_TOPICS), 2):
        topics = "、".join(U_TOPICS[k:k + 2])
        r = m.complete([{"role": "user", "content": U_PROMPT.format(n=max(4, n // 4), topics=topics)}],
                       max_tokens=1200, temperature=0.7)
        calls.append(_log(r, "U2_generate", topics))
        if r["status"] != "ok":
            continue
        try:
            items = json.loads(re.search(r"\[.*\]", r["text"], re.S).group())
        except Exception:
            cnt["removed_unparsable"] += 1
            continue
        for it in items:
            cnt["proposed"] += 1
            q, term = str(it.get("q", "")).strip(), str(it.get("term", "")).strip()
            if not q or not term or term not in q or q in seen:
                cnt["removed_unparsable"] += 1
                continue
            if has_ref(q):
                cnt["removed_ref"] += 1
                continue
            if squash(term)[0] in g.corpus:
                cnt["removed_term_in_corpus"] += 1
                continue
            seen.add(q)
            i = qid("U2", q)
            out.append({"qid": i, "type": "U2", "question": q, "key_term": term, "topic": topics,
                        "key_term_occurrences_in_corpus": 0, "acceptable": [], "gold_spans": [],
                        "split": split_of(i), "answerable": False, "generator_model": r["model_version"]})
    return out[:n] if len(out) > n else out, cnt


def dedup(qs: list) -> tuple[list, int]:
    kept, seen, rem = [], set(), 0
    for q in qs:
        k = (q["type"], re.sub(r"\s", "", q["question"]))
        if k in seen:
            rem += 1
            continue
        seen.add(k)
        kept.append(q)
    return kept, rem


def _log(r: dict, task: str, ref: str) -> dict:
    return {"task": task, "ref": ref, "model_name": r["model_name"], "model_version": r["model_version"],
            "family": r["family"], "status": r["status"], "usage": r["usage"], "ms": r["ms"],
            "n_attempts": len(r.get("attempts") or []), "error": r.get("error")}


def build(store: Store, sizes: dict, seed: int = 2026):
    gen, _ = load_models()
    if gen.status() != "ready":
        raise SystemExit(f"generator {gen.status()} - T3/P/U2 need it")
    m = ChatModel(gen)
    g = Gold(store)
    calls: list = []
    filt: dict = {}
    t2, filt["T2"] = build_t2(g, sizes["T2"], random.Random(seed))
    t4, filt["T4"] = build_t4(g, sizes["T4"], random.Random(seed + 1))
    t5, filt["T5"] = build_t5(g, sizes["T5"], random.Random(seed + 2))
    t3, filt["T3"] = build_t3(g, m, sizes["T3"], random.Random(seed + 3), calls, {q["src_chunk"] for q in t2})
    t3, filt["T3"]["back_translation"] = back_translate(g, m, t3, calls)
    t3, filt["T3"]["closed_book"] = closed_book(g, m, t3, calls)
    p, filt["P"] = build_p(g, m, t2 + t4 + t5, sizes["P"], random.Random(seed + 4), calls)
    p, filt["P"]["back_translation"] = back_translate(g, m, p, calls)
    u2, filt["U2"] = build_u2(g, m, sizes["U2"], calls)
    allq, n_dup = dedup(t2 + t3 + t4 + t5 + p + u2)
    filt["dedup_removed"] = n_dup
    for q in allq:
        assert not has_ref(q["question"]), q
    by = {}
    for q in allq:
        by.setdefault(q["type"], {"test": 0, "dev": 0})[q["split"]] += 1
    meta = {"built_local": time.strftime("%Y-%m-%d %H:%M %Z"), "seed": seed, "sizes_requested": sizes,
            "prompts": {"T3": T3_PROMPT, "P": P_PROMPT, "back_translation": BT_PROMPT, "closed_book": CB_PROMPT,
                        "U2": U_PROMPT, "U2_topics": U_TOPICS},
            "copy_guard_max_common_substring": COPY_MAX, "max_golds": MAX_GOLDS, "filters": filt,
            "counts_by_type_split": by, "generator": {"model": gen.model, "family": gen.family},
            "generator_calls": {"n": len(calls), "ok": sum(c["status"] == "ok" for c in calls),
                                "tokens_total": sum((c["usage"] or {}).get("total_tokens", 0) for c in calls)}}
    with open(QFILE, "w", encoding="utf-8") as f:
        f.write(json.dumps({"_meta": meta}, ensure_ascii=False) + "\n")
        for q in allq:
            f.write(json.dumps(q, ensure_ascii=False) + "\n")
    (REPORTS / "questions_block2_generator_calls.jsonl").write_text(
        "".join(json.dumps(c, ensure_ascii=False) + "\n" for c in calls), encoding="utf-8")
    print(json.dumps(meta, ensure_ascii=False, indent=1))


def load() -> tuple[dict, list]:
    lines = [json.loads(l) for l in open(QFILE, encoding="utf-8")]
    return lines[0]["_meta"], lines[1:]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["build", "dry"])
    ap.add_argument("--db", default=str(ROOT / "data" / "tushen.sqlite"))
    a = ap.parse_args()
    store = Store(a.db)
    sizes = {"T2": 120, "T3": 80, "T4": 60, "T5": 80, "P": 400, "U2": 40}
    if a.cmd == "dry":  # program-only parts, no model calls
        g = Gold(store)
        for name, fn, s in (("T2", build_t2, 0), ("T4", build_t4, 1), ("T5", build_t5, 2)):
            qs, c = fn(g, sizes[name], random.Random(2026 + s))
            print(name, len(qs), c)
            for q in qs[:4]:
                print("  ", q["question"], q["acceptable"], q.get("answer"))
        return
    build(store, sizes)


if __name__ == "__main__":
    main()
