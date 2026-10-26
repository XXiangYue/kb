#!/usr/bin/env bash
# Download the corpus PDFs listed in corpus/manifest.json and verify sha256.
# PDFs are gitignored: redistribution rights are not explicit (see README).
set -euo pipefail
cd "$(dirname "$0")/.."
PY=${PY:-.venv/bin/python}
mkdir -p data/pdfs
"$PY" - <<'PY'
import json, subprocess, sys
from tushen_kb.ingest import sha256
m = json.load(open("corpus/manifest.json", encoding="utf-8"))
bad = 0
for d in m["docs"]:
    f = d["file"]
    try:
        ok = sha256(f) == d["sha256"]
    except FileNotFoundError:
        ok = False
    if not ok:
        print("download", d["doc_id"], flush=True)
        subprocess.run(["curl", "-fsSL", "--max-time", "120", "-o", f, d["url"]], check=True)
        ok = sha256(f) == d["sha256"]
    print(d["doc_id"], "sha256 OK" if ok else "sha256 MISMATCH")
    bad += not ok
sys.exit(1 if bad else 0)
PY
