"""Ingest PDFs listed in corpus/manifest.json (or explicit paths) into SQLite."""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from pathlib import Path

from .chunker import chunk_document
from .pdfparse import assemble, parse_pdf
from .store import Store

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "corpus" / "manifest.json"


def sha256(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def load_manifest(path: Path = MANIFEST) -> list[dict]:
    return json.loads(Path(path).read_text(encoding="utf-8"))["docs"]


def mandatory_rule(meta: dict, doc_text: str) -> tuple[bool, str]:
    """Document-level mandatory rule; otherwise clause-level font heuristic."""
    if "全部条文必须严格执行" in doc_text.replace("\n", ""):
        return True, "doc:statement"
    code = (meta.get("std_code") or "").upper()
    if code.startswith(("GB ", "GB5", "GB4")) and "/T" not in code:
        return True, "doc:code"
    return False, ""


def ingest_file(store: Store, path: str | Path, meta: dict | None = None) -> dict:
    path = Path(path)
    meta = dict(meta or {})
    meta.setdefault("doc_id", re.sub(r"[^\w\-]", "", path.stem))
    meta.setdefault("file", str(path))
    digest = sha256(path)
    if meta.get("sha256") and meta["sha256"] != digest:
        raise ValueError(f"sha256 mismatch for {path}: {digest} != {meta['sha256']}")
    meta["sha256"] = digest
    parsed = parse_pdf(str(path))
    doc_text, page_spans = assemble(parsed.lines)
    if len(doc_text.strip()) < 200:
        raise ValueError(f"{path}: no usable text layer (scanned PDF?) — OCR 待实现")
    all_m, src = mandatory_rule(meta, doc_text)
    meta.update(n_pages=parsed.n_pages, all_mandatory=all_m, mandatory_source=src)
    chunks = chunk_document(meta["doc_id"], parsed.lines, doc_text, all_mandatory=all_m, mandatory_source=src)
    bad = [c.chunk_id for c in chunks if doc_text[c.start:c.end] != c.text]
    assert not bad, f"offset invariant violated: {bad[:3]}"
    stats = dict(parsed.stats)
    stats["chunks_by_kind"] = dict(Counter(c.kind for c in chunks))
    stats["mandatory_chunks"] = sum(c.is_mandatory for c in chunks)
    store.add_doc(meta, doc_text, page_spans, chunks, stats)
    return {"doc_id": meta["doc_id"], "n_pages": parsed.n_pages, "chars": len(doc_text), **stats}


def ingest_manifest(store: Store, manifest: Path = MANIFEST, root: Path = ROOT) -> list[dict]:
    out = []
    for m in load_manifest(manifest):
        p = root / m["file"]
        if not p.exists():
            out.append({"doc_id": m["doc_id"], "error": f"missing {m['file']} (run scripts/download_corpus.sh)"})
            continue
        out.append(ingest_file(store, p, m))
    return out
