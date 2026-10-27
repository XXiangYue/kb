# 预注册方案：`ask` Agent 级端到端评测 + 结构化可观测性

> 本文件与评测代码（`tushen_kb/eval/agent_eval.py`）、价格表（`config/prices.json`）、单测在**任何 test 划分运行之前**一起提交。
> 提交后不再改动评测标准、指标定义、失败分类规则、裁判提示词、阈值或参数；若运行中必须改动，会在结果文档“偏差”一节逐条写明。
> 所有数字只来自真实运行产物（`reports/agent_eval/` 下的 checkpoint / traces / report.json）。裁判均为模型裁判，不含任何人工判断。

## 1. 评测对象与一条命令

- 被测系统 `system`：冻结的 Block 3/4 配置。检索用 `hybrid_rerank`（BM25 + bge-small-zh 向量，RRF 融合，bge-reranker-base 重排，条款号路由）；门控用 `rerank_top`，阈值 0.94（s1 = sigmoid(top rerank logit)）。生成模型是 DeepSeek `deepseek-flash`（max_tokens 400）。该配置与 7fdc378 的 `reports/e2e_s1_deepseek_per_question.jsonl` 相同。
- 预声明基线：
  1. `baseline_bm25`：仓库默认配置，即 BM25 关键词检索 + coverage 门控 0.5，无 rerank，生成模型同为 DeepSeek。与 system 在同一次运行中、对同一批题目配对比较。
  2. 上一已提交版本：7fdc378 的同配置运行结果，只用于“拒答决策一致率 / 可答题误拒率”和 McNemar 检验，因为该文件没有逐题的裁判判定。
- 一条命令：
  - 全量回归：`make eval`，等价于 `python -m tushen_kb.eval.agent_eval --suite agent`。
  - 冒烟子集：`make eval-smoke`，等价于 `--suite smoke`。
  - 只重建报告：`make eval-report`，不发起任何 API 调用。
  - 产物：`reports/<suite>/report.md` + `report.json` + `badcases.jsonl` + `trace_example.json` + `traces.jsonl(.gz)`。
- 可断点续跑：每个 (config, run) 写一个 `items_*.jsonl`，已完成的题目跳过。

## 2. 任务集（复用已有题集与 gold，不新造标注）

只用 test 划分，共 414 题：

| 子集 | 来源 | 题数 | 是否可答 | gold |
|---|---|---|---|---|
| block2_answerable | questions_block2.jsonl (test, 非 U2) | 214 | 可答 | acceptable chunk ids |
| block2_U2 | questions_block2.jsonl (test, U2) | 31 | 不可答 | — |
| lowoverlap | questions_lowoverlap.jsonl (test) | 118 | 可答 | acceptable chunk ids |
| ooc_b3 | questions_ooc_b3.jsonl (test) | 51 | 不可答（库外） | — |

- 合计可答 332 题，不可答 82 题。
- dev 划分只用于调试评测脚本是否跑通（`--suite dev_smoke`），其结果不报告。**不在任何划分上调提示词、阈值或参数**：system 与 baseline 的参数全部沿用已提交的版本。
- 冒烟子集：4 个子集各取 qid 排序后的前 5 题，共 20 题；稳定性部分取其中前 5 题，k=3；不跑 judge2。

## 3. 指标定义（每项都写明分母和对照基线）

所有比例都报 `分子/分母`。主指标附 bootstrap 95% CI：2000 次重抽样，seed 0，百分位法。system 与 baseline_bm25 的差值用配对 bootstrap CI，并做精确 McNemar 检验。

| 指标 | 定义 | 分母 | 基线 |
|---|---|---|---|
| 任务成功率 | 可答题：已回答（mode=llm_answer 且未拒答）且 judge1 判“正确”；不可答题：被任一步拒答 | 全部 414 题；另按可答 332 / 不可答 82 / 4 个子集分别报 | baseline_bm25 |
| 回答率 / 可答题误拒率 | 可答题中已回答 / 被拒答的比例 | 可答题 332 | baseline_bm25；7fdc378 |
| 已回答题正确率 / 证据支持率 | judge1 correctness=是 / support=是 | 可答且已回答的题 | baseline_bm25 |
| 步骤（工具调用）成功率 | 见 §4，逐步计算 | 到达该步的题 | baseline_bm25 |
| 稳定性 | 预声明的 30 题子集，system 重复 k=3 次：pass^k（3 次都成功）、pass@k、单次平均成功率、成功一致率、拒答决策一致率、答案文本一致率（3 次都回答时，去空白后完全相同） | 30 题；答案一致率的分母是 3 次都回答的题 | 单次运行成功率（同一子集） |
| Token 成本 | 生成模型的 prompt / completion tokens 与 USD 成本（每题均值、每千题）；裁判成本单列 | 全部题目（含拒答，0 token） | baseline_bm25 |
| 延迟 | 端到端（`kb.ask` span）和各步骤 span 的 p50 / p95，最近秩法 | 每个 span 名的出现次数 | baseline_bm25 |
| 归因可复现性 | 报告阶段从 traces 文件重新执行 `attribute()`，与运行时结果比对，不一致数应为 0 | 全部 item | — |
| 追踪开销 | 最便宜路径（关键词检索、无 LLM）下全部 414 题，tracing 关 / 开交替各 2 遍，取每题中位数，比较整体中位数和均值之差 | 414 题 | tracing 关闭 |

