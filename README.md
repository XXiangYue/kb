# tushen-kb — 图审私域 RAG 检索原型（Block 1）

单机、仅绑定 `127.0.0.1` 的中文工程建设标准检索原型：PDF（文字层）→ 条文级切块 → SQLite（FTS5 + jieba）→ `/ingest` `/search` `/ask`，`/ask` 返回经**原文子串校验**的逐字引用，证据不足时拒答。

> 状态：Block 1 = 关键词检索 + 引用校验 + 拒答 + 程序判分评测。向量 / RRF 混合 / 重排为后续 block（接口已留，当前返回 HTTP 501）。

## 快速开始

```bash
bash scripts/repro.sh            # venv → 依赖 → 下载语料(sha256 校验) → 入库 → 冒烟 → pytest → 评测(不调模型)
RUN_LLM=1 bash scripts/repro.sh  # 另外跑少量真实模型调用（需环境变量中的 key）
.venv/bin/python -m tushen_kb.server        # http://127.0.0.1:8765
curl -s 127.0.0.1:8765/ask -d '{"q":"GB 55037 第4.1.2条"}'
```

## 语料：来源与许可（PDF 不入库）

| doc_id | 标准 | 页数 | 来源 URL | 许可/使用条件 |
|---|---|---|---|---|
| GB55037-2022 | 建筑防火通用规范（含条文说明） | 92 | js.119.gov.cn（江苏省消防救援总队官网「标准规范」栏目） | 强制性工程建设规范；住建部公告称本规范在住建部门户网站公开。**再分发权利未明示**；该文字版页眉带第三方汇编水印「消防规范网 gf.1190119.com」 |
| GB46034-2025 | 公众聚集场所投入使用营业消防安全检查规则 | 23 | js.119.gov.cn | 强制性国家标准，国家标准全文公开系统提供免费阅读；**再分发权利未明示** |
| DB32/T 4696-2024 | 建筑消防设施维护保养规程 | 60 | js.119.gov.cn | 江苏省推荐性地方标准，政府网站公开供阅读；**再分发权利未明示** |
| DB32/T 5053-2025 | 消防电梯的使用管理与维护保养规则 | 21 | js.119.gov.cn | 同上 |

精确 URL 与 sha256 见 `corpus/manifest.json`。由于再分发权利不明确，PDF **不提交到 git**（`.gitignore` 排除 `*.pdf`、`data/`），用 `scripts/download_corpus.sh` 下载并校验 sha256。
住建部官网（gov.cn）上的 GB 55037 / GB 55034 / GB 50016 官方版本为**扫描件、无文字层**，本原型不做 OCR，故未采用。

## 设计要点

- **解析**（`pdfparse.py`）：按字符基线聚类成行（修复 GB46034 标点/数字字体纵向偏移导致的断行）；表格单独抽取（≥2 行 ≥2 列 ≥40% 非空）；页眉页脚/水印按跨页重复率剔除；`assemble()` 生成归一化全文，**`doc_text[start:end] == chunk.text` 在入库时断言**。
- **切块**（`chunker.py`）：从正文「1 总则/范围」起；条号单调且章号连续；区分 `clause`（条文）/ `commentary`（条文说明，带 `ref_clause`）/ `table` / `text`；强制性：整本强制（「全部条文必须严格执行」或无 /T 的 GB 代号）或按条文粗体比例；每块保留文件、页码、条号、字符偏移。
- **检索**（`search.py`）：jieba 搜索切分 + CJK 二元组 + 条号/标准号保护词 → FTS5 BM25；`clause_route`（条号直达）与 `std_filter`（按问句中的标准号限定）为可开关变量。
- **问答**（`ask.py`）：标准/条号存在性检查 → 检索 → 证据覆盖率门槛（0.5，未校准）→ 逐字引用 + 校验（去空白子串 + 绝对偏移回指原文）→ 可选 LLM 生成（生成中「」引文再次逐字校验，任一不通过即拒答）。`context_hooks` 为后续 Skills 注入点，每次注入记录来源与 token 数（`trace.context_log`）。

## 模型角色（`config/models.json`，provider/model/env 变量名均可配置）

| 角色 | provider | family | 模型 | key 环境变量 |
|---|---|---|---|---|
| 生成 | DeepSeek | deepseek | deepseek-flash | `DEEPSEEK_API_KEY` |
| 裁判 1 | 小米 MiMo | xiaomi-mimo | mimo-v2.6-flash | `MIMO_API_KEY` |
| 裁判 2 | uuapi（OpenAI 兼容，`https://uuapi.io/v1`） | google | gemini-3.8-flash（timeout 180s，并发 ≤2，指数退避）。**只用这一个模型，从不切换**；持续失败时报告覆盖率 x/N。原备用裁判 gpt-5.6-luna 已删除并禁用：该中转返回的模型名是 gpt-5.6-terra，疑似隐藏提示注入（配置校验会拒绝该模型及任何 backup_models） | `NEW_API_KEY_2` |

