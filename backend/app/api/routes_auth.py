# -*- coding: utf-8 -*-
"""
routes_auth.py —— 认证（M7-01/02）：登录 / 当前用户

- POST /api/auth/login：用户名密码 → JWT + 用户（roles/permissions）；
  失败 401（登录失败也记审计）
- GET /api/auth/me：当前登录用户（roles + permissions）
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from ..auth.audit import record_audit
from ..auth.deps import get_current_user, load_user_dict
from ..auth.security import create_access_token, verify_password
from ..db import Database, get_db

router = APIRouter(prefix="/api/auth", tags=["认证"])


class LoginBody(BaseModel):
    username: str = Field(min_length=1)
    password: str = Field(min_length=1)


@router.post("/login")
def login(body: LoginBody, request: Request,
          db: Database = Depends(get_db)) -> dict:
    ip = request.client.host if request.client else ""
    row = db.query_one("SELECT * FROM users WHERE username = ?", (body.username,))
    if not row or not row.get("is_active") or \
            not verify_password(body.password, row["password_hash"]):
        record_audit(db, {"id": "", "username": body.username}, "login_failed",
                     "auth", body.username, detail="用户名或密码错误", ip=ip)
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    user = load_user_dict(db, row["id"])
    record_audit(db, user, "login", "auth", row["id"], ip=ip)
    return {"token": create_access_token(row["id"]), "token_type": "bearer",
            "user": user}


@router.get("/me")
def me(user: dict = Depends(get_current_user)) -> dict:
    """当前登录用户（roles + permissions 供前端守卫/菜单过滤）。"""
    return user