**说明：`ask` 是固定流水线，由 LLM 自主选择工具的环节不存在**，因此“工具调用准确率”没有可测对象。这里改测“步骤正确率”，即每个固定步骤是否给出了正确的中间结果：

- `existence_check_pass`：可答题通过了存在性检查。
- `retrieve_gold_in_pool`：gold 进入候选池。
- `context_gold_in_top3`：gold 进入前 3 条证据。
- `gate_pass_answerable`：可答题通过门控，另报“gold 已在上下文时”的条件通过率。
- `refused_before_llm(unanswerable)`：不可答题在调用 LLM 之前被拦截。
- `cite_verified_any`：引用校验至少通过一条。
- `citation_precision`：引用的 chunk 中属于 gold 的比例。
- `llm_call_ok`：生成调用成功。
- `quote_verify_pass`：「」引文全部能在证据中逐字找到。
- `quote_from_gold`：引文来自 gold chunk。

另报一项技术层面的成功率：所有步骤 span 中未以 ERROR 状态结束的比例。

## 4. 轨迹采集（可观测性）

- 实现：OpenTelemetry SDK 1.45.1，自定义 JSONL 文件导出器，每行一个 span，含 trace_id / span_id / parent_span_id、开始和结束时间、时长、kind、status、attributes、events、resource。
- 使用私有 TracerProvider，不改全局设置。默认关闭；`TUSHEN_TRACE_FILE` 或 `obs.setup()` 开启。关闭时 `span()` 返回 no-op 对象。
- 属性名采用 OTel GenAI 语义约定：`gen_ai.operation.name`（invoke_agent / execute_tool / chat）、`gen_ai.tool.name`、`gen_ai.provider.name`、`gen_ai.request.model`、`gen_ai.response.model`、`gen_ai.usage.input_tokens/output_tokens`、`error.type`。项目自有属性用 `kb.` / `tushen.` / `eval.` 前缀。
- 不记录 prompt 和回答文本，只记录长度；不记录任何密钥。
- span 树：

```
eval.item
└─ kb.ask
   ├─ kb.existence_check
   ├─ kb.retrieve
   │  └─ kb.search.{bm25, vector, rrf, rerank, clause_route}
   ├─ kb.evidence_gate
   ├─ kb.rerank_gate
   ├─ kb.cite
   ├─ kb.generate
   │  └─ chat deepseek-flash
   └─ kb.verify_quotes
eval.judge.{correctness, support}
└─ chat mimo-v2.6-flash
```

- 每个步骤 span 记录：`kb.step.outcome`（pass / refuse / fallback_evidence_only），拒答时加 `kb.refusal_reason`；检索命中 ids、rerank 输入和输出 ids 与分数、证据 ids、s1、引用 ids、引文校验结果。
- `eval.item` 根 span 记录 gold ids、`eval.success`、`eval.failure_category`、`eval.failed_step`、`eval.failure_reason`；失败时 status=ERROR，`error.type=eval_failure:<类别>`。仅凭 trace 就能定位失败步骤及原因。
- 成本：按 `config/prices.json` 的公开价目表计算，公式为 `命中缓存 × 命中价 + 未命中 × 未命中价 + 输出 × 输出价`。DeepSeek 在工作日 UTC 01–04 和 06–10 按 2 倍计价，按每次调用的 UTC 时间判断。MiMo 用的是 Token Plan 密钥，按量价格只是“列表价等值”，并非实际扣费。价目表里没有的模型，成本记为 null。

## 5. 失败分类（由代码从 trace 判定，LLM 不参与归因）

分类集合：检索未召回 / 召回但排序靠后未进上下文 / 生成幻觉 / 引用错误 / 拒答误判 / 该拒未拒 / 工具或步骤错误 / 其他。

`attribute(spans, gold, answerable, verdicts)` 按以下顺序取第一条命中的规则：

