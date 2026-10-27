# Agent 端到端评测报告（suite=agent）

生成时间 2026-10-27 23:00 NZDT；预注册方案 reports/plan_agent_eval_zh.md；数字全部来自本次运行的 `items_*.jsonl` 与 `traces.jsonl(.gz)`。被测系统 = `system`，预声明基线 = `baseline_bm25`。

## 1. 任务成功率（分母见每格）

| 指标 | system | baseline_bm25 |
|---|---|---|
| task_success | 285/414 = 0.688 [95% CI 0.640, 0.734] | 213/414 = 0.514 [95% CI 0.464, 0.563] |
| task_success_answerable | 203/332 = 0.611 [95% CI 0.560, 0.666] | 131/332 = 0.395 [95% CI 0.343, 0.449] |
| task_success_unanswerable(correct_refusal) | 82/82 = 1.000 [95% CI 1.000, 1.000] | 82/82 = 1.000 [95% CI 1.000, 1.000] |
| answered_rate_answerable | 209/332 = 0.630 | 141/332 = 0.425 |
| false_refusal_answerable | 123/332 = 0.370 | 191/332 = 0.575 |
| answer_correct_among_answered(judge1) | 203/209 = 0.971 | 131/141 = 0.929 |
| answer_supported_among_answered(judge1) | 207/209 = 0.990 | 140/141 = 0.993 |
| success[block2_U2] | 31/31 = 1.000 | 31/31 = 1.000 |
| success[block2_answerable] | 151/214 = 0.706 | 131/214 = 0.612 |
| success[lowoverlap] | 52/118 = 0.441 | 0/118 = 0.000 |
| success[ooc_b3] | 51/51 = 1.000 | 51/51 = 1.000 |

配对比较（system − baseline，n=414）：差值 0.174，bootstrap 95% CI [0.123, 0.222]；McNemar b01=96 b10=24 p=2.2e-11

## 2. 步骤（工具调用）成功率

| 步骤指标 | system | baseline_bm25 |
|---|---|---|
| existence_check_pass(answerable) | 332/332 = 1.000 | 332/332 = 1.000 |
| retrieve_gold_in_pool | 318/332 = 0.958 | 294/332 = 0.886 |
| context_gold_in_top3 | 293/332 = 0.883 | 254/332 = 0.765 |
| gate_pass_answerable(kb.rerank_gate) | 276/332 = 0.831 | - |
| gate_pass_answerable_gold_in_context | 259/293 = 0.884 | 175/254 = 0.689 |
| refused_before_llm(unanswerable) | 82/82 = 1.000 | 72/82 = 0.878 |
| cite_verified_any(answerable reaching cite) | 274/276 = 0.993 | 198/202 = 0.980 |
| citation_precision(non-refused answerable) | 213/499 = 0.427 | 139/296 = 0.470 |
| llm_call_ok(generate reached) | 274/274 = 1.000 | 208/208 = 1.000 |
| quote_verify_pass(answers reaching verify) | 209/232 = 0.901 | 141/164 = 0.860 |
| quote_from_gold(answerable answers with quotes) | 212/231 = 0.918 | 142/164 = 0.866 |
| step_spans_without_error(all steps) | 4508/4508 = 1.000 | 2655/2655 = 1.000 |
| gate_pass_answerable(kb.evidence_gate) | - | 202/332 = 0.608 |

## 3. 失败归因（代码规则，从 trace 判定）

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
| 失败合计 / 总数 | 129/414 | 201/414 |

`system` 失败按（类别 @ 定位步骤）：

- 拒答误判 @ kb.rerank_gate: 34
- 拒答误判 @ kb.generate: 28
- 召回但排序靠后未进上下文 @ kb.search.rerank: 25
- 引用错误 @ kb.verify_quotes: 23
- 检索未召回 @ kb.retrieve: 14
- 其他 @ kb.generate: 5

`baseline_bm25` 失败按（类别 @ 定位步骤）：

- 拒答误判 @ kb.evidence_gate: 79
- 召回但排序靠后未进上下文 @ kb.retrieve: 40
- 检索未召回 @ kb.retrieve: 38
- 引用错误 @ kb.verify_quotes: 23
- 拒答误判 @ kb.generate: 11
- 其他 @ kb.generate: 8
- 工具或步骤错误 @ kb.cite: 2

归因复算：从 trace 文件重新运行 `attribute()`，888 条中不一致 0 条。

## 4. 稳定性

- n_questions: 30
- k: 3
- pass^k(all k runs succeed): 17/30 = 0.567
- pass@k(any run succeeds): 19/30 = 0.633
- mean_single_run_success: 53/90 = 0.589
- success_consistency(same outcome in all k): 28/30 = 0.933
- decision_consistency(refuse/answer same in all k): 29/30 = 0.967
- answer_agreement(identical text, answered in all k): 7/12 = 0.583
- 与上一版本已提交运行（reports/e2e_s1_deepseek_per_question.jsonl (commit 7fdc378, same config, DeepSeek)）的拒答决策一致率: 398/414 = 0.961；可答题误拒 现 123/332 = 0.370 / 前 119/332 = 0.358；McNemar p=0.45

## 5. 裁判

- j1: ['mimo-v2.6-flash']，调用 748（ok 748），tokens 371354+1496，标价折算 $0.046903
- j2: ['mimo-v2.6-pro']，调用 42（ok 42），tokens 18051+84，标价折算 $0.007925
- 一致性（system run1, judge2 seeded sample (answered answerable items only)）：尝试 42，双方有效 42，原始一致率 40/42 = 0.952，Cohen's κ = -0.024；AND 规则正确率 40/42 = 0.952 vs judge1 单独 41/42 = 0.976

裁判调用台账（`reports/agent_eval/judge_calls.jsonl`，每次调用记录返回的模型 id、UTC 时间戳、失败原因）：trace 中共 790 次调用，无返回模型 id（失败）0 次。

| batch | 角色 | 返回模型 id | 调用数 | 失败数 | 首次 (UTC) | 末次 (UTC) |
|---|---|---|---|---|---|---|
| judge1#1 | judge1 | `mimo-v2.6-flash` | 748 | 0 | 2026-10-27T08:28:40Z | 2026-10-27T09:57:29Z |
| judge2#1 | judge2 | `mimo-v2.6-pro` | 42 | 0 | 2026-10-27T09:58:03Z | 2026-10-27T09:59:29Z |

返回模型 id 运行中途变化的角色：无（每个裁判在整个运行中只返回一个模型 id）

## 6. Token / 成本 / 延迟（生成模型，来自 span）

- `system`: 调用 274（ok 274），prompt 167801 + completion 11710 tokens，每题 433.6 tokens，标价成本 $0.030753（每题 $7.428e-05，每千题 $0.0743），模型 ['deepseek-flash/e72d3c7f663d3e1383b00b3884cb4501']
- `baseline_bm25`: 调用 208（ok 208），prompt 121705 + completion 6813 tokens，每题 310.4 tokens，标价成本 $0.036033（每题 $8.704e-05，每千题 $0.087），模型 ['deepseek-flash/e72d3c7f663d3e1383b00b3884cb4501']

| span | system p50 / p95 (n) | baseline_bm25 p50 / p95 (n) |
|---|---|---|
| chat (generator) | 1110 / 1677 (274) | 1079 / 1404 (208) |
| kb.ask | 2951 / 5080 (414) | 606 / 1343 (414) |
| kb.cite | 3 / 10 (276) | 2 / 5 (213) |
| kb.evidence_gate | 1 / 1 (414) | 0 / 1 (414) |
| kb.existence_check | 0 / 0 (414) | 0 / 0 (414) |
| kb.generate | 1110 / 1677 (274) | 1080 / 1405 (208) |
| kb.rerank_gate | 0 / 0 (414) | - |
| kb.retrieve | 2113 / 3966 (414) | 3 / 5 (414) |
| kb.search.bm25 | 4 / 7 (414) | 3 / 5 (414) |
| kb.search.clause_route | 0 / 0 (414) | 0 / 0 (414) |
| kb.search.rerank | 2093 / 3939 (414) | - |
| kb.search.rrf | 0 / 0 (414) | - |
| kb.search.vector | 12 / 23 (414) | - |
| kb.verify_quotes | 0 / 1 (232) | 0 / 1 (164) |

Tracing 开销（keyword BM25 + coverage gate, no LLM (cheapest path; overhead is the largest relative share here)，414 题 × 2 轮）：中位数 off 3.56 ms / on 4.163 ms，每题平均增量 0.499 ms。

完整 trace 示例：`reports/agent_eval/trace_example.json`（qid 06b12db505c4，trace_id 7fb611154408ab02824d74af099d7248，类别 其他）

## 7. Badcase 列表（每条链接 trace_id，trace 文件见 trace_file 列）