- 配置校验：任何裁判（含回退模型）的 family ≠ 生成模型 family，两位裁判 family 互不相同 → **DeepSeek 永远不能当裁判**。
- key 只在调用时从环境读取，不写入日志/仓库/产物；无 key → 状态 `skipped`，绝不伪造结果。
- 每次调用记录实际使用的模型名、响应中的模型版本、尝试次数（指数退避重试 429/5xx）、是否回退；最终失败的项以 `status=error` 保留在记录与分母中。
- 裁判记录均标注 `judge_type="model"`，不是人工标注。
- 免费档限流：Gemma 常返回上游 429；回退模型 Nemotron 是推理模型，需较大 token 预算（2048），判定取回复末行。无法解析的回复记为 `unparsable`，计入分母与失败列表。

## 评测（不使用 OpenAI Evals）——对应「两条线的最小可信方案」7 点

1. **主指标全部程序判分**：Recall@5、MRR@10（命中 = 可接受块集合或覆盖金标跨度）、拒答率、误拒率、引用精度，均带分母。
2. **反向生成 + 可接受集**：T1 由条文块模板反向生成，可接受集 = 该条所有块 id（>3 块的题剔除）；U 题用语料中出现次数为 0 的条号。
3. **三道过滤并报告剔除数**：回译可答（模型）、闭卷不可答（模型答案 + 程序检测 ≥15 字逐字泄漏）、去重（程序）。Block 1 中模型两道仅在抽样上运行（见报告），未覆盖全集。
4. **按重叠率分层**：报告 low/mid/high 分层；改写复测（T3）= 待测。
5. **一次只改一个变量**：A=纯 BM25 vs B=BM25+clause_route，配对精确 McNemar + 配对 bootstrap 95% CI。**B 在 T1 上的 396/396 是「条号路由正确性」，不是检索能力**；检索能力以纯 BM25 为基线，在不含条号的题（T2 内容反查、T3 改写、T4 表格数值、T5 否定）上衡量。
6. **忠实度由异族模型二元判定**（可选模块，不进主指标），记录模型名。
7. **双裁判交叉**：两个异族裁判独立二元判定；二选一题交换选项顺序再判；先报告各裁判覆盖率（status=ok 的条数/总条数）。**只有双裁判共同覆盖率 ≥ 0.8 时才报告一致率 x/N、Cohen's κ 与分歧项**，否则这些写「待测」，只报覆盖率与失败项列表。Block 1 首跑（OpenRouter Gemma/Nemotron，已弃用，记录移至 `reports/historical/`）judge2 覆盖率 3/20，故当时一致率未报告。

产物：`reports/eval_block1.json`（结果）、`reports/questions_block1.jsonl`（冻结题集）、`reports/historical/`（已弃用的 judge2 首跑记录）。

### 双裁判重判（judge2 = gemini-3.8-flash）

冻结题项 `reports/judge_items_block1.jsonl`（含 prompt；Block 1 run2 的生成答案未持久化，故用同样 6 道 T1 题重新生成 6 个 DeepSeek 答案 + 6 个程序篡改版 + 4 道二选一×2 顺序 = 每裁判 20 条判定）。命令：`python -m tushen_kb.eval.rejudge judge` 后 `... rejudge retry --role judge2`（仅对失败项同模型同 prompt 重试，被替换的失败记录保留在 `reports/judgments_block1_v2_superseded_failures.jsonl` 并计入调用数）。

| | judge1 mimo-v2.6-flash | judge2 gemini-3.8-flash |
|---|---|---|
| 覆盖率（status=ok） | 20/20 | 首轮 18/20（2 条 HTTP 400 upstream "Invalid request parameter"），重试后 20/20 |
| HTTP 调用数 | 20 | 23（含 3 次被替换的失败调用） |
| token 合计 | 12019 | 16225 |
| 忠实性判定 vs 程序标签 | 11/12 | 11/12 |
| 二选一（两种顺序） | 8/8 | 8/8 |

双裁判一致率 **18/20，Cohen's κ = 0.765**（**仅作内部诊断，不是对外指标**）（共同覆盖 20/20）。分歧项 2 条，同属 `b3a6e2c4d9a5`（DB32/T 4696 第 4.14 条）：原答案：MiMo 与 Gemini 分歧（MiMo「是」、Gemini「否」），程序标签记为「忠实」。该答案写了标准号「DB32/T 4696-2024」，而证据文本里没有这个标准号。篡改版（标准号 32→39）：MiMo 与 Gemini 分歧（MiMo「是」、Gemini「否」），程序标签记为「不忠实」。N=20 很小，κ 的置信区间很宽，只作参考，不作主指标。产物：`reports/judgments_block1_v2_records.jsonl`、`reports/judge_summary_block1_v2.json`。

失败归因类别（block 2 起自动化）：解析错误 / 切块边界 / 分词 / 向量漏召 / 融合损失 / 重排错误 / 标注错误。

## Block 2：向量 / 混合 / 重排（CPU）

