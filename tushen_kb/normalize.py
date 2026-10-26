"""Text normalization shared by ingestion and citation verification.

Stored page/document text is already normalized with :func:`normalize`, so chunk
offsets index directly into the stored text.  Citation matching additionally
ignores whitespace (:func:`squash`) because PDF line breaks are not meaningful
in Chinese running text.
"""
from __future__ import annotations

import re
import unicodedata

_WS = re.compile(r"[ \t\u3000\xa0]+")

# NFKC folds full-width ASCII (１２３ＡＢ) to half-width, but it also turns
# Chinese full-width punctuation such as ，。；：（） into ASCII.  We keep the
# Chinese punctuation as-is by protecting it.
_KEEP = set("，。；：？！、（）【】《》“”‘’—…·")


def normalize(text: str) -> str:
    out = []
    for ch in text:
        if ch in _KEEP:
            out.append(ch)
        else:
            out.append(unicodedata.normalize("NFKC", ch))
    s = "".join(out)
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    s = _WS.sub(" ", s)
    s = "\n".join(line.strip() for line in s.split("\n"))
    return s


def squash(text: str) -> tuple[str, list[int]]:
    """Remove all whitespace; return squashed text and index map back to input."""
    chars, idx = [], []
    for i, ch in enumerate(text):
        if not ch.isspace():
            chars.append(ch)
            idx.append(i)
    return "".join(chars), idx
