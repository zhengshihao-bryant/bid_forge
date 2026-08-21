# -*- coding: utf-8 -*-
"""
app/api/routes_matching.py —— 需求-能力匹配路由（M3）

端点一览：

    POST   /tenders/{tender_id}/match              启动后台匹配任务（202；状态轮询）
    GET    /tenders/{tender_id}                    匹配运行状态（matching_runs）
    GET    /tenders/{tender_id}/requirements       规范需求（REQ-C-XXXX）
    GET    /tenders/{tender_id}/matches            匹配记录（MAT-XXXX）
    GET    /tenders/{tender_id}/matches/{match_id} 单条匹配 + 证据链（M3-14）
    GET    /tenders/{tender_id}/response-table     需求响应表（?format=json|markdown）

口径：M3 只回答"招标方要求什么？我们有没有？证据是什么？"，不写标书（M4）。
"""
from __future__ import annotations

import json

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query

from .. import config
from ..auth.audit import record_audit
from ..auth.deps import get_current_user, require_permission
from ..db import Database
from ..services.matching.pipeline import run_matching_task
from ..services.matching.report import ResponseTableBuilder
from ..services.task_tracker import create_task

router = APIRouter(prefix="/api/matching", tags=["需求匹配"])

_DEFAULT_RUN = {"tender_id": "", "status": "未匹配", "progress": "",
                "canonical_count": 0, "match_count": 0, "updated_at": ""}


def _db() -> Database:
    return Database(config.DB_PATH)


def _get_tender_or_404(tender_id: str) -> dict:
    tender = _db().query_one("SELECT * FROM tenders WHERE id = ?", (tender_id,))
    if not tender:
        raise HTTPException(status_code=404, detail="招标项目不存在")
    return tender


def _run_dict(row: dict) -> dict:
    return {
        "tender_id": row["tender_id"], "status": row["status"],
        "progress": row["progress"],
        "canonical_count": row.get("canonical_count") or 0,
        "match_count": row.get("match_count") or 0,
        "updated_at": row.get("updated_at") or "",
    }


# ═══════════════════════════════════════════════════════════════════════
# 匹配任务
# ═══════════════════════════════════════════════════════════════════════
@router.post("/tenders/{tender_id}/match", status_code=202,
             dependencies=[Depends(require_permission("project", "edit"))])
def start_matching(tender_id: str, background_tasks: BackgroundTasks,
                   user: dict = Depends(get_current_user)) -> dict:
    """启动后台匹配：标准化 → 逐条匹配 → 判定 → 落库。

    状态轮询 GET /api/matching/tenders/{id}（status: 未匹配/匹配中/已完成/失败）。
    M7-05：任务中心登记（match；started_by=当前用户）。
    """
    _get_tender_or_404(tender_id)
    db = _db()
    row = db.query_one("SELECT * FROM matching_runs WHERE tender_id = ?",
                       (tender_id,))
    if row and row["status"] == "匹配中":
        raise HTTPException(status_code=409, detail="该招标项目正在匹配中")
    reqs = db.query_one(
        "SELECT COUNT(*) AS n FROM requirements WHERE tender_id = ?", (tender_id,))
    if not reqs["n"]:
        raise HTTPException(status_code=400, detail="该招标项目没有提取出的需求（请先跑 M1 提取）")
    task = create_task(db, "match", target_id=tender_id,
                       started_by=user["id"])
    background_tasks.add_task(run_matching_task, tender_id, task["id"],
                              user["id"])
    return {"tender_id": tender_id, "status": "匹配中",
            "task_id": task["id"],
            "hint": "轮询 GET /api/matching/tenders/{id} 查看 status / progress"}


@router.get("/tenders/{tender_id}",
            dependencies=[Depends(require_permission("project", "view"))])
def matching_status(tender_id: str) -> dict:
    """匹配运行状态（matching_runs 表；未跑过 → 未匹配）。"""
    _get_tender_or_404(tender_id)
    row = _db().query_one("SELECT * FROM matching_runs WHERE tender_id = ?",
                          (tender_id,))
    if not row:
        return {**_DEFAULT_RUN, "tender_id": tender_id}
    return _run_dict(row)


# ═══════════════════════════════════════════════════════════════════════
# 查询：规范需求 / 匹配记录 / 证据链
# ═══════════════════════════════════════════════════════════════════════
@router.get("/tenders/{tender_id}/requirements",
            dependencies=[Depends(require_permission("project", "view"))])
