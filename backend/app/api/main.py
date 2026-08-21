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

交互式文档（Swagger UI）：http://localhost:8001/docs
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .. import config
from ..db import Database
from .routes_tenders import router as tenders_router

API_VERSION = "0.1.0"
SERVICE_NAME = "企业标书生成平台 (Bid Generation Platform)"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("bidgen.api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动时建表（幂等）。"""
    Database(config.DB_PATH).init_schema()
    logger.info("%s v%s 启动，数据库: %s", SERVICE_NAME, API_VERSION, config.DB_PATH)
    yield


app = FastAPI(
    title="企业标书生成平台 API",
    description=(
        "企业级标书 AI 辅助生成平台 HTTP 服务（M1：招标文件解析 + 需求提取）。\n\n"
        "- `POST /api/tenders`：多文件上传（PDF/Word/Excel/图片）→ 解析 → 入库\n"
        "- `POST /api/tenders/{id}/extract`：后台需求提取（LLM，状态轮询）\n"
        "- 需求条目四元溯源（文件/页码/章节路径/块号），人工可修订\n"
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

app.include_router(tenders_router)


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
        "description": "企业级标书 AI 辅助生成平台（M1：招标文件解析 + 需求提取）",
        "endpoints": {
            "POST /api/tenders": "上传招标文件（PDF/Word/Excel/图片）→ 解析入库",
            "GET /api/tenders": "招标项目列表",
            "POST /api/tenders/{id}/extract": "启动需求提取（后台任务）",
            "GET /api/tenders/{id}/requirements": "需求列表（四元溯源）",
        },
        "docs": "/docs",
    }


@app.get("/health", tags=["服务"])
def health() -> dict:
    """健康检查（秒回，不跑重活）。"""
    db_ok = config.DB_PATH.exists()
    return {
        "status": "ok" if db_ok else "degraded",
        "service": SERVICE_NAME,
        "version": API_VERSION,
        "db": str(config.DB_PATH),
        "llm": "configured" if config.LLM_API_KEY else "mock",
    }


# M4 起前端构建产物由 FastAPI 托管（dist 不存在时不挂载）
_frontend_dist = config.REPO_ROOT / "frontend" / "dist"
if _frontend_dist.exists():
    app.mount("/", StaticFiles(directory=str(_frontend_dist), html=True), name="frontend")
