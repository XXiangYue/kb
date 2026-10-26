"""CLI: python -m tushen_kb.cli ingest|embed|search|ask"""
from __future__ import annotations

import argparse
import json

from .ask import ask
from .ingest import ROOT, ingest_manifest
from .search import SearchConfig, search
from .store import Store


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["ingest", "embed", "search", "ask"])
    ap.add_argument("q", nargs="?")
    ap.add_argument("--db", default=str(ROOT / "data" / "tushen.sqlite"))
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--mode", default="keyword")
    ap.add_argument("--batch", type=int, default=16)
    a = ap.parse_args()
    st = Store(a.db)
    if a.cmd == "ingest":
        print(json.dumps(ingest_manifest(st), ensure_ascii=False, indent=1))
    elif a.cmd == "embed":
        from .dense import build_vectors
        print(json.dumps(build_vectors(st, batch=a.batch), ensure_ascii=False))
    elif a.cmd == "search":
        for h in search(st, a.q, SearchConfig(mode=a.mode, k=a.k)):
            print(h["rank"], h["chunk_id"], round(h["score"], 2), h["route"], h["text"][:60].replace("\n", ""))
    else:
        r = ask(st, a.q)
        r.pop("trace")
        for e in r["evidence"]:
            e["text"] = e["text"][:80]
        print(json.dumps(r, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
