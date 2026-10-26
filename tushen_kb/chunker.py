"""Clause-level chunking of standard documents.

Chunk kinds:
  clause      normative clause text (e.g. "3.2.1 ...")
  commentary  条文说明 (inline 【条文说明】x.x.x blocks, or a separate 条文说明 section)
  table       a table, kept as its own chunk (rows "a | b | c"), linked to its clause
  text        normative body text not under any clause number (rare)

Every chunk satisfies ``doc_text[chunk.start:chunk.end] == chunk.text`` where
doc_text is the stored normalized document text (see pdfparse.assemble).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .pdfparse import Line

CLAUSE_RE = re.compile(r"^((?:[A-Z]|\d{1,2})(?:\.\d{1,3}){1,4})(?![\d.、，,．])\s*(?=[\u4e00-\u9fff（(“\"【])")
CHAPTER_RE = re.compile(r"^(\d{1,2}|附录\s*[A-Z])\s*([\u4e00-\u9fff][^。；，,;]{0,30})$")
COMMENTARY_INLINE = re.compile(r"^【条文说明】\s*((?:[A-Z]|\d{1,2})(?:\.\d{1,3}){1,4})?")
DOT_LEADER = re.compile(r"(\.{4,}|…{2,}|·{4,}|-{6,})")
SENT_PUNCT = re.compile(r"[。；，,;：:]")
TABLE_TITLE = re.compile(r"^表[A-Z0-9][A-Z0-9. -]*")
BODY_START = re.compile(r"^1\s*(总则|范围|总 则)")
END_MARKERS = ("本规范用词说明", "本标准用词说明", "引用标准名录", "本规程用词说明", "参考文献")
MAX_CHARS = 1200
WINDOW = 800
OVERLAP = 100  # ~12% overlap


@dataclass
class Chunk:
    chunk_id: str
    doc_id: str
    kind: str
    clause_no: str | None
    text: str
    start: int
    end: int
    page_start: int
    page_end: int
    section_path: str = ""
    is_mandatory: bool = False
    mandatory_source: str = ""
    ref_clause: str | None = None
    table_title: str | None = None
    part: int = 0
    meta: dict = field(default_factory=dict)


def clause_key(no: str) -> tuple:
    out = []
    for p in no.split("."):
        out.append(100 + ord(p) if p.isalpha() else int(p))
    return tuple(out)


def _is_heading(line: Line, nxt: Line | None) -> re.Match | None:
    t = line.text
    if len(t) > 40 or SENT_PUNCT.search(t) or DOT_LEADER.search(t):
        return None
    m = CHAPTER_RE.match(t)
    if m:
        # numbered list items ("3 建筑内部…") look like chapter headings; require bold
        # or a short title followed by a clause/section of that chapter.
        no = m.group(1).replace(" ", "")
        if line.bold_ratio >= 0.5:
            return m
        if len(m.group(2)) <= 12 and nxt is not None and nxt.text.startswith(no + "."):
            return m
        return None
    m = re.match(r"^((?:[A-Z]|\d{1,2})(?:\.\d{1,3}){1,3})\s*([\u4e00-\u9fff][^。；，,;：]{0,30})$", t)
    if m and (line.bold_ratio >= 0.5 or len(m.group(2)) <= 15):
        return m
    return None


def find_body_start(lines: list[Line]) -> int:
    for i, ln in enumerate(lines):
        if ln.kind == "body" and BODY_START.match(ln.text) and not DOT_LEADER.search(ln.text) and len(ln.text) < 20:
            nxt = next((l for l in lines[i + 1:i + 3] if l.kind == "body"), None)
            if nxt is None:
                continue
            if len(nxt.text) > 25 or "。" in nxt.text or re.match(r"^1\.0\.1|^1\.1", nxt.text):
                return i
    return 0


class _Builder:
    def __init__(self, doc_id: str, doc_text: str, all_mandatory: bool, mandatory_source: str):
        self.doc_id = doc_id
        self.doc_text = doc_text
        self.all_mandatory = all_mandatory
        self.mandatory_source = mandatory_source
        self.chunks: list[Chunk] = []
        self.cur: dict | None = None
        self.counts: dict[tuple, int] = {}

    def open(self, kind: str, clause_no: str | None, section: str, ref: str | None = None, title: str | None = None):
        self.close()
        self.cur = {"kind": kind, "clause_no": clause_no, "section": section, "ref": ref, "title": title, "lines": []}

    def add(self, ln: Line):
        if self.cur is None:
            return False
        self.cur["lines"].append(ln)
        return True

    def close(self):
        c = self.cur
        self.cur = None
        if not c or not c["lines"]:
            return
        lines = c["lines"]
        pieces = [lines]
        total = lines[-1].end - lines[0].start
        if c["kind"] != "table" and total > MAX_CHARS:
            pieces = []
            i = 0
            while i < len(lines):
                j = i
                while j < len(lines) and lines[j].end - lines[i].start < WINDOW:
                    j += 1
                j = max(j, i + 1)
                pieces.append(lines[i:j])
                if j >= len(lines):
                    break
                # step back for overlap
                k = j
                while k > i + 1 and lines[j - 1].end - lines[k - 1].start < OVERLAP:
                    k -= 1
                i = k if k < j else j
        for piece in pieces:
            s, e = piece[0].start, piece[-1].end
            body_lines = piece
            if c["kind"] == "commentary":
                mand, src = False, "commentary"
            elif self.all_mandatory:
                mand, src = True, self.mandatory_source
            else:
                # char-weighted bold ratio over body lines, excluding heading lines
                rs = [(l.bold_ratio, len(l.text)) for l in body_lines
                      if l.kind == "body" and _is_heading(l, None) is None]
                tot = sum(n for _, n in rs)
                ratio = sum(r * n for r, n in rs) / tot if (tot and c["kind"] == "clause") else 0.0
                mand, src = (ratio >= 0.6), ("font:bold" if ratio >= 0.6 else "")
            key = (c["kind"], c["clause_no"] or c["section"] or "-")
            part = self.counts.get(key, 0)
            self.counts[key] = part + 1
            cid = f"{self.doc_id}:{c['kind']}:{c['clause_no'] or c['section'] or '-'}:{part}"
            self.chunks.append(Chunk(
                chunk_id=cid, doc_id=self.doc_id, kind=c["kind"], clause_no=c["clause_no"],
                text=self.doc_text[s:e], start=s, end=e,
                page_start=piece[0].page_no, page_end=piece[-1].page_no,
                section_path=c["section"], is_mandatory=mand, mandatory_source=src,
                ref_clause=c["ref"], table_title=c["title"], part=part,
            ))


def chunk_document(doc_id: str, lines: list[Line], doc_text: str, all_mandatory: bool = False,
                   mandatory_source: str = "") -> list[Chunk]:
    b = _Builder(doc_id, doc_text, all_mandatory, mandatory_source)
    start = find_body_start(lines)
    chapter: str = ""
    section: list[str] = []
    last_key: tuple = ()
    commentary_mode = False
    skipping = False
    cur_clause: str | None = None
    cur_kind = "clause"
    cur_table: int | None = None
    pending: tuple[str, Line] | None = None  # GB/T 1.1 style: "5.3 底坑" heading + unnumbered paragraph = clause 5.3
    i = start
    while i < len(lines):
        ln = lines[i]
        nxt = lines[i + 1] if i + 1 < len(lines) else None
        t = ln.text
        sq = t.replace(" ", "")
        # tables -------------------------------------------------------
        if ln.kind == "table":
            if skipping:
                i += 1
                continue
            if cur_table != ln.table_index:
                resume = (cur_kind, cur_clause)
                b.open("table", cur_clause, "/".join(section), title=ln.table_title)
                cur_table = ln.table_index
            b.add(ln)
            if nxt is None or nxt.kind != "table" or nxt.table_index != ln.table_index:
                b.close()
                cur_table = None
                # continuation of the interrupted clause becomes a new part
                if resume[1] is not None:
                    b.open(resume[0], resume[1], "/".join(section), ref=resume[1] if resume[0] == "commentary" else None)
            i += 1
            continue
        # section markers ---------------------------------------------
        if sq in ("条文说明", "附：条文说明", "附:条文说明"):
            b.close()
            commentary_mode, skipping = True, False
            last_key, cur_clause = (), None
            i += 1
            continue
        if any(sq.startswith(m) for m in END_MARKERS) and len(sq) < 20:
            b.close()
            skipping = True
            cur_clause = None
            i += 1
            continue
        if skipping:
            i += 1
            continue
        # appendix headings / table titles ------------------------------
        if re.fullmatch(r"附录\s*[A-Z]", sq):
            b.close()
            cur_clause, pending = None, None
            chapter, section = sq[-1], [sq]
            last_key = ()
            i += 1
            continue
        if re.fullmatch(r"[（(](规范性|资料性|规范性附录|资料性附录)[）)]", sq):
            i += 1
            continue
        if TABLE_TITLE.match(sq) and nxt is not None and nxt.kind == "table":
            # the title is stored on the table chunk; don't let it leak into clause text
            b.close()
            i += 1
            continue
        # inline commentary ------------------------------------------
        m = COMMENTARY_INLINE.match(t)
        if m:
            ref = m.group(1) or cur_clause
            b.open("commentary", ref, "/".join(section), ref=ref)
            cur_kind, cur_clause = "commentary", ref
            b.add(ln)
            i += 1
            continue
        # headings ------------------------------------------------------
        h = _is_heading(ln, nxt)
        if h:
            no = h.group(1).replace(" ", "")
            b.close()
            cur_clause = None
            pending = (no, ln) if "." in no else None
            if CHAPTER_RE.match(t) and "." not in no:
                chapter = no
                section = [t]
            else:
                depth = no.count(".")
                section = section[:depth] + [t]
            i += 1
            continue
        # clause start ------------------------------------------------
        m = CLAUSE_RE.match(t)
        if m:
            no = m.group(1)
            k = clause_key(no)
            top_ok = (not chapter) or (str(k[0]) == chapter or no[0] == chapter[-1:] or
                                      (chapter.isdigit() and k[0] in (int(chapter), int(chapter) + 1)) or k[0] >= 100)
            if top_ok and (not last_key or k > last_key):
                last_key = k
                pending = None
                kind = "commentary" if commentary_mode else "clause"
                b.open(kind, no, "/".join(section), ref=no if commentary_mode else None)
                cur_kind, cur_clause = kind, no
                b.add(ln)
                i += 1
                continue
        # continuation -------------------------------------------------
        if b.cur is None and pending is not None and not commentary_mode:
            no, hl = pending
            k = clause_key(no)
            if not last_key or k > last_key:
                last_key = k
            b.open("clause", no, "/".join(section))
            cur_kind, cur_clause = "clause", no
            b.add(hl)
        pending = None if b.cur is not None else pending
        if not b.add(ln):
            b.open("commentary" if commentary_mode else "text", None, "/".join(section))
            cur_kind = "commentary" if commentary_mode else "text"
            b.add(ln)
        i += 1
    b.close()
    return b.chunks
