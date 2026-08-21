# 企业标书生成平台 —— 路线图

> 项目时间线：2024.09 – 2025.04 ｜ 五里程碑沿用企业法律知识助手的封版节奏

核心架构判断（已在需求分析阶段确定，不因实现困难而动摇）：

- **Agent 不是起点**。第一阶段是"解析 → 提取 → 检索 → 生成 → 人工确认"的确定性管线；Agent（自动规划/自动生成整套标书）放在第四阶段。
- **RAG 不是项目**。需求实体 × 能力实体的双向结构化才是核心，RAG 只是其中"检索"一段。
- **事实约束是第一优先级**。"不能写错"比"写得快"重要得多。

## 里程碑

### M1 招标文件解析 + 需求提取（2024.09–10）✅ 已完成

- [x] 四类解析器：PDF（PyMuPDF，TOC/字号/编号启发式标题）、Word（python-docx，iter_inner_content）、Excel（openpyxl）、扫描件（PaddleOCR 3.x 可插拔，延迟导入）
- [x] 统一产物 ParsedDocument：章节树 + 内容块 + 扫描页标记
- [x] LLM 需求提取：窗口切分（≤4000 字，【第p页】标记只存在于临时文本）→ JSON Schema 提示词 → Pydantic 校验（失败重试 3 次）→ 去重编号 REQ-XXXX
- [x] 评分标准表规则解析（不走 LLM）；★条款规则补扫
- [x] FastAPI 端点 + 后台提取 + 状态轮询 + 人工修订
- [x] 样例招标文件包（4 类格式 + 14 条预埋需求基线）
- [x] 前端脚手架（Vue3+Vite+Element Plus：招标列表/详情页可用，build 通过）
- **验收**（2026-08-14 实测）：样例四件套端到端提取 → 421 条需求 / 13 个评分点（技术 9 + 商务 4，权重和 70）/ 预埋基线 14/14 召回 / 量化数字原样保留 / ★条款 16 条含项目经理 5 年 / PDF 需求带页码、docx 带章节路径；33 个离线测试 + llm 集成测试通过。核查脚本 `scripts/verify_m1_extraction.py`

### M2 企业知识库（2024.10–11）✅ 已完成

- [x] 资料上传分类（产品/案例/资质/人员/方案/售后/介绍/历史标书，8 类枚举 + 多文件上传 + 状态轮询）
- [x] 能力卡片（结构化实体，与 chunk 并存：卡片管事实、向量管检索；全局编号 CAP-XXXX；历史标书只切块嵌入）
- [x] BGE 嵌入 + Milvus（复用法律助手方案，bge-large-zh 1024 维 + HNSW/COSINE）；SQLite 元数据过滤（Milvus 挂自动降级 SQLite 暴力余弦，engine 字段透明标识）
- [x] 样例企业资料包（8 类文件 + 样例说明，`scripts/make_sample_kb.py`，与 M1 招标基线对齐、为 M3 匹配预埋正向证据）
- **验收**（2026-08-14 实测，报告 `scripts/_m2_verify_report.txt`）：8 类样例资料全部解析入库并处理完成（14 张能力卡 / 10 块，索引 done）；6 条语义检索基线全部命中且出处正确（张伟 6 年/设备接入 2000 台/员工 320/质保 3 年/ISO9001 编号 00222Q12345R0S/案例 1250 万）；能力卡事实核对通过（张伟 experience_years=6、ISO9001 cert_no 原样）；Milvus（v2.3.3 × pymilvus 3.0.0，probe 通过）与 SQLite 降级两条引擎路径均实测通过。61 个离线测试 + 6 个 llm/milvus 标记集成测试。核查脚本 `scripts/verify_m2_knowledge.py`
- 口径声明：以上检索/提取结果基于本项目样例企业资料包与离线测试集，不代表通用准确率

### M3 需求-能力匹配（2024.11–12）✅ 已完成

口径：M3 只回答"招标方要求什么？我们有没有？证据是什么？"，不写标书（写标书是 M4）。

