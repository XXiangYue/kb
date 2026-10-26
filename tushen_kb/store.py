"""SQLite storage: docs, normalized document text, page spans, chunks, FTS5
index, and a (block-2) vector table.  One file, no server."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from . import CHUNKER_VERSION, PARSER_VERSION, TOKENIZER_VERSION
from .chunker import Chunk
from .tokenize import fts_doc

SCHEMA = """
CREATE TABLE IF NOT EXISTS docs (
  doc_id TEXT PRIMARY KEY, std_code TEXT, title TEXT, file TEXT, sha256 TEXT,
  url TEXT, license TEXT, n_pages INTEGER, all_mandatory INTEGER, mandatory_source TEXT,
  parser_version TEXT, chunker_version TEXT, tokenizer_version TEXT, stats TEXT
);
CREATE TABLE IF NOT EXISTS doc_text (doc_id TEXT PRIMARY KEY, text TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS pages (
  doc_id TEXT, page_no INTEGER, char_start INTEGER, char_end INTEGER,
  PRIMARY KEY (doc_id, page_no)
);
CREATE TABLE IF NOT EXISTS chunks (
  rowid INTEGER PRIMARY KEY AUTOINCREMENT,
  chunk_id TEXT UNIQUE, doc_id TEXT, kind TEXT, clause_no TEXT, ref_clause TEXT,
  section_path TEXT, is_mandatory INTEGER, mandatory_source TEXT,
  page_start INTEGER, page_end INTEGER, char_start INTEGER, char_end INTEGER,
  table_title TEXT, part INTEGER, text TEXT
);
CREATE INDEX IF NOT EXISTS idx_chunks_clause ON chunks(doc_id, clause_no);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
  tokens, tokenize = "unicode61 remove_diacritics 0 tokenchars '.-_'"
);
-- block 2: dense vectors (float32 BLOB) scored with numpy cosine
CREATE TABLE IF NOT EXISTS vectors (
  chunk_rowid INTEGER, model TEXT, dim INTEGER, vec BLOB, PRIMARY KEY (chunk_rowid, model)
);
"""


class Store:
    def __init__(self, path: str | Path):
        self.path = str(path)
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    def close(self):
        self.conn.close()

    def delete_doc(self, doc_id: str):
        c = self.conn
        rows = [r[0] for r in c.execute("SELECT rowid FROM chunks WHERE doc_id=?", (doc_id,))]
        c.executemany("DELETE FROM chunks_fts WHERE rowid=?", [(r,) for r in rows])
        c.executemany("DELETE FROM vectors WHERE chunk_rowid=?", [(r,) for r in rows])
        for t in ("chunks", "pages", "doc_text", "docs"):
            c.execute(f"DELETE FROM {t} WHERE doc_id=?", (doc_id,))

    def add_doc(self, meta: dict, doc_text: str, page_spans, chunks: list[Chunk], stats: dict):
        c = self.conn
        with c:
            self.delete_doc(meta["doc_id"])
            c.execute("INSERT INTO docs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                meta["doc_id"], meta.get("std_code"), meta.get("title"), meta.get("file"), meta.get("sha256"),
                meta.get("url"), meta.get("license"), meta.get("n_pages"), int(meta.get("all_mandatory", False)),
                meta.get("mandatory_source", ""), PARSER_VERSION, CHUNKER_VERSION, TOKENIZER_VERSION,
                json.dumps(stats, ensure_ascii=False)))
            c.execute("INSERT INTO doc_text VALUES (?,?)", (meta["doc_id"], doc_text))
            c.executemany("INSERT INTO pages VALUES (?,?,?,?)", [(meta["doc_id"], p, s, e) for p, s, e in page_spans])
            for ch in chunks:
                cur = c.execute(
                    "INSERT INTO chunks (chunk_id, doc_id, kind, clause_no, ref_clause, section_path, is_mandatory,"
                    " mandatory_source, page_start, page_end, char_start, char_end, table_title, part, text)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (ch.chunk_id, ch.doc_id, ch.kind, ch.clause_no, ch.ref_clause, ch.section_path, int(ch.is_mandatory),
                     ch.mandatory_source, ch.page_start, ch.page_end, ch.start, ch.end, ch.table_title, ch.part, ch.text))
                index_text = ch.text if ch.kind != "table" else f"{ch.table_title or ''}\n{ch.text}"
                c.execute("INSERT INTO chunks_fts(rowid, tokens) VALUES (?,?)", (cur.lastrowid, fts_doc(index_text)))

    # --- reads ---
    def docs(self) -> list[dict]:
        return [dict(r) for r in self.conn.execute("SELECT * FROM docs")]

    def doc_text(self, doc_id: str) -> str:
        r = self.conn.execute("SELECT text FROM doc_text WHERE doc_id=?", (doc_id,)).fetchone()
        return r[0] if r else ""

    def chunks(self, where: str = "1=1", args: tuple = ()) -> list[dict]:
        return [dict(r) for r in self.conn.execute(f"SELECT * FROM chunks WHERE {where} ORDER BY rowid", args)]

    def chunk_by_rowid(self, rowid: int) -> dict | None:
        r = self.conn.execute("SELECT * FROM chunks WHERE rowid=?", (rowid,)).fetchone()
        return dict(r) if r else None

    def clause_exists(self, clause_no: str, doc_id: str | None = None) -> bool:
        q = "SELECT 1 FROM chunks WHERE clause_no=? AND kind IN ('clause','table')"
        args: tuple = (clause_no,)
        if doc_id:
            q += " AND doc_id=?"
            args += (doc_id,)
        return self.conn.execute(q + " LIMIT 1", args).fetchone() is not None
