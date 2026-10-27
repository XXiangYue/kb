"""Standards-text references: keep NO verbatim standards text in the repository.

Committed artifacts (question files, per-question results, traces, reports) replace every
run of standards text with a reference token

    ⟦std:<doc_id>|<clause>|<start>+<length>|<sha256-16>⟧

where <start>/<length> index the document text produced by `python -m tushen_kb.cli ingest`
from the sha256-pinned PDFs in corpus/manifest.json (table doc_text in data/tushen.sqlite),
<clause> is the clause number of the chunk containing <start> (informational) and
<sha256-16> is the first 16 hex digits of sha256(utf-8 of the span).  `resolve_*` swaps the
tokens back for the text of YOUR local copy and verifies the hash.  Restored spans equal
the original strings up to whitespace (PDF line breaks), which is how every comparison in
this repo treats them (normalize.squash).

Users must obtain the standards themselves (scripts/download_corpus.sh fetches the public
copies; their redistribution rights are unclear, so neither PDFs nor text are committed).

CLI
  python -m tushen_kb.stdref check            # local doc_text sha256 vs corpus/manifest.json
  python -m tushen_kb.stdref scan [--history] [--k 20]   # residual standards-text scan
  python -m tushen_kb.stdref resolve FILE     # print FILE (jsonl/json/md) with tokens resolved
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import re
import sqlite3
import subprocess
import sys
import unicodedata
from bisect import bisect_right
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "tushen.sqlite"
TOKEN = re.compile(r"⟦std:([^|⟧]+)\|([^|⟧]*)\|(\d+)\+(\d+)\|([0-9a-f]{16})(?:\|([^⟧]*))?⟧")
REDACT_MIN = 12   # runs of >= 12 folded chars shared with a standard are replaced
SCAN_K = 20       # residual scan window
_HAN = re.compile(r"[\u3400-\u9fff]")


def _titles() -> tuple[str, ...]:
    try:
        man = json.loads((ROOT / "corpus" / "manifest.json").read_text(encoding="utf-8"))["docs"]
        return tuple(sorted({m["title"] for m in man}, key=len, reverse=True))
    except Exception:
        return ()


ALLOW = _titles()


def sha16(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]


def fold_char(ch: str) -> str:
    """1:1 char fold used for matching only (NFKC when it yields one char, lower-case)."""
    n = unicodedata.normalize("NFKC", ch)
    if len(n) != 1:
        n = ch
    return n.lower()


def folded(text: str, json_escapes: bool = True) -> tuple[str, list[int]]:
    """Drop whitespace (and literal JSON escapes \\n \\t \\r), fold the rest; keep an index map."""
    out, idx = [], []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if json_escapes and ch == "\\" and i + 1 < n and text[i + 1] in "ntr":
            i += 2
            continue
        if ch.isspace():
            i += 1
            continue
        out.append(fold_char(ch))
        idx.append(i)
        i += 1
    return "".join(out), idx


class Reference:
    """The local standards texts (doc_id -> text) plus a k-gram index on the folded text."""

    def __init__(self, texts: dict[str, str], clauses: dict[str, list[tuple[int, str]]] | None = None,
                 k: int = REDACT_MIN):
        self.texts = texts
        self.k = k
        self.clauses = clauses or {}
        self.fold, self.idx, self.index = {}, {}, {}
        for d, t in texts.items():
            f, m = folded(t, json_escapes=False)
            self.fold[d], self.idx[d] = f, m
            for i in range(len(f) - k + 1):
                self.index.setdefault(f[i:i + k], []).append((d, i))
        self.sha = {d: hashlib.sha256(t.encode("utf-8")).hexdigest() for d, t in texts.items()}

    @classmethod
    def from_db(cls, db: str | Path = DB, k: int = REDACT_MIN) -> "Reference":
        p = Path(db)
        if not p.exists():
            raise SystemExit(f"{p} not found: obtain the standards (scripts/download_corpus.sh) and run "
                             "`python -m tushen_kb.cli ingest` first — committed artifacts only hold references")
        c = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
        texts = dict(c.execute("SELECT doc_id, text FROM doc_text"))
        clauses: dict[str, list[tuple[int, str]]] = {}
        for d, s, no in c.execute("SELECT doc_id, char_start, COALESCE(clause_no,'') FROM chunks "
                                  "WHERE kind IN ('clause','table','commentary','text') ORDER BY doc_id, char_start"):
            clauses.setdefault(d, []).append((s, no))
        c.close()
        return cls(texts, clauses, k)

    def clause_at(self, doc: str, pos: int) -> str:
        lst = self.clauses.get(doc) or []
        i = bisect_right([s for s, _ in lst], pos) - 1
        return lst[i][1] if i >= 0 else ""

    # --- redaction ---------------------------------------------------------------------
    def runs(self, text: str, min_len: int | None = None, json_escapes: bool = True):
        """Greedy maximal runs (in folded space) shared with a standard: yields
        (text_start, text_end, doc, doc_start, doc_end)."""
        min_len = min_len or self.k
        f, m = folded(text, json_escapes)
        i, n, k = 0, len(f), self.k
        while i <= n - k:
            cands = self.index.get(f[i:i + k])
            if not cands:
                i += 1
                continue
            best = None
            for d, j in cands[:200]:
                F = self.fold[d]
                L = k
                while i + L < n and j + L < len(F) and f[i + L] == F[j + L]:
                    L += 1
                if best is None or L > best[0]:
                    best = (L, d, j)
            L, d, j = best
            if L >= min_len:
                dm = self.idx[d]
                yield m[i], m[i + L - 1] + 1, d, dm[j], dm[j + L - 1] + 1
                i += L
            else:
                i += 1

    def token(self, d: str, s: int, e: int, orig: str) -> str:
        span = self.texts[d][s:e]
        head = f"⟦std:{d}|{self.clause_at(d, s)}|{s}+{e - s}|{sha16(span)}"
        if orig == span:
            return head + "⟧"
        return head + "|" + _edits(span, orig) + "⟧"

    def redact(self, text: str, min_len: int | None = None, json_escapes: bool = False) -> tuple[str, int]:
        out, last, nred = [], 0, 0
        for ts, te, d, ds, de in self.runs(text, min_len, json_escapes):
            out.append(text[last:ts])
            tok = self.token(d, ds, de, text[ts:te])
            assert self._resolve_one(tok) == text[ts:te], "exact restore failed"
            out.append(tok)
            nred += te - ts
            last = te
        out.append(text[last:])
        return "".join(out), nred

    # --- resolution --------------------------------------------------------------------
    def _sub(self, mo) -> str:
        d, _, a, L, h, ed = mo.groups()
        a, L = int(a), int(L)
        span = self.texts.get(d, "")[a:a + L]
        if sha16(span) != h:
            raise ValueError(f"standards text mismatch for {mo.group(0)}: your local {d} text differs from "
                             f"the pinned one (python -m tushen_kb.stdref check)")
        return _apply_edits(span, ed) if ed else span

    def _resolve_one(self, tok: str) -> str:
        return self._sub(TOKEN.fullmatch(tok))

    def resolve(self, s: str) -> str:
        return TOKEN.sub(self._sub, s) if "⟦std:" in s else s

    def resolve_obj(self, o):
        if isinstance(o, str):
            return self.resolve(o)
        if isinstance(o, list):
            return [self.resolve_obj(x) for x in o]
        if isinstance(o, dict):
            return {k: self.resolve_obj(v) for k, v in o.items()}
        return o

    # --- residual scan -----------------------------------------------------------------
    def scan_text(self, text: str, k: int = SCAN_K) -> dict:
        """Count k-char folded windows of `text` that occur verbatim in a standard, and the
        number of distinct text chars they cover."""
        text = unescape(text)
        for t in ALLOW:                      # standard titles are names, not text
            text = text.replace(t, "|")
        f, _ = folded(text)
        grams = self._grams(k)
        hits, covered = 0, [False] * len(f)
        for i in range(len(f) - k + 1):
            w = f[i:i + k]
            if w in grams and _HAN.search(w):
                hits += 1
                for j in range(i, i + k):
                    covered[j] = True
        return {"windows": hits, "chars": sum(covered), "folded_len": len(f)}

    @lru_cache(maxsize=4)
    def _grams(self, k: int) -> frozenset:
        return frozenset(F[i:i + k] for F in self.fold.values() for i in range(len(F) - k + 1))


def _edits(span: str, orig: str) -> str:
    """Exact-restore script from the standard's span to the original string (they agree on the
    folded non-whitespace chars).  "S" = start from the whitespace-free span; s<i>.<hex> = replace
    char i (e.g. ASCII vs full-width punctuation); w<i>.<hex> = insert whitespace char at output i.
    Only whitespace / punctuation code points are stored, never text."""
    sq, _ = folded(span, json_escapes=False)
    raw_sq = "".join(ch for ch in span if not ch.isspace())
    items, k = ["S"], 0
    for p, ch in enumerate(orig):
        if ch.isspace():
            items.append(f"w{p}.{ord(ch):x}")
        else:
            if raw_sq[k] != ch:
                items.append(f"s{k}.{ord(ch):x}")
            k += 1
    return ",".join(items)


def _apply_edits(span: str, ed: str) -> str:
    items = ed.split(",")
    assert items[0] == "S", ed
    chars = [ch for ch in span if not ch.isspace()]
    ws = []
    for it in items[1:]:
        pos, _, hx = it[1:].partition(".")
        if it[0] == "s":
            chars[int(pos)] = chr(int(hx, 16))
        else:
            ws.append((int(pos), chr(int(hx, 16))))
    for pos, ch in ws:          # ascending output positions
        chars.insert(pos, ch)
    return "".join(chars)


_UESC = re.compile(r"\\u([0-9a-fA-F]{4})")


def unescape(text: str) -> str:
    return _UESC.sub(lambda m: chr(int(m.group(1), 16)), text)


@lru_cache(maxsize=1)
def default_reference() -> Reference:
    return Reference.from_db()


def resolve_obj(o):
    """Resolve all reference tokens in a parsed JSON object against data/tushen.sqlite."""
    return default_reference().resolve_obj(o)


def loads(s: str):
    """json.loads + resolve standards references (only when the line holds one)."""
    o = json.loads(s)
    return resolve_obj(o) if "⟦std:" in s else o


def load_jsonl(path: str | Path) -> list:
    """Read a committed jsonl file and resolve its standards references."""
    rows = [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]
    if any("⟦std:" in json.dumps(r, ensure_ascii=False) for r in rows):
        rows = resolve_obj(rows)
    return rows


def blob_text(data: bytes) -> str | None:
    if data[:2] == b"\x1f\x8b":
        try:
            data = gzip.decompress(data)
        except Exception:
            return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _git(*a, cwd=ROOT) -> bytes:
    return subprocess.check_output(["git", *a], cwd=cwd)


def scan_history(ref: Reference, repo: Path = ROOT, k: int = SCAN_K, all_refs: bool = True) -> dict:
    """Scan every blob reachable from any ref (all history) plus all commit messages."""
    objs = _git("rev-list", "--objects", "--all" if all_refs else "HEAD", cwd=repo).decode().split("\n")
    seen, per_blob, n_blobs, n_bin = set(), [], 0, 0
    paths = {}
    for line in objs:
        if not line.strip():
            continue
        sha, _, path = line.partition(" ")
        paths.setdefault(sha, path)
    batch = subprocess.run(["git", "cat-file", "--batch-check"], cwd=repo, input="\n".join(paths).encode(),
                           capture_output=True, check=True).stdout.decode().split("\n")
    blobs = [l.split()[0] for l in batch if l.endswith(tuple("0123456789")) and " blob " in l]
    for b in blobs:
        if b in seen:
            continue
        seen.add(b)
        t = blob_text(_git("cat-file", "blob", b, cwd=repo))
        if t is None:
            n_bin += 1
            continue
        n_blobs += 1
        r = ref.scan_text(t, k)
        if r["windows"]:
            per_blob.append({"blob": b, "path": paths[b], **r})
    msgs = _git("log", "--all", "--format=%H%x00%B%x01", cwd=repo).decode().split("\x01")
    msg_hits = []
    for m in msgs:
        h, _, body = m.strip("\n").partition("\x00")
        if h and ref.scan_text(body, k)["windows"]:
            msg_hits.append(h)
    return {"k": k, "text_blobs_scanned": n_blobs, "binary_blobs_skipped": n_bin, "commits_msgs_scanned": len(msgs) - 1,
            "blobs_with_hits": len(per_blob), "total_windows": sum(x["windows"] for x in per_blob),
            "total_chars": sum(x["chars"] for x in per_blob), "commit_msgs_with_hits": msg_hits,
            "hits": sorted(per_blob, key=lambda x: -x["chars"])}


def scan_worktree(ref: Reference, k: int = SCAN_K, repo: Path | str = ROOT) -> dict:
    """Scan tracked + untracked (non-ignored) files in the work tree, and the STAGED (index) content
    of every path with staged changes, so a pre-commit hook sees exactly what would be committed."""
    repo = Path(repo)
    files = _git("ls-files", "--cached", "--others", "--exclude-standard", cwd=repo).decode().split("\n")
    staged = [f for f in _git("diff", "--cached", "--name-only", "--diff-filter=ACMR", cwd=repo).decode().split("\n") if f]
    res, tot, totlen, n = [], 0, 0, 0

    def one(label, data):
        nonlocal tot, totlen, n
        t = blob_text(data)
        if t is None:
            return
        n += 1
        r = ref.scan_text(t, k)
        tot += r["chars"]
        totlen += r["folded_len"]
        if r["windows"]:
            res.append({"path": label, **r})

    for f in sorted({f for f in files if f}):
        if (repo / f).is_file():
            one(f, (repo / f).read_bytes())
    for f in staged:
        one(f"{f} (staged)", _git("show", f":{f}", cwd=repo))
    return {"k": k, "files_scanned": n, "files_with_hits": len(res), "total_windows": sum(x["windows"] for x in res),
            "total_chars": tot, "total_folded_chars": totlen, "hits": sorted(res, key=lambda x: -x["chars"])}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check")
    s = sub.add_parser("scan")
    s.add_argument("--history", action="store_true", help="all blobs + commit messages of all refs")
    s.add_argument("--k", type=int, default=SCAN_K)
    s.add_argument("--extra-ref", action="append", default=[], help="extra standards text file(s) to scan against")
    s.add_argument("--json", action="store_true")
    r = sub.add_parser("resolve")
    r.add_argument("file")
    ap.add_argument("--db", default=str(DB), help="local standards index (default data/tushen.sqlite)")
    a = ap.parse_args(argv)
    ref = Reference.from_db(a.db)
    if a.cmd == "check":
        man = json.loads((ROOT / "corpus" / "manifest.json").read_text(encoding="utf-8"))["docs"]
        ok = True
        for m in man:
            got = ref.sha.get(m["doc_id"])
            st = "OK" if got == m.get("text_sha256") else "MISMATCH"
            ok &= st == "OK"
            print(m["doc_id"], "text_sha256", st)
        sys.exit(0 if ok else 1)
    if a.cmd == "scan":
        if a.extra_ref:
            texts = dict(ref.texts)
            for p in a.extra_ref:
                texts[f"extra:{Path(p).name}"] = Path(p).read_text(encoding="utf-8")
            ref = Reference(texts, ref.clauses)
        out = scan_history(ref, k=a.k) if a.history else scan_worktree(ref, k=a.k)
        if a.json:
            print(json.dumps(out, ensure_ascii=False, indent=1))
        else:
            print(json.dumps({k: v for k, v in out.items() if k != "hits"}, ensure_ascii=False))
            for h in out["hits"][:50]:
                print(h)
        bad = out["total_windows"] or out.get("commit_msgs_with_hits")
        sys.exit(1 if bad else 0)
    if a.cmd == "resolve":
        p = Path(a.file)
        t = p.read_text(encoding="utf-8")
        if p.suffix == ".jsonl":
            for l in t.splitlines():
                if l.strip():
                    print(json.dumps(ref.resolve_obj(json.loads(l)), ensure_ascii=False))
        elif p.suffix == ".json":
            print(json.dumps(ref.resolve_obj(json.loads(t)), ensure_ascii=False, indent=1))
        else:
            print(ref.resolve(t))


if __name__ == "__main__":
    main()
