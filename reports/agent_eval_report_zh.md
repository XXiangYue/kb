# kb `ask` Agent 端到端评测与可观测性：结果报告

- 预注册方案：`reports/plan_agent_eval_zh.md`。方案与代码先于 test 划分上的运行提交（9461af2）。修订 A1（734ae6b，judge2 换模型）在 judge2 阶段运行前提交；修订 A2（13ed6f0，裁判调用台账与按版本分批）在报告生成前提交。
- 机器生成的完整报告：`reports/agent_eval/report.md` / `report.json`，badcase 清单 `badcases.jsonl`。
- 本文所有数字都来自一次真实运行：2026-10-27 21:27–22:59 NZDT，中途在第 174 题处断点续跑。数据文件为 `reports/agent_eval/items_*.jsonl` 与 `traces.jsonl.gz`。
- 判定全部由 LLM 裁判和代码规则完成，**没有任何人工标注或人工复核**。
- 在 test 划分上没有调过任何提示词、阈值或参数。

## 1. 一条命令

```bash
.venv/bin/python -m tushen_kb.eval.agent_eval --suite agent     # 全量：运行 + judge2 + 开销测量 + 报告（md + json）
.venv/bin/python -m tushen_kb.eval.agent_eval --suite smoke     # 冒烟（20 题，不跑 judge2）
.venv/bin/python -m tushen_kb.eval.agent_eval --suite agent --phase report   # 只从已保存的 items/trace 重算报告，不调用 API
```

- Makefile 中对应 `make eval` / `make eval-smoke` / `make eval-report`。本机没有安装 make，所以本次直接用上面的 python 命令运行。
- 运行支持断点续跑：已完成的题会跳过，不会重复调用 API。
- 验证：删除未压缩的 `traces.jsonl`，只用 `traces.jsonl.gz` 重算报告，得到的 `report.md` 除生成时间外与原报告逐字一致。

## 2. 评测对象、任务集、基线

- **被测系统 `system`**：冻结的 Block 3/4 配置。
  - 检索：hybrid（BM25 + bge-small-zh 向量 → RRF → bge-reranker-base 重排 → 条款路由）。
  - 门控：rerank_top 0.94。
  - 生成：DeepSeek `deepseek-flash`，响应返回的模型 id 为 `deepseek-flash/e72d3c7f663d3e1383b00b3884cb4501`。
- **预声明基线 `baseline_bm25`**：仓库默认配置（BM25 关键词检索 + coverage 门控 0.5，不做 rerank），生成模型相同。
- **第二对照**：上一版本已提交的同配置运行 `reports/e2e_s1_deepseek_per_question.jsonl`（7fdc378），用于比较拒答决策是否一致。
- **任务集**：test 划分共 414 题，全部复用已有题集与 gold，没有新造标注。
  - 可答 332 题：block2_answerable 214、lowoverlap 118。
  - 不可答 82 题：block2_U2 31、ooc_b3 51。
- **“工具调用”的含义**：`ask` 是固定流水线，没有由 LLM 自主选择工具的环节，所以“工具调用成功率”在这里是固定步骤的成功率。步骤依次为 existence_check → retrieve{bm25, vector, rrf, rerank, clause_route} → evidence_gate → rerank_gate → cite → generate → verify_quotes。

## 3. 任务成功率（bootstrap 95% CI，2000 次，seed 0）

| 指标（分母） | system | baseline_bm25 |
|---|---|---|
| 任务成功（全部 414） | **285/414 = 0.688 [0.640, 0.734]** | 213/414 = 0.514 [0.464, 0.563] |
| 可答题成功（332） | 203/332 = 0.611 [0.560, 0.666] | 131/332 = 0.395 [0.343, 0.449] |
| 不可答题正确拒答（82） | 82/82 = 1.000 | 82/82 = 1.000 |
| 可答题作答率（332） | 209/332 = 0.630 | 141/332 = 0.425 |
| 可答题误拒（332） | 123/332 = 0.370 | 191/332 = 0.575 |
| 作答中 judge1 判正确（已作答） | 203/209 = 0.971 | 131/141 = 0.929 |
| 作答中 judge1 判有证据支持（已作答） | 207/209 = 0.990 | 140/141 = 0.993 |
| lowoverlap 子集成功（118） | 52/118 = 0.441 | 0/118 = 0.000 |
| block2_answerable 子集成功（214） | 151/214 = 0.706 | 131/214 = 0.612 |