| config | qid | set | 类别 | 定位步骤 | 原因 | trace_id |
|---|---|---|---|---|---|---|
| system | 00a7d241c16f | block2_answerable | 召回但排序靠后未进上下文 | kb.search.rerank | gold 在 rerank 输入（rerank 前排名 2），rerank 后 top10 排名 7，未进前 3 条证据 | `26802ebdf48773e2b3a138673018efcb` |
| system | 06b12db505c4 | block2_answerable | 其他 | kb.generate | 答案判错但证据支持（答非所问/不完整） | `7fb611154408ab02824d74af099d7248` |
| system | 06c3a48271ad | block2_answerable | 召回但排序靠后未进上下文 | kb.search.rerank | gold 在 rerank 输入（rerank 前排名 5），rerank 后 top10 排名 4，未进前 3 条证据 | `3071272d7bbd679e48ad5da33baed143` |
| system | 09647c07c7ea | block2_answerable | 召回但排序靠后未进上下文 | kb.search.rerank | gold 在 rerank 输入（rerank 前排名 13），rerank 后 top10 排名 4，未进前 3 条证 | `e85c21699915c0c4d6d271345cd6cdf3` |
| system | 0bda6ae3bb88 | block2_answerable | 引用错误 | kb.verify_quotes | 生成答案中的引文未通过原文校验 | `11dd960c1ac28ce8cfbc8551afff6572` |
| system | 0c97eca8f45d | block2_answerable | 拒答误判 | kb.generate | 生成模型判定证据不足 | `7ebaac84d687af90c80a128eddd399c7` |
| system | 0cf31478a8b1 | block2_answerable | 拒答误判 | kb.generate | 生成模型判定证据不足 | `6e4c5f1c61dc3ef6c210b5d95d84a921` |
| system | 1370f5b8b9a9 | block2_answerable | 引用错误 | kb.verify_quotes | 生成答案中的引文未通过原文校验 | `4bd0f2e03b7757bbc24795e69e9dbea8` |
| system | 1372bf4d9eb0 | block2_answerable | 拒答误判 | kb.generate | 生成模型判定证据不足 | `a4648930f33fdd73bed077b02060141c` |
| system | 2435a38a4fc5 | block2_answerable | 拒答误判 | kb.generate | 生成模型判定证据不足 | `bd93376ab3366d8d645606758c24e078` |
| system | 25cfe98dd412 | block2_answerable | 引用错误 | kb.verify_quotes | 生成答案中的引文未通过原文校验 | `bde6f8e8b4ee3109d50c25488c6283be` |
| system | 2b5cbaa7e90a | block2_answerable | 引用错误 | kb.verify_quotes | 生成答案中的引文未通过原文校验 | `55bba3952ac868a1be4f740b9cc47e20` |
| system | 2b7b07f82223 | block2_answerable | 召回但排序靠后未进上下文 | kb.search.rerank | gold 在 rerank 输入（rerank 前排名 1），rerank 后 top10 排名 4，未进前 3 条证据 | `fc3cf47313e6e91262d3abc2a5181c65` |
| system | 2c5ab7c73cc7 | block2_answerable | 拒答误判 | kb.generate | 生成模型判定证据不足 | `6f67e2983127df8f62d695e08126ccfb` |
| system | 32b4c1066f57 | block2_answerable | 引用错误 | kb.verify_quotes | 生成答案中的引文未通过原文校验 | `e5f33f3f763add48393eab0250f67593` |
| system | 35bc5e1e100c | block2_answerable | 拒答误判 | kb.generate | 生成模型判定证据不足 | `82b860848c2c3064f6bb760fb65d2df9` |
| system | 35c92d294b6f | block2_answerable | 拒答误判 | kb.rerank_gate | 证据不足（rerank 最高分 0.903 < 阈值 0.94） | `558e3efce05d391e1803f7804c8e8de5` |
| system | 37fa65baeaa2 | block2_answerable | 拒答误判 | kb.rerank_gate | 证据不足（rerank 最高分 0.556 < 阈值 0.94） | `a585f30595e68c01e022403bceb39661` |
| system | 42cb6ba4c772 | block2_answerable | 引用错误 | kb.verify_quotes | 生成答案中的引文未通过原文校验 | `be82d2ff70a283da44118ce17c6578fa` |
| system | 496c2715cff6 | block2_answerable | 其他 | kb.generate | 答案判错但证据支持（答非所问/不完整） | `b54cdec7d1d681aef4c0967d5dba5b77` |
| system | 4b559cd0b8f4 | block2_answerable | 引用错误 | kb.verify_quotes | 生成答案中的引文未通过原文校验 | `bc929ae9e94eda3c1585b5d3d497106e` |
| system | 4c97176851af | block2_answerable | 拒答误判 | kb.rerank_gate | 证据不足（rerank 最高分 0.433 < 阈值 0.94） | `15e5ce62660a1e453b7f2dd074c21023` |
| system | 4d7ef3bcc694 | block2_answerable | 引用错误 | kb.verify_quotes | 生成答案中的引文未通过原文校验 | `2f85820703c34c1a4931085efcbbc3a1` |
| system | 549f57170e8d | block2_answerable | 拒答误判 | kb.generate | 生成模型判定证据不足 | `36a0877dcaa10b8f6a5a9da0e0a1913a` |
| system | 54ad3afa62e1 | block2_answerable | 拒答误判 | kb.generate | 生成模型判定证据不足 | `ede15cd1954d5e5ec3b2d7511c080bce` |
| system | 5bcc1cc278c9 | block2_answerable | 拒答误判 | kb.generate | 生成模型判定证据不足 | `db3948162be233f3d34bcd59518322d0` |
| system | 66fdf65edd58 | block2_answerable | 引用错误 | kb.verify_quotes | 生成答案中的引文未通过原文校验 | `32848e6375b0bb0f94d46b6b37a07b9f` |
| system | 68f552b331dc | block2_answerable | 引用错误 | kb.verify_quotes | 生成答案中的引文未通过原文校验 | `88d2063da9d7b239213839085104c628` |
| system | 6a5a89351303 | block2_answerable | 拒答误判 | kb.rerank_gate | 证据不足（rerank 最高分 0.870 < 阈值 0.94） | `0b15762ddb381d8d1d11c2821442f3c8` |
| system | 736995ec8a2a | block2_answerable | 召回但排序靠后未进上下文 | kb.search.rerank | gold 在 rerank 输入（rerank 前排名 6），rerank 后 top10 排名 7，未进前 3 条证据 | `a5f001a797d174ee0a0215549a430330` |
| system | 7b8722105045 | block2_answerable | 引用错误 | kb.verify_quotes | 生成答案中的引文未通过原文校验 | `fd3e6166a2e7290c1a6670818f625793` |
| system | 7df9a18653be | block2_answerable | 引用错误 | kb.verify_quotes | 生成答案中的引文未通过原文校验 | `cff54bc41a3dbf1eb629274618a4964c` |
| system | 825e12a62ed9 | block2_answerable | 引用错误 | kb.verify_quotes | 生成答案未给出原文引文 | `c086f1e37cb6007816fdcbc3aa42885e` |
| system | 83b4585b8f47 | block2_answerable | 召回但排序靠后未进上下文 | kb.search.rerank | gold 在 rerank 输入（rerank 前排名 17），rerank 后 top10 排名 6，未进前 3 条证 | `a8387b5b684a2e3fb0623e79d82d2990` |
| system | 845cda58e32b | block2_answerable | 拒答误判 | kb.generate | 生成模型判定证据不足 | `25abdb9fd1c938a834a1796f2fdac5f8` |
| system | 856ebb84d56d | block2_answerable | 拒答误判 | kb.generate | 生成模型判定证据不足 | `a651c3545d0edac2d8d51221e7b4a403` |
| system | 88eba932e84c | block2_answerable | 拒答误判 | kb.generate | 生成模型判定证据不足 | `db19e0c06b92d4f0a84d0a9093e42016` |
| system | 950d1addbc5b | block2_answerable | 引用错误 | kb.verify_quotes | 生成答案中的引文未通过原文校验 | `3e032f763b1524a1891f798efee55766` |
| system | 96f9fad72078 | block2_answerable | 拒答误判 | kb.rerank_gate | 证据不足（rerank 最高分 0.940 < 阈值 0.94） | `72e8ac714d6b9f93b09f5ea9fb2da069` |
| system | 9a11144a2c61 | block2_answerable | 拒答误判 | kb.rerank_gate | 证据不足（rerank 最高分 0.585 < 阈值 0.94） | `a919fd66c780a2960522c47085c29caf` |
| system | 9a247b9163b0 | block2_answerable | 其他 | kb.generate | 答案判错但证据支持（答非所问/不完整） | `9593a042ecb0435cb11f012f39e4d344` |
| system | 9c8d0789b805 | block2_answerable | 拒答误判 | kb.generate | 生成模型判定证据不足 | `09e171160e9f9613b6a9c7426f33b3af` |
| system | 9cb891101a34 | block2_answerable | 拒答误判 | kb.generate | 生成模型判定证据不足 | `90b1d2c106f527b8eb5c47c9171b85fb` |
| system | a0396a09c8ae | block2_answerable | 拒答误判 | kb.generate | 生成模型判定证据不足 | `fc849e5ebaa1081022cf2af645d84a85` |
| system | a9e5a2acaf35 | block2_answerable | 引用错误 | kb.verify_quotes | 生成答案中的引文未通过原文校验 | `4d16e64e2371fbe1e754bd3dfa6527ea` |
| system | ac2bae67382e | block2_answerable | 引用错误 | kb.verify_quotes | 生成答案中的引文未通过原文校验 | `3302e1e2ceab7adeba5cead7517f5d20` |
| system | b1dd861994fd | block2_answerable | 拒答误判 | kb.generate | 生成模型判定证据不足 | `f2917a6b19e95e41da7c355c5dba3df1` |
| system | bf9bf0fc45fb | block2_answerable | 拒答误判 | kb.generate | 生成模型判定证据不足 | `5d9d82488ab18e33e13214d49c32c683` |
| system | c2d070b4aad5 | block2_answerable | 召回但排序靠后未进上下文 | kb.search.rerank | gold 在 rerank 输入（rerank 前排名 17），rerank 后 top10 排名 5，未进前 3 条证 | `da690150e41f04514548c75b3fd2ac94` |
| system | c47d7ac50c2c | block2_answerable | 引用错误 | kb.verify_quotes | 生成答案中的引文未通过原文校验 | `57b8d28ed9b74c2b1e37865236d7ccf6` |
| system | c58a7232bed8 | block2_answerable | 拒答误判 | kb.rerank_gate | 证据不足（rerank 最高分 0.925 < 阈值 0.94） | `700f968d9fd728b6decc72f8c627b560` |
| system | c74358182299 | block2_answerable | 其他 | kb.generate | 答案判错但证据支持（答非所问/不完整） | `8def6c7e7cc1a362a4e8685bb69f5522` |
| system | d32ecc8c585b | block2_answerable | 引用错误 | kb.verify_quotes | 生成答案中的引文未通过原文校验 | `54e05534e7b1c01bccd7930dd7c17ba5` |
| system | d5b61dc62c88 | block2_answerable | 检索未召回 | kb.retrieve | gold 不在候选池（20 个候选） | `0784829025ee511ca221eaa62c64fa66` |
| system | da11868cf750 | block2_answerable | 拒答误判 | kb.generate | 生成模型判定证据不足 | `27569d7d7cd8f75e134d7599e064d2b3` |
| system | dac4b8f08e13 | block2_answerable | 引用错误 | kb.verify_quotes | 生成答案中的引文未通过原文校验 | `64a8531a50a75bd5a076cebf87fc8200` |
| system | e06fe0b50ab6 | block2_answerable | 检索未召回 | kb.retrieve | gold 不在候选池（20 个候选） | `587b0c47d24c01d63a75b0fee64750e7` |
| system | e30b43ef0328 | block2_answerable | 拒答误判 | kb.generate | 生成模型判定证据不足 | `23d618047b37f4a83c53d7cf948aa8b8` |
| system | ecf3e594426e | block2_answerable | 拒答误判 | kb.rerank_gate | 证据不足（rerank 最高分 0.825 < 阈值 0.94） | `a32cb6cb0f31e8ec59007cd6b1a380cf` |
| system | f122ce1c54ff | block2_answerable | 拒答误判 | kb.generate | 生成模型判定证据不足 | `fa3121417d71f3bc6fb848dd9674c759` |
| system | f2f2a8c1c06b | block2_answerable | 拒答误判 | kb.rerank_gate | 证据不足（rerank 最高分 0.852 < 阈值 0.94） | `8aaee83c3324e7512739f51665e37e1b` |
| system | f5db587695fb | block2_answerable | 引用错误 | kb.verify_quotes | 生成答案中的引文未通过原文校验 | `d7b860aa0a00cfa207e690bb26bdb632` |
| system | fc37d37bee7a | block2_answerable | 召回但排序靠后未进上下文 | kb.search.rerank | gold 在 rerank 输入（rerank 前排名 14），rerank 后 top10 排名 None，未进前 3 | `c25be2930e24a25425f284bb3a8296c7` |
| system | 07a916d9b255 | lowoverlap | 拒答误判 | kb.generate | 生成模型判定证据不足 | `0616af990f60cd059d161e67201c77c3` |
| system | 0cc953f69701 | lowoverlap | 检索未召回 | kb.retrieve | gold 不在候选池（20 个候选） | `4f6ea8a0c42c6a8d49ae229e96b77e4f` |
| system | 0dc1aa08165d | lowoverlap | 拒答误判 | kb.rerank_gate | 证据不足（rerank 最高分 0.938 < 阈值 0.94） | `c0d6f324d5ded9d85e32d2d8048e40be` |
| system | 1013399c8bdb | lowoverlap | 拒答误判 | kb.generate | 生成模型判定证据不足 | `fb652db378b3882fef1091ba9fcec0d6` |
| system | 1106faa251e3 | lowoverlap | 检索未召回 | kb.retrieve | gold 不在候选池（20 个候选） | `28e8ad3ab544bc9d0a6f7634e938948a` |
| system | 129e41c7a7ea | lowoverlap | 召回但排序靠后未进上下文 | kb.search.rerank | gold 在 rerank 输入（rerank 前排名 3），rerank 后 top10 排名 5，未进前 3 条证据 | `ad9f105622b4b5909d10513dfd8d0bb8` |
| system | 1342c14163c2 | lowoverlap | 拒答误判 | kb.rerank_gate | 证据不足（rerank 最高分 0.779 < 阈值 0.94） | `129410a344ba09063c3545f80cd08fbd` |
| system | 13b1f83d3c89 | lowoverlap | 召回但排序靠后未进上下文 | kb.search.rerank | gold 在 rerank 输入（rerank 前排名 4），rerank 后 top10 排名 6，未进前 3 条证据 | `4e155b213f467186940842cf3eb49fc3` |
| system | 1473381d0477 | lowoverlap | 检索未召回 | kb.retrieve | gold 不在候选池（20 个候选） | `c69fe199843543dcd6c45594bcef3080` |
| system | 179f1664c395 | lowoverlap | 拒答误判 | kb.rerank_gate | 证据不足（rerank 最高分 0.930 < 阈值 0.94） | `1bf3a907bdee70b40aa40ac214ae4c85` |
| system | 1aa2788d950c | lowoverlap | 拒答误判 | kb.rerank_gate | 证据不足（rerank 最高分 0.346 < 阈值 0.94） | `59a5c291569db1837f190b0e2c3b2f31` |
| system | 1eee8ee7c7a4 | lowoverlap | 检索未召回 | kb.retrieve | gold 不在候选池（20 个候选） | `8b61a2b37054aa7e3da1ec999a5543bb` |
| system | 238e33937d23 | lowoverlap | 拒答误判 | kb.generate | 生成模型判定证据不足 | `83b56fe3554b03db2c2dc00b9adf73a5` |
| system | 23deb71388b8 | lowoverlap | 召回但排序靠后未进上下文 | kb.search.rerank | gold 在 rerank 输入（rerank 前排名 8），rerank 后 top10 排名 None，未进前 3  | `334120d147e009ccb76b3b41f67bd542` |
| system | 2d7383b642dc | lowoverlap | 拒答误判 | kb.rerank_gate | 证据不足（rerank 最高分 0.872 < 阈值 0.94） | `a4e9b5098bf1d9203b2f69c5de015764` |
| system | 2e87f6b03612 | lowoverlap | 引用错误 | kb.verify_quotes | 生成答案中的引文未通过原文校验 | `ad08b9fcc3bc16efcecf1049e217c4b2` |
| system | 2ea1405144a8 | lowoverlap | 拒答误判 | kb.rerank_gate | 证据不足（rerank 最高分 0.735 < 阈值 0.94） | `655a356392f17e9fe0dfea766169f81c` |
| system | 3408311872bb | lowoverlap | 召回但排序靠后未进上下文 | kb.search.rerank | gold 在 rerank 输入（rerank 前排名 1），rerank 后 top10 排名 6，未进前 3 条证据 | `2c1a1de85beba55b60dd6ed8e3d49128` |
| system | 38b234dee0d6 | lowoverlap | 拒答误判 | kb.rerank_gate | 证据不足（rerank 最高分 0.631 < 阈值 0.94） | `1a9b6ce76f03b971892e0f7d6c9cfab1` |
| system | 396947718903 | lowoverlap | 召回但排序靠后未进上下文 | kb.search.rerank | gold 在 rerank 输入（rerank 前排名 4），rerank 后 top10 排名 9，未进前 3 条证据 | `32db31e53935840179059e7d310015d7` |
| system | 3afe892b2d1b | lowoverlap | 召回但排序靠后未进上下文 | kb.search.rerank | gold 在 rerank 输入（rerank 前排名 19），rerank 后 top10 排名 6，未进前 3 条证 | `8f526005a53524af33cffb00ef693d97` |
| system | 3e4b52b5abf5 | lowoverlap | 拒答误判 | kb.rerank_gate | 证据不足（rerank 最高分 0.622 < 阈值 0.94） | `e53bd3c589fcb9ae8e10076e72554c42` |
| system | 43847fec6f1d | lowoverlap | 召回但排序靠后未进上下文 | kb.search.rerank | gold 在 rerank 输入（rerank 前排名 8），rerank 后 top10 排名 8，未进前 3 条证据 | `44c6c76dd3dc904c493efc46e687de67` |
| system | 46030910286c | lowoverlap | 拒答误判 | kb.rerank_gate | 证据不足（rerank 最高分 0.886 < 阈值 0.94） | `194135fb292808d06c72b19d02cd50d1` |
| system | 4caf08abb60b | lowoverlap | 拒答误判 | kb.rerank_gate | 证据不足（rerank 最高分 0.686 < 阈值 0.94） | `79d3a1362aa1d83a269e08c469bd03cc` |
| system | 4e2f1eaa9288 | lowoverlap | 拒答误判 | kb.rerank_gate | 证据不足（rerank 最高分 0.330 < 阈值 0.94） | `0c6f353344b02b4d764965dec477f258` |
| system | 5079f6cd3c4a | lowoverlap | 召回但排序靠后未进上下文 | kb.search.rerank | gold 在 rerank 输入（rerank 前排名 1），rerank 后 top10 排名 5，未进前 3 条证据 | `428695745f0fac4fc1eaefd50d73acfc` |
| system | 544313ec9688 | lowoverlap | 召回但排序靠后未进上下文 | kb.search.rerank | gold 在 rerank 输入（rerank 前排名 13），rerank 后 top10 排名 9，未进前 3 条证 | `b1ebade49feacfab000521623e79953d` |
| system | 5541d938d9e3 | lowoverlap | 拒答误判 | kb.rerank_gate | 证据不足（rerank 最高分 0.528 < 阈值 0.94） | `508f5d754282fc7e73bb38c16a2a55a5` |
| system | 55d970d97d9e | lowoverlap | 检索未召回 | kb.retrieve | gold 不在候选池（20 个候选） | `d02fecb516db53722212a0a582653caf` |
| system | 59034e72e7e2 | lowoverlap | 引用错误 | kb.verify_quotes | 生成答案中的引文未通过原文校验 | `1c29914cacd830f1b2e13be25c792de3` |
| system | 68d83e94b7f1 | lowoverlap | 拒答误判 | kb.rerank_gate | 证据不足（rerank 最高分 0.232 < 阈值 0.94） | `d8157a52675b4361614ccb060e22736e` |
| system | 69fc8f3c00df | lowoverlap | 召回但排序靠后未进上下文 | kb.search.rerank | gold 在 rerank 输入（rerank 前排名 1），rerank 后 top10 排名 4，未进前 3 条证据 | `49e482822cd24bf43ed7e7ee04e09e56` |
| system | 6b5cf57b7f83 | lowoverlap | 拒答误判 | kb.rerank_gate | 证据不足（rerank 最高分 0.699 < 阈值 0.94） | `df2c88da948492a0900fb4e4feb01ea6` |
| system | 71a52060cdda | lowoverlap | 拒答误判 | kb.rerank_gate | 证据不足（rerank 最高分 0.719 < 阈值 0.94） | `72135686582a1fcb50025f33a150b367` |
| system | 731eefa9ed48 | lowoverlap | 检索未召回 | kb.retrieve | gold 不在候选池（20 个候选） | `80b96199728b08aba88a3c1e7052cabe` |
| system | 7cb7e382e220 | lowoverlap | 召回但排序靠后未进上下文 | kb.search.rerank | gold 在 rerank 输入（rerank 前排名 16），rerank 后 top10 排名 None，未进前 3 | `0f2fc1f50acfd72050dd2c881a983066` |
| system | 801c9ad867f7 | lowoverlap | 拒答误判 | kb.generate | 生成模型判定证据不足 | `d95565c862ddd889b343201d3c7b675a` |
| system | 861bccb15e04 | lowoverlap | 拒答误判 | kb.rerank_gate | 证据不足（rerank 最高分 0.416 < 阈值 0.94） | `c1b3dd465514085856b1aeffddb930bc` |
| system | 86e10c40b395 | lowoverlap | 召回但排序靠后未进上下文 | kb.search.rerank | gold 在 rerank 输入（rerank 前排名 1），rerank 后 top10 排名 5，未进前 3 条证据 | `ef09e97fa96fe905676a83f323b37fde` |
| system | 8911ca04fd60 | lowoverlap | 拒答误判 | kb.rerank_gate | 证据不足（rerank 最高分 0.408 < 阈值 0.94） | `2a52f5bd208db132c9793fdadbf4d9ca` |
| system | 8b0f1d5f0bfd | lowoverlap | 召回但排序靠后未进上下文 | kb.search.rerank | gold 在 rerank 输入（rerank 前排名 1），rerank 后 top10 排名 5，未进前 3 条证据 | `086e72d736d620b67656959eed1051f1` |
| system | 8d4959b8b4c6 | lowoverlap | 拒答误判 | kb.rerank_gate | 证据不足（rerank 最高分 0.560 < 阈值 0.94） | `d1edceea99cd2b4f8299fc75d176d854` |
| system | 8d82772a158a | lowoverlap | 拒答误判 | kb.generate | 生成模型判定证据不足 | `7309e3ac1a90592585ece072517aa266` |
| system | 95329ae14239 | lowoverlap | 其他 | kb.generate | 答案判错但证据支持（答非所问/不完整） | `1b5595ad1f1e59db690be7c42c1d4367` |
| system | 9694fbb3d098 | lowoverlap | 召回但排序靠后未进上下文 | kb.search.rerank | gold 在 rerank 输入（rerank 前排名 6），rerank 后 top10 排名 6，未进前 3 条证据 | `d15552ea8d7f9b1d4d6f1c9ed6cd0a89` |
| system | 9ce2bab40b6f | lowoverlap | 检索未召回 | kb.retrieve | gold 不在候选池（20 个候选） | `f7bf4f1597853c2c85d8b5d185ab9e20` |
| system | a32e65e3de76 | lowoverlap | 召回但排序靠后未进上下文 | kb.search.rerank | gold 在 rerank 输入（rerank 前排名 5），rerank 后 top10 排名 9，未进前 3 条证据 | `8d216d508164107c2c38b8090e904381` |
| system | a58464555ba8 | lowoverlap | 拒答误判 | kb.rerank_gate | 证据不足（rerank 最高分 0.761 < 阈值 0.94） | `28dc259735b8062a70306d139e30b0f0` |
| system | a7abdec587e5 | lowoverlap | 拒答误判 | kb.rerank_gate | 证据不足（rerank 最高分 0.919 < 阈值 0.94） | `890171ed1fc7e1d0e1707d4e552c6305` |
| system | a95e93b413ab | lowoverlap | 检索未召回 | kb.retrieve | gold 不在候选池（20 个候选） | `95f798ecfa406012e1f584451a0f0cba` |
| system | b3c5b9a1b4ac | lowoverlap | 拒答误判 | kb.rerank_gate | 证据不足（rerank 最高分 0.839 < 阈值 0.94） | `febde2daac845642b4beaa407f57652f` |
| system | b40f5c8d9596 | lowoverlap | 召回但排序靠后未进上下文 | kb.search.rerank | gold 在 rerank 输入（rerank 前排名 15），rerank 后 top10 排名 7，未进前 3 条证 | `dc27c06e4526125c581184bf133deee2` |
| system | bffaa68f38db | lowoverlap | 检索未召回 | kb.retrieve | gold 不在候选池（20 个候选） | `052693818fb8b97ef11b550545958685` |
| system | c147513246ec | lowoverlap | 检索未召回 | kb.retrieve | gold 不在候选池（20 个候选） | `9523fdcc7a5e0e6d31f26f4769eae094` |
| system | c2de3d025774 | lowoverlap | 拒答误判 | kb.generate | 生成模型判定证据不足 | `75e1c5451f8c8a10b0a820130acdb616` |
| system | c9232d4ceafc | lowoverlap | 检索未召回 | kb.retrieve | gold 不在候选池（20 个候选） | `4da3b4bad2b5645fe46a87d00f91ed50` |
| system | cad41c754210 | lowoverlap | 拒答误判 | kb.generate | 生成模型判定证据不足 | `baba7b5aabd4f58441c48605fe27e729` |
| system | d8c8b197a892 | lowoverlap | 引用错误 | kb.verify_quotes | 生成答案中的引文未通过原文校验 | `66b26e34fd8c2bf919f5f351e65bbb63` |
| system | db8d56285a0a | lowoverlap | 拒答误判 | kb.generate | 生成模型判定证据不足 | `8bba67745bd7de6c1509533e5a895498` |
| system | e512952d9b83 | lowoverlap | 拒答误判 | kb.rerank_gate | 证据不足（rerank 最高分 0.407 < 阈值 0.94） | `1ce76e2764ba97058e421530a1b0e7ce` |
| system | e95aaad379f0 | lowoverlap | 拒答误判 | kb.rerank_gate | 证据不足（rerank 最高分 0.854 < 阈值 0.94） | `9815855df43b831dd1b9b92c005db3b6` |
| system | eccd25e47c59 | lowoverlap | 检索未召回 | kb.retrieve | gold 不在候选池（20 个候选） | `aa82bd2fe40574b02eb9dba6007ecb2a` |
| system | f357ed5d37e9 | lowoverlap | 拒答误判 | kb.rerank_gate | 证据不足（rerank 最高分 0.663 < 阈值 0.94） | `5eebc7790ab25e89102325ce51b98a33` |
| system | f811eef5910c | lowoverlap | 拒答误判 | kb.rerank_gate | 证据不足（rerank 最高分 0.754 < 阈值 0.94） | `bc6420513e37dba270ca05b095026893` |
| system | f8c139823722 | lowoverlap | 召回但排序靠后未进上下文 | kb.search.rerank | gold 在 rerank 输入（rerank 前排名 2），rerank 后 top10 排名 4，未进前 3 条证据 | `72cd925f18e729c2c31f227cd074cc31` |
| baseline_bm25 | 00a7d241c16f | block2_answerable | 召回但排序靠后未进上下文 | kb.retrieve | gold 在 top10 第 5 位，未进前 3 条证据 | `67582c337219c4ac16f255cdebd6b8e2` |
| baseline_bm25 | 05fb9ea54fcf | block2_answerable | 拒答误判 | kb.generate | 生成模型判定证据不足 | `31a804fab927dd68a0854acf61701c70` |
| baseline_bm25 | 09647c07c7ea | block2_answerable | 拒答误判 | kb.generate | 生成模型判定证据不足 | `c2abe7e83fd9e2be52e10cc7c102c1db` |
| baseline_bm25 | 0c97eca8f45d | block2_answerable | 召回但排序靠后未进上下文 | kb.retrieve | gold 在 top10 第 8 位，未进前 3 条证据 | `437ebed5ab35549d5c0428db24ab88f7` |
| baseline_bm25 | 0cf31478a8b1 | block2_answerable | 召回但排序靠后未进上下文 | kb.retrieve | gold 在 top10 第 8 位，未进前 3 条证据 | `6e15dbdf3a0e01859d6b283147a8a23a` |
| baseline_bm25 | 133b3fd390d2 | block2_answerable | 召回但排序靠后未进上下文 | kb.retrieve | gold 在 top10 第 8 位，未进前 3 条证据 | `4059536b84055f9f78746285adfa9619` |
| baseline_bm25 | 1372bf4d9eb0 | block2_answerable | 召回但排序靠后未进上下文 | kb.retrieve | gold 在 top10 第 5 位，未进前 3 条证据 | `b6a57450c6906a76ca786cc7819f67f9` |
| baseline_bm25 | 223f1ae87b9f | block2_answerable | 召回但排序靠后未进上下文 | kb.retrieve | gold 在 top10 第 8 位，未进前 3 条证据 | `808024a6ac529bdeb93d5e912c7831e7` |
| baseline_bm25 | 244b6491b98d | block2_answerable | 召回但排序靠后未进上下文 | kb.retrieve | gold 在 top10 第 7 位，未进前 3 条证据 | `5aec79a187e622aca268f7cdfb1c676b` |
| baseline_bm25 | 25cfe98dd412 | block2_answerable | 引用错误 | kb.verify_quotes | 生成答案中的引文未通过原文校验 | `6abb84339e326b370a7e2237d91cffe5` |
| baseline_bm25 | 275c3916efb6 | block2_answerable | 工具或步骤错误 | kb.cite | 没有通过原文校验的引用 | `f7e6968fa8d84682c73efd4e2f1202cf` |
| baseline_bm25 | 294ee5f08c1e | block2_answerable | 引用错误 | kb.verify_quotes | 生成答案中的引文未通过原文校验 | `a847c1529150144774706396a082de62` |
| baseline_bm25 | 2b5cbaa7e90a | block2_answerable | 引用错误 | kb.verify_quotes | 生成答案中的引文未通过原文校验 | `e743509b62579c0cdbcaebdac9b37162` |
| baseline_bm25 | 2b7b07f82223 | block2_answerable | 引用错误 | kb.verify_quotes | 生成答案中的引文未通过原文校验 | `0eccc90dd426da3237543416e8adf83e` |
| baseline_bm25 | 2bdf76fa9dbe | block2_answerable | 召回但排序靠后未进上下文 | kb.retrieve | gold 在 top10 第 7 位，未进前 3 条证据 | `f41d7645a80bd4cefd3b0d02ed5b62c7` |
| baseline_bm25 | 308400da3dc2 | block2_answerable | 引用错误 | kb.verify_quotes | 生成答案中的引文未通过原文校验 | `57c065aed767595eca60c797b150f6cc` |
| baseline_bm25 | 30ce073109b0 | block2_answerable | 引用错误 | kb.verify_quotes | 生成答案中的引文未通过原文校验 | `c3814ab1249e32ddc88c3138a7af26a9` |
| baseline_bm25 | 32b4c1066f57 | block2_answerable | 引用错误 | kb.verify_quotes | 生成答案中的引文未通过原文校验 | `2423bb3ee0e881fd95d4e0307b73e7e7` |
| baseline_bm25 | 33175b4e3690 | block2_answerable | 召回但排序靠后未进上下文 | kb.retrieve | gold 在 top10 第 7 位，未进前 3 条证据 | `e35e4c5b755348cb85d1f284f6f1bce7` |
| baseline_bm25 | 35c92d294b6f | block2_answerable | 拒答误判 | kb.evidence_gate | 证据不足（最高覆盖率 0.38 < 阈值 0.5） | `164248700075f6f4c4101d706ce74312` |
| baseline_bm25 | 42cb6ba4c772 | block2_answerable | 引用错误 | kb.verify_quotes | 生成答案中的引文未通过原文校验 | `3db281566af5b473050dd242d1897e4c` |
| baseline_bm25 | 440d830e21e9 | block2_answerable | 拒答误判 | kb.evidence_gate | 证据不足（最高覆盖率 0.47 < 阈值 0.5） | `f11f1dae8b1adfa82aa0f55d6c07a229` |
| baseline_bm25 | 483b9faa5897 | block2_answerable | 召回但排序靠后未进上下文 | kb.retrieve | gold 在 top10 第 7 位，未进前 3 条证据 | `70b5eb02bbac9d1c654269862e0a2a87` |
| baseline_bm25 | 496c2715cff6 | block2_answerable | 其他 | kb.generate | 答案判错但证据支持（答非所问/不完整） | `adae0ea9a1bb62bbfb8711dc6e07df0f` |
| baseline_bm25 | 4c97176851af | block2_answerable | 拒答误判 | kb.evidence_gate | 证据不足（最高覆盖率 0.38 < 阈值 0.5） | `ebc71d305a9a9b3d411f8ae3d6bcce12` |
| baseline_bm25 | 549f57170e8d | block2_answerable | 召回但排序靠后未进上下文 | kb.retrieve | gold 在 top10 第 5 位，未进前 3 条证据 | `ab4c58658a574fa9dd53e9116d9c2ca0` |
| baseline_bm25 | 54ad3afa62e1 | block2_answerable | 拒答误判 | kb.generate | 生成模型判定证据不足 | `660121ec95b0c92ca883ffc7d9a68754` |
| baseline_bm25 | 585569a7e2b2 | block2_answerable | 其他 | kb.generate | 答案判错但证据支持（答非所问/不完整） | `03de0193cb276886531964c14154e5f0` |
| baseline_bm25 | 5a46aad5e22c | block2_answerable | 拒答误判 | kb.evidence_gate | 证据不足（最高覆盖率 0.33 < 阈值 0.5） | `ab54ed8322109b32327c8b2faf0092d5` |
| baseline_bm25 | 5bcc1cc278c9 | block2_answerable | 拒答误判 | kb.generate | 生成模型判定证据不足 | `8661b8f41ad6d35f5eb55715d9f80568` |
| baseline_bm25 | 5becc812d812 | block2_answerable | 拒答误判 | kb.evidence_gate | 证据不足（最高覆盖率 0.43 < 阈值 0.5） | `a5d97c429db6d374066cb36cc6f28353` |
| baseline_bm25 | 5dc0d1b13f5b | block2_answerable | 检索未召回 | kb.retrieve | gold 不在候选池（10 个候选） | `c70968e8c7bc4c62a5093944bd726926` |
| baseline_bm25 | 5dfe8fbe0424 | block2_answerable | 引用错误 | kb.verify_quotes | 生成答案中的引文未通过原文校验 | `d2d9a3b251551d3fedbd6441894a5912` |
| baseline_bm25 | 5e3bd65d0f9f | block2_answerable | 工具或步骤错误 | kb.cite | 没有通过原文校验的引用 | `7158869539c72bb8549b6997132f9137` |
| baseline_bm25 | 627ea0d4ac2c | block2_answerable | 检索未召回 | kb.retrieve | gold 不在候选池（10 个候选） | `3abc28f33f87e8e71389ff200cff142e` |
| baseline_bm25 | 6889f1cf4fbd | block2_answerable | 引用错误 | kb.verify_quotes | 生成答案中的引文未通过原文校验 | `72c1943678609e892c1e22f7ac57641c` |
| baseline_bm25 | 68f552b331dc | block2_answerable | 引用错误 | kb.verify_quotes | 生成答案中的引文未通过原文校验 | `6e23793f127b4692b0d9299cdc552c38` |
| baseline_bm25 | 6a5a89351303 | block2_answerable | 拒答误判 | kb.generate | 生成模型判定证据不足 | `3a6e408084ecda8367b1046d9f2fa0ea` |
| baseline_bm25 | 7269ecd94e28 | block2_answerable | 拒答误判 | kb.generate | 生成模型判定证据不足 | `f4a9efd065d33f25153513db0afa4fbd` |
| baseline_bm25 | 736995ec8a2a | block2_answerable | 召回但排序靠后未进上下文 | kb.retrieve | gold 在 top10 第 4 位，未进前 3 条证据 | `9ef2956c25c289a1aff9b50c39eb871c` |
| baseline_bm25 | 73f3396dcdbb | block2_answerable | 其他 | kb.generate | 答案判错但证据支持（答非所问/不完整） | `fea1db349cd6f969925b9f965acddbc2` |
| baseline_bm25 | 7b0ada7ebe53 | block2_answerable | 召回但排序靠后未进上下文 | kb.retrieve | gold 在 top10 第 7 位，未进前 3 条证据 | `ce50ed98af156ad9b87e12a18c181ac9` |
| baseline_bm25 | 7d77d542f185 | block2_answerable | 引用错误 | kb.verify_quotes | 生成答案中的引文未通过原文校验 | `cade0fa769d49ddb21b2b4c07e9227ab` |
| baseline_bm25 | 825e12a62ed9 | block2_answerable | 召回但排序靠后未进上下文 | kb.retrieve | gold 在 top10 第 10 位，未进前 3 条证据 | `91a1123fb8daea6f4c5b13aea0c3f713` |
| baseline_bm25 | 83b4585b8f47 | block2_answerable | 召回但排序靠后未进上下文 | kb.retrieve | gold 在 top10 第 5 位，未进前 3 条证据 | `410e0f0c9d3b8da853bbaf9116583b87` |
| baseline_bm25 | 845cda58e32b | block2_answerable | 拒答误判 | kb.generate | 生成模型判定证据不足 | `c363a9a78bcba9106e35146513e886a1` |
| baseline_bm25 | 86ec28a9e2f7 | block2_answerable | 引用错误 | kb.verify_quotes | 生成答案中的引文未通过原文校验 | `6a5a1fc47f7cc36b9473f3938442815b` |
| baseline_bm25 | 88bd1e01c89f | block2_answerable | 引用错误 | kb.verify_quotes | 生成答案中的引文未通过原文校验 | `d5d434023da2dd04e1cfa7ce940662b0` |
| baseline_bm25 | 88eba932e84c | block2_answerable | 拒答误判 | kb.generate | 生成模型判定证据不足 | `74dc30fac7971a4540a87495a09ff91c` |
| baseline_bm25 | 8fdf14f14c61 | block2_answerable | 引用错误 | kb.verify_quotes | 生成答案中的引文未通过原文校验 | `d68a3f370a8668dce3f2ff98aaee9104` |
| baseline_bm25 | 96f9fad72078 | block2_answerable | 引用错误 | kb.verify_quotes | 生成答案中的引文未通过原文校验 | `5978569a2d507d248e471c5aca96b157` |
| baseline_bm25 | 9a11144a2c61 | block2_answerable | 检索未召回 | kb.retrieve | gold 不在候选池（10 个候选） | `41b6f3a1c09282951a268a981d6d1382` |
| baseline_bm25 | 9a130fd48890 | block2_answerable | 拒答误判 | kb.evidence_gate | 证据不足（最高覆盖率 0.33 < 阈值 0.5） | `ef30a58acd6937c83f43bdb9e80266f8` |
| baseline_bm25 | 9c8d0789b805 | block2_answerable | 拒答误判 | kb.generate | 生成模型判定证据不足 | `52ca4f4a8cc98fa51830d1f222f6691c` |
| baseline_bm25 | 9cb891101a34 | block2_answerable | 拒答误判 | kb.generate | 生成模型判定证据不足 | `e06359d6400b3d459a756ceb59a512c3` |
| baseline_bm25 | a34cfd13d1ff | block2_answerable | 拒答误判 | kb.evidence_gate | 证据不足（最高覆盖率 0.35 < 阈值 0.5） | `06da136acfca2efa70f1d5133c88415c` |
| baseline_bm25 | a4bc32110b39 | block2_answerable | 拒答误判 | kb.evidence_gate | 证据不足（最高覆盖率 0.44 < 阈值 0.5） | `ece4e1eb715a5723e27493d0fef41126` |
| baseline_bm25 | a9e5a2acaf35 | block2_answerable | 引用错误 | kb.verify_quotes | 生成答案中的引文未通过原文校验 | `2135b68ac6a85e760325e86f6a2d39fc` |
| baseline_bm25 | b1dd861994fd | block2_answerable | 检索未召回 | kb.retrieve | gold 不在候选池（10 个候选） | `63567c5cd9523964d28264f976f91d2f` |
| baseline_bm25 | b8f227d82f8c | block2_answerable | 拒答误判 | kb.generate | 生成模型判定证据不足 | `ee3609bbfb39e434b89688a0eee1ff4d` |
| baseline_bm25 | ba4df6f7d94f | block2_answerable | 召回但排序靠后未进上下文 | kb.retrieve | gold 在 top10 第 4 位，未进前 3 条证据 | `06daa8d0249294b40bd8bd1bdae92f03` |
| baseline_bm25 | c298804ae6c7 | block2_answerable | 召回但排序靠后未进上下文 | kb.retrieve | gold 在 top10 第 10 位，未进前 3 条证据 | `40752c88638f41ea9acb89cfc69577a2` |
| baseline_bm25 | c2d070b4aad5 | block2_answerable | 召回但排序靠后未进上下文 | kb.retrieve | gold 在 top10 第 7 位，未进前 3 条证据 | `bf1de9fb5b13993949c775eca5b804e6` |
| baseline_bm25 | c342df63fa51 | block2_answerable | 引用错误 | kb.verify_quotes | 生成答案中的引文未通过原文校验 | `39fe9a34cbca7f705380317a09d1cba7` |
| baseline_bm25 | c407d9690ba1 | block2_answerable | 其他 | kb.generate | 答案判错但证据支持（答非所问/不完整） | `d395a464274ce513ed4323c9d99617f7` |
| baseline_bm25 | c43f62144d84 | block2_answerable | 召回但排序靠后未进上下文 | kb.retrieve | gold 在 top10 第 4 位，未进前 3 条证据 | `08327c2e54aee2fc9dcb5ddd729a87ed` |
| baseline_bm25 | c58a7232bed8 | block2_answerable | 检索未召回 | kb.retrieve | gold 不在候选池（10 个候选） | `f3a568d93f01dbce5a580ca6a62a1778` |
| baseline_bm25 | c74358182299 | block2_answerable | 召回但排序靠后未进上下文 | kb.retrieve | gold 在 top10 第 4 位，未进前 3 条证据 | `7ab449442f69cf24ffa8a098c96566fb` |
| baseline_bm25 | d32ecc8c585b | block2_answerable | 引用错误 | kb.verify_quotes | 生成答案中的引文未通过原文校验 | `f266e958ba3b2d875f343170470ec670` |
| baseline_bm25 | d81671a756d6 | block2_answerable | 其他 | kb.generate | 答案判错但证据支持（答非所问/不完整） | `e340b42a1a1fcaf4f59e1c465d4f86f2` |
| baseline_bm25 | da11868cf750 | block2_answerable | 其他 | kb.generate | 答案判错但证据支持（答非所问/不完整） | `d67c637646167ad36f23406cd6a7c3af` |
| baseline_bm25 | dac4b8f08e13 | block2_answerable | 引用错误 | kb.verify_quotes | 生成答案中的引文未通过原文校验 | `b81445567008bc8474cc3321481bff8c` |
| baseline_bm25 | dd1d39e2c424 | block2_answerable | 其他 | kb.generate | 答案判错但证据支持（答非所问/不完整） | `ca685ff3d55bb53fab342f25c5aead49` |
| baseline_bm25 | e06fe0b50ab6 | block2_answerable | 召回但排序靠后未进上下文 | kb.retrieve | gold 在 top10 第 7 位，未进前 3 条证据 | `f8f8ecce749535323db3469c8fd828f9` |
| baseline_bm25 | e30b43ef0328 | block2_answerable | 召回但排序靠后未进上下文 | kb.retrieve | gold 在 top10 第 8 位，未进前 3 条证据 | `0bbaeec61fbe2109438599a9e412306d` |
| baseline_bm25 | e5624e919eeb | block2_answerable | 其他 | kb.generate | 答案判错但证据支持（答非所问/不完整） | `8d60665b220fb2fc534ee6e5e5c3fbca` |
| baseline_bm25 | e8f58af95297 | block2_answerable | 引用错误 | kb.verify_quotes | 生成答案中的引文未通过原文校验 | `934047e24a496b7dc56dacee14523e15` |
| baseline_bm25 | ebb376b1c2de | block2_answerable | 引用错误 | kb.verify_quotes | 生成答案中的引文未通过原文校验 | `a33a13bc005eec530349961b47056739` |
| baseline_bm25 | ef7b1ef20f31 | block2_answerable | 引用错误 | kb.verify_quotes | 生成答案中的引文未通过原文校验 | `4dcbc89bd7eda2e1e15b8fbb6f2d1308` |
| baseline_bm25 | f2f2a8c1c06b | block2_answerable | 拒答误判 | kb.evidence_gate | 证据不足（最高覆盖率 0.43 < 阈值 0.5） | `201678761ae07001832f9061edded144` |
| baseline_bm25 | f635bf29aeef | block2_answerable | 召回但排序靠后未进上下文 | kb.retrieve | gold 在 top10 第 7 位，未进前 3 条证据 | `77a761d46094d89bcd09e78adfa17629` |
| baseline_bm25 | fa900145258d | block2_answerable | 召回但排序靠后未进上下文 | kb.retrieve | gold 在 top10 第 6 位，未进前 3 条证据 | `bd16492544c6e11dca7fea7f6dabb47b` |
| baseline_bm25 | fc37d37bee7a | block2_answerable | 检索未召回 | kb.retrieve | gold 不在候选池（10 个候选） | `6b506f85642b1f7eb7f86ead00a5cfd7` |
| baseline_bm25 | 07030be41d1c | lowoverlap | 拒答误判 | kb.evidence_gate | 证据不足（最高覆盖率 0.24 < 阈值 0.5） | `b91ff80387b848a678bd72c4bca720d6` |
| baseline_bm25 | 07a916d9b255 | lowoverlap | 拒答误判 | kb.evidence_gate | 证据不足（最高覆盖率 0.22 < 阈值 0.5） | `4a661062baa8bb3972177f819d39b0cf` |
| baseline_bm25 | 0cc953f69701 | lowoverlap | 检索未召回 | kb.retrieve | gold 不在候选池（10 个候选） | `dd01c332289a77a2f0c9f5ae4e7a4107` |
| baseline_bm25 | 0dc1aa08165d | lowoverlap | 拒答误判 | kb.evidence_gate | 证据不足（最高覆盖率 0.25 < 阈值 0.5） | `59cb8dbf985417a3f2a8b894a84c2494` |
| baseline_bm25 | 1013399c8bdb | lowoverlap | 召回但排序靠后未进上下文 | kb.retrieve | gold 在 top10 第 5 位，未进前 3 条证据 | `8fe3e47a7de38f5a033c44697692e690` |
| baseline_bm25 | 1106faa251e3 | lowoverlap | 检索未召回 | kb.retrieve | gold 不在候选池（10 个候选） | `db4a1de504c1b3bbf45116da6248a466` |
| baseline_bm25 | 11d0ca43d05a | lowoverlap | 拒答误判 | kb.evidence_gate | 证据不足（最高覆盖率 0.19 < 阈值 0.5） | `98d66fd6e06548bc360d24dd181457a5` |
| baseline_bm25 | 129e41c7a7ea | lowoverlap | 召回但排序靠后未进上下文 | kb.retrieve | gold 在 top10 第 10 位，未进前 3 条证据 | `806dcb4d10f18ae1d440a73c3cd8eb34` |
| baseline_bm25 | 1342c14163c2 | lowoverlap | 检索未召回 | kb.retrieve | gold 不在候选池（10 个候选） | `9ae41b9683ee544f1af05e2a5834f726` |
| baseline_bm25 | 13b1f83d3c89 | lowoverlap | 召回但排序靠后未进上下文 | kb.retrieve | gold 在 top10 第 10 位，未进前 3 条证据 | `a13a18ec12c6ef4c33e705ac39a9c2c5` |
| baseline_bm25 | 1473381d0477 | lowoverlap | 召回但排序靠后未进上下文 | kb.retrieve | gold 在 top10 第 10 位，未进前 3 条证据 | `104681dd85ffea5a55ccaeb46d202041` |
| baseline_bm25 | 178106b23540 | lowoverlap | 拒答误判 | kb.evidence_gate | 证据不足（最高覆盖率 0.21 < 阈值 0.5） | `f241612bcc11df792879bb81f4a4150a` |
| baseline_bm25 | 179f1664c395 | lowoverlap | 召回但排序靠后未进上下文 | kb.retrieve | gold 在 top10 第 10 位，未进前 3 条证据 | `77c472a4c1bcfe9a89e0f27c2a2d88a5` |
| baseline_bm25 | 17b4fd6235a3 | lowoverlap | 召回但排序靠后未进上下文 | kb.retrieve | gold 在 top10 第 4 位，未进前 3 条证据 | `1f23e4d1cfd3cb5dea910fe59aca5255` |
| baseline_bm25 | 1aa2788d950c | lowoverlap | 拒答误判 | kb.evidence_gate | 证据不足（最高覆盖率 0.14 < 阈值 0.5） | `c64495493f3ac4d96f8afb0072b56811` |
| baseline_bm25 | 1d6ef485577b | lowoverlap | 拒答误判 | kb.evidence_gate | 证据不足（最高覆盖率 0.29 < 阈值 0.5） | `d4a5c4d30f0632577d63167a021f6023` |
| baseline_bm25 | 1eee8ee7c7a4 | lowoverlap | 检索未召回 | kb.retrieve | gold 不在候选池（10 个候选） | `d736f60e6347c4ab04a5ac800e2693ef` |
| baseline_bm25 | 233867fe8659 | lowoverlap | 拒答误判 | kb.evidence_gate | 证据不足（最高覆盖率 0.35 < 阈值 0.5） | `07eb851a7ed1d5757fd2733788b21f7a` |
| baseline_bm25 | 238e33937d23 | lowoverlap | 拒答误判 | kb.evidence_gate | 证据不足（最高覆盖率 0.18 < 阈值 0.5） | `eeb836de3d2a4c6024bb385f2fed496a` |
| baseline_bm25 | 23deb71388b8 | lowoverlap | 检索未召回 | kb.retrieve | gold 不在候选池（10 个候选） | `6135dec5d3bcbac47fb03d43a27f3650` |
| baseline_bm25 | 26ee47e58f66 | lowoverlap | 检索未召回 | kb.retrieve | gold 不在候选池（10 个候选） | `55e4dff5c53ea2bef11c7c206faaad91` |
| baseline_bm25 | 2735c0a2d4ee | lowoverlap | 召回但排序靠后未进上下文 | kb.retrieve | gold 在 top10 第 4 位，未进前 3 条证据 | `9cf835a1ed30a1715dfa190d45ab958e` |
| baseline_bm25 | 2ce549dc70b0 | lowoverlap | 拒答误判 | kb.evidence_gate | 证据不足（最高覆盖率 0.23 < 阈值 0.5） | `af7c147ddea53e071a0f5dadc88339a3` |
| baseline_bm25 | 2d7383b642dc | lowoverlap | 拒答误判 | kb.evidence_gate | 证据不足（最高覆盖率 0.15 < 阈值 0.5） | `223f01ffbd05bfd20b5185f6ebdf4d4e` |
| baseline_bm25 | 2e87f6b03612 | lowoverlap | 拒答误判 | kb.evidence_gate | 证据不足（最高覆盖率 0.10 < 阈值 0.5） | `7c93be1f3e830fb0f93f3fe51f42e2d8` |
| baseline_bm25 | 2ea1405144a8 | lowoverlap | 拒答误判 | kb.evidence_gate | 证据不足（最高覆盖率 0.10 < 阈值 0.5） | `ea8955c88277478eaef9375bfe36f765` |
| baseline_bm25 | 31f6a6c0fb03 | lowoverlap | 召回但排序靠后未进上下文 | kb.retrieve | gold 在 top10 第 4 位，未进前 3 条证据 | `f3fb03d426dd9ae46da3ffb5cf0a71bf` |
| baseline_bm25 | 3408311872bb | lowoverlap | 拒答误判 | kb.evidence_gate | 证据不足（最高覆盖率 0.10 < 阈值 0.5） | `0243196e35682e2571b6bb261ce9e804` |
| baseline_bm25 | 3610797e7712 | lowoverlap | 拒答误判 | kb.evidence_gate | 证据不足（最高覆盖率 0.23 < 阈值 0.5） | `ebacad9179d5e255e37372b93c2f5c92` |
| baseline_bm25 | 3899315315fa | lowoverlap | 拒答误判 | kb.evidence_gate | 证据不足（最高覆盖率 0.26 < 阈值 0.5） | `67aca8264962e0a90591237f192f5541` |
| baseline_bm25 | 38b234dee0d6 | lowoverlap | 拒答误判 | kb.evidence_gate | 证据不足（最高覆盖率 0.21 < 阈值 0.5） | `b78365f056eab56e596769b50349f2c5` |
| baseline_bm25 | 396947718903 | lowoverlap | 拒答误判 | kb.evidence_gate | 证据不足（最高覆盖率 0.27 < 阈值 0.5） | `52aefca2d51c810c3b7d814719e0f271` |
| baseline_bm25 | 3a82635b39c1 | lowoverlap | 拒答误判 | kb.evidence_gate | 证据不足（最高覆盖率 0.26 < 阈值 0.5） | `541ec75fd9ae017351255c771548c24f` |
| baseline_bm25 | 3afe892b2d1b | lowoverlap | 检索未召回 | kb.retrieve | gold 不在候选池（10 个候选） | `0aadb5b4df4f0a4a1f7de729edd535f7` |
| baseline_bm25 | 3c3e01420ebe | lowoverlap | 拒答误判 | kb.evidence_gate | 证据不足（最高覆盖率 0.19 < 阈值 0.5） | `07a887913ac23b3c580fc86f6f299d12` |
| baseline_bm25 | 3c492d9ae898 | lowoverlap | 检索未召回 | kb.retrieve | gold 不在候选池（10 个候选） | `44784b4796075461091a1e459d8d8ce7` |
| baseline_bm25 | 3e4b52b5abf5 | lowoverlap | 拒答误判 | kb.evidence_gate | 证据不足（最高覆盖率 0.25 < 阈值 0.5） | `7fed66acc39d8c6a31a36d87e168ef69` |
| baseline_bm25 | 40881e1f5f63 | lowoverlap | 拒答误判 | kb.evidence_gate | 证据不足（最高覆盖率 0.18 < 阈值 0.5） | `c348f2e90eed53335247d8ea7eb2bc2d` |
| baseline_bm25 | 43847fec6f1d | lowoverlap | 检索未召回 | kb.retrieve | gold 不在候选池（10 个候选） | `4bd491a4ef9a7170736c3834dd467995` |
| baseline_bm25 | 46030910286c | lowoverlap | 拒答误判 | kb.evidence_gate | 证据不足（最高覆盖率 0.22 < 阈值 0.5） | `712f563a554db5bc28530e3061285f81` |
| baseline_bm25 | 48b3de9595d3 | lowoverlap | 召回但排序靠后未进上下文 | kb.retrieve | gold 在 top10 第 6 位，未进前 3 条证据 | `50f2d0ed2c8e62b890ed2ed200ac149c` |
| baseline_bm25 | 4caf08abb60b | lowoverlap | 拒答误判 | kb.evidence_gate | 证据不足（最高覆盖率 0.21 < 阈值 0.5） | `e54b093990db14a6af3fdb03268822c9` |
| baseline_bm25 | 4e2f1eaa9288 | lowoverlap | 召回但排序靠后未进上下文 | kb.retrieve | gold 在 top10 第 4 位，未进前 3 条证据 | `2b67fc147556f2174ddfce10b450ca0e` |
| baseline_bm25 | 5079f6cd3c4a | lowoverlap | 拒答误判 | kb.evidence_gate | 证据不足（最高覆盖率 0.21 < 阈值 0.5） | `76de51fc99feac603ddf2b88aad71f2d` |
| baseline_bm25 | 544313ec9688 | lowoverlap | 检索未召回 | kb.retrieve | gold 不在候选池（10 个候选） | `05548346f882896c1657ce95ae7be6ca` |
| baseline_bm25 | 5541d938d9e3 | lowoverlap | 召回但排序靠后未进上下文 | kb.retrieve | gold 在 top10 第 5 位，未进前 3 条证据 | `c6a4fe0cf42127b5f247f8206331fdd2` |
| baseline_bm25 | 55d970d97d9e | lowoverlap | 检索未召回 | kb.retrieve | gold 不在候选池（10 个候选） | `45501b5607403baa3bb8d7fb3f467fd9` |
| baseline_bm25 | 59034e72e7e2 | lowoverlap | 拒答误判 | kb.evidence_gate | 证据不足（最高覆盖率 0.26 < 阈值 0.5） | `a72691d81bff5b665391acd5b279f37f` |
| baseline_bm25 | 5b7e3c6d40c2 | lowoverlap | 拒答误判 | kb.evidence_gate | 证据不足（最高覆盖率 0.26 < 阈值 0.5） | `c586161ea01489e3c6adca3456c37253` |
| baseline_bm25 | 5ea63ac6c180 | lowoverlap | 拒答误判 | kb.evidence_gate | 证据不足（最高覆盖率 0.25 < 阈值 0.5） | `cab674288e07f15991c86776eb639972` |
| baseline_bm25 | 60e2ae63f7c3 | lowoverlap | 拒答误判 | kb.evidence_gate | 证据不足（最高覆盖率 0.30 < 阈值 0.5） | `38bbc56a73e10b265deb8f9cf8a303d9` |
| baseline_bm25 | 654582a48479 | lowoverlap | 拒答误判 | kb.evidence_gate | 证据不足（最高覆盖率 0.28 < 阈值 0.5） | `5192b9ccb7058f8c5e9b4f54130a86ea` |
| baseline_bm25 | 656a186d6c58 | lowoverlap | 拒答误判 | kb.evidence_gate | 证据不足（最高覆盖率 0.20 < 阈值 0.5） | `302fde54cb402d15a55e9530da0fb5df` |
| baseline_bm25 | 68d83e94b7f1 | lowoverlap | 检索未召回 | kb.retrieve | gold 不在候选池（10 个候选） | `98cedad283fb6bb93478ff90c8a76505` |
| baseline_bm25 | 69fc8f3c00df | lowoverlap | 拒答误判 | kb.evidence_gate | 证据不足（最高覆盖率 0.11 < 阈值 0.5） | `f4d59fe458d8d2d83673bed26dc3de8d` |
| baseline_bm25 | 6ae7633a5c86 | lowoverlap | 拒答误判 | kb.evidence_gate | 证据不足（最高覆盖率 0.19 < 阈值 0.5） | `cea4ad5c7b4475ee82f5c7b9c92c27ad` |
| baseline_bm25 | 6b5cf57b7f83 | lowoverlap | 拒答误判 | kb.evidence_gate | 证据不足（最高覆盖率 0.14 < 阈值 0.5） | `f8439c13c2431cbb3eda28caa5ecdd2e` |
| baseline_bm25 | 6e3cf1ce632f | lowoverlap | 拒答误判 | kb.evidence_gate | 证据不足（最高覆盖率 0.30 < 阈值 0.5） | `bfc33f7bb6ab30e34c5061af848b2b1d` |
| baseline_bm25 | 6eaa93fd65b7 | lowoverlap | 拒答误判 | kb.evidence_gate | 证据不足（最高覆盖率 0.27 < 阈值 0.5） | `7514f970fde5f395dc0e8bbba1334352` |
| baseline_bm25 | 6ecb2bbb96ca | lowoverlap | 拒答误判 | kb.evidence_gate | 证据不足（最高覆盖率 0.23 < 阈值 0.5） | `b36c22790ea72e1879be14ed8dff5fb3` |
| baseline_bm25 | 71a52060cdda | lowoverlap | 拒答误判 | kb.evidence_gate | 证据不足（最高覆盖率 0.27 < 阈值 0.5） | `63640b3efb469c03bf963e163cd3d8cf` |
| baseline_bm25 | 72c6c2c2c45f | lowoverlap | 拒答误判 | kb.evidence_gate | 证据不足（最高覆盖率 0.25 < 阈值 0.5） | `7e18dd67557dc2ee33a37a4834106138` |
| baseline_bm25 | 731eefa9ed48 | lowoverlap | 检索未召回 | kb.retrieve | gold 不在候选池（10 个候选） | `4231e50fd5faea2e3ac408545706d175` |
| baseline_bm25 | 7c4966329910 | lowoverlap | 拒答误判 | kb.evidence_gate | 证据不足（最高覆盖率 0.29 < 阈值 0.5） | `9ef791ba5d46307856bb142ed03b3038` |
| baseline_bm25 | 7cb7e382e220 | lowoverlap | 检索未召回 | kb.retrieve | gold 不在候选池（10 个候选） | `460ca4a4f36956d6a4b8fe96284ba9ea` |
| baseline_bm25 | 7ccc7d8a16d8 | lowoverlap | 检索未召回 | kb.retrieve | gold 不在候选池（10 个候选） | `8d7e393eefc39984c7f6bfd86785726f` |
| baseline_bm25 | 7fcfd8f799dc | lowoverlap | 拒答误判 | kb.evidence_gate | 证据不足（最高覆盖率 0.18 < 阈值 0.5） | `480a997835916a6098faeb26e2386cf8` |
| baseline_bm25 | 801c9ad867f7 | lowoverlap | 拒答误判 | kb.evidence_gate | 证据不足（最高覆盖率 0.14 < 阈值 0.5） | `b03021ad0ffda2ea294a72e86047e136` |
| baseline_bm25 | 861bccb15e04 | lowoverlap | 拒答误判 | kb.evidence_gate | 证据不足（最高覆盖率 0.22 < 阈值 0.5） | `e93f0d6cb32bf3eade358e85f89a77ec` |
| baseline_bm25 | 86e10c40b395 | lowoverlap | 拒答误判 | kb.evidence_gate | 证据不足（最高覆盖率 0.26 < 阈值 0.5） | `e76266767153dc96c5d095a533193c5d` |
| baseline_bm25 | 8911ca04fd60 | lowoverlap | 召回但排序靠后未进上下文 | kb.retrieve | gold 在 top10 第 5 位，未进前 3 条证据 | `46194ba145fdcb47ee82295958d4168a` |
| baseline_bm25 | 8988c46701ca | lowoverlap | 拒答误判 | kb.evidence_gate | 证据不足（最高覆盖率 0.22 < 阈值 0.5） | `c0c96e2a77253c21d6e88e79dc13292a` |
| baseline_bm25 | 8b0f1d5f0bfd | lowoverlap | 拒答误判 | kb.evidence_gate | 证据不足（最高覆盖率 0.19 < 阈值 0.5） | `4592187a595970d9d67f4d132524d003` |
| baseline_bm25 | 8d4959b8b4c6 | lowoverlap | 拒答误判 | kb.evidence_gate | 证据不足（最高覆盖率 0.32 < 阈值 0.5） | `44a447ca1a657598d19885f287c11433` |
| baseline_bm25 | 8d82772a158a | lowoverlap | 召回但排序靠后未进上下文 | kb.retrieve | gold 在 top10 第 6 位，未进前 3 条证据 | `164f6ce320b75d11e99e241310ad9ad0` |
| baseline_bm25 | 95329ae14239 | lowoverlap | 检索未召回 | kb.retrieve | gold 不在候选池（10 个候选） | `d722e52390320559bfb3a7627e570b2f` |
| baseline_bm25 | 9694fbb3d098 | lowoverlap | 检索未召回 | kb.retrieve | gold 不在候选池（10 个候选） | `661ce087f73d32de9e74e2835a780f34` |
| baseline_bm25 | 96e642cef4a5 | lowoverlap | 拒答误判 | kb.evidence_gate | 证据不足（最高覆盖率 0.17 < 阈值 0.5） | `2137d6ff02e1e56f645e36b2d87f96ab` |
| baseline_bm25 | 9c0c0f10914f | lowoverlap | 检索未召回 | kb.retrieve | gold 不在候选池（10 个候选） | `b446f29a7e9c9713d60517e448dbfa16` |
| baseline_bm25 | 9ce2bab40b6f | lowoverlap | 拒答误判 | kb.evidence_gate | 证据不足（最高覆盖率 0.18 < 阈值 0.5） | `d74e4b842deced4b789cc3425a6e9589` |
| baseline_bm25 | a32e65e3de76 | lowoverlap | 检索未召回 | kb.retrieve | gold 不在候选池（10 个候选） | `cd1123af83763a2c5fab94181bf6f703` |
| baseline_bm25 | a541f8e287d0 | lowoverlap | 拒答误判 | kb.evidence_gate | 证据不足（最高覆盖率 0.32 < 阈值 0.5） | `36154796cd898d5da09f7d001976a1c4` |
| baseline_bm25 | a56cc8e965f1 | lowoverlap | 拒答误判 | kb.evidence_gate | 证据不足（最高覆盖率 0.22 < 阈值 0.5） | `698cd2a495a62e78209e8967449ee048` |
| baseline_bm25 | a58464555ba8 | lowoverlap | 拒答误判 | kb.evidence_gate | 证据不足（最高覆盖率 0.29 < 阈值 0.5） | `ebab1e0f4c9073b45dbfeef492c33704` |
| baseline_bm25 | a7abdec587e5 | lowoverlap | 检索未召回 | kb.retrieve | gold 不在候选池（10 个候选） | `10dde7d14ac8946d64a412e3a8dc249a` |
| baseline_bm25 | a95e93b413ab | lowoverlap | 检索未召回 | kb.retrieve | gold 不在候选池（10 个候选） | `73cce74851e5d901f544e6e5c547fc43` |
| baseline_bm25 | b221090b9eb6 | lowoverlap | 拒答误判 | kb.evidence_gate | 证据不足（最高覆盖率 0.29 < 阈值 0.5） | `80f63d7315e19f66d566cb846d171fab` |
| baseline_bm25 | b286aa6e5ba0 | lowoverlap | 拒答误判 | kb.evidence_gate | 证据不足（最高覆盖率 0.29 < 阈值 0.5） | `8fd6792434a933d547fe28595a6a2c47` |
| baseline_bm25 | b3c5b9a1b4ac | lowoverlap | 拒答误判 | kb.evidence_gate | 证据不足（最高覆盖率 0.26 < 阈值 0.5） | `4c27d7413c699d5c8a21d9f2ccf65b61` |
| baseline_bm25 | b40f5c8d9596 | lowoverlap | 检索未召回 | kb.retrieve | gold 不在候选池（10 个候选） | `dad8b4b2477b2f6655ddd3d279e68018` |
| baseline_bm25 | bb6b24c67701 | lowoverlap | 检索未召回 | kb.retrieve | gold 不在候选池（10 个候选） | `4139f93f0ddb997d265e45c70f46cd73` |
| baseline_bm25 | bc057d474b86 | lowoverlap | 拒答误判 | kb.evidence_gate | 证据不足（最高覆盖率 0.22 < 阈值 0.5） | `6b121faed6b568cc493cf5bd09370011` |
| baseline_bm25 | bffaa68f38db | lowoverlap | 检索未召回 | kb.retrieve | gold 不在候选池（10 个候选） | `13e317be72c130441b22b5b0310c668f` |
| baseline_bm25 | c0fede4fb5ac | lowoverlap | 拒答误判 | kb.evidence_gate | 证据不足（最高覆盖率 0.27 < 阈值 0.5） | `faf776636faace15b3337f00b41653f4` |
| baseline_bm25 | c147513246ec | lowoverlap | 检索未召回 | kb.retrieve | gold 不在候选池（10 个候选） | `58264992f47a5d86c915aeba7f8ac7a8` |
| baseline_bm25 | c1d08c325201 | lowoverlap | 拒答误判 | kb.evidence_gate | 证据不足（最高覆盖率 0.16 < 阈值 0.5） | `3620a4b947e0f996e18e08abc45f0078` |
| baseline_bm25 | c2de3d025774 | lowoverlap | 检索未召回 | kb.retrieve | gold 不在候选池（10 个候选） | `c275ce8253766fb499604b97cef253d9` |
| baseline_bm25 | c9232d4ceafc | lowoverlap | 检索未召回 | kb.retrieve | gold 不在候选池（10 个候选） | `acb32311d035fd86da6a51a053ee017e` |
| baseline_bm25 | cad41c754210 | lowoverlap | 拒答误判 | kb.evidence_gate | 证据不足（最高覆盖率 0.30 < 阈值 0.5） | `b35ace0655aef1774f74f3040a702ee5` |
| baseline_bm25 | d37b336dc9bb | lowoverlap | 召回但排序靠后未进上下文 | kb.retrieve | gold 在 top10 第 4 位，未进前 3 条证据 | `b12c293a184ce69a1377fa104b8dba2d` |
| baseline_bm25 | d7c78c9696a4 | lowoverlap | 召回但排序靠后未进上下文 | kb.retrieve | gold 在 top10 第 4 位，未进前 3 条证据 | `7d882e76a7e3a96a6a5a39bd06f40969` |
| baseline_bm25 | d8c8b197a892 | lowoverlap | 拒答误判 | kb.evidence_gate | 证据不足（最高覆盖率 0.28 < 阈值 0.5） | `b56cb6396144b1e051b20029987a8735` |
| baseline_bm25 | db8d56285a0a | lowoverlap | 拒答误判 | kb.evidence_gate | 证据不足（最高覆盖率 0.13 < 阈值 0.5） | `2b5a266d81f034a9856f2a095e540696` |
| baseline_bm25 | dc11da3cb2bf | lowoverlap | 拒答误判 | kb.evidence_gate | 证据不足（最高覆盖率 0.24 < 阈值 0.5） | `104ecfaddab1f768f10f0dfd5f350986` |
| baseline_bm25 | dd8ac5440848 | lowoverlap | 拒答误判 | kb.evidence_gate | 证据不足（最高覆盖率 0.26 < 阈值 0.5） | `8e1b88947dba652518bb5a06b9afa4f1` |
| baseline_bm25 | de7f1c327e7b | lowoverlap | 拒答误判 | kb.evidence_gate | 证据不足（最高覆盖率 0.19 < 阈值 0.5） | `81ecb1435142aaf32a3ee1e39f34349f` |
| baseline_bm25 | e36fac1159e9 | lowoverlap | 拒答误判 | kb.evidence_gate | 证据不足（最高覆盖率 0.29 < 阈值 0.5） | `81952169c193187b12abb0434e94b0e7` |
| baseline_bm25 | e4d8383486e4 | lowoverlap | 检索未召回 | kb.retrieve | gold 不在候选池（10 个候选） | `87c45b3a8f5b54051ff50d98fcf2b060` |
| baseline_bm25 | e512952d9b83 | lowoverlap | 拒答误判 | kb.evidence_gate | 证据不足（最高覆盖率 0.16 < 阈值 0.5） | `0d82ed8a7a5747c4793a496ebc162f83` |
| baseline_bm25 | e95aaad379f0 | lowoverlap | 检索未召回 | kb.retrieve | gold 不在候选池（10 个候选） | `0340e5c2fb5fd62c1da030e613f88e3b` |
| baseline_bm25 | eccd25e47c59 | lowoverlap | 检索未召回 | kb.retrieve | gold 不在候选池（10 个候选） | `b493f2e0a99fba2eb6912ca3b9287d4d` |
| baseline_bm25 | f125ff0105dc | lowoverlap | 拒答误判 | kb.evidence_gate | 证据不足（最高覆盖率 0.29 < 阈值 0.5） | `16461874c0281c108dc6c8d898c31f37` |
| baseline_bm25 | f230fd28d898 | lowoverlap | 检索未召回 | kb.retrieve | gold 不在候选池（10 个候选） | `d497235aa94bfba611d525c1c76e306a` |
| baseline_bm25 | f357ed5d37e9 | lowoverlap | 检索未召回 | kb.retrieve | gold 不在候选池（10 个候选） | `d32fa8ab680d4352f7ce0d4738ec6ced` |
| baseline_bm25 | f663dc61c0dc | lowoverlap | 拒答误判 | kb.evidence_gate | 证据不足（最高覆盖率 0.27 < 阈值 0.5） | `277320fba355446e10c3f8495e474a32` |
| baseline_bm25 | f811eef5910c | lowoverlap | 召回但排序靠后未进上下文 | kb.retrieve | gold 在 top10 第 9 位，未进前 3 条证据 | `9edb6e64e982fa25767c5124b7b3eec0` |
| baseline_bm25 | f8c139823722 | lowoverlap | 拒答误判 | kb.evidence_gate | 证据不足（最高覆盖率 0.15 < 阈值 0.5） | `3bc91b8cc7b1e7e8b0ae329dd4cf4946` |
| baseline_bm25 | faa7dcf2d212 | lowoverlap | 拒答误判 | kb.evidence_gate | 证据不足（最高覆盖率 0.25 < 阈值 0.5） | `e37840ab1ceaece23da29a65ae844220` |
