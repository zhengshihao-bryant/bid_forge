# BidForge 项目技术地图

> 白皮书十章 ↔ 源码模块 ↔ 关键代码 ↔ 面试回答。
> 用途：面试前对照白皮书通读一遍；被追问"XX 是怎么实现的"时，按章定位文件，报出 `文件:函数` 即可。
> 口径：所有锚点均为当前代码库实测；数字引用自各里程碑验收报告（内部离线评估集口径）。

---

## 使用说明

1. **报代码用 `文件:函数` 格式**（如 `quality/checks/facts.py:check_facts`），行号不用背，文件与函数名要准。
2. **每章一条"面试回答"话术**——先讲设计与判断，再报实现位置，最后落到验收数字。
3. **数字口径**：评估数字一律带"基于项目内离线评估集，不代表通用准确率"。

## 模块总览（源码树）

```
backend/app/
├── api/                    # FastAPI 路由层（routes_*.py × 10 + main.py 装配）
│   └── main.py             # app 创建、CORS、include_router（认证依赖全局挂载）
├── parsers/                # M1 四类解析器（docx/pdf/xlsx/图片 OCR）
├── auth/                   # M7 认证/权限/审计（security.py / deps.py / audit.py）
├── evaluation/             # M7 评估体系（golden.py 基线 / runner.py / metrics.py / api.py）
├── services/
│   ├── extraction.py       # M1 需求提取 + 评分表规则解析（不 LLM）
│   ├── kb_chunking.py      # M2 Chunk 层（结构感知切块）
│   ├── capability_extractor.py  # M2 Capability 层（能力卡抽取）
│   ├── embedding.py        # M2 嵌入（BgeEmbedding / FakeEmbedding）
│   ├── vector_store.py     # M2 向量库（Milvus 主 + SQLite 降级，engine 字段透明）
│   ├── kb_versions.py      # M7 知识库版本（KV-xxxx + {日期}-v{n}）
│   ├── task_tracker.py     # M7 任务中心（TSK-xxxx 五态）
│   ├── trace.py            # M7 AgentTracer（trace/span 旁路）
│   ├── llm.py              # LLM 客户端封装 + MockLLM + llm_calls 计量
│   ├── matching/           # M3 匹配管线（normalize/extract/classify/retrieve/rules/judge/validate/report）
│   ├── generation/         # M4 生成引擎（outline/mapping/context/generator/strategies/assembler/job）
│   └── quality/            # M5 质检引擎（runner/registry/scoring + checks/ 四类检查器）
├── db.py                   # Database 封装层（无 ORM，每操作独立连接 + WAL）
├── config.py               # env 配置（JWT_SECRET / AUTH_ENABLED / LLM_API_KEY / EMBEDDING_BACKEND）
└── schemas.py              # API 模型
frontend/src/               # M6 Vue3 工作台（views/ 8 页面、components/ 5 组件、stores/workbench.ts）
scripts/verify_m*.py        # M1–M7 端到端验收脚本（urllib 直连 HTTP，报告写 _m*_verify_report.txt）
tests/                      # 离线测试（290 passed；pytest.ini 默认跳过 llm/milvus 标记）
```

---

## 第一章 业务与定位 →（无源码，案例层）

| 源码模块 | 关键位置 | 面试回答 |
|---|---|---|
| `README.md` / `ROADMAP.md` | 传统八步流程与四痛点落在白皮书 1.2/1.3 | 30 秒介绍原文（问答手册 Q1）：资料分散、需求分析难、生成不可控 → **可验证、可追溯、可控生成**。 |

---

## 第二章 总体架构 → api/ + db.py（七段管线装配）

