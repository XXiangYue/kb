"""Verbatim citation extraction and verification.

A citation is valid only if its quote is a substring of the stored chunk text
(compared after whitespace removal; the stored text is already normalized).
Verification also checks the chunk's char offsets point into the stored
document text, so a citation can always be traced back to file/page/offset.
"""
from __future__ import annotations

import re

from .normalize import normalize, squash
from .tokenize import content_tokens

_SENT = re.compile(r"[^。；;！？!?\n]+[。；;！？!?]?")


def verify_quote(quote: str, source_text: str) -> tuple[bool, int, int]:
    """Return (ok, start, end) where start/end are offsets into source_text."""
    q, _ = squash(normalize(quote or ""))
    if len(q) < 4:  # too short to be a meaningful verbatim citation
        return False, -1, -1
    s, idx = squash(source_text)
    pos = s.find(q)
    if pos < 0:
        return False, -1, -1
    return True, idx[pos], idx[pos + len(q) - 1] + 1


def verify_citation(cit: dict, chunk: dict, doc_text: str) -> bool:
    ok, s, e = verify_quote(cit.get("quote", ""), chunk["text"])
    if not ok:
        return False
    # chunk offsets must index into the stored normalized document text
    if doc_text[chunk["char_start"]:chunk["char_end"]] != chunk["text"]:
        return False
    abs_s, abs_e = chunk["char_start"] + s, chunk["char_start"] + e
    q_sq, _ = squash(normalize(cit["quote"]))
    if squash(doc_text[abs_s:abs_e])[0] != q_sq:
        return False
    # if the citation claims absolute offsets, they must point at the quote itself
    if "char_start" in cit or "char_end" in cit:
        cs, ce = cit.get("char_start", -1), cit.get("char_end", -1)
        if not (0 <= cs < ce <= len(doc_text)) or squash(doc_text[cs:ce])[0] != q_sq:
            return False
    return True


def locate(cit: dict, chunk: dict) -> tuple[int, int]:
    """Absolute [start, end) offsets of a quote in the stored document text."""
    ok, s, e = verify_quote(cit["quote"], chunk["text"])
    return (chunk["char_start"] + s, chunk["char_start"] + e) if ok else (-1, -1)


def pick_quote(query: str, chunk_text: str, max_len: int = 160) -> str:
    """Choose the sentence of the chunk with the highest query-token overlap
    (verbatim slice of chunk_text, newlines removed)."""
    qt = set(content_tokens(query))
    best, best_score = None, -1.0
    flat = chunk_text.replace("\n", "")
    for m in _SENT.finditer(flat):
        sent = m.group(0).strip()
        if len(sent) < 6:
            continue
        st = set(content_tokens(sent))
        score = len(qt & st) + (0.001 * min(len(sent), 80))
        if score > best_score:
            best, best_score = sent, score
    if best is None:
        best = flat.strip()
    return best[:max_len]


def format_ref(doc: dict, chunk: dict) -> str:
    pages = f"{chunk['page_start']}" if chunk["page_start"] == chunk["page_end"] else f"{chunk['page_start']}-{chunk['page_end']}"
    where = {"clause": f"第 {chunk['clause_no']} 条", "commentary": f"第 {chunk['clause_no']} 条条文说明",
             "table": f"{chunk.get('table_title') or '表格'}"}.get(chunk["kind"], "正文")
    if chunk["kind"] != "clause" and chunk["clause_no"] is None and chunk["kind"] != "table":
        where = "正文"
    return f"《{doc.get('title')}》{doc.get('std_code')} {where}，PDF 第 {pages} 页"
