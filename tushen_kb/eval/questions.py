"""Question construction for program-graded eval (block 1: T1 + U).

Follows 「两条线的最小可信方案」:
  * questions are reverse-generated from chunks, each with an *acceptable answer
    set* computed by program (all chunk ids of the gold clause, plus a gold span
    so hits are defined independent of chunking);
  * three filter passes, each reporting how many questions it removed:
      1. back-translation answerable  (model; needs generator key)
      2. closed-book unanswerable     (model answer + PROGRAM check for verbatim leakage)
      3. dedup                        (program, char-3gram Jaccard)
    plus a program validity pre-filter (gold must exist; >3 golds dropped);
  * overlap rate question-vs-source is recorded for stratified reporting;
  * U (unanswerable) questions use clause numbers verified to have 0
    occurrences in the stored document text.
"""
from __future__ import annotations

import hashlib
import random
import re
from collections import defaultdict

from ..normalize import squash
from ..store import Store
from ..tokenize import content_tokens

T1_TEMPLATES = ["《{title}》第{no}条是如何规定的？", "{code} 第{no}条的内容是什么？", "{code}中第{no}条讲了什么？"]
MAX_GOLDS = 3


def qid(*parts) -> str:
    return hashlib.sha1("|".join(map(str, parts)).encode()).hexdigest()[:12]


def split_of(q_id: str, dev_frac: float = 0.2) -> str:
    return "dev" if int(q_id, 16) % 100 < dev_frac * 100 else "test"


def overlap_rate(question: str, source: str) -> float:
    qt = set(content_tokens(question))
    if not qt:
        return 0.0
    src = source.replace("\n", "").lower()
    return sum(1 for t in qt if t in src) / len(qt)


def stratum(r: float) -> str:
    return "low(<0.3)" if r < 0.3 else ("high(>=0.6)" if r >= 0.6 else "mid")


def build_t1(store: Store, seed: int = 13) -> tuple[list[dict], dict]:
    rng = random.Random(seed)
    docs = {d["doc_id"]: d for d in store.docs()}
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for c in store.chunks("kind IN ('clause','table') AND clause_no IS NOT NULL"):
        groups[(c["doc_id"], c["clause_no"])].append(c)
    counts = {"candidates": len(groups), "removed_validity_no_clause_chunk": 0, "removed_validity_too_many_golds": 0}
    out = []
    for (doc_id, no), cs in sorted(groups.items()):
        clause_cs = [c for c in cs if c["kind"] == "clause"]
        if not clause_cs:
            counts["removed_validity_no_clause_chunk"] += 1
            continue
        if len(cs) > MAX_GOLDS:
            counts["removed_validity_too_many_golds"] += 1
            continue
        d = docs[doc_id]
        tpl = rng.choice(T1_TEMPLATES)
        q = tpl.format(title=d["title"], code=d["std_code"], no=no)
        gold_text = "\n".join(c["text"] for c in clause_cs)
        i = qid("T1", doc_id, no)
        out.append({
            "qid": i, "type": "T1", "question": q, "template": tpl, "doc_id": doc_id, "clause_no": no,
            "acceptable": sorted(c["chunk_id"] for c in cs),
            "gold_span": [doc_id, min(c["char_start"] for c in clause_cs), max(c["char_end"] for c in clause_cs)],
            "overlap": round(overlap_rate(q, gold_text), 3), "split": split_of(i),
            "answerable": True,
        })
    counts["after_validity"] = len(out)
    return out, counts


def build_u(store: Store, per_doc: int = 10, seed: int = 7) -> list[dict]:
    """Unanswerable: real standard code + clause number with 0 occurrences."""
    rng = random.Random(seed)
    out = []
    for d in store.docs():
        text_sq, _ = squash(store.doc_text(d["doc_id"]))
        nos = [c["clause_no"] for c in store.chunks("doc_id=? AND clause_no IS NOT NULL", (d["doc_id"],))]
        tops = sorted({int(n.split(".")[0]) for n in nos if n.split(".")[0].isdigit()})
        if not tops:
            continue
        made, tries = 0, 0
        while made < per_doc and tries < 500:
            tries += 1
            top = rng.choice(tops + [tops[-1] + rng.randint(1, 5)])
            cand = f"{top}.{rng.randint(1, 12)}.{rng.randint(20, 60)}"
            if cand in text_sq or store.clause_exists(cand, d["doc_id"]):
                continue  # must have 0 occurrences anywhere in the stored text
            i = qid("U", d["doc_id"], cand)
            if any(o["qid"] == i for o in out):
                continue
            out.append({"qid": i, "type": "U", "question": f"{d['std_code']} 第{cand}条的内容是什么？",
                        "doc_id": d["doc_id"], "clause_no": cand, "acceptable": [], "gold_span": None,
                        "occurrences_in_corpus": 0, "split": split_of(i), "answerable": False})
            made += 1
    return out


def dedup(questions: list[dict]) -> tuple[list[dict], int]:
    """Remove duplicates: same normalized question text, or same target
    (doc + acceptable set).  Template questions about *different* clauses are
    textually near-identical but not duplicates, so char-similarity alone is not used."""
    kept, removed, seen = [], 0, set()
    for q in questions:
        k1 = re.sub(r"\s", "", q["question"])
        k2 = (q["doc_id"], tuple(q["acceptable"]) or q["clause_no"])
        if k1 in seen or k2 in seen:
            removed += 1
            continue
        seen.update([k1, k2])
        kept.append(q)
    return kept, removed


LEAK_MIN = 15


def closed_book_leak(answer: str | None, gold_text: str) -> bool:
    """PROGRAM check: does a closed-book model answer reproduce >=15 consecutive
    chars of the gold clause verbatim?  (then the question is guessable without
    retrieval and is removed)."""
    if not answer:
        return False
    a, _ = squash(answer)
    g, _ = squash(gold_text)
    for i in range(0, max(0, len(g) - LEAK_MIN + 1)):
        if g[i:i + LEAK_MIN] in a:
            return True
    return False