- [x] 需求标准化（M3-01/02/03）：去重（精确 + bigram Jaccard）/ 聚类（同类型 0.45、跨类型 0.6）/ LLM 归并（成员约束并集、出处逐条保留）+ 10 类关键词分类 + 约束结构化（M1 quantitative + 正则扫描 + 资质存在性，数字原样不改写）
- [x] 匹配管线（M3-06~11/14）：规则引擎（数值比较/单位归一/存在性）→ 能力卡 + 语义检索（BGE+Milvus，双下限 Rerank）→ 证据验证（VALID/INVALID，内容回原文精确匹配）→ 冲突检测（来源权威/时间仲裁，未决 → UNKNOWN）→ LLM Judge（严格 JSON + 证据编号白名单铁律）
- [x] 四状态判定 FULL/PARTIAL/MISSING/UNKNOWN：MISSING = 资料明确显示不满足；无证据 → UNKNOWN（**没有证据 ≠ 不满足**）；历史标书不能覆盖正式项目资料
- [x] **需求响应表**（M3-15）：JSON/Markdown 双形态（需求编号 × 匹配结果 × 企业能力 × 证据 × 出处 × 置信度）+ 逐条证据链（REQ-C → MATCH → EVD → CAP/块 → DOC → 章节 → 页码 → 原文）
- [x] API：`POST /match` 后台任务 + 状态轮询 + 规范需求/匹配列表/单条证据链/响应表
- **验收**（2026-08-17 实测，报告 `scripts/_m3_verify_report.txt`）：HTTP 端到端 **47/47 项全 OK** —— 36 条原始需求收敛为 33 条规范需求（<421）；分布 FULL 17 / PARTIAL 6 / MISSING 5 / UNKNOWN 5；预埋基线全命中（设备接入 2000台→FULL、项目经理 5 年→FULL（张伟 6 年）、ISO9001→FULL、质保 ≥2 年→FULL（3 年）、业绩 ≥3 个 ≥500 万→FULL、工期 ≤12 月→PARTIAL（仅历史标书证据，置信度 0.48）、报价/格式/涉密→UNKNOWN、5000台/质保 5 年→MISSING 带相反证据）；冲突 time 仲裁（正式资料 2000 台 vs 旧版 1250 台）；FULL 17 条全部 ≥1 VALID 证据且四元溯源非空；响应表双形态 33 行 + 证据链可追溯到原文。58 个离线测试 + llm/milvus 标记集成测试。核查脚本 `scripts/verify_m3_matching.py`（验收中修复：Milvus 连接失败 60s 冷却、响应表 ORDER BY id、Markdown 证据链 dict 键访问）
- 口径声明：以上结果基于项目内置验收基线（与 tests/test_m3_matcher.py 同源）与离线确定性匹配路径，不代表通用准确率

### M4 标书生成引擎（2025.01–02）✅ 已完成

口径：M4 只负责"把正确的需求 + 正确的企业证据组织成一份完整标书"；一致性/事实核验/完整性/质量评估留给 M5。

