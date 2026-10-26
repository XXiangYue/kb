# Block 2 报告（检索：关键词 / 向量 / 混合 / 混合+重排）

题集 `reports/questions_block2.jsonl` 已在跑评测**之前**冻结提交（cb65e05）。评测只用 test 划分，dev 划分没有用来调参。全部问题都不含条文号或标准号（构建时已过滤），因此本报告测的是检索能力，而不是条号路由。所有主要指标都由程序判定。
运行：`python -m tushen_kb.eval.run_block2`，2026-10-27 05:03 NZDT，结果见 `reports/eval_block2.json`。

## 题集（生成模型 deepseek-flash；435 次调用，435 次成功，共 75541 tokens）
| 类型 | test | dev | 过滤情况 |
|---|---|---|---|
| T2 原句定位 | 88 | 32 | 候选 121，因 gold 校验去掉 1 |
| T3 模型出题 | 31 | 7 | 生成 80；去掉照抄/含编号 27、回译 4（共检 53）、闭卷泄漏 11（共检 49） |
| T4 表格单元格 | 34 | 4 | 候选 44，去掉 0（去掉纯数字行键、□、占位值） |
| T5 禁止性条款 | 25 | 8 | 候选 35（去掉代词/泛指主语） |
| P 改写 | 36 | 10 | 生成 199；去掉照抄/含编号 150、回译 1 |
| U2 库外问题 | 31 | 9 | 提出 50；去掉无法解析 7、术语在语料库中出现 2 |
另有去重去掉 10 题。

## 四种模式（test 中可回答题 n=214，U2 n=31）
引用精确率按**口径 B**（8f559d8：覆盖率计算前去掉文档引用）计算，口径说明见 README「引用精度的两种口径」。

| 模式 | Recall@5 | MRR@10 | U2 正确拒答 | 误拒 | 引用精确率（口径 B） | 至少 1 条正确引用 |
|---|---|---|---|---|---|---|
| keyword (BM25) | 192/214=0.897 | 162.93/214=0.761 | 27/31 | 16/214 | 173/424=0.408 | 169/214 |
| vector | 162/214=0.757 | 119.52/214=0.559 | 31/31 | 43/214 | 123/289=0.426 | 123/214 |
| hybrid (RRF) | 190/214=0.888 | 150.40/214=0.703 | 31/31 | 26/214 | 149/374=0.398 | 149/214 |
| hybrid_rerank | 208/214=0.972 | 188.57/214=0.881 | 31/31 | 23/214 | 188/359=0.524 | 179/214 |

配对比较，每次只改一个变量。Recall@5 用精确 McNemar 检验（b01=A 错 B 对），差值给出配对 bootstrap 95% CI：
- keyword→hybrid：15:17，p=0.86，CI [-0.061, 0.042]，无显著差异。MRR 差 -0.059，CI [-0.098, -0.020]，显著变差。
- vector→hybrid：30:2，p=2.5e-7，CI [0.084, 0.178]，显著。
- hybrid→hybrid_rerank：19:1，p=4.0e-5，CI [0.047, 0.126]，显著。MRR 差 +0.178，CI [0.132, 0.223]。
- 引用精确率（聚类 bootstrap）：hybrid→rerank 差 +0.125，CI [0.090, 0.160]，显著。keyword→hybrid 差 -0.010，CI [-0.043, 0.022]，无显著差异。
- 误拒（McNemar）：keyword→hybrid 12:2，p=0.013，hybrid 误拒更多。hybrid→rerank 1:4，p=0.38。

按重叠度分层的 Recall@5：
| 分层 | keyword | hybrid_rerank |
|---|---|---|
| 高 | 155/163 | 160/163 |
| 中 | 37/47 | 44/47 |
| 低 | 0/4 | 4/4 |

低重叠层只有 4 题，**不下结论**。

