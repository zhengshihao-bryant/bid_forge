# BidForge 面试演示手册

> 现场作战手册：**3 分钟介绍 → 10 分钟架构讲解 → 一次完整演示（T-M3 六步闭环）→ 20 个高频追问 → 模块源码速查**。
> 演示前 30 分钟过一遍；演示中按第三部分走六步；被追问按第四部分答。
> 口径：文中编号与数字均来自真实数据库与验收报告（scripts/_m*_verify_report.txt），如实演示，不夸大。

---

## 一、3 分钟项目介绍

**第一分钟：业务与判断（先讲业务，不讲技术）**

传统标书流程八步纯人工：客户发布招标文件 → 人工阅读 → 提取需求 → 寻找企业资料 → 复制历史标书 → 修改方案 → 人工检查 → 提交。四个痛点：**信息获取成本高**（几百页 PDF 人工易遗漏）、**企业能力难复用**（资料分散每次重找）、**生成不可控**（LLM 编造资质/金额 = 商业风险）、**缺少质量闭环**（AI 写完谁检查）。所以我的核心判断：**企业 AI 系统最重要的问题不是生成能力，而是结果可信。**

**第二分钟：系统主线（一条数据流）**

系统是七段确定性管线，不是对话 Agent：文档解析 → 企业知识库 → 需求匹配 → 标书生成 → 质量检查 → 工作台 → 企业治理。围绕六个实体（招标项目/需求/证据/能力/生成标书/质量报告）建立一条可追溯数据链——**任何一句标书都能回答"这话哪来的"**。三个关键设计：① 知识双层——向量管找、能力卡管事实，生成时数字只能来自能力卡；② 四状态匹配——FULL/PARTIAL/MISSING/UNKNOWN，没有找到 ≠ 不具备；③ 质检三层——事实/引用/需求覆盖，系统生成的东西系统自己不信，要过一遍检查、过一遍人。

**第三分钟：结果与工程（数字）**

七个里程碑全部完成，全量离线回归 290 passed；各里程碑 HTTP 端到端验收 47/47、18/18、23/23、30/30、52/52。可复述的实测：81 处证据引用零编造；9 组变异注入（篡改数字/伪造证书/假引用）全部被抓；企业级能力（RBAC 5 角色 × 17 权限/审计/知识版本/任务中心/链路监控/评估体系）齐全。收尾一句话：**这个项目的卖点不是"AI 会写标书"，而是让 AI 进入企业核心流程，并且让企业敢用。**

---

## 二、10 分钟架构讲解（七段管线逐段过）

> 节奏建议：每段 60~90 秒。每段"一句话设计 + 一个源码锚点 + 一个验收数字"。

| # | 阶段 | 讲什么（话术） | 源码锚点 |
|---|---|---|---|
| 1 | 文档解析 M1 | "没有把 PDF 转文本，而是恢复 document/section/paragraph 层级，每个块带文件、章节、页码、原文四元溯源——后续生成引用的不是向量结果，而是可定位证据。" | `parsers/pdf_parser.py:66 PdfParser`、`docx_parser.py:52 DocxParser` |
| 2 | 企业知识库 M2 | "双层知识：Chunk 找内容（~600 字结构感知切块），Capability 管事实（能力卡把'设备接入 2000 台'锁成结构化字段）。历史标书只切块不抽卡——它是写作风格参考，绝不是企业事实。**生成时数字只能来自 Capability。**" | `services/kb_chunking.py:140 build_chunks`、`capability_extractor.py:156 CapabilityExtractor` |
| 3 | 需求匹配 M3 | "不是让 AI 判断满不满足，而是系统辅助企业判断需求覆盖：量化约束走规则引擎直接比数字，四状态 FULL/PARTIAL/MISSING/UNKNOWN——**没有找到 ≠ 不具备**；知识冲突按来源权威/时间确定性仲裁。" | `matching/rules/rule_engine.py:149 RuleEngine`、`pipeline/matcher.py:50 Matcher` |
| 4 | 标书生成 M4 | "不是 LLM 生成标书，而是 需求 + 证据 + 章节规划 → LLM 组织语言。每个章节带三件套（章节定义/需求列表/证据池）；段落分 FACT/WRITING_STYLE/INFERENCE 三分类；证据编号白名单、无证据禁止生成、数字不可溯源标【待确认】——铁律在生成器里，不在事后。" | `generation/generator.py:122 _validate_fact_constraints`、`strategies.py:339 strategy_for` |
| 5 | 质量检查 M5 | "生成后不直接交付，而是再次验证。三层检查：事实（Layer A 成员资格 + Layer B 32 字符锚定比较防串线）/引用三重检查/需求覆盖。只检查不重写，未清 CRITICAL 直接 409 拒绝发布。" | `quality/checks/facts.py:56 check_facts`、`quality/runner.py:110 finalize` |
| 6 | 工作台 M6 | "六阶段工作台只读聚合 + 生成 SSE 实时进度，人工编辑走终版闭环。" | `api/routes_workbench.py`、`frontend/src/views/projects/` |
| 7 | 企业治理 M7 | "能跑通 ≠ 能上线：认证（PBKDF2+JWT）、RBAC 5 角色 × 17 权限 + 项目成员三段判定、13 类审计、知识版本（每次变更自动升版 + 生成任务快照）、任务中心（TSK-xxxx 可取消）、trace/span 链路监控、评估体系。" | `auth/deps.py:78 require_permission`、`services/kb_versions.py:28 next_label` |