```bash
uv pip install --python .venv/bin/python torch==2.14.1 --index-url https://download.pytorch.org/whl/cpu
uv pip install --python .venv/bin/python -r requirements-dense.txt
bash scripts/download_models.sh        # BAAI/bge-small-zh-v1.5 + BAAI/bge-reranker-base（MIT），经 hf-mirror.com（当时 308 跳转到 huggingface.co），sha256 记于 reports/models_sha256.txt
python -m tushen_kb.cli embed          # 754 个块全部嵌入，batch=16，实测 26.4 s，峰值 RSS 760 MB
python -m tushen_kb.cli search "消防电梯前室面积" --mode hybrid_rerank
```

| mode | 与上一档相比只变一个变量 |
|---|---|
| keyword | FTS5 BM25（jieba + 二元组） |
| vector | bge-small-zh-v1.5 CLS 向量余弦（查询加 BGE 官方检索指令前缀），块文本与 BM25 索引同（表格前置表名），截断 512 token |
| hybrid | keyword 池(50) + vector 池(50) 做 RRF(k=60) |
| hybrid_rerank | hybrid 前 20 名用 bge-reranker-base 交叉编码重排 |
| bm25_rerank | keyword（BM25）前 20 名用同一个 reranker 重排；用于把「加 reranker」和「加向量召回」两个因素拆开 |

标准号过滤与条文号路由在四种模式中完全一致。模型权重与向量（SQLite 中 `vectors` 表）不入库；未构建向量时 `/search` 的向量类模式返回 HTTP 503。

## 引用精度修复（单变量、同一冻结题集 `reports/questions_block1.jsonl`）

`python -m tushen_kb.eval.cite_ab --variable <名>`，结果在 `reports/cite_ab_<名>.json`。配对检验单位 = 题：McNemar 用「该题全部引用都精确」，引用精度差用按题聚类的 bootstrap 95% CI。

| 变量 | 旧 → 新 | 引用精度（T1-test） | 全部精确的题 | McNemar p | 精度差 95% CI |
|---|---|---|---|---|---|
| `cite_kinds`（条文说明不作为引用，仍可作为证据） | 含 commentary → 不含 | 404/719 = 0.562 → 404/710 = 0.569 | 228/396 → 237/396 | 0.0039（9:0） | [+0.003, +0.012] |

对外只把 `cite_kinds` 作为系统修复报告：**404/719 → 404/710**。

诊断：旧配置 315 条不精确引用中，条文说明只占 9 条；其余为同标准的其他条文（134）与非条文正文块（172），主因是按标题词凑足覆盖率的 BM25 邻居块，见下节「口径 B」。

### 引用精度的两种口径（8f559d8 是口径变更，不是系统修复）

`coverage_strip_doc_refs` 改的是「覆盖率」怎么算：计算前先去掉问句里的《标题》、标准号和「第x条」。覆盖率决定哪些检索块有资格成为引用，所以它改变的是计入分母的引用集合。分子不变（都是 404），提升全部来自分母变小。因此它记为口径变更，不作为系统修复对外报告。

| 口径 | 覆盖率计算 | 引用精度（T1-test，在 cite_kinds 修复之上） | 全部精确的题 |
|---|---|---|---|
| 口径 A（原） | 用原问句 | 404/710 = 0.569 | 237/396 |
| **口径 B**（8f559d8 起默认） | 先去掉文档引用 | 404/532 = 0.759 | 300/396 |

配对检验（同一冻结题集）：McNemar 100:37，p = 7.0e-8，精度差 95% CI [+0.152, +0.228]。
理由：问句里的标题词和标准号在同一标准的每个块里都会出现，不能作为「证据覆盖了问题」的依据。
是否在看到结果之前决定：**无法证实**。git 历史只表明它在 ea328ba 之后 40 秒提交。实际上这个变量是在看了 ea328ba 运行的不精确引用诊断（上节）之后才提出的，因此不能声称是事先决定的。
Block 2 报告里的引用精度一律按口径 B 计算。

注意 37 题变差：去掉文档引用后只剩「规定/内容」等泛词，邻居块覆盖率反而变高。剩余不精确引用：其他条文 101、正文块 27。

## 已知限制

- 无 OCR；扫描版官方 PDF 不可用。语料为第三方/地方站点的文字版，GB55037 文字版带水印。
- 未找到「部分条文强制、以粗体标识」的样本，粗体强制判定只在合成夹具上测试。
- T1 题带显式条号，`clause_route` 几乎必中：B 配置的 396/396 只说明条号路由正确，不代表检索能力；检索能力看纯 BM25 基线（T1 上 212/396）及 T2–T5。
- 覆盖率门槛 0.5 未在 dev 集上校准；问句提及语料外标准号即拒答，可能误拒。
- 峰值内存：dense 模式下两个模型同时加载，峰值 RSS 约 1.6GB（Block 2 评测实测 1597MB），在约 2GB 可用内存的机器上余量很小。
- token 计数为估算（CJK 字 + ASCII 词）；调用模型时另记录服务端 usage。

## Skills（后续，未实现）

计划按题型拆分 SKILL.md（条文查找 / 表格数值 / 交叉引用 / 拒答），渐进披露，每次加载记日志；边界：Skill = 方法，检索工具 = 取证据，MCP = 暴露工具的协议。有/无 Skills 对照实验须先冻结并提交题集与提示词，差异在噪声内即写「无显著差异」。