改写稳健性（P 与其源题配对，n=35）：
| 模式 | 源题 Recall@5 | 改写题 Recall@5 | McNemar |
|---|---|---|---|
| keyword | 33/35 | 27/35 | p=0.031（只有 35 对，样本小，仅作提示） |
| vector | 25/35 | 26/35 | — |
| hybrid | 30/35 | 29/35 | — |
| hybrid_rerank | 34/35 | 34/35 | — |

## 失败归因（top-5 未命中，每题只记一个主因）
- keyword 共 22 题：chunk_boundary 6，unattributed（词汇鸿沟）16。
- vector 共 52 题：parse_error 1，chunk_boundary 23，vector_miss 28。
- hybrid 共 24 题：parse_error 1，chunk_boundary 14，vector_miss 3，fusion_loss 6。
- hybrid_rerank 共 6 题：chunk_boundary 3，vector_miss 1，fusion_loss 1，rerank_error 1。
- tokenization 和 label_error 在所有模式下都是 0。

## 限制与资源
峰值内存 1.6GB RSS（实测 1597MB，两个模型同时加载）。在约 2GB 可用内存下余量很小，这是一项限制。

CPU 4 线程。峰值 RSS 1597MB（两个模型同时加载）。每题检索耗时：keyword 2.6ms，vector 0.9ms，hybrid 3.6ms，hybrid_rerank 2188ms。整次评测耗时 545s。

## 待测
需要模型判定的研究（Judge2 gemini-3.8-flash）在 Block 2 中还没有跑。route_only_citations 变量、dev 阈值校准、更大的低重叠样本也都待测。

## 引用修复的对外口径
系统修复只报 ea328ba（cite_kinds：条文说明不作为引用）：404/719 → 404/710（McNemar 9:0，p=0.0039）。
8f559d8 是口径变更（口径 B），口径 A 与口径 B 的对照见 README。它是否在看到结果之前决定，无法证实。
## 双裁判
一致率 18/20，κ=0.765。两处分歧都写成「MiMo 与 Gemini 分歧，程序标签记为 忠实 / 不忠实」，不评价两个裁判的对错。

## Step 2：bm25_rerank（BM25 + bge-reranker-base）
同一冻结 test 集（可回答 214 题，U2 31 题），同一 AskConfig，只改检索模式。结果文件 `reports/eval_block2_bm25_rerank.json`（keyword / hybrid / hybrid_rerank 在同一进程中重跑，数字与 cc9b11c 一致）。

| 模式 | Recall@5 | MRR@10 | 引用精度（口径 B） | ≥1 条正确引用 | 误拒 | U2 正确拒答 |
|---|---|---|---|---|---|---|
| keyword | 192/214 | 162.93/214 = 0.761 | 173/424 = 0.408 | 169/214 | 16/214 | 27/31 |
| bm25_rerank | 208/214 | 187.45/214 = 0.876 | 186/372 = 0.500 | 177/214 | 23/214 | 31/31 |
| hybrid_rerank | 208/214 | 188.57/214 = 0.881 | 188/359 = 0.524 | 179/214 | 23/214 | 31/31 |

配对检验（n=214；McNemar b01=A 错 B 对，b10=A 对 B 错；bootstrap 2000 次）：

| 对比 | Recall@5 McNemar | Recall@5 差 CI | MRR 差 [CI] | 引用精度差 [CI，按题聚类] | 误拒 McNemar |
|---|---|---|---|---|---|
| keyword→bm25_rerank | 19:3，p=0.00086，显著 | +0.075 [0.037, 0.117] | +0.115 [0.069, 0.158] 显著 | +0.092 [0.063, 0.121] 显著 | 9:2，p=0.065，无显著差异 |
| bm25_rerank→hybrid_rerank | 2:2，p=1.0，无显著差异 | 0.000 [-0.019, 0.019] | +0.005 [-0.014, 0.025] 无显著差异 | +0.024 [0.005, 0.043] CI 不含 0 | 0:0 |

（误拒 McNemar 的 b01 = A 不拒、B 误拒。）