---

## 三、一次完整演示流程（T-M3 智慧园区项目 · 六步闭环）

> 固定案例：**T-M3「智慧园区平台建设项目」**——36 条原始需求（9 类）、8 份企业资料、9 张能力卡、26 章节标书。
> 这条链路就是面试核心：**需求 → 证据 → 生成 → 质检 → 治理，全程可追溯。**

### 演示前准备（5 分钟）

```bash
# 终端 A：以验收模式起服务，重建 T-M3 基线（种子直接写 SQLite）
cd backend
AUTH_ENABLED=false python -m uvicorn app.api.main:app --port 8001
# 终端 B：跑 M4 验收脚本（内置种子：36 需求 + 8 资料 + 9 能力卡 + 匹配 + 生成 26 章节）
cd backend && python scripts/verify_m4_generation.py
# 重建完毕：Ctrl+C 停掉终端 A，恢复正常服务
python -m uvicorn app.api.main:app --port 8001
# 终端 C：前端
cd frontend && npm run dev
# 浏览器登录：admin / admin123（演示全程用 admin，Step 6 切 manager/staff）
```

### Step 1 · 上传招标文件 → 36 条需求

- **操作**：项目页创建/打开 T-M3，上传 `backend/data/samples/智慧园区项目/01_招标文件.docx`。
- **屏幕**：需求列表 36 条。点开 **REQ-C-0002「设备接入不少于1000台」**（重要度：高）——成员来源两条：REQ-0001（第三章 技术要求 p12）、REQ-0002（第三章 技术要求 p31）。
- **话术**："同一需求在招标书里出现两次，聚类合并成一条 canonical，并保留全部溯源。这就是结构恢复 + 四元溯源的价值。"

### Step 2 · 上传企业资料 → 能力卡 + 证据

- **操作**：知识库页上传 8 份企业资料（01_产品介绍.pdf / 02_项目案例.docx / 03_公司资质.docx / 04_人员资质.docx / 06_售后服务.docx / 07_公司介绍.pdf…）。
- **屏幕**：9 张能力卡。点开 **CAP-0001「智慧园区综合管理平台V3.2」**（产品类）：attributes = `max_devices=2000 / concurrent_users=1000 / availability=99.95%`，来源 01_产品介绍.pdf p3。切块证据 **EVD-0007**（第一章 产品能力 p3）："智慧园区综合管理平台V3.2，设备接入支持不少于2000台，并发1000用户，系统可用性99.95%。"
- **话术**："同一份资料同时产出两个层次：Chunk 负责语义检索，能力卡把关键事实结构化。生成时数字只从能力卡走，不从模型嘴里走。"

### Step 3 · 匹配 → 四状态 + 证据链

- **操作**：匹配页运行匹配（~9 秒）。
- **屏幕**：REQ-C-0002 = **FULL（0.973，rule）**，证据链 EVD-0007~0011（2 条 chunk + 3 条能力卡证据）。**对比展示** REQ-C-0010「设备接入能力不低于2500台」= **PARTIAL**——知识库只有旧版平台 1250 台的证据，如实标"部分满足"。
- **话术**："判断不靠 LLM 猜：量化约束走规则引擎直接比数字。四状态的关键是 **没有找到 ≠ 不具备**——找不到证据标 UNKNOWN 待确认，绝不冒充满足，也不误伤企业资格。"

