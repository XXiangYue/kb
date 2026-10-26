"""Tokenization for FTS5 BM25.

Pre-tokenize with jieba (search mode) and additionally emit CJK character
bigrams for robustness against segmentation errors (idea borrowed from the
MIT-licensed changjuan project's retrieval.py; reimplemented here).
Clause numbers like ``5.5.17`` / ``A.0.1`` and standard codes like
``55037-2022`` are kept as single tokens (FTS5 is configured with
tokenchars '.-' so they are not split again).
"""
from __future__ import annotations

import logging
import re

import jieba

jieba.setLogLevel(logging.WARNING)

_PROTECT = re.compile(r"(?:[A-Z]|\d{1,3})(?:\.\d{1,3}){1,4}|\d{3,6}-\d{4}|[A-Za-z]+\d*|\d+(?:\.\d+)?")
_CJK_RUN = re.compile(r"[\u4e00-\u9fff]+")
STOPWORDS = set("的 了 和 与 及 或 是 在 应 等 对 为 中 按 其 并 时 由 下列 规定 哪些 什么 怎么 如何 多少 是否 吗 呢 条 第 本 要求 关于".split())


def tokens(text: str, bigrams: bool = True) -> list[str]:
    out: list[str] = []
    pos = 0
    for m in _PROTECT.finditer(text):
        out.extend(_cjk_tokens(text[pos:m.start()], bigrams))
        out.append(m.group(0).lower())
        pos = m.end()
    out.extend(_cjk_tokens(text[pos:], bigrams))
    return out


def _cjk_tokens(seg: str, bigrams: bool) -> list[str]:
    out = []
    for run in _CJK_RUN.findall(seg):
        out.extend(w for w in jieba.cut_for_search(run) if w.strip())
        if bigrams and len(run) >= 2:
            out.extend("_" + run[i:i + 2] for i in range(len(run) - 1))  # '_' marks bigram tokens
    return out


def content_tokens(text: str) -> list[str]:
    """Query-side content words (no bigrams, no stopwords, no single chars)."""
    return [t for t in tokens(text, bigrams=False) if t not in STOPWORDS and len(t) > 1]


def fts_doc(text: str) -> str:
    return " ".join(tokens(text))


def fts_query(text: str) -> str:
    toks = []
    seen = set()
    for t in tokens(text):
        if t in STOPWORDS or t.lstrip("_") in STOPWORDS or t in seen:
            continue
        seen.add(t)
        toks.append('"' + t.replace('"', "") + '"')
    return " OR ".join(toks)
