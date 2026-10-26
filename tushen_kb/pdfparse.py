"""PDF -> normalized lines (with font info) using pdfplumber.

Why a custom line builder instead of ``page.extract_text()``: several standard
PDFs (e.g. GB 46034-2025) embed punctuation/digit fonts whose glyph bboxes are
offset vertically, so pdfplumber's top-based clustering splits "6.1.3 检查…，…"
into two broken lines.  We cluster chars by their *baseline* (text matrix f
component), which is font-metric independent.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

from .normalize import normalize

BOLD_FONT = re.compile(r"(bold|hei|FZHT|HT[KJ]|黑)", re.I)
_CJK = re.compile(r"[\u4e00-\u9fff]")
_ALNUM = re.compile(r"[0-9A-Za-z.%]")


@dataclass
class Line:
    text: str
    page_no: int  # 1-based physical page index
    kind: str = "body"  # body | table
    bold_ratio: float = 0.0
    size: float = 0.0
    table_title: str | None = None
    table_index: int | None = None
    # filled in by assemble(): offsets into the stored normalized document text
    start: int = -1
    end: int = -1


@dataclass
class ParsedDoc:
    n_pages: int
    lines: list[Line]
    removed_lines: list[tuple[int, str]] = field(default_factory=list)
    stats: dict = field(default_factory=dict)


def _is_bold(fontname: str) -> bool:
    return bool(BOLD_FONT.search(fontname or ""))


def chars_to_lines(chars: list[dict], page_height: float) -> list[dict]:
    """Cluster pdfplumber char dicts into lines by baseline; returns dicts with
    text, baseline, size, bold_ratio."""
    cs = [c for c in chars if c.get("upright", True) and not c["text"].isspace()]
    if not cs:
        return []
    for c in cs:
        m = c.get("matrix")
        c["_base"] = (page_height - m[5]) if m else c["bottom"]
    cs.sort(key=lambda c: (c["_base"], c["x0"]))
    sizes = sorted(c["size"] for c in cs)
    med = sizes[len(sizes) // 2] or 10.0
    tol = 0.45 * med
    groups: list[list[dict]] = []
    for c in cs:
        if groups and abs(c["_base"] - groups[-1][0]["_base"]) <= tol:
            groups[-1].append(c)
        else:
            groups.append([c])
    out = []
    for g in groups:
        g.sort(key=lambda c: c["x0"])
        buf = []
        prev = None
        for c in g:
            if prev is not None:
                gap = c["x0"] - prev["x1"]
                both_ascii = _ALNUM.match(prev["text"]) and _ALNUM.match(c["text"])
                if (both_ascii and gap > 0.25 * c["size"]) or gap > 1.5 * c["size"]:
                    buf.append(" ")
            buf.append(c["text"])
            prev = c
        cjk = [c for c in g if _CJK.match(c["text"])]
        bold = sum(1 for c in cjk if _is_bold(c.get("fontname", ""))) / len(cjk) if cjk else 0.0
        text = normalize("".join(buf))
        text = re.sub(r"(?<=\d)\. (?=\d)", ".", text)  # "5. 2. 5" -> "5.2.5"
        out.append({
            "text": text,
            "baseline": g[0]["_base"],
            "size": max(c["size"] for c in g),
            "bold_ratio": bold,
        })
    return out


def _accept_table(rows: list[list]) -> bool:
    if not rows or len(rows) < 2:
        return False
    ncol = max(len(r) for r in rows)
    if ncol < 2:
        return False  # pdfplumber often returns 1-column "tables" for page frames
    cells = [c for r in rows for c in r]
    filled = sum(1 for c in cells if c and str(c).strip())
    return filled / max(1, len(cells)) >= 0.4


def _table_rows(page, table, h: float) -> list[list[str]]:
    """Rebuild cell text with the baseline line builder (pdfplumber's own cell
    text suffers from the same punctuation-offset bug)."""
    rows = []
    for row in table.rows:
        cells = []
        for bbox in row.cells:
            if bbox is None:
                cells.append("")
                continue
            x0, top, x1, bottom = bbox
            cs = [c for c in page.chars if c["x0"] >= x0 - 1 and c["x1"] <= x1 + 1
                  and c["top"] >= top - 1 and c["bottom"] <= bottom + 1]
            cells.append("".join(l["text"] for l in chars_to_lines([dict(c) for c in cs], h)))
        rows.append(cells)
    return rows


def _table_lines(rows: list[list]) -> list[str]:
    out = []
    for r in rows:
        cells = [normalize(str(c or "")).replace("\n", "") for c in r]
        out.append(" | ".join(cells))
    return out


_TABLE_TITLE = re.compile(r"^表\s*[A-Z0-9][A-Z0-9.\-]*")


def parse_pdf(path: str, header_frac: float = 0.08, repeat_frac: float = 0.4) -> ParsedDoc:
    import pdfplumber

    raw_pages: list[tuple[int, float, list[dict], list[tuple]]] = []
    with pdfplumber.open(path) as pdf:
        n = len(pdf.pages)
        for i, page in enumerate(pdf.pages):
            h = float(page.height)
            tables = []
            try:
                found = page.find_tables()
            except Exception:  # pragma: no cover - pdfplumber edge cases
                found = []
            for t in found:
                rows = _table_rows(page, t, h)
                if _accept_table(rows):
                    tables.append((t.bbox, rows))
            chars = page.chars
            if tables:
                def inside(c):
                    for (x0, top, x1, bottom), _ in tables:
                        if c["x0"] >= x0 - 1 and c["x1"] <= x1 + 1 and c["top"] >= top - 1 and c["bottom"] <= bottom + 1:
                            return True
                    return False
                chars = [c for c in chars if not inside(c)]
            lines = chars_to_lines(list(chars), h)
            raw_pages.append((i + 1, h, lines, tables))
            page.flush_cache()

    # --- header/footer/watermark removal by repetition ---
    def key(t: str) -> str:
        return re.sub(r"\d+", "#", t.replace(" ", ""))

    margin_counts: Counter = Counter()
    any_counts: Counter = Counter()
    for _, h, lines, _ in raw_pages:
        seen_m, seen_a = set(), set()
        for ln in lines:
            k = key(ln["text"])
            seen_a.add(k)
            if ln["baseline"] < h * header_frac or ln["baseline"] > h * (1 - header_frac):
                seen_m.add(k)
        margin_counts.update(seen_m)
        any_counts.update(seen_a)
    n_pages = max(1, len(raw_pages))
    out_lines: list[Line] = []
    removed: list[tuple[int, str]] = []
    n_tables = 0
    for page_no, h, lines, tables in raw_pages:
        body = []
        for ln in lines:
            k = key(ln["text"])
            in_margin = ln["baseline"] < h * header_frac or ln["baseline"] > h * (1 - header_frac)
            if (in_margin and (margin_counts[k] / n_pages >= repeat_frac or re.fullmatch(r"[#\-—IVXⅠ-Ⅻ ]+", k)))                     or (any_counts[k] / n_pages >= 0.6 and len(k) > 3):
                removed.append((page_no, ln["text"]))
                continue
            body.append(ln)
        # interleave tables at their vertical position
        items = [(ln["baseline"], "line", ln) for ln in body]
        for bbox, rows in tables:
            items.append((bbox[1], "table", (bbox, rows)))
        items.sort(key=lambda x: x[0])
        last_lines: list[str] = []
        for _, typ, obj in items:
            if typ == "line":
                out_lines.append(Line(text=obj["text"], page_no=page_no, bold_ratio=obj["bold_ratio"], size=obj["size"]))
                last_lines.append(obj["text"])
            else:
                _, rows = obj
                title = None
                for t in reversed(last_lines[-3:]):
                    if _TABLE_TITLE.match(t):
                        title = t
                        break
                for tl in _table_lines(rows):
                    out_lines.append(Line(text=tl, page_no=page_no, kind="table", table_title=title, table_index=n_tables))
                n_tables += 1
    out_lines = [l for l in out_lines if l.text.strip()]
    return ParsedDoc(n_pages=n, lines=out_lines, removed_lines=removed,
                     stats={"tables": n_tables, "removed_lines": len(removed), "lines": len(out_lines)})


def assemble(lines: list[Line]) -> tuple[str, list[tuple[int, int, int]]]:
    """Join lines into the stored normalized document text.

    Sets ``line.start/end`` and returns (doc_text, page_spans) where page_spans
    is a list of (page_no, char_start, char_end).
    """
    parts: list[str] = []
    pos = 0
    page_spans: dict[int, list[int]] = {}
    for ln in lines:
        ln.start = pos
        ln.end = pos + len(ln.text)
        parts.append(ln.text)
        sp = page_spans.setdefault(ln.page_no, [ln.start, ln.end])
        sp[1] = ln.end
        pos = ln.end + 1  # newline separator
    text = "\n".join(parts)
    return text, [(p, s, e) for p, (s, e) in sorted(page_spans.items())]
