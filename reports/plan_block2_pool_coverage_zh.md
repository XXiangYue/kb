# Block 2 候选池覆盖率 · 预注册计划（本文件在运行之前提交；运行时不得修改）

性质：**只做测量**。不调参、不换模型、不改题集、不改任何默认配置，不调用任何 LLM/API。

## 测什么
在冻结题集 `reports/questions_block2.jsonl` 的 test 可回答 214 题上（与 `reports/eval_block2.json`、
`reports/eval_block2_bm25_rerank.json` 同一批题），统计「重排器实际看到的候选池里是否含金标」：

- 命中定义与 block2 相同：候选池中出现任一 `acceptable` chunk（冻结的可接受集合）即算覆盖。
- 两个候选池，均严格复现 `tushen_kb/search.py::search()` 在重排之前的行为（默认 `SearchConfig`，
  run_block2 用的 `k=10`，`std_filter=True`，`kinds` 默认）：
  1. **BM25 池**（bm25_rerank 的输入）：`_bm25_pool(...)` 的前 `rerank_n` 条。
  2. **混合池**（hybrid_rerank 的输入）：`_rrf(_bm25_pool, _vector_pool, rrf_k)` 的前 `rerank_n` 条。
- 池大小 K 取代码里的实际值：`_rerank(q, rows, cfg.rerank_n)` 只对 `rows[:rerank_n]` 打分
  （`search.py:34` `rerank_n: int = 20`，`search.py:141`）；进入截断前，每个检索器取
  `max(k*5, pool)` = max(50, 50) = 50 条（`search.py:32, 92, 119`）。
- 主指标：覆盖率 @K=20，写成 x/214。
- 辅助（同次计算，不另跑）：覆盖率 @50（截断前的整池）、@5（应分别等于 block2 已报的
  keyword 192/214、hybrid 190/214，用作复现自检；不一致则如实报告）。
- 对两个 rerank 模式各自的 6 道 top-5 未命中题（取自已提交的 per_question 文件，不重跑重排），
  逐题报告金标是否在对应池内 → 回答「6 道漏检是池子没覆盖，还是重排排错」。
- 两池覆盖的配对差异（仅 BM25 覆盖 / 仅混合覆盖 的题数）。

## 不做什么
- 不重跑 reranker（rerank 结果用已提交的 `eval_block2_bm25_rerank_per_question.jsonl`）。
- 不改 `pool`、`rerank_n`、`rrf_k` 或任何阈值；不尝试其他 K 作为「调参」，@50/@5 只作描述。
- dense 检索复用 box 上已有的 `models/BAAI/bge-small-zh-v1.5` 与 `data/tushen.sqlite` 中已建的向量（CPU）。

## 输出
- 脚本：`tushen_kb/eval/pool_coverage.py`（与本计划同一提交）。
- 结果：`reports/eval_block2_pool_coverage.json` 与 `_per_question.jsonl`。
