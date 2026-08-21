# -*- coding: utf-8 -*-
"""
routes_tasks.py —— M7-05 任务中心

- GET  /api/tasks：统一任务列表（type/status 过滤；admin 全量，他人只见自己启动的）
- POST /api/tasks/{task_id}/cancel：取消任务（仅 pending；running 409——
  BackgroundTasks 无取消能力，进程级任务队列列入 ROADMAP 演进方向）

只记录 M7 起的新任务，不 union 回填历史（matching_runs 单行无历史等，
文档声明）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ..auth.audit import record_audit
from ..auth.deps import get_current_user
from ..db import Database, get_db
from ..services.task_tracker import cancel_task

router = APIRouter(prefix="/api/tasks", tags=["任务中心"],
                   dependencies=[Depends(get_current_user)])


@router.get("")
def list_tasks(task_type: str = "", status: str = "",
               limit: int = 100, user: dict = Depends(get_current_user),
               db: Database = Depends(get_db)) -> dict:
    where, params = [], []
    if task_type:
        where.append("task_type = ?")
        params.append(task_type)
    if status:
        where.append("status = ?")
        params.append(status)
    if "admin" not in user.get("roles", []):
        where.append("started_by = ?")
        params.append(user["id"])
    cond = (" WHERE " + " AND ".join(where)) if where else ""
    rows = db.query(
        f"SELECT * FROM tasks{cond} ORDER BY created_at DESC LIMIT ?",
        (*params, min(max(limit, 1), 500)))
    return {"tasks": [dict(r) for r in rows]}


@router.post("/{task_id}/cancel")
def cancel(task_id: str, user: dict = Depends(get_current_user),
           db: Database = Depends(get_db)) -> dict:
    row = db.query_one("SELECT * FROM tasks WHERE id = ?", (task_id,))
    if not row:
        raise HTTPException(status_code=404, detail="任务不存在")
    if "admin" not in user.get("roles", []) and row["started_by"] != user["id"]:
        raise HTTPException(status_code=403, detail="只能取消自己启动的任务")
    if row["status"] != "pending":
        raise HTTPException(
            status_code=409,
            detail="运行中任务无法取消（当前架构限制，仅待执行任务可取消）")
    cancel_task(db, task_id)
    record_audit(db, user, "task_cancel", "task", task_id,
                 detail=f"type={row['task_type']}")
    return {"task_id": task_id, "status": "cancelled"}
