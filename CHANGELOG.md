# 更新日志（CHANGELOG）

> 口径：各里程碑验收数字基于项目内置离线评估集与样例数据（T-M3 基线），不代表通用准确率。
> 版本节奏沿用五里程碑封版惯例；推送由仓库所有者执行。

## v1.1.0（2026-08-19）

M7 企业级能力（第四阶段首批）完成，封版。

### M7 企业级能力（认证/RBAC/审计/知识库版本/任务中心/Agent 链路监控/评估体系）

- 认证（M7-01）：PBKDF2 600k 口令散列 + JWT HS256（登录/登出/me）；5 个演示账号（admin/manager/editor/reviewer/staff）；错误口令记 login_failed 审计；`AUTH_ENABLED=false` 时系统用户降级（无鉴权部署仍可跑验收）
- RBAC（M7-02）：5 角色 × 17 权限矩阵 + seed_rbac 幂等；`require_project_permission` 三段判定（admin 旁路 → 权限检查 → `final:*` 额外要求项目成员身份）；项目成员增删查（重复添加 409）
- 操作审计（M7-03）：audit_logs 全量留痕（13 类关键动作：登录/失败登录/上传/生成/修订/质检/终版/成员变更/取消任务/查看终版等），action/resource/actor 过滤，仅 admin 可见
- 知识库版本（M7-04）：KV-xxxx 版本行 + `{日期}-v{n}` label；资料重处理/能力卡修订自动升版；生成任务 `kb_version` 快照（回答"这份标书用什么知识生成的"）
- 任务中心（M7-05）：TSK-xxxx 统一任务登记（extract/kb_process/match/generate/quality_check 5 类）+ 进度更新；cancel 仅 pending（本人→cancelled / 他人 403 / running 409 / 终态 409 / 不存在 404）；非 admin 只见自己启动的任务；generate 任务 ref_id 直达生成 job
- Agent 链路 + LLM 监控（M7-06）：AgentTracer trace/span 两级（success/failed 终态，异常重抛不吞错）；5 类任务全接入（user_id 经端点传入）；llm_calls 记录增强 LLM 客户端调用（MockLLM 不记录——离线口径）；监控写库失败绝不打断业务（旁路）
- 评估体系（M7-07）：检索评估（合并 Recall@K/MRR）、生成评估（引用完整率/引用准确率/事实一致率/需求覆盖率）、质量趋势（相邻报告 delta）、三合一 summary；每个响应带 disclaimer
- 验收：HTTP 端到端 **52/52 全 OK** —— 5 演示账号登录 + admin 17/17 权限；RBAC 越权抽查 8 项全 403 + 项目成员闭环 6 步 + workbench delivery_only；审计 31 条含 13/13 类动作 + 过滤 + 仅 admin；知识库版本 2026-08-19-v1/-v2 + 生成任务快照最新 label；任务中心 5 类 × 7 条任务全 success + cancel 五态语义 + 可见性过滤；traces 5 类 success（spans≥1）+ user_id 正确；评估 Recall@10=0.9231 / MRR=0.7173（evaluated=13）、生成 4 指标全 1.0（事实核对 36 条 0 问题、需求覆盖 forward 33/33 + reverse 36/36）、趋势 2 期 1 delta
- 验收修复：pytest.ini 补充 `addopts = -m "not llm and not milvus"`（marker 注释早已宣称"默认跳过"但此前未配置——裸跑 `pytest -q` 时 milvus 回环测试在无 docker 环境连接失败；现默认命令即离线全绿）

## v1.0.0（2026-08-18）

五里程碑 + 标书工作台全部完成，封版。

### M1 招标文件解析 + 需求提取

- 四类解析器（PDF/Word/Excel/扫描件 OCR）→ 统一 ParsedDocument（章节树 + 内容块 + 扫描页标记）；LLM 需求提取（窗口切分 + JSON Schema + Pydantic 校验重试）；评分标准表规则解析（不走 LLM）
- 验收：样例四件套 421 条需求 / 13 评分点（权重和 70）/ 预埋基线 14/14 召回 / ★条款 16 条含项目经理 5 年

