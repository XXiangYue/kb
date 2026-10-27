# 复现 Block 2 主结果：BM25 192/214 → BM25 + rerank 208/214

目标：从干净 clone 出发，在 CPU 上复现冻结题集 `reports/questions_block2.jsonl`（test 可回答 214 题）上的
- `keyword`（BM25）Recall@5 = **192/214 = 0.897**
- `bm25_rerank`（BM25 前 20 名 + bge-reranker-base）Recall@5 = **208/214 = 0.972**
- 同一次运行还给出 `hybrid` 190/214、`hybrid_rerank` 208/214（与 `reports/eval_block2_bm25_rerank.json` 对照）。

**不需要任何 API key。** 本流程不调用任何 LLM（题集已冻结并提交；评测全部是程序判分）。只需要能访问
js.119.gov.cn（语料）、hf-mirror.com（模型）、PyPI 与 download.pytorch.org（依赖）。

## 环境

- Linux x86_64，CPU 即可（无 GPU）。实测机器：Intel Xeon 8 逻辑 CPU，15 GB 内存；torch 默认 4 线程（`TUSHEN_THREADS`）。
- Python **3.12**（实测 3.12.15）；用 [uv](https://github.com/astral-sh/uv) 建 venv 和装依赖。
- 磁盘：模型约 1.2 GB，venv（含 CPU 版 torch）另计。评测峰值 RSS 约 1.6 GB。

## 步骤（逐条照抄执行）

```bash
git clone https://github.com/XXiangYue/kb tushen-kb && cd tushen-kb

# 1. Python 3.12 venv + 依赖
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r requirements.txt
uv pip install --python .venv/bin/python torch==2.14.1 --index-url https://download.pytorch.org/whl/cpu
uv pip install --python .venv/bin/python -r requirements-dense.txt

# 2. 下载语料（4 份 PDF，按 corpus/manifest.json 校验 sha256；PDF 不入 git）
PY=.venv/bin/python bash scripts/download_corpus.sh

# 3. 建索引：解析 → 条文级切块 → SQLite FTS5（data/tushen.sqlite）
.venv/bin/python -m tushen_kb.cli ingest

# 4. 下载两个模型到 models/（经 hf-mirror.com；可用 HF_ENDPOINT 改源）
bash scripts/download_models.sh

# 5. 建向量（hybrid 两档和评测诊断需要；bm25_rerank 本身不用向量）
.venv/bin/python -m tushen_kb.cli embed

# 6. 评测（四种模式；--out 用新名字，不覆盖仓库里已提交的结果）
.venv/bin/python -m tushen_kb.eval.run_block2 --modes keyword,hybrid,bm25_rerank,hybrid_rerank --out repro_block2
```

模型（BAAI，MIT 许可）：

| 模型 | 用途 | model.safetensors 大小 |
|---|---|---|
| `BAAI/bge-reranker-base` | 交叉编码重排（bm25_rerank / hybrid_rerank） | 1,112,206,140 字节（约 1.1 GB） |
| `BAAI/bge-small-zh-v1.5` | 向量召回（vector / hybrid） | 95,827,648 字节（约 92 MB） |

全部文件的 sha256 见 `reports/models_sha256.txt`；`download_models.sh` 会重写这个文件，复现后 `git status` 应显示它**没有变化**。

## 预期输出

- 第 2 步：4 行 `<doc_id> sha256 OK`。
- 第 5 步：`{"model": "BAAI/bge-small-zh-v1.5", "n_chunks": 754, "embedded_now": 754, ...}`。
- 第 6 步写出 `reports/repro_block2.json`，其中 `retrieval.<mode>["Recall@5"]` 应为：

```
keyword        192/214 = 0.897
hybrid         190/214 = 0.888
bm25_rerank    208/214 = 0.972
hybrid_rerank  208/214 = 0.972
```

快速查看：

```bash
.venv/bin/python -c "import json;d=json.load(open('reports/repro_block2.json'))
for m,v in d['retrieval'].items(): print(m, v['Recall@5'])"
```

逐题核对（应无差异）：比较 `reports/repro_block2_per_question.jsonl` 与已提交的
`reports/eval_block2_bm25_rerank_per_question.jsonl` 中每题的 `rank_<mode>`。

## 实测（2026-10-27，box：Intel Xeon 8 逻辑 CPU / 15 GB，与其他进程共用）

从本地仓库 `git clone` 到 `/tmp/kb_repro`（HEAD b9b9aeb，代码与本文件所在提交相同；本文件只新增文档），照上面 6 步执行：

| 步骤 | 用时 |
|---|---|
| clone + venv + 依赖 | 2 s（**uv 缓存是热的**；干净机器需另外下载 CPU 版 torch 等，未实测） |
| 语料下载 + sha256 | 11 s |
| ingest | 12 s |
| 模型下载 | 27 s（取决于网络） |
| embed（754 块） | 27.2 s |
| run_block2 四种模式 | 1070 s（json 内 `wall_s` = 1067.9） |
| **合计** | **约 1148 s ≈ 19 分钟** |

结果：四种模式 Recall@5 与上表一致，且 214 题逐题 rank 与 `reports/eval_block2_bm25_rerank_per_question.jsonl` **完全相同**；`reports/models_sha256.txt` 无变化。
评测时间几乎全在 reranker（CPU 上每次查询约 2.3 s，见 README「rerank 延迟实测」）。
