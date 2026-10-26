# Block 4 预注册计划（本文件在任何运行之前提交；运行时不得修改）

前提：
- 所有题集都沿用已冻结的版本，不新建题：
  - `questions_block2.jsonl`（test 可回答 214 题，U2 31 题）
  - `questions_lowoverlap.jsonl`（test 118 题）
  - `questions_ooc_b3.jsonl`（test 51 题）
- S1 冻结为 rerank_top，t = 0.94（来自 081a449），不重调。
- 不调任何阈值，不换模型。
- 生成模型 deepseek-flash 只做生成（temperature 0），不做任何判定。
- 每题 1 次调用，失败按 ChatModel 现有重试。最终失败的题记为失败，并报告覆盖率 x/N，不换模型、不补跑第二轮。
- 只在 CPU 空闲时运行，不与其他重任务并行。批次 20，带断点续跑，预计峰值 RSS 约 1.6GB。
- 每个指标都写明分母，没有测到的写「待测」。

## (a) 引用精度口径 A（coverage_strip_doc_refs = False）

**a1. Step 5 端到端（对应 14d729f，阈值 0.05，已废弃，仅存档）**
- 做法：用同一个 `run_e2e`，配置 `AskConfig(search=hybrid_rerank, min_coverage=0.05, coverage_strip_doc_refs=False)`，在同样的 363 道 test 题上重跑一次（含 DeepSeek）。
  - 必须重跑 LLM：口径改变后，证据门槛和引用集合会变，拒答集合也会随之变化。
  - 输出：`reports/e2e_deepseek_A.json` 和 `_per_question.jsonl`。
- 指标：引用精度口径 A，计算方式是「非拒答的可回答题中，命中金标的程序引用数 / 程序引用总数」。其余 Step 5 指标也照常记录。
- 与 14d729f（口径 B）按题配对：
  - 误拒、U2 正确拒答用 McNemar exact。
  - 引用精度差用按题聚类的 bootstrap（2000 次）。
- 这一组结果只用来补齐口径 A，不作为卖点。

**a2. Block 3（S1，min_coverage = 0）**
- 按构造推断：覆盖率闸门和按条覆盖率过滤都已关闭（阈值 0），所以 S1 的引用集合与口径无关，口径 A 应等于口径 B。
- 只做程序验证：S1 用 `coverage_strip_doc_refs=False` 做一次 evidence-only 重跑，逐题断言引用 chunk 集合和拒答结果与 7818948 相同，并报告「一致 x/N」。如果不一致，如实报告口径 A 的数值。
- 对照组「当前默认 0.5」的口径 A，同样通过一次 evidence-only 重跑得到。

## (b) S1 + DeepSeek 端到端

- **实现**：在 `AskConfig` 中新增 `gate`（默认 `"coverage"`）和 `gate_threshold` 两个字段，默认行为完全不变。
  - `gate="rerank_top"` 时，令 min_coverage = 0，并在 LLM 之前执行：若 sigmoid(top rerank logit) < t，则拒答。
  - LLM 阶段（「无法回答」→ 拒答；「」引文必须通过原文校验，否则拒答；没有引文也拒答）与 Step 5 相同。
  - 实现改动单独提交，附单元测试，证明默认配置下的输出不变。
- **题集与报告**（每集分别给出 x/N）：
  - Block 2 test 可回答（214 题）：误拒，≥1 条正确引用。
  - 低重叠/改写 test（118 题）：误拒，≥1 条正确引用。
  - 新 OOC test（51 题，未见过）：正确拒答。
  - U2 test（31 题，标注「已见过」）：正确拒答，单独列出，不并入 OOC。
  - 每集都报告：
    - DeepSeek 调用次数、成功、失败、tokens、延迟均值/p50/p95。
    - 引文逐字校验通过。
    - 已校验引文中来自金标的比例。
    - 引用精度：口径 B；口径 A 按 a2 的结论填写。
    - 拒答原因分布。
- **配对检验**（相同 qid，McNemar exact；引用精度用聚类 bootstrap）：
  - 对 14d729f（覆盖率 0.05 + DeepSeek）：Block 2 可回答、低重叠、U2。
    - 注意：两边同时改了闸门和阈值，差异只能归因于「整套配置」，不能归因于单一因素。0.05 是已废弃的阈值，这组对比只作参考。
  - 对 7818948 中的 S1 evidence-only：所有四个集合。这一组用来衡量加上 LLM 这一层的影响。
  - OOC 51 题不在 14d729f 中，只与 7818948 配对。
- **口径约束**：
  - 只跑一次，跑完不再调整 t 或提示词。
  - 结论不论好坏都如实写，不作为卖点包装。
  - 默认配置不会因本结果自动改变；如需改默认值，另行决策。