### M2 企业知识库

- 8 类资料上传处理；能力卡片（CAP-XXXX，卡片管事实）与向量 chunk 并存；BGE + Milvus 检索，Milvus 挂自动降级 SQLite 暴力余弦（engine 字段透明标识）
- 验收：8 类样例全部入库处理完成（14 张能力卡），6 条语义检索基线全命中，Milvus 与 SQLite 降级双引擎路径实测通过

### M3 需求-能力匹配

- 需求标准化（去重/聚类/LLM 归并/10 类分类/约束结构化）→ 规则引擎 + 能力卡 + 语义检索（双下限 Rerank）→ 证据验证 → 冲突仲裁 → LLM Judge（证据编号白名单铁律）
- 四状态判定 FULL/PARTIAL/MISSING/UNKNOWN（没有证据 ≠ 不满足）；需求响应表 JSON/Markdown 双形态 + 逐条证据链四元溯源
- 验收：HTTP 端到端 47/47 全 OK —— 33 条规范需求（FULL 17 / PARTIAL 6 / MISSING 5 / UNKNOWN 5），预埋基线全命中，冲突 time 仲裁生效

### M4 标书生成引擎

- 默认大纲 8 章 26 章节（四段 + 封面/目录/投标函/响应表）+ 需求→章节确定性映射；四类生成策略（固定格式/事实型/方案型 LLM/表格型，方案型失败回退事实模板）；事实约束铁律（证据白名单 + 无证据【待确认】）
- 需求响应表三列三态；Markdown + DOCX 双输出（eastAsia 宋体）；任务状态机（断点继续/单章节重生成 version+1/409 并发防护）+ 人工编辑
- 验收：HTTP 端到端 18/18 全 OK —— 26 章节全部生成、33 条需求全映射、81 处 EVD- 引用全部 ∈ 证据池（无编造）

### M5 标书一致性与质量检查引擎

- 事实注册表 + 数字双层校验（成员资格 + 锚定比较，防串线）+ 证书双向校验 + 过期检测；完整性（需求/评分项/章节/项目名）/ 一致性（跨章节冲突/引用有效性/待确认收集）；五维评分；质量报告 JSON/Markdown + 人工处理审计留痕 + 仅格式类自动修复 + finalize 终版闭环（CRITICAL/ERROR 未清 409）
- 验收：HTTP 端到端 23/23 全 OK —— 基线 score=99.1、9 组变异全部抓取（事实/完整性/引用/跨章节冲突）、finalize 闭环（409 → 人工确认 → final 三件套 + 审计）

### M6 标书工作台

- 工作台聚合端点（只读派生，不落库）：项目列表（全流程聚合 + 六阶段状态派生 + KB 全局统计）/ 单项目概览（文档明细 + 待处理问题前 5 条）；生成 SSE 事件流（历史日志 tail + 终态 done 关闭流 + 404 防护）
- 前端工作台 8 页面（项目/招标文件/需求分析/知识库/标书生成/质量检查/最终交付）+ 5 组件 + 8 路由 + 自动轮询 store，全量真实接线（fetch + ReadableStream SSE + 断连回退轮询）
- 验收：HTTP 端到端 30/30 全 OK —— 聚合字段（文档 3/2/1、匹配 17/6/5/5、26/26 章节、质量 88.5 分 1 待处理）、六阶段派生、KB 统计、SSE 流、前端文件核查；vue-tsc + vite build 通过；浏览器抽查 3 关键页面 DOM 断言 15/15 无控制台错误
- 验收修复：outline 章节规划 `load_tender_doc_sections` 的 `str.startswith(WindowsPath)` TypeError 与相对 parsed_file 路径前缀（回归测试 `test_outline_with_documents_rows`）