- 配对比较（system − baseline，n=414）：差值 **+0.174**，bootstrap 95% CI [0.123, 0.222]。McNemar 检验：b01 = 96（基线错、系统对），b10 = 24，p = 2.2e-11。
- 与上一版本（7fdc378）对比：拒答决策一致率 398/414 = 0.961。可答题误拒现为 123/332，上一版本为 119/332，McNemar p = 0.45，差异不显著。

## 4. 步骤（工具调用）成功率

| 步骤指标（分母） | system | baseline_bm25 |
|---|---|---|
| retrieve：gold 进入候选池（可答 332） | 318/332 = 0.958 | 294/332 = 0.886 |
| 上下文：gold 进入前 3 条证据（332） | 293/332 = 0.883 | 254/332 = 0.765 |
| 门控放行：gold 已在上下文的可答题 | 259/293 = 0.884 | 175/254 = 0.689 |
| 不可答题在调用 LLM 前即拒答（82） | 82/82 = 1.000 | 72/82 = 0.878 |
| cite：至少一条引用通过校验 | 274/276 = 0.993 | 198/202 = 0.980 |
| 引用精确率：引用条款是 gold（未拒答的可答题） | 213/499 = 0.427 | 139/296 = 0.470 |
| generate：LLM 调用成功 | 274/274 = 1.000 | 208/208 = 1.000 |
| verify_quotes：引文校验通过 | 209/232 = 0.901 | 141/164 = 0.860 |
| 技术层面：步骤 span 无 ERROR | 4508/4508 | 2655/2655 |

注意：引用精确率 system 低于基线。原因是 system 每题引用更多条款（499 条对 296 条），其中更多不是 gold。这一点如实报告，没有调整。

## 5. 失败归因（代码规则从 trace 判定，LLM 只提供“正确”和“有证据支持”两个二值输入）

| 类别 | system | baseline_bm25 |
|---|---|---|
| 检索未召回 | 14 | 38 |
| 召回但排序靠后未进上下文 | 25 | 40 |
| 生成幻觉 | 0 | 0 |
| 引用错误 | 23 | 23 |
| 拒答误判 | 62 | 90 |
| 该拒未拒 | 0 | 0 |
| 工具或步骤错误 | 0 | 2 |
| 其他 | 5 | 8 |
| **失败合计 / 总数** | **129/414** | **201/414** |

- **system 失败定位到的步骤**：
  - 拒答误判 @ kb.rerank_gate 34、@ kb.generate 28（LLM 自行拒答）。
  - 召回但排序靠后 @ kb.search.rerank 25。
  - 引用错误 @ kb.verify_quotes 23。
  - 检索未召回 @ kb.retrieve 14。
  - 其他 @ kb.generate 5（判错但有证据支持，即答非所问或回答不完整）。
- **归因复算**：报告阶段从 trace 文件重新运行 `attribute()`，覆盖 888 条（system run1/2/3 与 baseline），与运行时的判定不一致 **0 条**。
- **badcase 定位**：每个 badcase 都带有 trace_id、定位步骤和原因，见 `reports/agent_eval/badcases.jsonl`（330 行，包括 run1 两个配置的全部失败），以及 report.md 第 7 节。
- **“生成幻觉 = 0”的含义**：按预注册规则，幻觉指“答案判错且 judge1 判定答案含证据不支持的陈述”。本次作答中被判为无证据支持的只有 2 条（system）和 1 条（基线），且都不满足“同时判错”这一条件。

## 6. 稳定性（预声明子集：30 题，seed 20261028，system 重复 k=3 次）

| 指标（分母） | 值 |
|---|---|
| pass^3（3 次全部成功） | 17/30 = 0.567 |
| pass@3（任一次成功） | 19/30 = 0.633 |
| 单次平均成功率 | 53/90 = 0.589 |
| 3 次结果一致 | 28/30 = 0.933 |
| 拒答/作答决策一致 | 29/30 = 0.967 |
| 答案文本完全一致（3 次都作答的题） | 7/12 = 0.583 |

- 基线是 pass@3 与 pass^3 之间的差距。生成时 temperature 为 0，但答案文本仍有 5/12 不同，结果层面有 2/30 不一致。
- 与上一版本的一致率见第 3 节。

## 7. 裁判与一致性

- **judge1**：Xiaomi MiMo `mimo-v2.6-flash`（provider xiaomi-mimo，token-plan-cn 端点，关闭 thinking）。
  - 对所有已作答的可答题做两项判定：正确性、证据支持。
  - 调用 748 次，全部 ok；tokens 371,354 + 1,496；按标价折算 $0.046903。