| 源码模块 | 关键代码 | 面试回答 |
|---|---|---|
| `api/main.py:116-127` | 10 个 router 挂载，业务路由全局挂认证依赖 | 讲"一次请求怎么流动"：上传 → 解析(M1) → 知识库(M2) → 匹配(M3) → 生成(M4) → 质检(M5) → 工作台(M6) → 治理(M7)。不是技术列表，是数据流。 |
| `db.py Database`（连接/CRUD，`:38 connect` `:49 execute` `:61 query`）+ `db_mappers.py MappersMixin`（ORM 映射）+ `db_schema.py DDL/seed` | 数据访问全部收敛在封装层，业务代码不写裸 SQL 之外的连接管理；每操作独立连接 + WAL；存储层三层拆分便于迁移 | 六大实体（Tender/Requirement/Evidence/Capability/BidDocument/QualityReport）贯穿一条可追溯数据链——任何一句标书能回答"这话哪来的"。 |
| `config.py` | env 驱动（AUTH_ENABLED 可关，验收脚本两阶段用） | 演示账号 5 个（admin/admin123…）与 JWT_SECRET 在 README 已记录。 |
| `frontend/src/router/index.ts` + `views/projects/*.vue` | 8 路由对应 6 阶段工作台 | 前端只是管线的工作台视图，核心价值在后端链路。 |

---

## 第三章 招标文件解析 → parsers/ + services/extraction.py

| 源码模块 | 关键代码 | 面试回答 |
|---|---|---|
| `parsers/base.py:26 parse_file` `:45 build_section_tree` | 分派 + 结构树恢复（document → section → paragraph） | **示范回答（背）：**"我们没有简单把 PDF 转成文本，而是保留文档结构。解析阶段会恢复 document、section、paragraph 层级，并生成带文件、章节、页码、原文的四元溯源信息，后续生成阶段直接引用证据。" |
| `parsers/docx_parser.py:52 DocxParser` `:103 _heading_level` | docx 标题层级（样式 + 编号推断） | 追加细节：每个 Block 带页码、块号，溯源定位到页。 |
| `parsers/pdf_parser.py:66 PdfParser` `:124 _parse_page` `:212 _heading_kind` | PDF 用字体大小/样式识别标题层级，不是纯文本流 | 追加细节：无文本层 PDF 走 `ocr.py:68 ocr_pdf_pages`。 |
| `parsers/xlsx_parser.py:22 XlsxParser` | 表格文档 | 评分表场景 |
| `services/extraction.py:225 RequirementExtractor` `:235 extract` | 需求提取（窗口切片 + LLM 结构化 + 校验 + 去重编号） | 提取结果每条带 source_document/page/section_path/block_id——这就是四元溯源。 |
| `services/extraction.py:455 parse_score_tables` | **评分表规则解析，不用 LLM** | 大模型读表不可靠（漏行/串列/读错权重），格式有限可枚举 → 规则解析又准又便宜。这是"能确定性就确定性"原则的第一个实例。 |

---

## 第四章 企业知识库 → kb_chunking / capability_extractor / vector_store / embedding

| 源码模块 | 关键代码 | 面试回答 |
|---|---|---|
| `services/kb_chunking.py:140 build_chunks` | 结构感知切块（~600 字，表格/长文本切分，保留章节祖先路径） | 一句话：**向量负责找，结构化负责证明**。 |
| `services/capability_extractor.py:156 CapabilityExtractor` `:166 extract` `:312 run_kb_task` | 能力卡抽取（category × attributes 结构化事实） | Capability 层把事实锁进能力卡（如 CAP-0001 智慧园区综合管理平台V3.2：max_devices=2000），规则引擎直接比较数字，不让模型去"理解"。**生成时数字只能来自 Capability。** |
| `services/vector_store.py:42 VectorStore` `:105 MilvusVectorStore` `:240 SqliteVectorStore` | Milvus 主 + SQLite 降级，engine 字段透明标识（降级不撒谎） | 历史标书只切块不抽卡——它是写作风格参考，绝不是企业事实。 |
| `services/embedding.py:51 BgeEmbedding` `:78 FakeEmbedding` | bge 嵌入 + 离线假嵌入（验收可离线跑） | 验收基线用 FakeEmbedding 种子，真实样例需 bge。 |
| `services/kb_versions.py:28 next_label` `:37 record_version` `:55 latest_kb_label` | 知识库每次变更自动升版 `{日期}-v{n}`，生成任务快照版本号 | 见第八章。 |

---

## 第五章 需求匹配 → services/matching/（全链）

