"""Block 2: dense retrieval (bge-small-zh-v1.5) and cross-encoder rerank
(bge-reranker-base).  CPU only, small batches, models loaded lazily from
models/ (downloaded by scripts/download_models.sh, never committed).
Vectors are stored as float32 BLOBs in the `vectors` table and scored with
numpy cosine (normalized dot product)."""
from __future__ import annotations

import os
import threading
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
EMB_MODEL = "BAAI/bge-small-zh-v1.5"
RERANK_MODEL = "BAAI/bge-reranker-base"
# bge-zh v1.5 retrieval instruction for short queries (documented by BAAI)
QUERY_INSTRUCTION = "为这个句子生成表示以用于检索相关文章："
MAX_LEN = 512
_lock = threading.Lock()
_cache: dict = {}


class VectorsMissing(RuntimeError):
    """Vector index not built for this store (HTTP 503)."""


def _model_dir(name: str) -> Path:
    p = Path(os.environ.get("TUSHEN_MODELS", ROOT / "models")) / name
    if not (p / "model.safetensors").exists():
        raise VectorsMissing(f"{p} 缺失：先运行 scripts/download_models.sh")
    return p


def _torch():
    import torch
    torch.set_num_threads(int(os.environ.get("TUSHEN_THREADS", "4")))
    return torch


def embedder():
    with _lock:
        if "emb" not in _cache:
            torch = _torch()
            from transformers import AutoModel, AutoTokenizer
            d = _model_dir(EMB_MODEL)
            tok = AutoTokenizer.from_pretrained(d)
            m = AutoModel.from_pretrained(d).eval()
            _cache["emb"] = (tok, m, torch)
        return _cache["emb"]


def reranker():
    with _lock:
        if "rr" not in _cache:
            torch = _torch()
            from transformers import AutoModelForSequenceClassification, AutoTokenizer
            d = _model_dir(RERANK_MODEL)
            tok = AutoTokenizer.from_pretrained(d)
            m = AutoModelForSequenceClassification.from_pretrained(d).eval()
            _cache["rr"] = (tok, m, torch)
        return _cache["rr"]


def embed(texts: list[str], batch: int = 16) -> np.ndarray:
    tok, m, torch = embedder()
    out = []
    for i in range(0, len(texts), batch):
        enc = tok(texts[i:i + batch], padding=True, truncation=True, max_length=MAX_LEN, return_tensors="pt")
        with torch.inference_mode():
            cls = m(**enc).last_hidden_state[:, 0]
            cls = torch.nn.functional.normalize(cls, dim=-1)
        out.append(cls.numpy().astype(np.float32))
    return np.concatenate(out) if out else np.zeros((0, 512), np.float32)


def embed_query(q: str) -> np.ndarray:
    return embed([QUERY_INSTRUCTION + q])[0]


def rerank_scores(q: str, passages: list[str], batch: int = 8) -> list[float]:
    tok, m, torch = reranker()
    out: list[float] = []
    for i in range(0, len(passages), batch):
        enc = tok([q] * len(passages[i:i + batch]), passages[i:i + batch], padding=True, truncation="only_second",
                  max_length=MAX_LEN, return_tensors="pt")
        with torch.inference_mode():
            out.extend(m(**enc).logits.view(-1).float().tolist())
    return out


def passage_text(c: dict) -> str:
    """Same text the BM25 index sees (table title prepended for tables)."""
    return c["text"] if c["kind"] != "table" else f"{c.get('table_title') or ''}\n{c['text']}"


def build_vectors(store, batch: int = 16) -> dict:
    t0 = time.time()
    rows = store.chunks()
    have = {r[0] for r in store.conn.execute("SELECT chunk_rowid FROM vectors WHERE model=?", (EMB_MODEL,))}
    todo = [r for r in rows if r["rowid"] not in have]
    for i in range(0, len(todo), batch * 4):
        part = todo[i:i + batch * 4]
        vecs = embed([passage_text(r) for r in part], batch=batch)
        with store.conn:
            store.conn.executemany("INSERT OR REPLACE INTO vectors VALUES (?,?,?,?)",
                                   [(r["rowid"], EMB_MODEL, int(v.shape[0]), v.tobytes()) for r, v in zip(part, vecs)])
    _cache.pop(("mat", store.path), None)
    return {"model": EMB_MODEL, "n_chunks": len(rows), "embedded_now": len(todo), "seconds": round(time.time() - t0, 1)}


def matrix(store):
    key = ("mat", store.path)
    if key not in _cache:
        rs = list(store.conn.execute("SELECT chunk_rowid, vec FROM vectors WHERE model=? ORDER BY chunk_rowid",
                                     (EMB_MODEL,)))
        if not rs:
            raise VectorsMissing("向量未构建：先运行 `python -m tushen_kb.cli embed`")
        ids = np.array([r[0] for r in rs])
        mat = np.stack([np.frombuffer(r[1], dtype=np.float32) for r in rs])
        _cache[key] = (ids, mat)
    return _cache[key]