### Step 4 · 生成 → 26 章节 + 响应表 + 质检通过

- **操作**：生成页启动生成（26/26 章节，秒级完成，SSE 实时进度）。
- **屏幕**：打开 **CH-05-2 总体技术方案**："智慧园区综合管理平台V3.2：max_devices=2000；concurrent_users=1000；availability=99.95%。"——能力卡数字直接进入技术方案。打开 **CH-05-4 技术指标响应表**：设备接入行 = "满足（…支持不少于2000台…）**FULL**"；信创行 = "待确认【待确认】**UNKNOWN**"。质检报告 score=97.9，已批准。
- **话术**："生成器拿到的是三件套（章节定义/需求列表/证据池），LLM 只负责语言组织；响应表的状态与证据来自匹配结果，不是重新生成的。"

### Step 5 · 质检变异 → 当场抓篡改（全场高潮）

- **操作**：在生成工作台**人工编辑 CH-05-2，把 2000 改成 5000** → 重新运行质检。
- **屏幕**：质检报告新增 1 条问题：**NUMBER_MISMATCH**（section=CH-05-2，WARNING，扣 3 分）——"max_devices 标书声明 5000，知识库为 2000（CAP-0001）"。
- **加戏（可选，按验收实录）**：注入引用 `[EVD-9999]` → **INVALID_REFERENCE（CRITICAL）**；清空某章节 → **SECTION_MISSING（CRITICAL）**；此时点"定稿" → **HTTP 409 拒绝发布**。
- **话术**："这是 M5 验收的 9 组变异之一（2000→5000、ISO9001→9002、张伟 6→3 年、合同额 500→800、跨章节冲突 5000 vs 2000、注入 EVD-9999……），全部被抓。系统的态度是：**自己生成的东西自己不信，过一遍检查、过一遍人。**"

### Step 6 · 企业治理 → 谁生成、什么时候、用了哪个知识版本

- **操作**（切 manager 或 admin）：
  1. 知识库页 **编辑 CAP-0001 任一属性** → 版本列表自动新增 **KV-xxxx「2026-08-19-v{n}」**，摘要含 before/after 属性快照；
  2. 再跑一次生成 → 任务记录 **kb_version = 最新版本号**（现场即可制造"生成快照"）；
  3. 管理端审计日志：login / upload_tender / generate_bid / quality_check / edit_capability / finalize_bid…13 类动作留痕；
  4. 任务中心：TSK-xxxx 五态 + cancel；管理端 traces/span + 评估页（Recall@10=0.9231 / MRR=0.7173，带 disclaimer）。
- **话术（收尾金句）**："终版标书被客户质疑'你们凭什么写 2000 台'时，系统能回答：**这数字来自哪份资料的哪一页、哪个版本的知识库、谁在什么时间生成的**。这就是企业敢用的原因。"

---

## 四、20 个高频追问（问题 → 一句话 → 深挖锚点）

**1. 为什么不用直接 RAG 生成标书？**
一句话：标书不是问答，是企业交付场景——RAG 解决信息召回，但无法保证企业事实正确，所以加了 Capability 结构化事实层 + 生成后质量验证。
锚点：`capability_extractor.py` / `generator.py:122` / `quality/checks/facts.py`（白皮书 4.1、第九章难点 2）

**2. 为什么要设计 Capability？（核心亮点）**
一句话：向量检索适合解决"在哪里"，企业业务要回答"是否真实拥有"——不应依赖模型从文本里推理，所以单独建立能力卡，让事实可比较、可验证、可审计。
锚点：`services/capability_extractor.py` / `matching/rules/rule_engine.py:149`（白皮书 4.2）

**3. MISSING 和 UNKNOWN 为什么区分？**
一句话：企业场景里没有找到证据 ≠ 企业没有能力，混淆会导致系统错误拒绝企业参与投标——MISSING 带相反证据，UNKNOWN 标【待确认】。
锚点：`matching/models/match_result.py:23 MatchStatus` / `pipeline/matcher.py:137 _match_one`（白皮书 5.3）