| 源码模块 | 关键代码 | 面试回答 |
|---|---|---|
| `normalize/normalizer.py:82 RequirementNormalizer` `:100 normalize` | 需求标准化 + LLM 合并语义相同项（可关） | 36 条原始需求 → 34 条 canonical（REQ-C-xxxx），带成员来源。 |
| `normalize/cluster.py:32 RequirementClusterer` `:41 cluster` `normalize/deduplicator.py:33 Deduplicator` | 聚类 + 代表项选取 | 同一条"设备接入"在招标书里出现两次（p12/p31），聚类成 REQ-C-0002 一条。 |
| `extract/constraint_extractor.py:144 ConstraintExtractor` `:148 extract` | 量化约束提取（数字 + 操作符 + 单位） | 约束是规则引擎的输入——"不少于 1000 台"。 |
| `classify/requirement_classifier.py:69 RequirementClassifier` `:76 classify` | 需求分类（PRODUCT_CAPABILITY 等） | 决定去哪找证据。 |
| `retrieve/semantic_retriever.py:51 SemanticRetriever` `retrieve/capability_retriever.py:53 CapabilityRetriever` `retrieve/evidence_ranker.py:46 source_tier` | 三路检索 + 来源分层排序 | 检索只是"找"，判断靠规则。 |
| `rules/rule_engine.py:149 RuleEngine` `:153 evaluate_constraint` `:285 evaluate_requirement` | **确定性规则比较数字**（容差 ±1%） | "设备接入 1000 台 vs 能力卡 2000 台" → FULL。这是核心：判断不是 LLM 猜的。 |
| `judge/llm_judge.py:69 LLMJudge` `:138 HeuristicJudge` | LLM Judge 兜底 + 启发式离线判官；**证据编号白名单铁律**（Judge 只能引用传入池内的证据号） | LLM Judge 只做规则覆盖不了的语义判断，且输出证据编号必须 ∈ 传入池——防 Judge 自己编证据。 |
| `validate/conflict_detector.py:77 ConflictDetector` `:85 detect` | 冲突检测 + **仲裁按来源权威/时间**（正式资料覆盖旧宣传册） | 知识冲突：正式资料 2000 台 vs 旧宣传册 1250 台 → 正式资料胜。 |
| `validate/evidence_validator.py:41 EvidenceValidator` | 证据存在性/一致性校验 | 引用的每个证据必须真实存在于库。 |
| `pipeline/matcher.py:50 Matcher` `:82 match` `:137 _match_one` | 管线编排 + 四状态产出 | **四状态：FULL/PARTIAL/MISSING/UNKNOWN——没有找到 ≠ 不具备。** MISSING 带相反证据，UNKNOWN 标【待确认】。 |
| `report/response_table.py:28 ResponseTableBuilder` | 需求响应表（M3 版） | 输出招标方直接可看的响应表。 |

---

## 第六章 标书生成 → services/generation/

| 源码模块 | 关键代码 | 面试回答 |
|---|---|---|
| `outline.py:117 OutlineBuilder` `:150 materialize` `:191 load_tender_doc_sections` `:240 _match_source_refs` | 26 章节大纲 + 招标文档章节结构对齐 | 不是"LLM 生成标书"，而是**需求 + 证据 + 章节规划 → LLM 组织语言**。 |
| `context.py:101 GenerationContextBuilder` `:107 build` | 三件套：章节定义 / 需求列表 / 证据池 | 每章节生成前先组装上下文——生成只拿这三样。 |
| `mapping.py:42 RequirementSectionMapper` `:51 map_all` | 需求 → 章节映射（覆盖度统计） | 每条需求必须有章节响应（否则 M5 抓 REQUIREMENT_MISSING）。 |
| `models.py:37 FactClass` | 三分类：FACT / WRITING_STYLE / INFERENCE | FACT 段只许用证据事实；INFERENCE 段（如施工进度安排）允许模板推断。 |
| `generator.py:40 SectionGenerator` `:49 generate_section` `:122 _validate_fact_constraints` `:145 _check_evidence_ids` `:152 _check_no_claim` `:174 _check_number_trace` | **事实约束铁律**：证据编号白名单 / 无证据禁止生成 / 数字不可溯源标【待确认】 | 生成器自带三道闸，不是生成完再查。 |
| `strategies.py:54 FixedFormatStrategy` `:189 FactTemplateStrategy` `:278 TableTemplateStrategy` `:299 SolutionLLMStrategy` `:339 strategy_for` | 4 类章节策略：固定格式/事实模板/表格模板/LLM 方案；LLM 失败回退事实模板 | "能模板的就不 LLM"——封面/资质表/响应表走模板，技术方案才用 LLM。 |
| `response_table.py:21 BidResponseTableBuilder` | 生成版响应表（M4） | 响应表行直接引用匹配状态与证据。 |
| `job.py:40 GenerationJobRunner` `:77 run` `:186 run_generation_task` | 任务状态机 + 逐章节生成 + 失败重试 | 长任务可追踪、可取消。 |
| `assembler.py:34 BidDocumentAssembler` `:41 assemble` `:113 render_docx` | Markdown + DOCX 组装（封面/页眉页脚/响应表） | 交付物是正式 DOCX。 |

