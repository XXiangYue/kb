# Agent 端到端评测报告（suite=smoke）

生成时间 2026-10-27 23:00 NZDT；预注册方案 reports/plan_agent_eval_zh.md；数字全部来自本次运行的 `items_*.jsonl` 与 `traces.jsonl(.gz)`。被测系统 = `system`，预声明基线 = `baseline_bm25`。

## 1. 任务成功率（分母见每格）

| 指标 | system | baseline_bm25 |
|---|---|---|
| task_success | 13/20 = 0.650 [95% CI 0.450, 0.850] | 12/20 = 0.600 [95% CI 0.400, 0.800] |
| task_success_answerable | 3/10 = 0.300 [95% CI 0.100, 0.600] | 2/10 = 0.200 [95% CI 0.000, 0.500] |
| task_success_unanswerable(correct_refusal) | 10/10 = 1.000 [95% CI 1.000, 1.000] | 10/10 = 1.000 [95% CI 1.000, 1.000] |
| answered_rate_answerable | 4/10 = 0.400 | 4/10 = 0.400 |
| false_refusal_answerable | 6/10 = 0.600 | 6/10 = 0.600 |
| answer_correct_among_answered(judge1) | 3/4 = 0.750 | 2/4 = 0.500 |
| answer_supported_among_answered(judge1) | 4/4 = 1.000 | 4/4 = 1.000 |
| success[block2_U2] | 5/5 = 1.000 | 5/5 = 1.000 |
| success[block2_answerable] | 2/5 = 0.400 | 2/5 = 0.400 |
| success[lowoverlap] | 1/5 = 0.200 | 0/5 = 0.000 |
| success[ooc_b3] | 5/5 = 1.000 | 5/5 = 1.000 |

配对比较（system − baseline，n=20）：差值 0.050，bootstrap 95% CI [-0.1, 0.25]；McNemar b01=2 b10=1 p=1

## 2. 步骤（工具调用）成功率

| 步骤指标 | system | baseline_bm25 |
|---|---|---|
| existence_check_pass(answerable) | 10/10 = 1.000 | 10/10 = 1.000 |
| retrieve_gold_in_pool | 9/10 = 0.900 | 9/10 = 0.900 |
| context_gold_in_top3 | 7/10 = 0.700 | 7/10 = 0.700 |
| gate_pass_answerable(kb.rerank_gate) | 9/10 = 0.900 | - |
| gate_pass_answerable_gold_in_context | 6/7 = 0.857 | 4/7 = 0.571 |
| refused_before_llm(unanswerable) | 10/10 = 1.000 | 9/10 = 0.900 |
| cite_verified_any(answerable reaching cite) | 9/9 = 1.000 | 5/5 = 1.000 |
| citation_precision(non-refused answerable) | 4/12 = 0.333 | 3/8 = 0.375 |
| llm_call_ok(generate reached) | 9/9 = 1.000 | 6/6 = 1.000 |
| quote_verify_pass(answers reaching verify) | 4/4 = 1.000 | 4/4 = 1.000 |
| quote_from_gold(answerable answers with quotes) | 4/4 = 1.000 | 3/4 = 0.750 |
| step_spans_without_error(all steps) | 202/202 = 1.000 | 116/116 = 1.000 |
| gate_pass_answerable(kb.evidence_gate) | - | 5/10 = 0.500 |

## 3. 失败归因（代码规则，从 trace 判定）

| 类别 | system | baseline_bm25 |
|---|---|---|
| 检索未召回 | 1 | 1 |
| 召回但排序靠后未进上下文 | 2 | 2 |
| 生成幻觉 | 0 | 0 |
| 引用错误 | 0 | 0 |
| 拒答误判 | 3 | 4 |
| 该拒未拒 | 0 | 0 |
| 工具或步骤错误 | 0 | 0 |
| 其他 | 1 | 1 |
| 失败合计 / 总数 | 7/20 | 8/20 |

`system` 失败按（类别 @ 定位步骤）：

