# 事实表（每条带分母与出处；只记已提交的数据）

| 日期 | 事实 | 分子/分母 | 出处 |
|---|---|---|---|
| 2026-10-27 | 重排器输入 K = 20（rerank_n），每个检索器截断前取 50 条，RRF k = 60 | — | `tushen_kb/search.py:32-34, :141` |
| 2026-10-27 | BM25 候选池 @20 覆盖金标 | 212/214 = 0.991 | `reports/eval_block2_pool_coverage.json:16`（计划 61475cb，结果 b9b9aeb） |
| 2026-10-27 | 混合 RRF 候选池 @20 覆盖金标 | 212/214 = 0.991 | `reports/eval_block2_pool_coverage.json:21` |
| 2026-10-27 | 两池覆盖配对：都覆盖 210、仅 BM25 2、仅混合 2、都不覆盖 0 | 214 | `reports/eval_block2_pool_coverage.json:25-29` |
| 2026-10-27 | 每个 rerank 模式的 6 道 top-5 未命中 = 2 道池未覆盖 + 4 道重排排错（4 道相同）；2:2 不一致的 4 道 = 两池覆盖不同的 4 道 | 6/214 每模式 | `reports/eval_block2_pool_coverage.json:32-117`；`reports/block2_report_zh.md` 候选池覆盖率一节 |
