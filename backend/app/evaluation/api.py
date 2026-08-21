# -*- coding: utf-8 -*-
"""
app/evaluation/api.py —— M7-07 评估端点

- GET /api/eval/retrieval?k=10       RAG 检索评估（无 LLM，确定性）
- GET /api/eval/generation?tender_id= 生成评估（引用/事实/覆盖三指标）
- GET /api/eval/trends?tender_id=    质量趋势（序列 + delta）
- GET /api/eval/summary?tender_id=   三项汇总

权限：retrieval → knowledge:view；generation/trends/summary → project:view。
每个响应都带 disclaimer（口径声明铁律——本 API 不产出"通用准确率"）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from ..auth.deps import require_permission
from ..db import Database, get_db
from . import runner

router = APIRouter(prefix="/api/eval", tags=["评估体系"])


@router.get("/retrieval",
            dependencies=[Depends(require_permission("knowledge", "view"))])
def eval_retrieval(k: int = 10, db: Database = Depends(get_db)) -> dict:
    """RAG 检索评估：KB 8 条基线 + 需求→证据子集，Recall@K / MRR。"""
    return runner.run_retrieval(k=min(max(k, 1), 50))


@router.get("/generation",
            dependencies=[Depends(require_permission("project", "view"))])
def eval_generation(tender_id: str,
                    db: Database = Depends(get_db)) -> dict:
    """生成评估：引用完整率 / 引用准确率 / 事实一致率 / 需求覆盖率。"""
    tender = db.query_one("SELECT id FROM tenders WHERE id = ?", (tender_id,))
    if not tender:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="项目不存在")
    return runner.run_generation(db, tender_id)


@router.get("/trends",
            dependencies=[Depends(require_permission("project", "view"))])
def eval_trends(tender_id: str, db: Database = Depends(get_db)) -> dict:
    """质量趋势：报告序列 + 相邻 delta（score / issue_counts 逐类）。"""
    tender = db.query_one("SELECT id FROM tenders WHERE id = ?", (tender_id,))
    if not tender:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="项目不存在")
    return runner.run_trends(db, tender_id)


@router.get("/summary",
            dependencies=[Depends(require_permission("project", "view"))])
def eval_summary(tender_id: str = "", k: int = 10,
                 db: Database = Depends(get_db)) -> dict:
    """评估汇总（可只跑检索）。"""
    return runner.run_summary(db, tender_id=tender_id,
                              k=min(max(k, 1), 50))