- 召回但排序靠后未进上下文 @ kb.search.rerank: 2
- 拒答误判 @ kb.generate: 2
- 其他 @ kb.generate: 1
- 检索未召回 @ kb.retrieve: 1
- 拒答误判 @ kb.rerank_gate: 1

`baseline_bm25` 失败按（类别 @ 定位步骤）：

- 拒答误判 @ kb.evidence_gate: 3
- 召回但排序靠后未进上下文 @ kb.retrieve: 2
- 拒答误判 @ kb.generate: 1
- 其他 @ kb.generate: 1
- 检索未召回 @ kb.retrieve: 1

归因复算：从 trace 文件重新运行 `attribute()`，50 条中不一致 0 条。

## 4. 稳定性

- n_questions: 5
- k: 3
- pass^k(all k runs succeed): 3/5 = 0.600
- pass@k(any run succeeds): 4/5 = 0.800
- mean_single_run_success: 11/15 = 0.733
- success_consistency(same outcome in all k): 4/5 = 0.800
- decision_consistency(refuse/answer same in all k): 5/5 = 1.000
- answer_agreement(identical text, answered in all k): 1/1 = 1.000

## 5. 裁判

- j1: ['mimo-v2.6-flash']，调用 20（ok 20），tokens 14158+40，标价折算 $0.001993
- 一致性（system run1, judge2 seeded sample (answered answerable items only)）：尝试 0，双方有效 0，原始一致率 0/0 = 待测，Cohen's κ = None；AND 规则正确率 0/0 = 待测 vs judge1 单独 0/0 = 待测

裁判调用台账（`reports/agent_eval_smoke/judge_calls.jsonl`，每次调用记录返回的模型 id、UTC 时间戳、失败原因）：trace 中共 20 次调用，无返回模型 id（失败）0 次。

| batch | 角色 | 返回模型 id | 调用数 | 失败数 | 首次 (UTC) | 末次 (UTC) |
|---|---|---|---|---|---|---|
| judge1#1 | judge1 | `mimo-v2.6-flash` | 20 | 0 | 2026-10-27T08:23:23Z | 2026-10-27T08:27:01Z |

返回模型 id 运行中途变化的角色：无（每个裁判在整个运行中只返回一个模型 id）

## 6. Token / 成本 / 延迟（生成模型，来自 span）

- `system`: 调用 9（ok 9），prompt 4992 + completion 197 tokens，每题 259.4 tokens，标价成本 $0.000831（每题 $4.154e-05，每千题 $0.0415），模型 ['deepseek-flash/e72d3c7f663d3e1383b00b3884cb4501']
- `baseline_bm25`: 调用 6（ok 6），prompt 3745 + completion 164 tokens，每题 195.4 tokens，标价成本 $0.001283（每题 $6.413e-05，每千题 $0.0641），模型 ['deepseek-flash/e72d3c7f663d3e1383b00b3884cb4501']

| span | system p50 / p95 (n) | baseline_bm25 p50 / p95 (n) |
|---|---|---|
| chat (generator) | 1019 / 1433 (9) | 1122 / 1678 (6) |
| kb.ask | 1675 / 4290 (20) | 5 / 1388 (20) |
| kb.cite | 3 / 7 (9) | 2 / 9 (6) |
| kb.evidence_gate | 1 / 1 (20) | 0 / 1 (20) |
| kb.existence_check | 0 / 0 (20) | 0 / 0 (20) |
| kb.generate | 1020 / 1433 (9) | 1122 / 1678 (6) |
| kb.rerank_gate | 0 / 0 (20) | - |
| kb.retrieve | 1623 / 3262 (20) | 3 / 6 (20) |
| kb.search.bm25 | 3 / 8 (20) | 3 / 5 (20) |
| kb.search.clause_route | 0 / 0 (20) | 0 / 0 (20) |
| kb.search.rerank | 1611 / 3241 (20) | - |
| kb.search.rrf | 0 / 0 (20) | - |
| kb.search.vector | 10 / 21 (20) | - |
| kb.verify_quotes | 0 / 0 (4) | 0 / 0 (4) |

