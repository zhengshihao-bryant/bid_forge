# 企业标书生成平台（Bid Generation Platform）

> 项目时间线：2024.09 – 2025.04 ｜ 定位：企业级 AI 内容生产 / 文档智能处理系统

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

## 三、里程碑路线图

| # | 里程碑 | 时间 | 状态 |
|---|---|---|---|
| M1 | 招标文件解析 + 需求提取 | 2024.09–10 | ✅ 完成 |
| M2 | 企业知识库（能力卡片 + RAG 索引） | 2024.10–11 | 未开始 |
| M3 | 需求-能力匹配 + 章节生成（证据注入 + 需求响应表） | 2024.11–12 | 未开始 |
| M4 | Word 导出 + Web 界面（Vue3/Vite） | 2025.01–02 | 未开始 |
| M5 | 一致性/完整性检查 + 测试集 + 白皮书 + 封版 v1.0.0 | 2025.03–04 | 未开始 |

详见 [ROADMAP.md](ROADMAP.md)。Agent 不是本项目的起点——第四阶段才考虑。

## 四、目录结构

```
├── backend/
│   ├── app/
│   │   ├── schemas.py        # 六大实体 Pydantic 模型（数据模型是项目的分水岭）
│   │   ├── config.py         # 路径与 env 配置
│   │   ├── db.py             # SQLite（每任务独立连接 + WAL，线程安全）
│   │   ├── api/              # FastAPI（main + routes_tenders）
│   │   ├── parsers/          # PDF/Word/Excel/OCR 四类解析器，统一 ParsedDocument 产物
│   │   └── services/         # llm（重试/Mock 降级）+ extraction（窗口切分→提取→校验→去重）
│   └── data/
│       ├── samples/          # 样例招标文件包（入库，测试确定性来源）
│       ├── raw/ parsed/      # 上传原文与解析产物（gitignored）
├── scripts/
│   ├── make_sample_tender.py    # 样例生成器（LLM 模式 / --no-llm 离线模式，可续跑）
│   └── verify_m1_extraction.py  # M1 验收核查（预埋基线对照报告）
├── tests/                    # 33 个离线用例 + llm 标记集成用例
├── frontend/                 # Vue3 + Vite + Element Plus（M4 完善；招标列表/详情页已可用）
└── docs/                     # 白皮书（M5）
```

## 五、快速开始

```bash
# 1. 环境（依赖已在 requirements.txt 钉版；Windows 中文控制台）
#    python -m pip install -r requirements.txt
cp .env.example .env   # 填入真实 DeepSeek Key

# 2. 生成样例招标文件包（含 4 类格式 + 14 条预埋需求基线）
cd backend && python ../scripts/make_sample_tender.py
# 离线模式（不调 API）：python ../scripts/make_sample_tender.py --no-llm

# 3. 单元测试（离线，FakeLLM）
pytest tests/ -m "not llm" -v
# 真实 LLM 集成测试（需 Key，对照预埋基线抽查召回与数字保真）
pytest tests/test_llm_integration.py -m llm -v

# 4. 启动服务（8001 端口，8000 被法律助手占用）
cd backend && python -m uvicorn app.api.main:app --port 8001
# Swagger UI: http://localhost:8001/docs

# 5. 端到端
curl -F "files=@data/samples/智慧园区项目/01_招标文件正文.docx" http://localhost:8001/api/tenders
curl -X POST http://localhost:8001/api/tenders/{id}/extract      # 后台提取，轮询状态
curl "http://localhost:8001/api/tenders/{id}/requirements?importance=高"
```

## 六、API 一览（M1）

| 端点 | 说明 |
|---|---|
| `POST /api/tenders` | 多文件上传 → 解析 → 入库（扩展名白名单 + 50MB 上限 + uuid 落盘） |
| `GET /api/tenders` / `GET /api/tenders/{id}` | 列表 / 详情（章节树 + 解析统计） |
| `POST /api/tenders/{id}/extract` | 后台任务启动需求提取（状态轮询） |
| `GET /api/tenders/{id}/requirements` | 需求列表（type/importance/status/is_star 过滤） |
| `PATCH /api/tenders/{id}/requirements/{rid}` | 人工修订（置 human_confirmed） |
| `GET /api/tenders/{id}/score-points` | 规则解析的评分点列表 |

## 七、已知限制（如实记录）

- docx 无页码信息（Word 页面属于渲染层），docx 来源需求的出处锚点以**章节路径 + 块号**为准；PDF 才锚定页码
- 扫描件 OCR 依赖 PaddleOCR（可选安装），未安装时管线优雅降级为"检测标记 + 待 OCR"
- LLM 提取质量与预埋基线对照见 `backend/data/samples/智慧园区项目/样例说明.md`（M1 验收标准）

## 八、惯例与纪律

- 真实 API Key 只存在于 gitignored 的 `.env`；入库模板是 `.env.example`（占位 Key）
- commit 前必须复核 `.env` 不在暂存清单
- 推送 Gitee 由仓库所有者执行
