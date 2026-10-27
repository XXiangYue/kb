"""Retrieval modes (one variable changes between adjacent modes):

  keyword       FTS5 BM25 over jieba+bigram tokens                     (block 1)
  vector        bge-small-zh-v1.5 cosine over the same chunks           (block 2)
  hybrid        RRF(k=60) of the keyword pool and the vector pool       (block 2)
  hybrid_rerank hybrid top-N candidates re-ordered by bge-reranker-base (block 2)
  bm25_rerank   keyword top-N candidates re-ordered by the same reranker  (block 2, vs keyword:
                adds only the reranker; vs hybrid_rerank: only the candidate source differs)

Standard-code filter and clause-number routing are applied identically in all
modes (routing promotes exact clause-number hits to the top)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import obs
from .store import Store
from .tokenize import fts_query

MODES = ("keyword", "vector", "hybrid", "hybrid_rerank", "bm25_rerank")
CLAUSE_IN_QUERY = re.compile(r"(?<![\d.])((?:[A-Z]|\d{1,2})(?:\.\d{1,3}){1,4})(?![\d.])")
STD_IN_QUERY = re.compile(r"(GB|DB\s*\d{2}|JGJ|CJJ|XF)\s*(/\s*T)?\s*(\d{3,6})(?:\s*[-—]\s*(\d{4}))?", re.I)


@dataclass
class SearchConfig:
    mode: str = "keyword"
    k: int = 10
    clause_route: bool = True   # exact clause-number match is promoted to the top
    std_filter: bool = True     # a standard code in the query restricts to that doc
    kinds: tuple = ("clause", "commentary", "table", "text")
    pool: int = 50              # candidates per retriever before fusion
    rrf_k: int = 60
    rerank_n: int = 20          # hybrid candidates passed to the cross-encoder
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
    doc_ids: list[str] = resolve_docs(store, q)[0] if cfg.std_filter else []
    if cfg.mode == "keyword":
        rows = _traced("bm25", _bm25_pool, store, q, cfg, doc_ids)
    elif cfg.mode == "bm25_rerank":
        rows = _traced_rerank(q, _traced("bm25", _bm25_pool, store, q, cfg, doc_ids), cfg.rerank_n)
    elif cfg.mode == "vector":
        rows = _traced("vector", _vector_pool, store, q, cfg, doc_ids)
    else:
        a = _traced("bm25", _bm25_pool, store, q, cfg, doc_ids)
        b = _traced("vector", _vector_pool, store, q, cfg, doc_ids)
        with obs.span("kb.search.rrf", **{"kb.rrf_k": cfg.rrf_k}) as sp:
            rows = _rrf(a, b, cfg.rrf_k)
            sp.set_attribute("kb.n_out", len(rows))
        if cfg.mode == "hybrid_rerank":
            rows = _traced_rerank(q, rows, cfg.rerank_n)
    if cfg.clause_route:
        with obs.span("kb.search.clause_route") as sp:
            n0 = sum(r.get("route") == "clause_route" for r in rows)
            rows = _route(store, q, rows, doc_ids)
            sp.set_attribute("kb.n_routed", sum(r.get("route") == "clause_route" for r in rows) - n0)
    for i, r in enumerate(rows[:cfg.k]):
        r["rank"] = i + 1
    return rows[:cfg.k]


def _traced(name, fn, store, q, cfg, doc_ids):
    with obs.span(f"kb.search.{name}", **{"kb.pool": max(cfg.k * 5, cfg.pool), "kb.doc_filter": doc_ids}) as sp:
        rows = fn(store, q, cfg, doc_ids)
        obs.set_attrs(sp, **{"kb.n_out": len(rows), "kb.top_chunk_ids": [r["chunk_id"] for r in rows[:10]]})
        return rows


def _traced_rerank(q, rows, n):
    with obs.span("kb.search.rerank", **{"kb.rerank_n": min(n, len(rows)), "kb.rerank_model": "BAAI/bge-reranker-base",
                                         "kb.input_chunk_ids": [r["chunk_id"] for r in rows[:n]]}) as sp:
        out = _rerank(q, rows, n)
        obs.set_attrs(sp, **{"kb.top_chunk_ids": [r["chunk_id"] for r in out[:10]],
                             "kb.top_score": out[0]["score"] if out else None})
        return out


def _bm25_pool(store: Store, q: str, cfg: SearchConfig, doc_ids: list[str]) -> list[dict]:
    fq = fts_query(q)
    if not fq:
        return []
    where, args = ["chunks_fts MATCH ?"], [fq]
    if doc_ids:
        where.append("c.doc_id IN (%s)" % ",".join("?" * len(doc_ids)))
        args += doc_ids
    where.append("c.kind IN (%s)" % ",".join("?" * len(cfg.kinds)))
    args += list(cfg.kinds)
    pool = max(cfg.k * 5, cfg.pool)
    sql = ("SELECT c.*, bm25(chunks_fts) AS bm FROM chunks_fts JOIN chunks c ON c.rowid = chunks_fts.rowid "
           f"WHERE {' AND '.join(where)} ORDER BY bm LIMIT {pool}")
    rows = [dict(r) for r in store.conn.execute(sql, args)]
    for r in rows:
        r["score"] = -r.pop("bm")  # higher is better
        r["route"] = "bm25"
    return rows


def _vector_pool(store: Store, q: str, cfg: SearchConfig, doc_ids: list[str]) -> list[dict]:
    from . import dense
    import numpy as np
    ids, mat = dense.matrix(store)
    sims = mat @ dense.embed_query(q)
    allowed = {r[0] for r in store.conn.execute(
        "SELECT rowid FROM chunks WHERE kind IN (%s)" % ",".join("?" * len(cfg.kinds))
        + (" AND doc_id IN (%s)" % ",".join("?" * len(doc_ids)) if doc_ids else ""),
        list(cfg.kinds) + list(doc_ids))}
    out = []
    for j in np.argsort(-sims):
        rid = int(ids[j])
        if rid in allowed:
            r = store.chunk_by_rowid(rid)
            r["score"] = float(sims[j])
            r["route"] = "vector"
            out.append(r)
            if len(out) >= max(cfg.k * 5, cfg.pool):
                break
    return out


def _rrf(a: list[dict], b: list[dict], k: int) -> list[dict]:
    score: dict[int, float] = {}
    rows: dict[int, dict] = {}
    for lst, name in ((a, "bm25"), (b, "vector")):
        for i, r in enumerate(lst):
            score[r["rowid"]] = score.get(r["rowid"], 0.0) + 1.0 / (k + i + 1)
            d = rows.setdefault(r["rowid"], dict(r))
            d[f"rank_{name}"] = i + 1
    out = sorted(rows.values(), key=lambda r: -score[r["rowid"]])
    for r in out:
        r["score"] = score[r["rowid"]]
        r["route"] = "rrf"
    return out


def _rerank(q: str, rows: list[dict], n: int) -> list[dict]:
    from . import dense
    head, tail = rows[:n], rows[n:]
    if not head:
        return rows
    sc = dense.rerank_scores(q, [dense.passage_text(r) for r in head])
    for r, s, i in zip(head, sc, range(len(head))):
        r["rank_pre_rerank"] = i + 1
        r["score"] = s
        r["route"] = "rerank"
    head.sort(key=lambda r: -r["score"])
    return head + tail


def _route(store: Store, q: str, rows: list[dict], doc_ids: list[str]) -> list[dict]:
    nos = set(CLAUSE_IN_QUERY.findall(q))
    if not nos:
        return rows
    routed = [r for r in rows if r["clause_no"] in nos and r["kind"] in ("clause", "table")]
    have = {r["rowid"] for r in routed}
    for no in nos:  # also fetch exact clause chunks the retriever did not surface
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
    return routed + [r for r in rows if r["rowid"] not in have]