def list_canonical_requirements(tender_id: str, include_scoring: bool = Query(False)) -> dict:
    """规范需求列表（REQ-C-XXXX；默认不含评分细则）。"""
    _get_tender_or_404(tender_id)
    db = _db()
    rows = db.query(
        "SELECT * FROM canonical_requirements WHERE tender_id = ? ORDER BY id",
        (tender_id,))
    canonicals = [db.row_to_canonical(r) for r in rows]
    if not include_scoring:
        canonicals = [c for c in canonicals if not c.is_scoring]
    return {"tender_id": tender_id, "total": len(canonicals),
            "requirements": [c.model_dump(mode="json") for c in canonicals]}


@router.get("/tenders/{tender_id}/matches",
            dependencies=[Depends(require_permission("project", "view"))])
def list_matches(tender_id: str, status: str | None = Query(None)) -> dict:
    """匹配记录列表（MAT-XXXX；可按 FULL/PARTIAL/MISSING/UNKNOWN 过滤）。"""
    _get_tender_or_404(tender_id)
    db = _db()
    sql = "SELECT * FROM requirement_matches WHERE tender_id = ?"
    args: tuple = (tender_id,)
    if status:
        if status.upper() not in ("FULL", "PARTIAL", "MISSING", "UNKNOWN"):
            raise HTTPException(
                status_code=422,
                detail="status 必须是 FULL/PARTIAL/MISSING/UNKNOWN")
        sql += " AND status = ?"
        args = (tender_id, status.upper())
    sql += " ORDER BY id"
    matches = [db.row_to_match(r) for r in db.query(sql, args)]
    counts = {"FULL": 0, "PARTIAL": 0, "MISSING": 0, "UNKNOWN": 0}
    for m in matches:
        counts[m.status.value] += 1
    return {"tender_id": tender_id, "total": len(matches), "counts": counts,
            "matches": [m.model_dump(mode="json") for m in matches]}


@router.get("/tenders/{tender_id}/matches/{match_id}",
            dependencies=[Depends(require_permission("project", "view"))])
def match_detail(tender_id: str, match_id: str,
                 user: dict = Depends(get_current_user)) -> dict:
    """单条匹配详情：判定 + 冲突 + 证据明细 + 证据链（M3-14 可追溯）。"""
    _get_tender_or_404(tender_id)
    db = _db()
    row = db.query_one(
        "SELECT * FROM requirement_matches WHERE tender_id = ? AND id = ?",
        (tender_id, match_id))
    if not row:
        raise HTTPException(status_code=404, detail="匹配记录不存在")
    record_audit(db, user, "view_evidence", "match", match_id,
                 detail=f"tender_id={tender_id}")
    match = db.row_to_match(row)
    evidences = [db.row_to_evidence(r) for r in db.query(
        "SELECT * FROM evidences WHERE requirement_id = ? ORDER BY id",
        (match.requirement_id,))]
    doc_names = {r["id"]: r["file_name"] for r in db.query(
        "SELECT id, file_name FROM kb_materials", ())}
    builder = ResponseTableBuilder(db)
    return {
        "match": match.model_dump(mode="json"),
        "evidences": [builder._evidence_dict(e, doc_names) for e in evidences],
        "trace": [t.model_dump(mode="json")
                  for t in builder.trace_chain(match, evidences, doc_names)],
    }


# ═══════════════════════════════════════════════════════════════════════
# 需求响应表（M3-15）
# ═══════════════════════════════════════════════════════════════════════
@router.get("/tenders/{tender_id}/response-table",
            dependencies=[Depends(require_permission("project", "view"))])
def response_table(tender_id: str, format: str = Query("json")) -> dict:
    """需求响应表：format=json（默认，结构化）或 format=markdown（文档形态）。"""
    _get_tender_or_404(tender_id)
    builder = ResponseTableBuilder()
    if format == "markdown":
        return {"tender_id": tender_id, "format": "markdown",
                "content": builder.to_markdown(tender_id)}
    if format != "json":
        raise HTTPException(status_code=422, detail="format 必须是 json 或 markdown")
    payload = json.loads(builder.to_json(tender_id))
    return {"tender_id": tender_id, "format": "json", "data": payload}