**4. 为什么生成后还需要检查？**
一句话：LLM 擅长语言组织，不适合当事实数据库——生成和验证分离：生成负责表达，检查负责可信，且只检查不重写。
锚点：`quality/runner.py:54 QualityRunner` / `checks/facts.py:56 check_facts`（白皮书 7.1）

**5. 为什么没有用 Agent？（非常加分）**
一句话：这个阶段标书流程高度确定，优先保证流程可控和结果可信；Agent 更适合开放探索任务，后续研究型任务再引入 Agent 编排。
锚点：`task_tracker.py` / `trace.py` / `generation/job.py:40 GenerationJobRunner`（白皮书第九章难点 4）

**6. 一句话介绍项目？**
一句话：把"招标文件 + 企业资料"变成"可交付标书"的企业级 AI 工作流系统，核心命题是结果可信——AI 生成的一切必须能回溯到证据。

**7. RAG 在项目里算什么？**
一句话：RAG 只是一段检索能力；核心是需求 × 能力的双向结构化，检索之上有规则判断、白名单生成、确定性质检三层保障。
锚点：`matching/retrieve/semantic_retriever.py:51`（白皮书 4.1、Q2）

**8. 知识库怎么设计？**
一句话：向量负责找、结构化负责证明——Chunk 层检索，Capability 层管事实；Milvus 挂了自动降级 SQLite（engine 字段透明标识，降级不撒谎）。
锚点：`kb_chunking.py:140` / `vector_store.py:42`（白皮书第四章）

**9. 幻觉怎么控制？**
一句话：三道闸——生成前证据白名单 + 数字可溯源检查、生成后双层确定性校验（成员资格 + 锚定比较）、引用三重检查；实测 81 处引用零编造、9 组变异全抓。
锚点：`generator.py:145/152/174` / `checks/facts.py` / `checks/consistency.py:125`（白皮书 6.1、7.2）

**10. 需求怎么保证被完整响应？**
一句话：确定性规则做完整性检查——响应表行 + 章节正文双重检查，★/高重要缺失直接 CRITICAL，LLM 只补语义覆盖二次审查。
锚点：`quality/checks/completeness.py:39`（白皮书 7.2）

**11. 质量怎么量化？**
一句话：五维评分 = clamp(100 − Σ严重度权重)（CRITICAL=20/ERROR=10/WARNING=3/INFO=0.5），验收靠 9 组变异注入而不是"看着还行"；未清 CRITICAL 不能 finalize。
锚点：`quality/scoring.py:46` / `runner.py:110 finalize` / `scripts/verify_m5_quality.py`（白皮书 7.3/7.4）

**12. 评分表为什么不用 LLM 解析？**
一句话：大模型读表不可靠（漏行/串列/读错权重），评分表格式有限可枚举，规则解析又准又便宜——能确定性的就确定性。
锚点：`services/extraction.py:455 parse_score_tables`（白皮书 3.4、第九章难点 3）

**13. 项目里最难的问题是什么？**
一句话：事实正确性——"不能写错"比"写得快"重要；三个子问题（数字串线/证据编造/知识冲突）各配确定性解法并验收证明。
锚点：`checks/scan.py:145 extract_claims`（32 字符窗口）/ `matching/validate/conflict_detector.py:77`（白皮书第九章）

**14. 企业级能力为什么需要？**
一句话：能跑通 ≠ 能上线——权限管谁能看、审计管谁干了、版本管"当时用的什么知识"，终版被质疑时全部能回答。
锚点：`auth/`、`services/kb_versions.py`（白皮书第八章）

**15. 任务中心 / Trace 解决什么？**
一句话：AI 链路不是黑盒——任务统一登记（TSK-xxxx、可取消）、trace/span 两级留痕、监控旁路绝不打断业务。
锚点：`services/task_tracker.py` / `services/trace.py:25 AgentTracer`（白皮书第八章）

**16. 从 0 到 1 怎么推进的？**
一句话：里程碑式推进，每个里程碑交付"实现 + 离线测试 + HTTP 端到端验收脚本"，验收脚本入库可复跑，暴露的缺陷全部带回归测试。
锚点：`scripts/verify_m*.py` + `tests/`（白皮书 10.3）

