# -*- coding: utf-8 -*-
"""
routes_projects.py —— M7-02 项目级权限与成员（RBAC 数据层）

- GET  /api/projects/{tender_id}/permissions：当前用户在该项目的有效权限集
- POST /api/projects/{tender_id}/members：按 username 添加成员（默认 member）
- GET  /api/projects/{tender_id}/members：成员列表
- DELETE /api/projects/{tender_id}/members/{user_id}：移除成员（owner 不可删）

成员约束（见 auth/deps.py require_project_permission）：仅 final:* 资源强制
成员校验；其余资源凭全局角色权限放行。建单人（create_tender）自动成为 owner。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..auth.audit import record_audit
from ..auth.deps import require_permission
from ..db import Database, get_db
from ..schemas import now_str

router = APIRouter(prefix="/api/projects", tags=["项目权限"])


class MemberBody(BaseModel):
    username: str = Field(min_length=1)
    role: str = "member"


def _tender_or_404(db: Database, tender_id: str) -> dict:
    row = db.query_one("SELECT * FROM tenders WHERE id = ?", (tender_id,))
    if not row:
        raise HTTPException(status_code=404, detail="项目不存在")
    return row


@router.get("/{tender_id}/permissions",
            dependencies=[Depends(require_permission("project", "view"))])
def project_permissions(tender_id: str,
                        user: dict = Depends(require_permission("project", "view")),
                        db: Database = Depends(get_db)) -> dict:
    """当前用户在该项目的有效权限集（角色权限 + 成员身份）。"""
    _tender_or_404(db, tender_id)
    member = db.query_one(
        "SELECT * FROM project_members WHERE project_id = ? AND user_id = ?",
        (tender_id, user["id"]))
    return {
        "tender_id": tender_id,
        "user_id": user["id"],
        "username": user["username"],
        "roles": user.get("roles", []),
        "permissions": sorted(user.get("permissions", set())),
        "is_member": bool(member),
        "project_role": (member or {}).get("role", ""),
    }


@router.get("/{tender_id}/members",
            dependencies=[Depends(require_permission("project", "manage"))])
def list_members(tender_id: str, db: Database = Depends(get_db)) -> dict:
    _tender_or_404(db, tender_id)
    rows = db.query(
        "SELECT pm.user_id, pm.role, pm.created_at, u.username, u.display_name "
        "FROM project_members pm LEFT JOIN users u ON u.id = pm.user_id "
        "WHERE pm.project_id = ? ORDER BY pm.created_at", (tender_id,))
    return {"project_id": tender_id,
            "members": [dict(r) for r in rows]}


@router.post("/{tender_id}/members", status_code=201,
             dependencies=[Depends(require_permission("project", "manage"))])
def add_member(tender_id: str, body: MemberBody,
               user: dict = Depends(require_permission("project", "manage")),
               db: Database = Depends(get_db)) -> dict:
    """按 username 添加成员（角色固定 member；owner 由建单自动产生）。"""
    _tender_or_404(db, tender_id)
    u = db.query_one("SELECT * FROM users WHERE username = ?", (body.username,))
    if not u:
        raise HTTPException(status_code=404, detail="用户不存在")
    exists = db.query_one(
        "SELECT 1 AS x FROM project_members WHERE project_id = ? AND user_id = ?",
        (tender_id, u["id"]))
    if exists:
        raise HTTPException(status_code=409, detail="该用户已是项目成员")
    db.insert("project_members", {
        "project_id": tender_id, "user_id": u["id"], "role": "member",
        "created_at": now_str(),
    })
    record_audit(db, user, "member_add", "project", tender_id,
                 detail=f"添加成员 {body.username}({u['id']})")
    return {"project_id": tender_id, "user_id": u["id"],
            "username": u["username"], "role": "member"}


@router.delete("/{tender_id}/members/{user_id}",
               dependencies=[Depends(require_permission("project", "manage"))])
def remove_member(tender_id: str, user_id: str,
                  user: dict = Depends(require_permission("project", "manage")),
                  db: Database = Depends(get_db)) -> dict:
    _tender_or_404(db, tender_id)
    member = db.query_one(
        "SELECT * FROM project_members WHERE project_id = ? AND user_id = ?",
        (tender_id, user_id))
    if not member:
        raise HTTPException(status_code=404, detail="该用户不是项目成员")
    if member["role"] == "owner":
        raise HTTPException(status_code=409, detail="项目所有者不可移除")
    db.execute("DELETE FROM project_members WHERE project_id = ? AND user_id = ?",
               (tender_id, user_id))
    record_audit(db, user, "member_remove", "project", tender_id,
                 detail=f"移除成员 {user_id}")
    return {"deleted": True, "user_id": user_id}