---

## 第七章 质量检查 → services/quality/

| 源码模块 | 关键代码 | 面试回答 |
|---|---|---|
| `runner.py:54 QualityRunner` `:63 run` `:110 finalize` | 质检运行 + finalize（**有未清 CRITICAL/ERROR → 409 拒绝发布**） | 系统生成内容后**不直接交付，而是再次验证**；只检查不重写，确认权在人。 |
| `registry.py:122 FactRegistryBuilder` `:128 build` | 事实注册表（从能力卡/证据构建 metric/person/cert/project 条目） | 检查的"基准真值"全部来自知识库，不是模型。 |
| `checks/facts.py:56 check_facts` | **Layer A 成员资格**（每个数字 ∈ 允许语料）+ **Layer B 锚定比较**（32 字符窗口防"质保 3 年 vs 张伟 3 年"串线）+ 证书双向校验 | 篡改 2000→5000 → NUMBER_MISMATCH；ISO9001→9002 → CERTIFICATE_MISMATCH；过期证书 → CRITICAL。 |
| `checks/completeness.py:39 check_completeness` | 需求完整性（★/高重要缺失 → CRITICAL）+ 评分项覆盖 + 章节完整性 + 项目名一致 | 响应表行 + 章节正文双重检查。 |
| `checks/consistency.py:37 check_consistency` `:48 _conflicts` `:125 _references` | 跨章节冲突（同一事实两个值 → CONFLICT）+ 引用三重检查（∈证据池/∈本项目/∈该需求绑定证据） | 注入 EVD-9999 → INVALID_REFERENCE(CRITICAL)。 |
| `checks/format_check.py:32 check_format` `checks/llm_judge.py:38 check_semantic_coverage` | 格式 4 项 + LLM 语义覆盖二次审查 | LLM 只补"语义上有没有回应"这一层。 |
| `checks/scan.py:145 extract_claims` `:82 iter_numbers` | 数字扫描 + 指标锚定提取 | 抽数的确定性基础。 |
| `scoring.py:46 score_report` | 五维评分 = clamp(100 − Σ权重)，CRITICAL=20/ERROR=10/WARNING=3/INFO=0.5 | 验收不是"看着还行"：**9 组变异注入全部被抓**（M5 验收 23/23）。 |
| `models.py:29 IssueType` `:46 Severity` | 问题类型与严重度枚举 | 能报出真实类型名：NUMBER_MISMATCH/CERTIFICATE_MISMATCH/CONFLICT/INVALID_REFERENCE… |

---

## 第八章 企业级能力 → auth/ + task_tracker / trace / evaluation