- [x] 标书结构规划（M4-01）：默认大纲 8 章 26 章节（商务/技术/实施/售后四段 + 封面/目录/投标函/响应表），章节树含 section_type（方案型/事实型/表格型/固定格式）与 source_refs 关联原始招标章节；`POST /outline` 规划即落库
- [x] 需求→章节映射（M4-02）：确定性规则（需求 M1 类型 ∩ 章节声明类型，一对多），覆盖统计 total/mapped/unmapped，评分细则天然排除；`POST /outline` 一并落库
- [x] 生成上下文（M4-03/04）：证据去重/按置信度排序/四元溯源/长度截断；历史标书只作 WRITING_STYLE 参考（不当企业事实）
- [x] 事实约束铁律（M4-05）：FACT/WRITING_STYLE/INFERENCE 三分类；量化指标不在证据/能力卡 → 原位标【待确认】；MISSING/UNKNOWN 不声称具备（LLM 提示词 + 确定性校验器双保险）
- [x] 章节生成器（M4-06/08）：四类策略分派（固定格式模板 / 事实型模板+能力卡 / 方案型 LLM+证据 / 表格型结构化行）；方案型 LLM 失败回退事实模板不产生空章节
- [x] 需求响应表（M4-07）：招标要求 | 企业响应 | 证据 三列；FULL/PARTIAL/MISSING/UNKNOWN 状态口径如实陈述；JSON/Markdown/DOCX 三态
- [x] 完整标书组装（M4-09）：前序章节组装 + 封面/目录/页眉页脚/表格/生成信息元数据；Markdown + DOCX 双输出（python-docx 程序化，中文字体 eastAsia 宋体）
- [x] 生成任务状态机（M4-10）：job 未生成→生成中→已完成/部分失败/失败；断点继续（跳过已完成章节）、单章节重新生成（version+1）、409 并发防护、后台任务 + 状态轮询、SSE 读源日志
- [x] API：`/api/generation` 12 端点（outline/coverage/jobs/sections/regenerate/response-table/document/logs）
- [x] 人工编辑章节（PATCH content_md → 草稿→已编辑，人工确认留给前端/M5）
- **验收**（2026-08-17 实测，报告 `scripts/_m4_verify_report.txt`）：HTTP 端到端 **18/18 项全 OK** —— 26 章节全部生成（四大块齐全、前序组装、content 全非空）；需求覆盖 33 条全映射（FULL17/PARTIAL6/MISSING5/UNKNOWN5 状态口径逐条核验，MISSING 不编造、UNKNOWN 待确认）；可追溯（整本 81 处 EVD- 引用全部 ∈ 证据池 87 条，无编造；能力卡数值原样落章：注册资本5000万/ISO9001/等保三级/张伟+PMP+6年不串线）；Markdown+DOCX 双文件生成（DOCX zip 魔数）。59 个离线测试 + 1 个 llm 标记集成测试（真实 LLM 方案型：证据编号白名单 + 数字可溯源）。核查脚本 `scripts/verify_m4_generation.py`（验收中发现并修复：outline 端点未触发需求→章节映射落库导致 coverage mapped=0，现 POST /outline 即落库 + 回归测试）
- 前端脚手架（Vue3+Vite+Element Plus）与 Web 界面、Word 人工编辑流程保留到 M5 之后阶段（后端引擎为本里程碑主交付）
- 口径声明：以上结果基于项目内置验收基线（与 tests/test_m3_matcher.py 同源）与离线确定性生成路径，不代表通用准确率

### M5 标书一致性与质量检查引擎（2025.01–02）✅ 已完成

口径：M5 只检查、不重写——消费 M4 的 BidDocument + M1 Requirements + M2 Evidence/Capability + M3 MatchResult，产出 QualityReport；事实检查只发现不修改，自动修复仅限格式类（行尾空白/连续空行/标题缺空格/表行管道数），绝不碰企业事实/金额/年限/资质/商务承诺。

