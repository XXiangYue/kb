"""CLI: python -m tushen_kb.cli ingest|search|ask"""
from __future__ import annotations

import argparse
import json

from .ask import ask
from .ingest import ROOT, ingest_manifest
from .search import SearchConfig, search
from .store import Store


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["ingest", "search", "ask"])
    ap.add_argument("q", nargs="?")
    ap.add_argument("--db", default=str(ROOT / "data" / "tushen.sqlite"))
    ap.add_argument("--k", type=int, default=5)
    a = ap.parse_args()
    st = Store(a.db)
    if a.cmd == "ingest":
        print(json.dumps(ingest_manifest(st), ensure_ascii=False, indent=1))
    elif a.cmd == "search":
        for h in search(st, a.q, SearchConfig(k=a.k)):
            print(h["rank"], h["chunk_id"], round(h["score"], 2), h["route"], h["text"][:60].replace("\n", ""))
    else:
        r = ask(st, a.q)
        r.pop("trace")
        for e in r["evidence"]:
            e["text"] = e["text"][:80]
        print(json.dumps(r, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
