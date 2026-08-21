# -*- coding: utf-8 -*-
"""M7 步骤3：认证——登录 / me / 401 / AUTH_ENABLED 开关 / 密码与 token 往返。"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import jwt
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app import config  # noqa: E402
from app.api.main import app  # noqa: E402
from app.auth.deps import get_current_user  # noqa: E402
from app.auth.security import (create_access_token, decode_token,  # noqa: E402
                               hash_password, verify_password)
from app.db import Database  # noqa: E402


@pytest.fixture()
def no_override():
    """移除全局 admin override（autouse _auth_admin），测真实 token 鉴权路径。"""
    app.dependency_overrides.pop(get_current_user, None)
    yield


@pytest.fixture()
def auth_client(tmp_env, no_override):
    """隔离 DB + 真实鉴权 TestClient（lifespan 自动建表 + RBAC 种子）。"""
    with TestClient(app) as c:
        yield c


def test_password_hash_roundtrip():
    h = hash_password("s3cret")
    assert h.startswith("pbkdf2_sha256$600000$")
    assert verify_password("s3cret", h)
    assert not verify_password("wrong", h)
    # 格式损坏不抛异常
    assert not verify_password("x", "garbage")
    assert not verify_password("x", "")


def test_token_roundtrip():
    tok = create_access_token("U-ADMIN")
    assert decode_token(tok) == "U-ADMIN"
    assert decode_token("not-a-token") is None
    expired = jwt.encode(
        {"sub": "U-ADMIN", "exp": datetime.now(timezone.utc) - timedelta(hours=1)},
        config.JWT_SECRET, algorithm="HS256")
    assert decode_token(expired) is None


def test_login_success_and_me(auth_client):
    r = auth_client.post("/api/auth/login",
                         json={"username": "admin", "password": "admin123"})
    assert r.status_code == 200
    data = r.json()
    assert data["token_type"] == "bearer"
    assert "admin" in data["user"]["roles"]
    assert "project:manage" in data["user"]["permissions"]
    me = auth_client.get("/api/auth/me",
                         headers={"Authorization": f"Bearer {data['token']}"})
    assert me.status_code == 200
    assert me.json()["username"] == "admin"


def test_login_wrong_password(auth_client):
    r = auth_client.post("/api/auth/login",
                         json={"username": "admin", "password": "nope"})
    assert r.status_code == 401


def test_me_requires_token(auth_client):
    assert auth_client.get("/api/auth/me").status_code == 401
    assert auth_client.get(
        "/api/auth/me",
        headers={"Authorization": "Bearer bogus"}).status_code == 401


def test_existing_endpoints_require_login(auth_client):
    """存量业务路由整体挂登录依赖：无 token → 401。"""
    assert auth_client.get("/api/tenders").status_code == 401
    assert auth_client.get("/api/workbench/projects").status_code == 401


def test_auth_disabled_returns_system_user(auth_client, monkeypatch):
    """AUTH_ENABLED=false：无 token 放行为系统用户（旧验收脚本兼容）。"""
    monkeypatch.setattr(config, "AUTH_ENABLED", False)
    r = auth_client.get("/api/auth/me")
    assert r.status_code == 200
    assert r.json()["username"] == "system"


def test_login_records_audit(auth_client):
    auth_client.post("/api/auth/login",
                     json={"username": "admin", "password": "admin123"})
    auth_client.post("/api/auth/login",
                     json={"username": "admin", "password": "bad"})
    db = Database()
    rows = db.query("SELECT action FROM audit_logs WHERE resource_type='auth'")
    actions = {r["action"] for r in rows}
    assert "login" in actions and "login_failed" in actions