**17. 评估数字怎么理解？（防追问）**
一句话：Recall@10=0.9231 / MRR=0.7173 是 13 条基线在内部离线评估集上的检索质量，是迭代标尺，不代表生产准确率——所有响应带 disclaimer。
锚点：`evaluation/metrics.py:55/62` / `evaluation/golden.py:87`（白皮书 10.4）

**18. 如果重来一次，你会改什么？**
一句话：三个"更早"——更早做评估框架、更早做审计、更早做知识版本化；不改的是事实约束铁律、四状态口径、只检查不重写。
锚点：白皮书 10.2

**19. 存储为什么用 SQLite？生产怎么办？**
一句话：降低部署复杂度完成端到端验证，数据访问收敛在 Database 封装层（无 ORM），迁移 PostgreSQL 改动集中在存储层。
锚点：`db.py Database`（`:38 connect` `:49 execute` `:61 query`，含 db_mappers/db_schema 分层）/ `vector_store.py` engine 字段（白皮书 10.1）

**20. 这个项目最自豪的设计是什么？**
一句话：事实约束铁律 + 四状态口径 + 只检查不重写——这三条从第一天定下，被 290 个测试和 7 次端到端验收证明是对的；它们共同支撑一个判断：结果可信。
锚点：`generator.py:122` / `matcher.py:82` / `quality/runner.py:63`（白皮书 1.4、10.2）

---

## 五、模块 ↔ 源码位置速查（被追问"代码在哪"时用）

| 模块 | 位置 | 关键符号（报名字即可） |
|---|---|---|
| 解析 | `backend/app/parsers/` | `PdfParser.parse` `DocxParser.parse` `build_section_tree` `ocr_pdf_pages` |
| 需求提取 | `backend/app/services/extraction.py` | `RequirementExtractor.extract` `parse_score_tables` |
| 知识库 | `services/kb_chunking.py` `capability_extractor.py` | `build_chunks` `CapabilityExtractor.extract` |
| 向量/嵌入 | `services/vector_store.py` `embedding.py` | `MilvusVectorStore` `SqliteVectorStore` `BgeEmbedding` |
| 匹配 | `services/matching/` | `Matcher.match` `RuleEngine.evaluate_constraint` `LLMJudge.judge` `ConflictDetector.detect` `ResponseTableBuilder` |
| 生成 | `services/generation/` | `OutlineBuilder.materialize` `GenerationContextBuilder.build` `SectionGenerator.generate_section` `_validate_fact_constraints` `strategy_for` `BidDocumentAssembler.assemble` |
| 质检 | `services/quality/` | `QualityRunner.run` `finalize` `FactRegistryBuilder.build` `check_facts` `check_completeness` `check_consistency` `score_report` |
| 认证/RBAC | `backend/app/auth/` | `hash_password` `create_access_token` `has_permission` `require_project_permission` |
| 审计/版本/任务/链路 | `services/` | `record_audit` `record_version` `create_task` `cancel_task` `AgentTracer.start/span/finish` |
| 评估 | `backend/app/evaluation/` | `run_retrieval` `run_generation` `recall_at_k` `mrr` `fact_consistency` |
| API 装配 | `backend/app/api/main.py` | `include_router`（10 个路由） |
| 存储层 | `backend/app/db.py` | `Database.query/insert/update/init_schema` |
| 前端 | `frontend/src/views/` `components/` `stores/workbench.ts` | 6 阶段工作台 + SSE |
| 验收 | `scripts/verify_m1..m7_*.py` | 每里程碑一份，报告 `_m*_verify_report.txt` |

---

## 防失分提醒（演示现场版）

1. **先讲业务痛点与"结果可信"判断**，技术是为判断服务的，别一上来报技术栈。
2. **演示失败预案**：若服务未起/数据被清，按"演示前准备"重跑 `verify_m4_generation.py` 即可重建基线（约 1 分钟）。
3. **数字只报验收实录**：47/47、23/23、52/52、290 passed 是脚本实跑结果；评估数字带"内部离线评估集"口径，被追问主动声明。
4. **报真实类型名**：篡改数字 → NUMBER_MISMATCH；证书造假 → CERTIFICATE_MISMATCH；跨章节矛盾 → CONFLICT；假引用 → INVALID_REFERENCE（别编不存在的类型名）。
5. **收尾落在价值**：让 AI 进入企业核心流程，并且让企业敢用——这是与普通 AI 项目的分水岭。
