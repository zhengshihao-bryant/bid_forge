# -*- coding: utf-8 -*-
"""
app/auth/deps.py —— FastAPI 鉴权依赖（get_current_user / require_*）

- get_current_user：Bearer 解析 → users + roles + permissions → 401
  AUTH_ENABLED=false 时返回 admin 等价系统用户（旧版验收脚本 / 本机调试）
- require_permission(resource, action)：全局资源权限，admin 旁路，403 缺权限
- require_project_permission(resource, action)：项目资源权限三段判定——
  admin 旁路；权限集不含 (resource,action) → 403；
  resource=="final" 且非该项目 project_members → 403（"普通员工仅最终版本
  且需成员"的落点）；其余资源仅凭角色权限放行（投标经理等开箱即用）
- require_admin：仅 admin 角色（审计日志 / 监控 / 用户列表端点）
"""

from __future__ import annotations

from typing import Callable, Optional

from fastapi import Depends, Header, HTTPException, Path

from .. import config
from ..db import Database, get_db
from .security import decode_token


def load_user_dict(db: Database, user_id: str) -> Optional[dict]:
    """users + user_roles + role_permissions → 鉴权用 dict（含 roles/permissions）。"""
    row = db.query_one("SELECT * FROM users WHERE id = ?", (user_id,))
    if not row or not row.get("is_active"):
        return None
    roles = [r["role_id"] for r in db.query(
        "SELECT role_id FROM user_roles WHERE user_id = ?", (user_id,))]
    perms: set[str] = set()
    if roles:
        marks = ", ".join("?" for _ in roles)
        perms = {r["permission_id"] for r in db.query(
            f"SELECT permission_id FROM role_permissions WHERE role_id IN ({marks})",
            tuple(roles))}
    return {"id": row["id"], "username": row["username"],
            "email": row.get("email") or "",
            "display_name": row.get("display_name") or "",
            "roles": roles, "permissions": perms}


def _system_user() -> dict:
    """AUTH_ENABLED=false 时的系统用户（admin 等价，权限检查全放行）。"""
    return {"id": "U-SYSTEM", "username": "system", "email": "",
            "display_name": "系统用户", "roles": ["admin"], "permissions": set()}


def get_current_user(
    authorization: Optional[str] = Header(None),
    db: Database = Depends(get_db),
) -> dict:
    """解析 Authorization: Bearer <token> → 当前用户（401 未登录/无效）。"""
    if not config.AUTH_ENABLED:
        return _system_user()
    token = ""
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    if not token:
        raise HTTPException(status_code=401, detail="未登录：缺少 Authorization: Bearer <token>")
    user_id = decode_token(token)
    if not user_id:
        raise HTTPException(status_code=401, detail="未登录：token 无效或已过期")
    user = load_user_dict(db, user_id)
    if not user:
        raise HTTPException(status_code=401, detail="未登录：用户不存在或已停用")
    return user


def has_permission(user: dict, resource: str, action: str) -> bool:
    """权限判定（admin 角色旁路全放行）。"""
    return "admin" in user.get("roles", []) or \
        f"{resource}:{action}" in user.get("permissions", set())


def require_permission(resource: str, action: str) -> Callable:
    """全局资源权限依赖工厂（admin 旁路）。"""
    def dep(user: dict = Depends(get_current_user)) -> dict:
        if not has_permission(user, resource, action):
            raise HTTPException(
                status_code=403, detail=f"无权限：缺少 {resource}:{action}")
        return user

    return dep


def require_project_permission(resource: str, action: str) -> Callable:
    """项目资源权限依赖工厂（三段判定，见模块 docstring）。tender_id 取自路径参数。"""
    def dep(tender_id: str = Path(...),
            user: dict = Depends(get_current_user),
            db: Database = Depends(get_db)) -> dict:
        if "admin" in user.get("roles", []):
            return user
        if not has_permission(user, resource, action):
            raise HTTPException(
                status_code=403, detail=f"无权限：缺少 {resource}:{action}")
        if resource == "final":
            member = db.query_one(
                "SELECT 1 AS x FROM project_members "
                "WHERE project_id = ? AND user_id = ?",
                (tender_id, user["id"]))
            if not member:
                raise HTTPException(
                    status_code=403, detail="不是该项目成员，无权访问最终版本")
        return user

    return dep


def require_admin(user: dict = Depends(get_current_user)) -> dict:
    """仅 admin 角色（审计日志 / 监控 / 用户列表端点）。"""
    if "admin" not in user.get("roles", []):
        raise HTTPException(status_code=403, detail="仅管理员可访问")
    return user
