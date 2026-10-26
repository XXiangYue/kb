#!/usr/bin/env bash
# One-click reproduction: venv -> deps -> download -> ingest -> smoke -> tests -> eval.
# Model calls only happen with RUN_LLM=1 and keys present in the environment.
set -euo pipefail
cd "$(dirname "$0")/.."
if [ ! -x .venv/bin/python ]; then
  if command -v uv >/dev/null; then uv venv --python 3.12 .venv; else python3.12 -m venv .venv; fi
fi
if command -v uv >/dev/null; then uv pip install --python .venv/bin/python -q -r requirements.txt
else .venv/bin/pip install -q -r requirements.txt; fi
PY=.venv/bin/python bash scripts/download_corpus.sh
.venv/bin/python -m tushen_kb.cli ingest
echo "== smoke =="
.venv/bin/python -m tushen_kb.cli ask "GB 55037 第4.1.2条" | head -c 600; echo
.venv/bin/python -m tushen_kb.cli ask "GB 50016 第5.5.17条" | head -c 300; echo
echo "== tests =="
.venv/bin/python -m pytest -q -p no:cacheprovider tests
echo "== eval =="
if [ "${RUN_LLM:-0}" = "1" ]; then .venv/bin/python -m tushen_kb.eval.run_block1 --llm --llm-sample 6
else .venv/bin/python -m tushen_kb.eval.run_block1; fi
