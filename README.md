# 企业标书生成平台（BidForge · Bid Generation Platform）

![CI](https://github.com/zhengshihao-bryant/bid_forge/actions/workflows/ci.yml/badge.svg)
![Python](https://img.shields.io/badge/Python-3.10%20%7C%203.11-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.110%2B-009688?logo=fastapi&logoColor=white)
![Vue 3](https://img.shields.io/badge/Vue_3-Vite%20%2B%20Element%20Plus-4FC08D?logo=vuedotjs&logoColor=white)
![RAG](https://img.shields.io/badge/RAG-Milvus%20%2F%20SQLite-5A29E4)
![LLM](https://img.shields.io/badge/LLM-DeepSeek%20%28OpenAI%20compatible%29-6A5AF9)

## English Summary

**BidForge** is an enterprise-grade, AI-powered **bid proposal generation platform** that turns 200–500 page tender documents (PDF / Word / Excel / scanned attachments) into compliant, evidence-backed bid proposals. It was built and verified milestone-by-milestone (M1–M7) between 2024.09 and 2025.05.

**What makes it different from "just another RAG":** BidForge treats RAG as only one capability inside a larger, bidirectional-structure pipeline — parsed **Requirements** from tender documents are matched against enterprise **Capabilities** extracted from a company knowledge base, producing evidence-chained match results that drive a template-based generation engine with strict fact-conservation rules (numbers and qualifications may only come from retrieved evidence; anything unverified is rendered as `【待确认】`).

**Feature highlights:**

| Area | What it does |
|---|---|
| Tender parsing (M1) | 4 parser types (PDF/Word/Excel/OCR) → unified `ParsedDocument` → structured requirements with 4-way provenance (file/page/section/block) |
| Enterprise knowledge base (M2) | Capability cards + ~600-char chunking + BGE embeddings + Milvus vector store (auto SQLite fallback) |
| Requirement–Capability matching (M3) | Normalize → retrieve → rule/LLM judge → evidence verification → conflict arbitration → response table |
| Proposal generation (M4) | 26-section outline planning, requirement mapping, 4 generation strategies, SSE progress, task state machine, manual editing |
| Quality & consistency engine (M5) | Fact / completeness / consistency / format checks + 5-dimension scoring + finalization gate |
| Workbench UI (M6) | Vue 3 + Vite + Element Plus, 8 pages covering the full lifecycle with aggregated dashboards |
| Enterprise capabilities (M7) | JWT auth, RBAC, audit logging, knowledge-base versioning, task center, Agent-trace monitoring, offline evaluation suite |

**Stack:** Python 3.10+ · FastAPI · SQLite (WAL) · Milvus / SQLite vector store · OpenAI-compatible LLM client (DeepSeek) · Vue 3 · Vite · Element Plus · pytest (offline, FakeLLM/FakeEmbedding by default)

> 完整中文文档（含架构说明、API 一览、已知限制）见下文各章节。

---

> 项目时间线：2024.09 – 2025.05 ｜ 定位：企业级 AI 内容生产 / 文档智能处理系统

五大 AI 项目矩阵中的「内容生产」一环：

| 项目 | 角色 |
|---|---|
| 企业法律知识助手 | 知识获取 |
| AI 招聘助手 | 流程执行 |
| **企业标书生成平台** | **内容生产** |
| 深度搜索系统 | 复杂分析 / Agent |
| AI 应用平台 | 平台化 |

## 一、解决什么问题

软件公司收到 200–500 页招标文件（PDF/Word/Excel/扫描件/附件）后，售前、方案、技术人员制作标书时面对四个真实痛点：

1. **招标文件太长** —— "甲方到底要求我们提供什么？"
2. **企业资料分散** —— 产品部、项目部、商务部、技术部、共享文件夹各管一摊，复用难
3. **每次重复写** —— 同类项目（智慧园区 A → 智慧园区 B）的公司介绍、系统架构、实施方案高度相似却要重新整理
4. **不能写错** —— 招标要求项目经理 5 年经验，AI 生成"8 年"而候选人实际只有 3 年 = 直接失分

因此本项目天然需要：**知识检索 + 事实约束 + 引用 + 审核**。AI 不是最终决策者，而是辅助企业人员完成标书制作，最终责任在人。

## 二、核心设计：这不是"又一个 RAG"

RAG 在这里只是检索能力，不是项目本身。核心命题是双向结构化：

```
招标文件 ──解析/提取──▶ 需求实体 (Requirement)      "我要满足什么？"
企业资料 ──入库/向量化─▶ 能力实体 (Capability)      "我能提供什么？"
匹配      = 需求 × 能力的【关系】(MatchResult)
生成      = 带引用的【产物】(SectionDraft)          ← 证据注入，禁止编造
```

**六大实体**：Tender/Document（原文锚点）、Requirement（需求）、Capability（能力）、MatchResult（匹配）、Outline（标书模板）、SectionDraft（章节稿）。

**事实约束铁律**：生成只允许使用检索证据中出现的数字/资质/年限；证据中没有的量化指标一律输出 `【待确认】`。每条需求四元溯源（文件/页码/章节路径/块号），M3 生成时每个关键数字都能回溯原文。

**评分标准表不走 LLM**：表格交给规则引擎解析（LLM 读表不可靠，规则更准）。

### 系统架构图

```mermaid
flowchart TB
    subgraph INPUT["输入层"]
        T["招标文件<br/>PDF / Word / Excel / 扫描件"]
        K["企业资料<br/>产品/案例/资质/方案…"]
    end

    subgraph PARSING["M1 · 解析与需求提取"]
        P1["四类解析器<br/>ParsedDocument 统一产物"]
        P2["需求提取管线<br/>六类需求实体 + 四元溯源"]
    end

    subgraph KB["M2 · 企业知识库"]
        K1["能力卡提取<br/>Capability"]
        K2["切块嵌入 ~600字<br/>BGE / FakeEmbedding"]
        K3["向量存储<br/>Milvus ⇄ SQLite 降级"]
    end

    subgraph MATCH["M3 · 需求-能力匹配"]
        M1["归一化 / 分类 / 约束提取"]
        M2["检索 + 规则引擎 + LLM Judge"]
        M3["证据验证 + 冲突仲裁<br/>四状态 MatchResult"]
    end

    subgraph GEN["M4 · 标书生成引擎"]
        G1["大纲规划 26 章节"]
        G2["需求→章节映射"]
        G3["四策略生成器<br/>证据白名单 · 【待确认】约束"]
        G4["组装 docx / markdown"]
    end

    subgraph QA["M5 · 质量检查引擎"]
        Q1["事实 / 完整性 / 一致性 / 格式"]
        Q2["5 维评分 + 终版闭环门禁"]
    end

    subgraph M7["M7 · 企业级能力"]
        E1["JWT 认证 + RBAC + 审计"]
        E2["任务中心 + Agent 链路监控"]
        E3["离线评估体系"]
    end

    T --> P1 --> P2
    K --> K1 --> K2 --> K3
    P2 --> M1
    K3 --> M2
    M1 --> M2 --> M3
    M3 --> G1 --> G2 --> G3 --> G4
    G4 --> Q1 --> Q2
    P2 -. 需求基线 .-> M3
    E1 -. 全链路鉴权 .-> MATCH & GEN & QA
    E2 -. 任务状态 .-> PARSING & KB & MATCH & GEN


## 三、里程碑路线图

| # | 里程碑 | 时间 | 状态 |
|---|---|---|---|
| M1 | 招标文件解析 + 需求提取 | 2024.09–10 | ✅ 完成 |
| M2 | 企业知识库（能力卡片 + RAG 索引） | 2024.10–11 | ✅ 完成 |
| M3 | 需求-能力匹配（四状态 + 证据链 + 需求响应表） | 2024.11–12 | ✅ 完成 |
| M4 | 标书生成引擎（大纲/映射/生成器/响应表/组装/任务状态机） | 2025.01–02 | ✅ 完成 |
| M5 | 标书一致性与质量检查引擎（事实/完整性/一致性/格式检查 + 5 维评分 + 终版闭环） | 2025.03–04 | ✅ 完成 |
| M6 | 标书工作台（项目/招标/需求/知识库/生成/质检/交付 8 页面 + 全流程聚合 + SSE 实时进度） | 2025.04 | ✅ 完成 |
| M7 | 企业级能力（认证/RBAC/审计/知识库版本/任务中心/Agent 链路监控/评估体系） | 2025.04–05 | ✅ 完成 |

详见 [ROADMAP.md](ROADMAP.md)。Agent 不是本项目的起点——第四阶段才考虑。

## 四、目录结构

```
├── backend/
│   ├── app/
│   │   ├── schemas.py        # 六大实体 Pydantic 模型（数据模型是项目的分水岭）
│   │   ├── config.py         # 路径与 env 配置
│   │   ├── db.py             # SQLite 存储层入口（连接 + 基础 CRUD，Mixin 聚合映射层）
│   │   ├── db_mappers.py     # ORM 映射层（行 ↔ 模型双向转换，Database 继承 MappersMixin）
│   │   ├── db_schema.py      # Schema 层（DDL / RBAC 权限矩阵 / seed_rbac / get_db）
│   │   ├── api/              # FastAPI（main + routes_tenders + routes_knowledge）
│   │   ├── parsers/          # PDF/Word/Excel/OCR 四类解析器，统一 ParsedDocument 产物
│   │   └── services/         # llm + extraction（M1 提取）
│   │                         # embedding（BGE/FakeEmbedding）+ vector_store（Milvus/SQLite 降级）
│   │                         # kb_chunking（~600 字切块）+ capability_extractor（能力卡 + 后台任务）
│   │                         # matching/（M3 需求-能力匹配）+ generation/（M4 标书生成引擎）
│   │                         # quality/（M5 一致性/完整性/事实/格式检查 + 评分 + 终版闭环）
│   │                         # auth/（M7 认证/RBAC/审计）+ task_tracker + trace（M7 任务中心 + Agent 链路）
│   │                         # evaluation/（M7 检索/生成/趋势评估）
│   └── data/
│       ├── samples/          # 样例招标文件包 + 样例企业资料包（入库，测试确定性来源）
│       ├── raw/ parsed/      # 上传原文与解析产物（gitignored）
├── scripts/
│   ├── make_sample_tender.py    # M1 样例生成器（LLM 模式 / --no-llm 离线模式，可续跑）
│   ├── verify_m1_extraction.py  # M1 验收核查（预埋基线对照报告）
│   ├── make_sample_kb.py        # M2 样例企业资料包生成器（8 类，--no-llm 事实精确版）
│   ├── verify_m2_knowledge.py   # M2 验收核查（上传→处理→检索基线→能力卡核对）
│   ├── verify_m3_matching.py    # M3 需求-能力匹配验收核查
│   ├── verify_m4_generation.py  # M4 标书生成引擎验收核查（章节/覆盖/可追溯/文件）
│   ├── verify_m5_quality.py     # M5 质量检查引擎验收核查（基线 + 9 组变异 + 终版闭环）
│   ├── verify_m6_workbench.py   # M6 工作台验收核查（聚合字段/六阶段/SSE/前端文件）
│   ├── verify_m7_enterprise.py  # M7 企业级能力验收核查（认证/RBAC/审计/版本/任务/链路/评估）
│   └── probe_milvus.py          # Milvus 兼容性探针（临时集合 spike，不碰既有集合）
├── tests/                    # M1-M7 离线用例 + llm/milvus 标记集成用例
├── frontend/                 # Vue3 + Vite + Element Plus（M6 工作台：8 页面 + 5 组件 + 聚合 + SSE）
└── docs/whitepaper/          # 技术白皮书（架构与设计决策详解）
```

## 五、快速开始

### 方式 A：Docker 一键启动（推荐，30 秒）

```bash
# 前端 http://localhost:8080 · 后端 API http://localhost:8001/docs
docker compose up --build
# 或 Windows PowerShell：.\scripts\demo.ps1   Linux/macOS：./scripts/demo.sh
```

镜像内置轻量运行依赖（fake 嵌入 + SQLite 降级，无需 torch/Milvus 即可跑通全流程）；
数据卷持久化上传内容，样例包由 entrypoint 首次启动自动补齐。

### 方式 B：本机源码运行

```bash
# 1. 环境（依赖已在 requirements.txt 钉版；Windows 中文控制台）
#    python -m pip install -r requirements.txt
cp .env.example .env   # 填入真实 DeepSeek Key

# 2. 生成样例招标文件包（含 4 类格式 + 14 条预埋需求基线）
cd backend && python ../scripts/make_sample_tender.py
# 离线模式（不调 API）：python ../scripts/make_sample_tender.py --no-llm

# 3. 生成样例企业资料包（8 类；--no-llm 为事实精确版，推荐提交/测试用）
python scripts/make_sample_kb.py --no-llm

# 4. 单元测试（离线，FakeLLM + FakeEmbedding）
pytest tests/ -m "not llm and not milvus" -v
# 真实 LLM 集成测试（需 Key，对照预埋基线抽查召回与数字保真）
pytest tests/test_llm_integration.py -m llm -v
pytest tests/test_m4_integration.py -m llm -v   # M4 真实 LLM 方案型：证据白名单 + 数字溯源
# 真实 Milvus 回环测试（需 docker start milvus-etcd milvus-minio milvus-standalone）
pytest tests/test_vector_store.py -m milvus -v

# 5. 启动服务（8001 端口，8000 被法律助手占用）
cd backend && python -m uvicorn app.api.main:app --port 8001
# Swagger UI: http://localhost:8001/docs

# 6. M1 端到端
curl -F "files=@data/samples/智慧园区项目/01_招标文件正文.docx" http://localhost:8001/api/tenders
curl -X POST http://localhost:8001/api/tenders/{id}/extract      # 后台提取，轮询状态
curl "http://localhost:8001/api/tenders/{id}/requirements?importance=高"

# 7. M2 端到端（上传样例资料包 → 处理 → 检索；验收报告 scripts/_m2_verify_report.txt）
python scripts/verify_m2_knowledge.py          # 上传 + 处理 + 检索基线 + 能力卡核对
python scripts/verify_m2_knowledge.py --skip-ingest   # 复用已入库数据只跑核查
python scripts/verify_m2_knowledge.py --reprocess     # Milvus 恢复后重写索引再核查

# 8. M3 匹配 + M4 生成端到端（同一 T-M3 验收基线；报告 scripts/_m3_verify_report.txt / _m4_verify_report.txt）
python scripts/verify_m3_matching.py           # 需求-能力匹配（四状态 + 证据链 + 响应表）
python scripts/verify_m4_generation.py         # 标书生成引擎（章节/覆盖/可追溯/文件）

# 9. M5 质量检查端到端（同一 T-M3 验收基线；报告 scripts/_m5_verify_report.txt）
python scripts/verify_m5_quality.py            # 质量检查引擎（基线 + 9 组变异 + 终版闭环）

# 10. M6 工作台端到端（同一 T-M3 验收基线；报告 scripts/_m6_verify_report.txt）
python scripts/verify_m6_workbench.py          # 工作台聚合 + 六阶段派生 + SSE + 前端文件

# 11. M7 企业级能力端到端（需服务运行中；报告 scripts/_m7_verify_report.txt）
python scripts/verify_m7_enterprise.py         # 认证/RBAC/审计/知识库版本/任务中心/Agent 链路/评估
```

M7 演示账号（`AUTH_ENABLED=true` 默认启用）：`admin/admin123`（管理员）、`manager/manager123`（投标经理）、`editor/editor123`（标书编辑）、`reviewer/reviewer123`（审核员）、`staff/staff123`（普通员工）。M7 环境变量：`JWT_SECRET`（HS256 签名密钥，生产必须覆盖默认值）、`JWT_EXPIRE_HOURS`、`ADMIN_USERNAME`/`ADMIN_PASSWORD`（管理员口令）、`AUTH_ENABLED`（false 时系统用户降级，无鉴权部署/离线验收用）。

## 六、API 一览（M1 + M2 + M3 + M4 + M5 + M6 + M7）

| 端点 | 说明 |
|---|---|
| `POST /api/tenders` | 多文件上传 → 解析 → 入库（扩展名白名单 + 50MB 上限 + uuid 落盘） |
| `GET /api/tenders` / `GET /api/tenders/{id}` | 列表 / 详情（章节树 + 解析统计） |
| `POST /api/tenders/{id}/extract` | 后台任务启动需求提取（状态轮询） |
| `GET /api/tenders/{id}/requirements` | 需求列表（type/importance/status/is_star 过滤） |
| `PATCH /api/tenders/{id}/requirements/{rid}` | 人工修订（置 human_confirmed） |
| `GET /api/tenders/{id}/score-points` | 规则解析的评分点列表 |
| `POST /api/knowledge/materials` | 企业资料上传（8 类 category 枚举，多文件，单文件失败不阻塞） |
| `GET /api/knowledge/materials[/{id}]` | 列表（category/status 过滤）/ 详情（章节树） |
| `GET /api/knowledge/materials/{id}/chunks` | 内容块分页（不含 embedding；~600 字块 + 四元溯源元数据） |
| `POST /api/knowledge/materials/{id}/process` | 后台处理：切块 → 嵌入 + 向量写入 → 能力卡提取（历史标书跳过卡片） |
| `GET /api/knowledge/materials/{id}/capabilities` / `GET /api/knowledge/capabilities` | 资料/全局能力卡（category/source_doc 过滤） |
| `PATCH /api/knowledge/capabilities/{cap_id}` | 能力卡人工修订（attributes 整体替换） |
| `DELETE /api/knowledge/materials/{id}` | 级联删除（chunks/卡片/Milvus/落盘文件） |
| `GET /api/knowledge/search` | 语义检索（q/category/material_id/top_k；engine 字段标识 milvus/sqlite 降级路径） |

M3（需求-能力匹配，`/api/matching`）：

| 端点 | 说明 |
|---|---|
| `POST /tenders/{id}/match` | 后台匹配任务（归一化→检索→规则/LLM 判定→证据验证→冲突仲裁） |
| `GET /tenders/{id}` | 匹配状态（已完成/失败 + progress） |
| `GET /tenders/{id}/requirements` / `matches` | 规范需求列表 / 匹配结果（四状态 + method + evidence_ids） |
| `GET /tenders/{id}/matches/{mid}` | 单条证据链（REQ-C → MAT → EVD → CAP/块 → DOC → 页码 → 原文） |
| `GET /tenders/{id}/response-table` | 需求响应表（?format=json\|markdown） |

M4（标书生成引擎，`/api/generation`）：

| 端点 | 说明 |
|---|---|
| `POST /tenders/{id}/outline` | 默认大纲规划 → 实例化 26 章节树 + 需求→章节映射落库（M4-01/02） |
| `GET /tenders/{id}/outline` | 章节树（含 status/requirement 声明） |
| `GET /tenders/{id}/coverage` | 需求覆盖统计（total/mapped/unmapped/by_section） |
| `GET /tenders/{id}/sections/{sid}` | 章节草稿明细（paragraphs/coverage/evidence_refs/warnings/content_md） |
| `PATCH /tenders/{id}/sections/{sid}` | 人工编辑（content_md → 草稿→已编辑） |
| `POST /tenders/{id}/sections/{sid}/regenerate` | 单章节重新生成（version+1，走 job） |
| `POST /tenders/{id}/jobs` | 后台生成任务（?section_id= 单章节重生成；409 防并发） |
| `GET /tenders/{id}/jobs[/{job_id}]` | 任务状态 + 章节级进度（section_states） |
| `GET /tenders/{id}/response-table` | 三列响应表（招标要求\|企业响应\|证据，json/markdown） |
| `GET /tenders/{id}/document` | 完整标书组装（?format=markdown\|docx，docx 走 FileResponse） |
| `GET /tenders/{id}/logs` | 生成日志（SSE 读源） |

M5（质量检查引擎，`/api/quality`）：

| 端点 | 说明 |
|---|---|
| `POST /tenders/{id}/check?include_llm=` | 同步质量检查（事实/完整性/一致性/格式 + 可选 LLM 语义覆盖），报告落库；无已生成章节→409 |
| `GET /tenders/{id}/reports` / `GET /reports/{report_id}` | 报告列表（最新在前）/ 详情（含 issues） |
| `GET /tenders/{id}/issues?status=` | 问题列表（severity/issue_type/status 过滤） |
| `PATCH /issues/{issue_id}` | 人工处理（已确认/已忽略/已修复 → review_records 审计留痕） |
| `POST /issues/{issue_id}/autofix` | 仅格式类自动修复（行尾空白/空行/标题空格/表行管道），修复后重查 |
| `POST /tenders/{id}/finalize` | 终版闭环：CRITICAL/ERROR 未清→409；通过 → final.docx/final.md/quality-report.json + 审计 |
| `GET /tenders/{id}/final?format=json\|docx\|markdown` | 终版产物读取 |

M6（标书工作台，`/api/workbench` + 生成 SSE）：

| 端点 | 说明 |
|---|---|
| `GET /api/workbench/projects` | 项目列表 + 全流程状态聚合（文档统计/匹配分布/章节进度/质量快照/交付标记 + 六阶段状态派生 + KB 全局统计），只读 SQL 聚合不落库 |
| `GET /api/workbench/projects/{id}` | 单项目概览（+ 文档明细 + 待处理问题前 5 条按严重度排序；未知项目 404） |
| `GET /api/generation/tenders/{id}/jobs/{job_id}/events` | SSE 流式生成进度（tail generation_logs；job 终态推 `event: done` 关闭流；未知 job/tender 404） |

M7（企业级能力，`/api/auth` + `/api/admin` + `/api/projects` + `/api/knowledge/versions` + `/api/tasks` + `/api/eval`）：

| 端点 | 说明 |
|---|---|
| `POST /api/auth/login` / `POST /api/auth/logout` / `GET /api/auth/me` | 登录（返回 token + roles + permissions）/ 登出 / 当前用户 |
| `GET /api/admin/users` | 用户列表（仅 admin；seed_rbac 幂等种子 5 演示账号） |
| `GET /api/admin/audit-logs?action=&resource_type=&actor=` | 操作审计全量留痕 + 过滤（仅 admin） |
| `GET /api/admin/traces?task_type=&target_id=` / `GET /api/admin/llm-calls` | Agent 链路 trace/span 两级 + LLM 调用指标（仅 admin） |
| `POST /api/projects/{id}/members` / `GET ...` / `DELETE ...` | 项目成员增删查（`final:*` 权限三段判定额外要求成员身份；重复添加 409） |
| `GET /api/knowledge/versions` | 知识库版本行（KV-xxxx + `{日期}-v{n}` label；资料重处理/能力卡修订自动升版） |
| `GET /api/tasks` / `GET /api/tasks/{id}` / `POST /api/tasks/{id}/cancel` | 任务中心（5 类任务统一登记；非 admin 只见自己任务；cancel 仅 pending：本人→cancelled/他人 403/running 409/终态 409/不存在 404） |
| `GET /api/eval/retrieval` / `GET /api/eval/generation` / `GET /api/eval/trends` / `GET /api/eval/summary` | 评估体系（Recall@K/MRR、生成 4 指标、质量趋势、三合一 summary；均带 disclaimer） |

## 七、重构亮点（个人重写过程中的设计取舍）

本项目基于作者在企业投标业务中的真实经历**独立重写**（代码、架构、测试全部个人实现）。重写时相比最初版本的主要改进：

| 改进点 | 原设计的痛点 | 本项目的做法 |
|---|---|---|
| **确定性优先于 LLM** | 生成结果靠"看效果"把关 | 规则引擎解析评分表、四状态匹配、确定性校验器兜底——LLM 只负责语言组织，判断与校验全部可复现、可测试 |
| **证据即产品** | 引用是事后补的装饰 | 每条需求四元溯源（文件/页码/章节路径/块号），生成只允许消费证据池内容，`【待确认】`原位标注未证实数字 |
| **降级不撒谎** | 组件挂了要么报错要么静默 | Milvus → SQLite 暴力余弦、BGE → FakeEmbedding、LLM → Mock，全部透明标识 engine/backend，离线可完整演示 |
| **存储层分层** | 单文件 1100+ 行难以维护 | 拆为 `db.py`（连接/CRUD）+ `db_mappers.py`（ORM 映射 Mixin）+ `db_schema.py`（DDL/种子），对外 API 不变 |
| **部署即演示** | 演示依赖本机 Python/Node 环境 | Docker 多阶段构建 + compose 三容器（后端/前端 nginx/init），`docker compose up` 30 秒可跑通全流程 |
| **测试隔离** | 测试碰真实 LLM/向量库，慢且不确定 | pytest 默认离线（FakeLLM/FakeEmbedding），llm/milvus 标记显式开启；样例数据入库保证确定性 |

## 八、已知限制（如实记录）

- docx 无页码信息（Word 页面属于渲染层），docx 来源需求/能力卡的出处锚点以**章节路径 + 块号**为准；PDF 才锚定页码（docx 能力卡 source_page 恒为空，不采信 LLM 臆测页码）
- 扫描件 OCR 依赖 PaddleOCR（可选安装），未安装时管线优雅降级为"检测标记 + 待 OCR"
- 能力卡以 `source_doc`（文件名）关联资料：同类别重传同名文件会清掉旧卡（capabilities 表无 material_id，M1 定版）——重传请先删除旧资料
- Milvus 挂/未启动时检索自动降级 SQLite 暴力余弦（engine 字段透明标识），嵌入失败仅标记 index_status=degraded、不整任务失败；Milvus 恢复后重跑 process 即重建索引
- LLM 提取质量与预埋基线对照见 `backend/data/samples/智慧园区项目/样例说明.md`（M1）与 `backend/data/samples/企业资料包/样例说明.md`（M2）
- M4 生成（事实约束铁律）：方案型章节 LLM 输出之上有确定性校验器兜底（证据编号白名单 / 无证据不声称 / 数字溯源标【待确认】）；无 LLM_API_KEY 时方案型自动回退事实模板（离线验收仍全绿，方案章节为能力卡事实型内容，不走真实生成）
- DOCX 目录为静态目录（python-docx 无原生可更新 TOC 域），页码占位；中文字体已设 eastAsia 宋体防乱码
- M5 检查（事实/完整性/一致性/格式）：事实区排除需求回显章节（CH-08 响应表、CH-05-4 技术指标表实时回显需求原文，不参与数字/冲突判定）；语义覆盖审查默认关闭（include_llm=true 才跑，需配置 .env 的 LLM_API_KEY，FakeLLM/无 Key 空返回不新增 issue）
- M5 终版跳过 PDF（final.docx + final.md + quality-report.json 三件套）；quality report 以结构化 JSON 落盘，Markdown 报告另出；PDF 为已知限制，留待后续阶段
- M6 工作台聚合为只读派生（SQL 聚合 + 六阶段状态由数据派生，不落库）；生成 SSE 断连可回退轮询 job 状态端点；质量工作台"定位章节"按钮跳转生成工作台（不解析章节号精确定位），为有意简化
- M7 认证：JWT_SECRET 默认值仅本机开发用，生产必须覆盖；`AUTH_ENABLED=false` 时系统用户降级（无鉴权部署，审计记 system 用户）
- M7 任务中心：running 任务不可取消（BackgroundTasks 不可杀 → 409 语义）；任务登记表为过程记录（清理验收数据不触碰用户业务数据）
- M7 监控：llm_calls 仅增强 LLM 客户端（配置真实 LLM_API_KEY）落库，MockLLM 不记录调用（离线口径恒 0）；trace/span 写库失败不影响业务本体（旁路）
- M7 评估：评估数字为 BidForge 内部离线评估集口径（T-M3 基线 + 样例文件），不代表通用准确率；每个评估响应均带 disclaimer

## 九、惯例与纪律

- 真实 API Key 只存在于 gitignored 的 `.env`；入库模板是 `.env.example`（占位 Key）
- commit 前必须复核 `.env` 不在暂存清单
- 推送 Gitee 由仓库所有者执行