- [x] 事实注册表（M5-02/10）：capabilities 结构化为主 + evidences 兜底（排除历史标书）；数值归一镜像 rule_engine（万元↔元、年↔月、区间 lo/hi）；同指标多卡并组、命中任一即过；人员/资质/项目/指标四类锚点关键词窗口比较
- [x] 事实一致性（M5-03/04/05/06）：数字双层——Layer A 成员资格（事实区数字必须 ∈ 证据/需求/能力卡语料，结构性数字豁免集防误报：待确认/年份/页码/序号/日期/7×24 等）+ Layer B 锚定比较（窗口 32 字符须含全部锚点关键词，防"质保3年 vs 张伟3年"串线）；人员/资质/项目/指标归类四种 mismatch 严重度分级；证书双向校验（名称缺失 + 伪造 token 如 ISO9001→9002）+ valid_until 过期 → CRITICAL
- [x] 完整性（M5-07/08/09）：需求完整性（响应表行 + 章节正文标题匹配，证据出现 ≠ 需求被响应；★/高重要缺失 → CRITICAL）；评分项覆盖（score_points 关键词在正文 → 覆盖，否则 SCORE_MISSING）；章节完整性（26 前序章节逐对校验行/内容/状态）；项目名一致性（封面/生成信息 ≠ tenders.name → PROJECT_MISMATCH）
- [x] 一致性（M5-10/11/12）：跨章节冲突（同指标两处事实区声明数值区间不相交 → CONFLICT，两 section 都记 source_refs）；引用有效性（EVD- 引用 ∈ 池 / ∈ 当前 tender / ∈ 该需求 evidence_ids，否则 INVALID_REFERENCE）；待确认收集（【待确认】逐条 PENDING_CONFIRMATION）
- [x] 语义覆盖二次审查（M5-13）：LLM 只判语义覆盖（covered/not + 理由），数字/证书/证据存在性全由确定性程序负责；FakeLLM/无 Key 空返回 → 不新增 issue（离线兜底口径）
- [x] 五维评分（M5-14）：完整性/事实准确性/证据覆盖/一致性/格式完整性，每维 = clamp(100 − Σ严重度权重, 0, 100)，总分 5 维均值 round(,1)（CRITICAL=20/ERROR=10/WARNING=3/INFO=0.5）
- [x] 质量报告 + 人工处理 + 自动修复（M5-15/16/17）：JSON + Markdown 报告（总分/5 维/按严重度分组/待确认汇总）；PATCH 人工确认/忽略/修复 → review_records 审计留痕；autofix 仅格式类（422 防护）
- [x] 终版闭环（M5-19）：finalize 校验 CRITICAL/ERROR 未清 → 409 → 清状态 → final.docx + final.md + quality-report.json 三件套 + 审计快照
- [x] API：`/api/quality` 8 端点（check/reports/issues/patch/autofix/finalize/final）
- **验收**（2026-08-18 实测，报告 `scripts/_m5_verify_report.txt`）：HTTP 端到端 **23/23 项全 OK** —— 基线 score=99.1、9 条待确认、0 CRITICAL/ERROR；9 组变异全部抓取（设备接入 2000→5000 / 张伟 6→3 年 / ISO9001→9002 / 合同额 500→800 / 删 canonical（UNKNOWN 需求）→ REQUIREMENT_MISSING / 章节清空 CH-06-1 / 封面项目名替换 / 跨章节冲突 5000 vs 2000 / 注入 EVD-9999）；finalize 闭环（未清 CRITICAL → 409 → PATCH 确认 → 已批准 score=95.1 → final.json/docx + review_records 审计）。67 个 M5 测试（66 离线确定性 + 1 llm 标记）。核查脚本 `scripts/verify_m5_quality.py`（种子嵌入后端 bge，确定性生成基线）
- 已知限制：PDF 终版跳过（final.docx + final.md + quality-report.json 三件套）；语义覆盖审查默认关闭（include_llm=true 才跑，需配置 .env 的 LLM_API_KEY）；前端质量工作台（评分卡/问题列表/标记确认/导出）为 M5 之后阶段，API 字段已备好
- 技术白皮书（docs/whitepaper/）+ CHANGELOG + tag v1.0.0 封版推送留待后续
- 口径声明：以上结果基于项目内置验收基线（与 tests/test_m3_matcher.py 同源）与离线确定性生成路径；score 为 BidForge 内部质量指标（按问题严重度扣分的 5 维公式），不代表通用准确率

## 阶段扩展（M5 之后，对应需求分析第二~四阶段）

- 第二阶段：需求自动分类、历史标书复用、引用来源强化
- 第三阶段：需求响应表增强、评分点分析、标书完整性检查、事实一致性检查、敏感信息检查
- 第四阶段：多人协作、权限、版本管理、审核流程、项目管理 → 最后才考虑 Agent

## 工作惯例

- 本地 commit 按里程碑粒度；推送 Gitee 由仓库所有者执行
- `.env` 不入库（真实 Key 只在本机）；commit 前复核暂存清单
- 评估数字必须附口径声明（"基于项目内离线评估集，不代表通用准确率"）
