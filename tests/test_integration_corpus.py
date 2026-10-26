"""Integration on the real corpus; skipped if PDFs/DB not downloaded."""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "tushen.sqlite"
pytestmark = pytest.mark.skipif(not DB.exists(), reason="corpus not ingested (run scripts/repro.sh)")


def test_real_clause_lookup():
    from tushen_kb.ask import ask
    from tushen_kb.store import Store
    s = Store(DB)
    r = ask(s, "GB 55037 第4.1.2条")
    assert not r["refused"] and r["citations"][0]["clause_no"] == "4.1.2"
    assert ask(s, "GB 50016 第5.5.17条")["refused"]


def test_offsets_hold_on_real_docs():
    from tushen_kb.store import Store
    s = Store(DB)
    for d in s.docs():
        t = s.doc_text(d["doc_id"])
        for c in s.chunks("doc_id=?", (d["doc_id"],))[:400]:
            assert t[c["char_start"]:c["char_end"]] == c["text"]
