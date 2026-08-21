# -*- coding: utf-8 -*-
"""
routes_admin.py —— M7 管理端点（仅 admin）：审计日志 / LLM 调用 / Agent 链路 / 用户

- GET /api/admin/audit-logs：操作审计（user/action/resource 过滤 + 分页）
- GET /api/admin/llm-calls：LLM 调用指标 + 汇总
- GET /api/admin/traces：Agent 链路（trace + spans）
- GET /api/admin/users：用户列表（成员管理下拉用）
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from ..auth.deps import require_admin
from ..db import Database, get_db

router = APIRouter(prefix="/api/admin", tags=["管理"],
                   dependencies=[Depends(require_admin)])


@router.get("/audit-logs")
def list_audit_logs(user_id: str = "", action: str = "",
                    resource_type: str = "", limit: int = 50,
                    offset: int = 0, db: Database = Depends(get_db)) -> dict:
    """审计日志列表（时间倒序）。"""
    where, params = [], []
    if user_id:
        where.append("user_id = ?")
        params.append(user_id)
    if action:
        where.append("action = ?")
        params.append(action)
    if resource_type:
        where.append("resource_type = ?")
        params.append(resource_type)
    cond = (" WHERE " + " AND ".join(where)) if where else ""
    total = db.query_one(f"SELECT COUNT(*) AS n FROM audit_logs{cond}",
                         tuple(params))["n"]
    rows = db.query(
        f"SELECT * FROM audit_logs{cond} "
        f"ORDER BY id DESC LIMIT ? OFFSET ?",
        (*params, min(max(limit, 1), 200), max(offset, 0)))
    return {"total": total, "logs": [dict(r) for r in rows]}


@router.get("/llm-calls")
def list_llm_calls(model: str = "", success: str = "",
                   limit: int = 50, db: Database = Depends(get_db)) -> dict:
    """LLM 调用明细（时间倒序）+ 汇总（总次数/token/平均耗时/失败数）。"""
    where, params = [], []
    if model:
        where.append("model = ?")
        params.append(model)
    if success in ("0", "1"):
        where.append("success = ?")
        params.append(int(success))
    cond = (" WHERE " + " AND ".join(where)) if where else ""
    rows = db.query(
        f"SELECT * FROM llm_calls{cond} ORDER BY id DESC LIMIT ?",
        (*params, min(max(limit, 1), 200)))
    agg = db.query_one(
        f"SELECT COUNT(*) AS total_calls, COALESCE(SUM(total_tokens),0) AS total_tokens, "
        f"COALESCE(AVG(duration_ms),0) AS avg_duration_ms, "
        f"COALESCE(SUM(CASE WHEN success=0 THEN 1 ELSE 0 END),0) AS failed_count "
        f"FROM llm_calls{cond}", tuple(params))
    return {"total": len(rows), "calls": [dict(r) for r in rows],
            "summary": dict(agg)}


@router.get("/traces")
def list_traces(task_type: str = "", status: str = "",
                limit: int = 20, db: Database = Depends(get_db)) -> dict:
    """Agent 链路（trace + 各阶段 spans）。"""
    where, params = [], []
    if task_type:
        where.append("task_type = ?")
        params.append(task_type)
    if status:
        where.append("status = ?")
        params.append(status)
    cond = (" WHERE " + " AND ".join(where)) if where else ""
    traces = db.query(
        f"SELECT * FROM agent_traces{cond} "
        f"ORDER BY started_at DESC, id DESC LIMIT ?",
        (*params, min(max(limit, 1), 100)))
    result = []
    for t in traces:
        spans = db.query(
            "SELECT stage, status, detail, started_at, finished_at "
            "FROM agent_spans WHERE trace_id = ? ORDER BY id", (t["id"],))
        item = dict(t)
        item["spans"] = [dict(s) for s in spans]
        result.append(item)
    return {"traces": result}


@router.get("/users")
def list_users(db: Database = Depends(get_db)) -> dict:
    """用户列表（id/username/display_name/roles，成员管理下拉用）。"""
    users = db.query(
        "SELECT u.id, u.username, u.display_name, u.email, u.is_active "
        "FROM users u WHERE u.is_active = 1 ORDER BY u.username")
    for u in users:
        roles = [r["role_id"] for r in db.query(
            "SELECT role_id FROM user_roles WHERE user_id = ?", (u["id"],))]
        u["roles"] = roles
    return {"users": users}
