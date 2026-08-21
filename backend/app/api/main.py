# -*- coding: utf-8 -*-
"""
app/api/main.py —— FastAPI 应用入口

启动（在 backend/ 目录下）：

    python -m uvicorn app.api.main:app --host 0.0.0.0 --port 8001

端点一览：

    GET  /                              服务说明
    GET  /health                        健康检查
    POST /api/tenders                   上传招标文件 → 解析入库
    GET  /api/tenders[/{id}]            列表 / 详情（章节树 + 解析统计）
    POST /api/tenders/{id}/extract      后台任务启动需求提取（状态轮询）
    GET  /api/tenders/{id}/requirements 需求列表（type/importance/status/is_star 过滤）
    PATCH /api/tenders/{id}/requirements/{rid}  人工修订（置 human_confirmed）
    GET  /api/tenders/{id}/score-points 评分点列表
    POST /api/knowledge/materials           企业资料上传（8 类）→ 解析入库
    GET  /api/knowledge/materials[/{id}]    列表 / 详情（章节树）
    GET  /api/knowledge/materials/{id}/chunks  内容块分页
    POST /api/knowledge/materials/{id}/process 后台处理：切块 + 嵌入 + 能力卡提取
    GET  /api/knowledge/capabilities        能力卡列表（人工修订 PATCH）
    GET  /api/knowledge/search              语义检索（Milvus 挂自动降级 SQLite）
    POST /api/matching/tenders/{id}/match   后台匹配（标准化 + 匹配 + 判定；状态轮询）
    GET  /api/matching/tenders/{id}/requirements  规范需求（REQ-C-XXXX）
    GET  /api/matching/tenders/{id}/matches      匹配记录（FULL/PARTIAL/MISSING/UNKNOWN）
    GET  /api/matching/tenders/{id}/response-table  需求响应表（json/markdown）
    POST /api/generation/tenders/{id}/outline    章节规划 + 需求→章节映射（落库）
    POST /api/generation/tenders/{id}/jobs       启动后台生成（状态轮询，409 防并发）
    GET  /api/generation/tenders/{id}/jobs/{job_id}/events  SSE 流式进度
    PATCH /api/generation/tenders/{id}/sections/{sid}  章节人工编辑（草稿→已编辑）
    POST /api/quality/tenders/{id}/check         质量检查（确定性 + 可选 LLM 语义覆盖）
    GET  /api/quality/tenders/{id}/reports       质量报告列表 / 详情
    PATCH /api/quality/issues/{issue_id}         问题人工处理（已确认/已忽略/已修复）
    POST /api/quality/tenders/{id}/finalize      终版闭环（final.docx + final.md + 报告）
    GET  /api/workbench/projects[/{id}]          工作台聚合（项目列表 / 单项目概览）

交互式文档（Swagger UI）：http://localhost:8001/docs
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .. import config
from ..auth.deps import get_current_user
from ..db import Database, seed_rbac
from ..services.vector_store import get_milvus_store
from .routes_admin import router as admin_router
from .routes_auth import router as auth_router
from .routes_generation import router as generation_router
from .routes_knowledge import router as knowledge_router
from .routes_matching import router as matching_router
from .routes_projects import router as projects_router
from .routes_quality import router as quality_router
from .routes_tasks import router as tasks_router
from .routes_tenders import router as tenders_router
from .routes_workbench import router as workbench_router
from ..evaluation.api import router as eval_router

API_VERSION = "0.1.0"
SERVICE_NAME = "企业标书生成平台 (Bid Generation Platform)"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("bidgen.api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动时建表（幂等）+ RBAC 种子（幂等）。"""
    db = Database(config.DB_PATH)
    db.init_schema()
    seed_rbac(db)
    logger.info("%s v%s 启动，数据库: %s", SERVICE_NAME, API_VERSION, config.DB_PATH)
    yield