1. 任一 `kb.*` span 或生成 chat span 的 status 为 ERROR → **工具或步骤错误** @ 该 span。
2. mode=evidence_only 且未拒答（LLM 未返回答案，退化为仅证据）→ **工具或步骤错误** @ kb.generate。
3. 不可答题：已拒答 → 成功；未拒答 → **该拒未拒** @ kb.rerank_gate（无 rerank 门控时为 kb.evidence_gate）。
4. 可答题已回答且 judge1 判正确 → 成功。
5. 在 existence_check 处拒答 → **拒答误判** @ kb.existence_check。
6. gold 不在候选池（rerank 输入 ∪ 最终命中）→ **检索未召回** @ kb.retrieve。
7. gold 在候选池但不在前 3 条证据 → **召回但排序靠后未进上下文**，位置为 kb.search.rerank（gold 在 rerank 输入中时），否则为 kb.retrieve。
8. 在 evidence_gate / rerank_gate 处拒答 → **拒答误判** @ 该门控。
9. 在 cite 处拒答（没有可校验的引用）→ **工具或步骤错误** @ kb.cite。
10. LLM 输出“无法回答” → **拒答误判** @ kb.generate。
11. 在 verify_quotes 处拒答（引文校验失败）→ **引用错误** @ kb.verify_quotes。
12. 已回答但判错时：
    - judge1 调用失败或输出无法解析 → **其他** @ eval.judge，按未通过计。
    - support=否 → **生成幻觉** @ kb.generate。
    - 其余 → **其他** @ kb.generate（答非所问或答案不完整）。

LLM 裁判只提供两个二值输入，即 correctness 和 support，由上述代码规则使用。报告给出各类别计数表和“类别 @ 步骤”计数表；每个 badcase 都给出 trace_id 与 trace 文件路径。

## 6. 裁判

- 被测生成模型：DeepSeek `deepseek-flash`（provider/family 为 deepseek）。裁判与它不同 provider、不同 family，由 `llm.validate` 和断言强制。
- judge1（主裁判，对全部“可答且已回答”的题打分）：Xiaomi MiMo `mimo-v2.6-flash`，family 为 xiaomi-mimo。
  - correctness 提示词 `CORRECT_PROMPT`：输入问题、参考条文（acceptable chunk 原文，最多 3 条，每条 1500 字）和系统回答，只输出“是 / 否”。
  - support 提示词 `FAITH_PROMPT`：输入证据和回答，判断回答中的每个事实陈述在证据中是否有直接依据。
  - max_tokens 16。
- judge2（交叉裁判）：OpenRouter `google/gemma-4-26b-a4b-it:free`，family 为 google，只做 correctness，使用同一提示词。
  - 原计划用 Gemini flash，但 OpenRouter 密钥额度为 0（`google/gemini-3.8-flash` 返回 HTTP 403 “Key limit exceeded”），所以改用同为 Google 家族的免费 Gemma。
  - 抽样：从 test 可答题中用 seed 20261027 抽 60 题，取其中 system run1 已回答的题，按顺序最多 45 题。
  - 调用策略：顺序调用（免费端点上游限流）；每次调用内部最多重试 3 次，退避基数 10s，超时 120s；整体最多 3 轮，每轮只重做“最后一条记录为错误”的题，轮间隔 60s；每题以最后一条记录为准。
- 分歧裁决规则（预声明）：
  - **主结果只用 judge1**。judge2 只用于计算原始一致率和 Cohen's κ（分母为两者都给出有效判定的题），并报告“AND 规则”（两裁判都判正确才算正确）下的正确率作为敏感性分析。
  - judge2 失败或无效的题不进入一致率分母，但计入覆盖率（attempted / both_valid）。
  - judge1 失败时该题按未通过计，归类为“其他 @ eval.judge”。

## 7. 运行顺序与开销控制

1. 预热：3 题 × 2 配置，不追踪、不计入。
2. system 和 baseline_bm25 各对 414 题跑 1 遍（judge1 inline）。
3. system 在稳定性子集上再跑 2 遍：用 seed 20261028 从 414 题中抽 30 题。
4. judge2 阶段。
5. 追踪开销阶段。
6. 报告阶段：重建归因并检查不一致，计算全部指标，写出 report.md / report.json / badcases.jsonl / trace_example.json。

trace_example.json 的选取规则：优先选一个“生成幻觉”题；没有则选第一个已判分的失败题；再没有则选第一题。

预计调用量：生成约 900 次，judge1 约 1000 次，judge2 ≤45 题（含重试）。结果中报告实际调用数、token 合计和成本。

## 8. 评测覆盖的能力