- **judge2**：Xiaomi MiMo `mimo-v2.6-pro`（修订 A1）。
  - 原计划使用的 OpenRouter `google/gemini-3.8-flash` 当天复查时 key 额度为 0，返回 HTTP 403。
  - 用于 system run1 的预声明抽样（seed 20261027，60 题中已作答的 42 题，上限 45）。
  - 调用 42 次，全部 ok，没有重试；tokens 18,051 + 84；$0.007925。
- **独立性**：两个裁判与 DeepSeek 生成模型的 provider 和 family 都不同（对每个裁判分别用 `llm.validate` 校验）。但 judge2 与 judge1 属于同一 MiMo 家族，独立性较弱，这是本次的限制之一。
- **一致性（n=42，双方均有效）**：
  - 原始一致率 40/42 = 0.952。
  - Cohen's κ = −0.024。原因是两个裁判各自都有 41/42 判“正确”，标签极度不平衡，κ 几乎没有信息量（prevalence paradox）。
  - 2 条分歧：9a247b9163b0（judge1 否 / judge2 是）、fe475a8053ce（judge1 是 / judge2 否）。
  - 按 AND 规则（两个裁判都判正确才算正确），正确率为 40/42 = 0.952；judge1 单独为 41/42 = 0.976。
- **裁判调用台账**（修订 A2）：`reports/agent_eval/judge_calls.jsonl`，共 790 行，每行一次调用，记录角色、qid、trace_id、请求的模型、**接口返回的模型 id**、UTC 时间戳、状态、判定结果、失败原因和尝试次数。
  - judge1#1 返回 `mimo-v2.6-flash`：748 次，时间 21:28:40–22:57:29 NZDT。
  - judge2#1 返回 `mimo-v2.6-pro`：42 次，时间 22:58:03–22:59:29 NZDT。
  - 运行中两个裁判返回的模型 id 都**没有变化**，所以不需要分批。若有变化，报告会按批次单列，不合并统计。
  - 失败调用 0 次。
  - 说明：MiMo 不返回 system_fingerprint，所以只能从模型 id 检测版本变化；DeepSeek 返回 fingerprint，可检测到更细的版本变化。

## 8. 可观测性（OpenTelemetry span 树）

- **实现**：`tushen_kb/obs.py`。
  - 默认关闭，关闭时为 no-op；开启后写 JSONL，每行一个 span。
  - 每个 span 含 trace_id、span_id、parent_span_id、起止时间、duration_ms、status 和属性。
  - 属性名遵循 GenAI semconv：`gen_ai.operation.name`、`gen_ai.request.model`、`gen_ai.response.model`、`gen_ai.usage.input_tokens/output_tokens`。此外有 `tushen.cost_usd`（按 `config/prices.json` 计算）、`kb.step.outcome`、`kb.hit_chunk_ids` 等。
- **span 树结构**：`eval.item → kb.ask → 各步骤 span（retrieve 下挂 kb.search.*；generate 下挂 chat deepseek-flash）→ eval.judge.correctness / eval.judge.support → chat mimo-*`。
- **trace 示例**：`reports/agent_eval/trace_example.json`。这是一个完整的失败样例：qid 06b12db505c4，trace_id 7fb611154408ab02824d74af099d7248，类别“其他 @ kb.generate”。
  - 各步骤均为 pass，rerank 耗时 2775 ms，generate 耗时 1501 ms（906 个输入 token，$9.9e-5）。
  - judge1 判“错误但有证据支持”，因此归为答非所问或回答不完整。
- **全部 trace**：`reports/agent_eval/traces.jsonl.gz`（0.8 MB，解压后 11.5 MB），可回放进任意 OTLP 后端。
- **单元测试**：`tests/test_obs.py`，共 13 项。覆盖 schema 与父子关系、异常/ERROR 状态、关闭时 no-op、成本与 DeepSeek 峰时价、分位数、合成 span 上的各类归因、`ask()` 的真实 span，以及 A2 的台账分批。全仓库 pytest 结果为 84 passed。
- **Tracing 开销**：在最便宜的路径上测量（BM25 + coverage 门控，不调 LLM，此时开销占比最大），414 题 × 2 轮交替。中位数关闭时 3.56 ms、开启时 4.16 ms，每题平均增加 0.50 ms。

## 9. Token / 成本 / 延迟

