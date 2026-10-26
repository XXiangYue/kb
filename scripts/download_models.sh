#!/usr/bin/env bash
# Download the two open models used in block 2 (MIT license, BAAI) into models/ (gitignored).
# Source: hf-mirror.com (at the time of block 2 it answered with HTTP 308 -> huggingface.co; curl -L follows).
set -euo pipefail
cd "$(dirname "$0")/.."
BASE=${HF_ENDPOINT:-https://hf-mirror.com}
dl() { local repo=$1; shift; mkdir -p "models/$repo"; for f in "$@"; do
  [ -s "models/$repo/$f" ] || curl -sSfL --retry 3 -o "models/$repo/$f" "$BASE/$repo/resolve/main/$f"; done; }
dl BAAI/bge-small-zh-v1.5 config.json model.safetensors tokenizer.json tokenizer_config.json special_tokens_map.json vocab.txt
dl BAAI/bge-reranker-base config.json model.safetensors tokenizer.json tokenizer_config.json special_tokens_map.json sentencepiece.bpe.model
( cd models && find ./BAAI -type f | sort | xargs sha256sum ) > reports/models_sha256.txt
cat reports/models_sha256.txt