| 能力 | 本方案对应 |
|---|---|
| eval harness：任务集、评分逻辑、一条命令的自动化回归 | §1 一条命令回归；§2 任务集；§3 指标 |
| 指标：任务成功率、稳定性（pass^k）、工具调用（步骤）成功率、失败率与异常类型 | §3 |
| 成本与延迟：token、调用次数、成本、延迟 p50 / p95 | §3 |
| badcase 归因：从 trace / 日志 / 评测结果定位失败步骤，输出评测报告 | §5 代码归因；报告阶段的归因一致性校验 |
| 可复现与回归：固定种子、断点续跑、从 trace 重建归因 | §1、§5 |
| 可观测性：OTel span 树、链路追踪、轨迹采集与回放（traces.jsonl 可回放进任意 OTLP 后端） | §4 |
| 低侵入观测：默认关闭、no-op，开启后测量开销 | §3 开销测量 |
| 数据集管理与批量评测：冻结题集、划分、批量运行与汇总报告 | §1–§2 |

## 9. 已知限制（运行前即已知）

- `ask` 没有 LLM 自主选工具的环节，“工具调用准确率”以固定步骤的正确率代替。
- judge2 是抽样子集；Gemini 因额度原因不可用（见修订 A1）。
- 裁判均为 LLM，没有人工标注或复核。
- 稳定性只覆盖 30 题 × 3 次。
- MiMo 成本是列表价等值，不是实际扣费；DeepSeek 峰谷价没有考虑中国法定节假日。

## 10. 修订 A1（2026-10-27 21:55 NZDT，于 judge2 阶段运行前提交）

- 背景：主运行进行到 system run1 第 174/414 题时，按负责人的指示，judge2 应使用“OpenRouter Gemini flash（若有额度），否则用 MiMo”，不得使用 uuapi。
- 复查结果：同日 21:51 NZDT 复查 OpenRouter 密钥，`limit=0`、`is_free_tier=true`，`google/gemini-3.8-flash` 仍返回 HTTP 403 “Key limit exceeded”。因此 judge2 由 §6 的 `google/gemma-4-26b-a4b-it:free` 改为 **Xiaomi MiMo `mimo-v2.6-pro`**，与 judge1 使用相同的 token-plan 端点，关闭 thinking，max_tokens 16，提示词与 judge1 的 correctness 相同。
- 独立性：judge2 与 judge1 同属 xiaomi-mimo 家族，但不是同一模型（pro 与 flash，有断言保证）。两者都与被测的 DeepSeek 生成模型不同 provider、不同 family（对两个裁判分别调用 `llm.validate` 检查）。同家族两裁判的一致率可能偏高，结果中将作为限制说明。
- 不变项：抽样（seed 20261027，60 题，上限 45 题）、调用与重试策略、分歧裁决规则（主结果只用 judge1，judge2 只用于一致率、κ 和 AND 规则敏感性分析）、其余所有指标与阈值。
- 未受影响：judge2 只在主运行结束后单独运行，此前从未在 test 划分上调用过 judge2，因此本修订不涉及已产生的任何结果。已完成的 174 条 checkpoint 保留，续跑时不重复调用 API。
- 成本：`config/prices.json` 新增 `xiaomi-mimo/mimo-v2.6-pro` 的海外实时推理列表价 $0.0036 / $0.435 / $0.87 每百万 token（Token Plan 列表价等值）。

## 11. 修订 A2（2026-10-27 22:45 NZDT，在报告阶段生成前提交；不改变任何指标、阈值或提示词）

- 背景：负责人新增要求：每次裁判调用都必须在 trace 中记录**接口返回的模型 id、时间戳和失败原因**；若运行中途返回的模型版本发生变化，该批次须单独标注，不得与之前的批次合并统计。
- 本次运行中已经发生的裁判调用：judge1 的调用在主运行中完成，当时代码已在 `eval.judge.*` span 的子 span `chat <model>` 上记录了 `gen_ai.response.model`（接口返回的模型 id；DeepSeek 会附加 `system_fingerprint`）、span 起止时间（unix ns）以及失败时的 status description（已脱敏的 HTTP/网络错误）。报告阶段的 `judge_ledger()` 从 trace 文件读取这些字段，生成 `judge_calls.jsonl`（每行对应一次调用）。这些字段不是事后补写的，trace 文件不做修改。
- 之后的运行：`Judges._call` 会直接在 `eval.judge.*` span 上写入 `eval.judge.returned_model`、`eval.judge.called_at_utc`、`eval.judge.failure_reason` 和 `eval.judge.attempts`。
- 分批规则（预先声明）：按裁判角色把调用按时间排序，返回的模型 id 一旦变化即开始新批次（例如 `judge1#2`）。失败调用没有返回 id，计入它所在时间点的当前批次的失败数。若任一角色出现多个批次：报告中标注 `version_changed_roles`，裁判相关指标（judge1 判定的正确率、judge1/judge2 一致率）按批次分别给出，合并值标注为无效。若只有一个批次，则照常报告。
