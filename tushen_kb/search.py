"""Retrieval.  Block 1 implements mode=keyword (FTS5 BM25 over jieba+bigram
tokens).  vector / hybrid (RRF k=60) / hybrid_rerank are declared so the API
and eval harness are stable, but raise NotImplementedError until block 2."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .store import Store
from .tokenize import fts_query

MODES = ("keyword", "vector", "hybrid", "hybrid_rerank")
CLAUSE_IN_QUERY = re.compile(r"(?<![\d.])((?:[A-Z]|\d{1,2})(?:\.\d{1,3}){1,4})(?![\d.])")
STD_IN_QUERY = re.compile(r"(GB|DB\s*\d{2}|JGJ|CJJ|XF)\s*(/\s*T)?\s*(\d{3,6})(?:\s*[-—]\s*(\d{4}))?", re.I)


@dataclass
class SearchConfig:
    mode: str = "keyword"
    k: int = 10
    clause_route: bool = True   # exact clause-number match is promoted to the top
    std_filter: bool = True     # a standard code in the query restricts to that doc
    kinds: tuple = ("clause", "commentary", "table", "text")
    extra: dict = field(default_factory=dict)


def parse_std_codes(q: str) -> list[str]:
    out = []
    for m in STD_IN_QUERY.finditer(q):
        prefix = re.sub(r"\s", "", m.group(1)).upper()
        out.append(f"{prefix}{'T' if m.group(2) else ''}{m.group(3)}" + (f"-{m.group(4)}" if m.group(4) else ""))
    return out


def resolve_docs(store: Store, q: str) -> tuple[list[str], list[str]]:
    """Return (matched doc_ids, codes mentioned but absent from corpus)."""
    docs = store.docs()
    matched, missing = [], []
    for code in parse_std_codes(q):
        hit = [d["doc_id"] for d in docs if d["doc_id"].upper().replace("/", "").startswith(code.split("-")[0])
               and (("-" not in code) or d["doc_id"].upper().endswith(code.split("-")[1]))]
        if hit:
            matched.extend(h for h in hit if h not in matched)
        else:
            missing.append(code)
    return matched, missing


def search(store: Store, q: str, cfg: SearchConfig | None = None) -> list[dict]:
    cfg = cfg or SearchConfig()
    if cfg.mode not in MODES:
        raise ValueError(f"unknown mode {cfg.mode}")
    if cfg.mode != "keyword":
        raise NotImplementedError(f"mode={cfg.mode} 待实现（block 2）")
    return _keyword(store, q, cfg)


def _keyword(store: Store, q: str, cfg: SearchConfig) -> list[dict]:
    fq = fts_query(q)
    if not fq:
        return []
    doc_ids: list[str] = []
    if cfg.std_filter:
        doc_ids, _ = resolve_docs(store, q)
    where, args = ["chunks_fts MATCH ?"], [fq]
    if doc_ids:
        where.append("c.doc_id IN (%s)" % ",".join("?" * len(doc_ids)))
        args += doc_ids
    where.append("c.kind IN (%s)" % ",".join("?" * len(cfg.kinds)))
    args += list(cfg.kinds)
    pool = max(cfg.k * 5, 50)
    sql = ("SELECT c.*, bm25(chunks_fts) AS bm FROM chunks_fts JOIN chunks c ON c.rowid = chunks_fts.rowid "
           f"WHERE {' AND '.join(where)} ORDER BY bm LIMIT {pool}")
    rows = [dict(r) for r in store.conn.execute(sql, args)]
    for r in rows:
        r["score"] = -r.pop("bm")  # higher is better
        r["route"] = "bm25"
    if cfg.clause_route:
        nos = set(CLAUSE_IN_QUERY.findall(q))
        if nos:
            routed = [r for r in rows if r["clause_no"] in nos and r["kind"] in ("clause", "table")]
            # also fetch exact clause chunks that BM25 did not surface
            have = {r["rowid"] for r in routed}
            for no in nos:
                sql2 = "SELECT c.* FROM chunks c WHERE c.clause_no=? AND c.kind IN ('clause','table')"
                a2 = [no]
                if doc_ids:
                    sql2 += " AND c.doc_id IN (%s)" % ",".join("?" * len(doc_ids))
                    a2 += doc_ids
                for r in store.conn.execute(sql2 + " ORDER BY rowid", a2):
                    if r["rowid"] not in have:
                        d = dict(r)
                        d["score"] = 0.0
                        routed.append(d)
                        have.add(d["rowid"])
            for r in routed:
                r["route"] = "clause_route"
            rows = routed + [r for r in rows if r["rowid"] not in have]
    for i, r in enumerate(rows[:cfg.k]):
        r["rank"] = i + 1
    return rows[:cfg.k]