| 项 | system | baseline_bm25 |
|---|---|---|
| 生成调用（ok） | 274（274） | 208（208） |
| 生成 tokens（prompt + completion） | 167,801 + 11,710 | 121,705 + 6,813 |
| 每题 tokens（/414） | 433.6 | 310.4 |
| 生成标价成本：总计 / 每千题 | $0.030753 / $0.0743 | $0.036033 / $0.0870 |
| `kb.ask` 端到端 p50 / p95（n=414） | 2951 / 5080 ms | 606 / 1343 ms |
| `kb.search.rerank` p50 / p95 | 2093 / 3939 ms | – |
| `kb.generate` p50 / p95 | 1110 / 1677 ms | 1080 / 1405 ms |
| 含裁判的 `eval.item` p50 / p95（system run1） | 4766 / 16902 ms | – |

- **成本说明**：system 每题 token 更多，但成本更低。原因是 system 的 prompt 命中 DeepSeek 缓存更多，而基线更多未命中缓存（缓存命中 $0.003/M，未命中 $0.15/M）。DeepSeek 工作日 UTC 01–04、06–10 点按 2× 峰时价计算（不考虑中国法定节假日）。
- **延迟说明**：延迟主要来自 CPU 上的 bge-reranker（约占 system `kb.ask` 的 70%）。`eval.item` 的 p95 偏高，是因为个别 MiMo 裁判调用耗时达 8 秒。
- **本次全量运行的 API 总量**：
  - 生成：516 次调用（含稳定性 run2/run3 各 17 次），305,874 + 19,933 tokens，$0.070453。
  - judge1：748 次，$0.046903。
  - judge2：42 次，$0.007925。
  - 合计 **1,306 次调用，约 $0.125**（标价折算）。
  - 第一次启动时被中断的那一题所发生的调用没有记录，也未计入，最多 1 次生成和 2 次裁判。
  - 另有冒烟运行，判定成本 $0.001993，见第 10 节。

## 10. 冒烟结果（`--suite smoke`，test 划分的前 20 题）

- system 13/20 = 0.650 [0.45, 0.85]，baseline 12/20 = 0.600 [0.40, 0.80]。
- 配对差 +0.05，CI [−0.10, 0.25]；McNemar b01 = 2、b10 = 1，p = 1。
- 归因复算覆盖 50 条，不一致 0 条。
- 稳定性 5 题 × 3 次，pass^3 = 3/5。
- judge1 调用 20 次，返回的模型 id 不变。
- 结果文件：`reports/agent_eval_smoke/`。

## 11. 限制与偏离

1. **judge2 偏离原计划**：原定 OpenRouter Gemini flash，因 key 额度为 0（403）改用 MiMo `mimo-v2.6-pro`（修订 A1，在 judge2 运行前提交）。它与 judge1 同属 MiMo 家族，裁判之间的独立性较弱。judge2 只覆盖 42 题抽样。
2. **仓库中没有任何人工标注或人工复核**，correctness 与 support 完全依赖 LLM 裁判。
3. **断点续跑**：主运行在 system run1 第 174/414 题时被手动终止，目的是确保 judge2 使用修订后的模型。随后按 checkpoint 续跑，已完成的题没有重跑。因此 run1 的前后两段来自两个进程，服务器端缓存状态可能不同，对延迟和缓存命中率有轻微影响。
4. **稳定性样本小**：只有 30 题 × 3 次，CI 较宽（报告中只给了计数）。
5. **成本口径**：MiMo 用的是 Token Plan 套餐，成本是按列表价换算的等值，不是实际扣费。
6. **本机没有 make**，因此使用等价的 python 命令。
7. **“工具调用”指固定步骤**：`ask` 没有 LLM 选工具的环节。
8. **κ 不可解读**：裁判标签极度不平衡，κ 没有信息量，以原始一致率和分歧清单为准。
9. **A2 字段的来源**：本次运行中，裁判返回的模型 id、时间和失败原因取自子 span `chat <model>` 的 `gen_ai.response.model`、span 起始时间和 status description。直接写在 `eval.judge.*` span 上的 A2 属性只在之后的运行中出现。trace 文件没有做任何事后修改。

## 12. 评测覆盖的能力

与方案 §8 对应，本次评测覆盖：
- eval harness 与一条命令的自动化回归；
- 任务成功率与稳定性（pass^k）；
- 工具调用（步骤）成功率；
- badcase 自动归因，并可从 trace 定位到具体步骤；
- token、成本与延迟 p50/p95；
- OTel 链路追踪与轨迹回放；
- 低侵入观测（关闭时 no-op，开启后每题约 0.5 ms 开销）。