完整 trace 示例：`reports/agent_eval_smoke/trace_example.json`（qid 05fb9ea54fcf，trace_id 1d44eea63d3a457da6a8b9b0c22bdce3，类别 其他）

## 7. Badcase 列表（每条链接 trace_id，trace 文件见 trace_file 列）

| config | qid | set | 类别 | 定位步骤 | 原因 | trace_id |
|---|---|---|---|---|---|---|
| system | 00a7d241c16f | block2_answerable | 召回但排序靠后未进上下文 | kb.search.rerank | gold 在 rerank 输入（rerank 前排名 2），rerank 后 top10 排名 7，未进前 3 条证据 | `f22176a2cd88eb6441b179ff97379dc2` |
| system | 05fb9ea54fcf | block2_answerable | 其他 | kb.generate | 答案判错但证据支持（答非所问/不完整） | `1d44eea63d3a457da6a8b9b0c22bdce3` |
| system | 06c3a48271ad | block2_answerable | 召回但排序靠后未进上下文 | kb.search.rerank | gold 在 rerank 输入（rerank 前排名 5），rerank 后 top10 排名 4，未进前 3 条证据 | `73fffc221ab2f18ab9d837f2fc39a54a` |
| system | 07a916d9b255 | lowoverlap | 拒答误判 | kb.generate | 生成模型判定证据不足 | `40e8bd341c4fdddb0df93de0bba0150d` |
| system | 0cc953f69701 | lowoverlap | 检索未召回 | kb.retrieve | gold 不在候选池（20 个候选） | `36e8c2232a99941986777896db2c75e8` |
| system | 0dc1aa08165d | lowoverlap | 拒答误判 | kb.rerank_gate | 证据不足（rerank 最高分 0.938 < 阈值 0.94） | `7fef340cc77662a4c035c1bc52aafe46` |
| system | 1013399c8bdb | lowoverlap | 拒答误判 | kb.generate | 生成模型判定证据不足 | `27f4f3a9f3598a8bb9870793fad79789` |
| baseline_bm25 | 00a7d241c16f | block2_answerable | 召回但排序靠后未进上下文 | kb.retrieve | gold 在 top10 第 5 位，未进前 3 条证据 | `5b0446234447a9f3b39c7e47a9745282` |
| baseline_bm25 | 05fb9ea54fcf | block2_answerable | 拒答误判 | kb.generate | 生成模型判定证据不足 | `874da0b8aa163943162496daad350f73` |
| baseline_bm25 | 06b12db505c4 | block2_answerable | 其他 | kb.generate | 答案判错但证据支持（答非所问/不完整） | `dd0fbdc0e24938520637fdaed32504d0` |
| baseline_bm25 | 07030be41d1c | lowoverlap | 拒答误判 | kb.evidence_gate | 证据不足（最高覆盖率 0.24 < 阈值 0.5） | `8917a467aeb4aa91897f2b5f047bb61f` |
| baseline_bm25 | 07a916d9b255 | lowoverlap | 拒答误判 | kb.evidence_gate | 证据不足（最高覆盖率 0.22 < 阈值 0.5） | `ad63aabdd6ac385d5af384172052dc55` |
| baseline_bm25 | 0cc953f69701 | lowoverlap | 检索未召回 | kb.retrieve | gold 不在候选池（10 个候选） | `5f3c0939128f55b503857683b9f7894a` |
| baseline_bm25 | 0dc1aa08165d | lowoverlap | 拒答误判 | kb.evidence_gate | 证据不足（最高覆盖率 0.25 < 阈值 0.5） | `c3e2092d01b3a593e3d6e4178f0c9ca1` |
| baseline_bm25 | 1013399c8bdb | lowoverlap | 召回但排序靠后未进上下文 | kb.retrieve | gold 在 top10 第 5 位，未进前 3 条证据 | `df3de32bef43eac58d75fd6ef2171436` |