app = FastAPI(
    title="企业标书生成平台 API",
    description=(
        "企业级标书 AI 辅助生成平台 HTTP 服务（M1：招标文件解析 + 需求提取；"
        "M2：企业知识库 + 语义检索；M3：需求-能力匹配；M4：标书生成引擎；"
        "M5：一致性与质量检查；M6：标书工作台）。\n\n"
        "- `POST /api/tenders`：多文件上传（PDF/Word/Excel/图片）→ 解析 → 入库\n"
        "- `POST /api/tenders/{id}/extract`：后台需求提取（LLM，状态轮询）\n"
        "- 需求条目四元溯源（文件/页码/章节路径/块号），人工可修订\n"
        "- `POST /api/knowledge/materials`：企业资料上传（8 类）→ 切块 + 能力卡\n"
        "- `GET /api/knowledge/search`：BGE 语义检索（Milvus 挂自动降级 SQLite）\n"
        "- `GET /api/workbench/projects`：工作台全流程聚合（六阶段状态派生 + KB 统计）\n"
        "- `GET /api/generation/tenders/{id}/jobs/{job_id}/events`：SSE 流式生成进度\n"
        "- 未配置 `LLM_API_KEY` 时自动使用 Mock LLM（可离线验证管线）"
    ),
    version=API_VERSION,
    lifespan=lifespan,
)

# 开发期放开跨域（前端 Vite dev server 5173）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# 认证路由公开注册（login 匿名）；其余业务路由整体要求登录（401 兜底），
# 细粒度权限用端点级 dependencies 挂 require_*（M7-02）
app.include_router(auth_router)
app.include_router(tenders_router, dependencies=[Depends(get_current_user)])
app.include_router(knowledge_router, dependencies=[Depends(get_current_user)])
app.include_router(matching_router, dependencies=[Depends(get_current_user)])
app.include_router(generation_router, dependencies=[Depends(get_current_user)])
app.include_router(quality_router, dependencies=[Depends(get_current_user)])
app.include_router(workbench_router, dependencies=[Depends(get_current_user)])
# M7 新模块（projects 端点自带 require_permission；tasks/eval 自带 get_current_user）
app.include_router(projects_router)
app.include_router(admin_router)
app.include_router(tasks_router)
app.include_router(eval_router)


# ═══════════════════════════════════════════════════════════════════════
# 服务路由（必须先于下方前端静态挂载注册：Starlette 按注册顺序匹配，
# Mount("/") 会拦截其后注册的一切路径，包括 / 与 /health）
# ═══════════════════════════════════════════════════════════════════════
@app.get("/", tags=["服务"])
def service_info() -> dict:
    """服务说明。"""
    return {
        "service": SERVICE_NAME,
        "version": API_VERSION,
        "description": "企业级标书 AI 辅助生成平台（M1–M6：解析→提取→匹配→生成→质检→工作台）",
        "endpoints": {
            "POST /api/tenders": "上传招标文件（PDF/Word/Excel/图片）→ 解析入库",
            "GET /api/tenders": "招标项目列表",
            "POST /api/tenders/{id}/extract": "启动需求提取（后台任务）",
            "GET /api/tenders/{id}/requirements": "需求列表（四元溯源）",
            "POST /api/knowledge/materials": "企业资料上传（8 类）→ 解析入库",
            "GET /api/knowledge/search": "语义检索（Milvus 挂自动降级 SQLite）",
            "GET /api/workbench/projects": "工作台聚合（六阶段状态 + KB 统计）",
            "GET /api/generation/tenders/{id}/jobs/{job_id}/events": "SSE 流式生成进度",
        },
        "docs": "/docs",
    }


@app.get("/health", tags=["服务"])
def health() -> dict:
    """健康检查（秒回，不跑重活；不构造嵌入器——BGE 懒加载约 21s）。"""
    db_ok = config.DB_PATH.exists()
    milvus = get_milvus_store()
    if milvus is None:
        milvus_status: dict = {"status": "degraded", "reason": "disabled"}
    else:
        info = milvus.info()
        milvus_status = ({"status": "ok", "version": info.get("version", "")}
                         if info.get("reachable")
                         else {"status": "degraded",
                               "reason": info.get("error", "unreachable")})
    return {
        "status": "ok" if db_ok else "degraded",
        "service": SERVICE_NAME,
        "version": API_VERSION,
        "db": str(config.DB_PATH),
        "llm": "configured" if config.LLM_API_KEY else "mock",
        "milvus": milvus_status,
    }


# M4 起前端构建产物由 FastAPI 托管（dist 不存在时不挂载）
_frontend_dist = config.REPO_ROOT / "frontend" / "dist"
if _frontend_dist.exists():
    app.mount("/", StaticFiles(directory=str(_frontend_dist), html=True), name="frontend")