结论：
- 在这套题上，Recall@5 / MRR 的提升主要来自 reranker。加不加向量召回（bm25_rerank→hybrid_rerank）在 Recall@5 和 MRR 上都没有显著差异。
- 引用精度上 hybrid_rerank 比 bm25_rerank 高 0.024，CI 不含 0。但「≥1 条正确引用」的题级 McNemar 是 3:1，p=0.625，所以只能说引用条数更少、更准，不能说多答对了题。
- 低重叠层这里只有 4 题（bm25_rerank 2/4，hybrid_rerank 4/4），**不下结论**，见 Step 3。
- bm25_rerank 失败 6 题：rerank_error 2，chunk_boundary 1，unattributed 3。
- 耗时：bm25_rerank 每题 2840ms，hybrid_rerank 2259ms。本次两个 rerank 模式同时跑，墙钟 1237s，峰值 RSS 1612MB。

## Step 3：扩大低重叠层（overlap < 0.3）
题目在任何运行之前已冻结并提交（2f508c0，`reports/questions_lowoverlap.jsonl`，生成于 05:13 NZDT）。题型 L：DeepSeek（只当生成器）把条文改写成不熟悉术语的人的口语提问。来源条文排除 Block 2 已用过的全部 src_chunk。

过滤计数：
- 可用来源条文 308 条，抽 260 条，生成调用 260 次，失败 0。
- 引用/照抄过滤去掉 7，程序金标有效性去掉 0，overlap ≥ 0.3 去掉 52。
- 回译检查 201 题中去掉 41，闭卷泄漏检查 160 题中去掉 15，去重 0。
- 剩 145 题：test 118，dev 27。生成器共调用 621 次，成功 621 次，共 151,244 tokens。

test 结果（n=118，全部是 L 型，全部可回答，没有 U2；`reports/eval_lowoverlap.json`）：

| 模式 | Recall@5 | MRR@10 |
|---|---|---|
| keyword | 79/118 = 0.669 | 0.524 |
| vector | 80/118 = 0.678 | 0.535 |
| hybrid | 86/118 = 0.729 | 0.599 |
| bm25_rerank | 89/118 = 0.754 | 0.644 |
| hybrid_rerank | 95/118 = 0.805 | 0.683 |

配对（Recall@5 McNemar b01:b10，p，差值 CI；MRR 差值 [CI]）：
- keyword→hybrid：11:4，p=0.12，[0.000, 0.127]，无显著差异；MRR +0.075 [0.017, 0.131]。
- vector→hybrid：14:8，p=0.29，无显著差异；MRR +0.063 [0.002, 0.126]。
- hybrid→hybrid_rerank：13:4，p=0.049，[0.008, 0.144]，显著；MRR +0.084 [0.016, 0.151]。
- keyword→vector：18:17，p=1.0，无显著差异。
- keyword→bm25_rerank：11:1，p=0.0063，[0.034, 0.144]，显著；MRR +0.121 [0.049, 0.195]。
- bm25_rerank→hybrid_rerank：11:5，p=0.21，[-0.017, 0.119]，无显著差异；MRR +0.038 [-0.016, 0.093]。

结论：
- 在低重叠题上，所有模式都比高重叠题低很多。hybrid_rerank 最高（95/118），但它对 bm25_rerank 的优势在 n=118 下仍不显著。
- **重要发现：/ask 在这 118 题上全部误拒（每个模式都是 118/118）。** 原因是覆盖率闸门 min_coverage=0.5 衡量的是问题词在证据中出现的比例，而低重叠题的定义就是问题词很少出现在条文里。所以检索即使命中，闸门也会拒答。阈值校准见 Step 4。
- 失败归因（hybrid_rerank 23 题）：vector_miss 15，rerank_error 4，fusion_loss 3，chunk_boundary 1。keyword 39 题里 38 题是 unattributed（词汇鸿沟）。
- 这批题由 DeepSeek 生成，改写风格单一，可能不代表真实用户的口语分布。