| 源码模块 | 关键代码 | 面试回答 |
|---|---|---|
| `auth/security.py:27 hash_password` `:50 create_access_token` `:61 decode_token` | PBKDF2 600k + JWT HS256 | 能跑通 ≠ 能上线。 |
| `auth/deps.py:72 has_permission` `:78 require_permission` `:89 require_project_permission` `:112 require_admin` | 5 角色 × 17 权限 + 项目成员三段判定（持 final:view 也必须是项目成员） | 认证管谁能用、RBAC 管谁能操作。 |
| `auth/audit.py:18 record_audit` | 13 类关键动作留痕，仅 admin 可见 | 审计管谁做过什么。 |
| `services/kb_versions.py:28-55` | 知识库版本 + 生成任务 kb_version 快照 | **终版被质疑时能回答"当时用的企业资料是哪个版本"**。 |
| `services/task_tracker.py:22 create_task` `:84 cancel_task` | TSK-xxxx 五态；cancel 仅 pending（本人可取消/他人 403/running 409） | 长任务统一登记、进度可见。 |
| `services/trace.py:25 AgentTracer` `:31 start` `:48 span` `:76 finish` | trace/span 旁路留痕，监控绝不打断业务 | AI 链路不是黑盒。 |
| `evaluation/runner.py:23 run_retrieval` `:48 run_generation` `:72 run_trends` `:78 run_summary` + `metrics.py:55 recall_at_k` `:62 mrr` `:109 citation_accuracy` `:154 fact_consistency` `:183 requirement_coverage` | 评估四端点，**每个响应带 disclaimer** | 效果可衡量：Recall@10=0.9231 / MRR=0.7173（13 条基线，内部离线评估集口径）。 |

---

## 第九章 五个难点 →（交叉引用，见上）

| 难点 | 落点 | 面试回答 |
|---|---|---|
| 1. 如何避免幻觉 | `generator.py:122/145/152/174` + `quality/checks/facts.py:56` | 生成前三道闸 + 生成后双层确定性校验，两道保险。 |
| 2. 为什么不用纯 RAG | `capability_extractor.py` + `rules/rule_engine.py:149` | RAG 找"像"，投标要"对"——结构化事实 + 规则比较。 |
| 3. 为什么规则优先 | `extraction.py:455` + `rule_engine.py` + `strategies.py` 模板策略 | 能确定性的就确定性，LLM 只用在必须语义理解处。 |
| 4. 为什么没直接上 Agent | `task_tracker.py` + `trace.py` + `generation/job.py` 状态机 | 流程高度确定 → 管线可控可回溯；探索型任务再引入 Agent 编排（任务底座已就位）。 |
| 5. 如何保证可追溯 | `parsers/`（四元溯源）→ `evidences` → `matching/report` → `auth/audit.py` | 文件→章节→页码→原文，全链可回查。 |

---

## 第十章 演进 → db.py / vector_store.py / config.py

| 源码模块 | 关键代码 | 面试回答 |
|---|---|---|
| `db.py Database`（`:49 execute` `:61 query` `:69 insert` `:74 update` 全封装）+ `db_mappers.py` / `db_schema.py` | 无 ORM，数据访问收敛在封装层 | 当前 SQLite 为降低部署复杂度完成端到端验证；**迁移 PostgreSQL 改动集中在存储层**，业务逻辑不感知存储实现。 |
| `vector_store.py` engine 字段 | 存储后端抽象 | 同理可换真实 Milvus 集群。 |
| 演进路径 | — | PostgreSQL（并发/事务）→ Redis（热点缓存）→ 对象存储（文件）→ 分布式任务队列（长任务解耦）。 |

---

## 附录：验收脚本与数字索引

| 里程碑 | 脚本 | 报告 | 关键数字 |
|---|---|---|---|
| M1 | `scripts/verify_m1_extraction.py` | `_m1_verify_report.txt` | 421 需求 / 13 评分点 / 基线 14/14 |
| M2 | `scripts/verify_m2_knowledge.py` | `_m2_verify_report.txt` | 8 类资料全命中 |
| M3 | `scripts/verify_m3_matching.py` | `_m3_verify_report.txt` | 47/47（FULL17/PARTIAL6/MISSING5/UNKNOWN5） |
| M4 | `scripts/verify_m4_generation.py` | `_m4_verify_report.txt` | 18/18（26 章节，81 处 EVD 引用零编造） |
| M5 | `scripts/verify_m5_quality.py` | `_m5_verify_report.txt` | 23/23（基线 99.1，9 组变异全抓） |
| M6 | `scripts/verify_m6_workbench.py` | `_m6_verify_report.txt` | 30/30 + 浏览器 15/15 |
| M7 | `scripts/verify_m7_enterprise.py` | `_m7_verify_report.txt` | 52/52；全量回归 290 passed |
