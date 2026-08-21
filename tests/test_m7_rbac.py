# -*- coding: utf-8 -*-
"""
tests/test_m7_rbac.py —— M7-02 RBAC 端点权限矩阵

覆盖（auth_user(role) 覆盖 get_current_user，鉴权依赖真实执行）：
- 角色矩阵：staff 仅 final:*（且需成员）；bid_editor 可知识库/生成；
  bid_manager 不可 quality:confirm；reviewer 不可 bid:edit
- 建单 owner：POST /api/tenders → project_members(owner)
- 项目成员管理：add/duplicate/unknown/owner-delete/remove 语义
- final:* 三段判定：非成员 403 → 加成员后 200（字段裁剪）
- workbench 角色过滤：staff 只见成员项目（delivery_only），概览非成员 404
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import config
from app.api.main import app
from app.db import Database


@pytest.fixture()
def client(tmp_env, monkeypatch, tmp_path):
    """TestClient + DATA_DIR 隔离（final 产物测试不污染真实 out 目录）。"""
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    with TestClient(app) as c:   # lifespan：init_schema + seed_rbac（演示用户入库）
        yield c


def _seed_tender(db: Database, tender_id: str = "T-RBAC",
                 owner_id: str = "U-MANAGER") -> None:
    """建招标项目（owner 成员行可选，None 表示非成员场景）。"""
    db.insert("tenders", {"id": tender_id, "name": "RBAC 测试项目",
                          "created_at": "2026-01-01 00:00:00",
                          "extraction_status": "未提取", "owner_id": owner_id})
    if owner_id:
        db.insert("project_members", {"project_id": tender_id,
                                      "user_id": owner_id, "role": "owner",
                                      "created_at": "2026-01-01 00:00:00"})


# ═══════════════════════════════════════════════════════════════════════
# 角色矩阵
# ═══════════════════════════════════════════════════════════════════════
def test_knowledge_matrix(client, auth_user):
    """知识库：bid_editor 可看，staff 403。"""
    db = Database()
    auth_user("bid_editor")
    assert client.get("/api/knowledge/materials").status_code == 200
    auth_user("staff")
    assert client.get("/api/knowledge/materials").status_code == 403
    assert client.get("/api/knowledge/search?q=设备").status_code == 403


def test_tenders_matrix_and_owner(client, auth_user, tmp_env):
    """招标文件：staff 看不了列表；bid_manager 可上传且成为 owner。"""
    auth_user("staff")
    assert client.get("/api/tenders").status_code == 403

    manager = auth_user("bid_manager", user_id="U-MANAGER")
    assert client.get("/api/tenders").status_code == 200
    r = client.post("/api/tenders",
                    data={"name": "RBAC 上传项目"},
                    files=[("files", ("test.pdf", b"not-a-real-pdf",
                                      "application/pdf"))])
    assert r.status_code == 201, r.text
    tid = r.json()["id"]
    db = Database()
    row = db.query_one("SELECT * FROM project_members "
                       "WHERE project_id = ? AND user_id = ?", (tid, manager["id"]))
    assert row and row["role"] == "owner"
    tender = db.query_one("SELECT owner_id FROM tenders WHERE id = ?", (tid,))
    assert tender["owner_id"] == manager["id"]


def test_generation_matrix(client, auth_user, tmp_env):
    """生成：bid_editor 可规划/编辑；reviewer 不可编辑；staff 全 403。"""
    _seed_tender(Database())
    auth_user("bid_editor")
    r = client.post("/api/generation/tenders/T-RBAC/outline")
    assert r.status_code == 200, r.text
    sections = r.json()["sections"]

    auth_user("staff")
    assert client.get("/api/generation/tenders/T-RBAC/outline").status_code == 403
    assert client.post("/api/generation/tenders/T-RBAC/outline").status_code == 403

    auth_user("reviewer")
    assert client.get("/api/generation/tenders/T-RBAC/outline").status_code == 200
    sid = sections[0]["id"]
    assert client.patch(f"/api/generation/tenders/T-RBAC/sections/{sid}",
                        json={"content_md": "x"}).status_code == 403


def test_quality_confirm_matrix(client, auth_user, tmp_env):
    """质量确认：reviewer 可确认问题；bid_editor/bid_manager 不可。"""
    db = Database()
    _seed_tender(db)
    db.insert("quality_reports", {"id": "QR-RBAC", "tender_id": "T-RBAC",
                                  "score": 100, "dimensions": "[]",
                                  "counts": "{}", "issue_counts": "{}",
                                  "summary": "", "status": "草稿",
                                  "reviewer": "", "review_time": "",
                                  "created_at": "2026-01-01 00:00:00"})
    db.insert("quality_issues", {"id": "QI-RBAC", "tender_id": "T-RBAC",
                                 "report_id": "QR-RBAC", "section_id": "",
                                 "requirement_id": "", "issue_type": "FORMAT_ERROR",
                                 "severity": "WARNING", "message": "格式",
                                 "status": "待处理",
                                 "autofixable": 0, "created_at": "2026-01-01 00:00:00"})

    for role in ("bid_editor", "bid_manager"):
        auth_user(role)
        r = client.patch("/api/quality/issues/QI-RBAC",
                         json={"status": "已确认"})
        assert r.status_code == 403, f"{role} 不应有 quality:confirm"

    auth_user("reviewer")
    r = client.patch("/api/quality/issues/QI-RBAC",
                     json={"status": "已确认", "reviewer": "审核员甲"})
    assert r.status_code == 200, r.text


# ═══════════════════════════════════════════════════════════════════════
# final:* 三段判定（唯一强制成员校验的资源）
# ═══════════════════════════════════════════════════════════════════════
def test_final_requires_membership(client, auth_user, tmp_env, tmp_path):
    """staff 有 final:view 权限但非成员 → 403；加成员后 → 200（产物文件在）。"""
    db = Database()
    _seed_tender(db, owner_id="U-MANAGER")
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    # format=json（默认）读 quality-report.json；docx 分支读 final.docx
    (out_dir / "T-RBAC_quality-report.json").write_text(
        '{"tender_id": "T-RBAC", "content": "# 终版"}', encoding="utf-8")

    staff = auth_user("staff", user_id="U-STAFF")
    assert client.get("/api/quality/tenders/T-RBAC/final").status_code == 403

    db.insert("project_members", {"project_id": "T-RBAC",
                                  "user_id": staff["id"], "role": "member",
                                  "created_at": "2026-01-02 00:00:00"})
    r = client.get("/api/quality/tenders/T-RBAC/final")
    assert r.status_code == 200, r.text
    assert r.json()["content"] == "# 终版"

    # docx 分支：staff 有 final:export → 通过权限检查，到达产物缺失 409
    r = client.get("/api/quality/tenders/T-RBAC/final?format=docx")
    assert r.status_code == 409


def test_final_view_matrix_non_member(client, auth_user, tmp_env):
    """bid_manager 有 final:view 但同样需成员——owner 成员行满足。"""
    db = Database()
    _seed_tender(db, owner_id="U-MANAGER")   # manager 即 owner（成员行存在）
    auth_user("bid_manager", user_id="U-MANAGER")
    r = client.get("/api/quality/tenders/T-RBAC/final")
    assert r.status_code in (200, 409)       # 权限通过；产物存在性另说


# ═══════════════════════════════════════════════════════════════════════
# 项目成员管理（project:manage）
# ═══════════════════════════════════════════════════════════════════════
def test_members_management(client, auth_user, tmp_env):
    """添加/重复/未知用户/列表/移除/owner 不可删。"""
    _seed_tender(Database())
    auth_user("bid_manager")

    # staff 无 project:manage
    auth_user("staff")
    assert client.post("/api/projects/T-RBAC/members",
                       json={"username": "editor"}).status_code == 403

    auth_user("bid_manager")
    r = client.post("/api/projects/T-RBAC/members",
                    json={"username": "editor"})
    assert r.status_code == 201, r.text
    assert r.json()["user_id"] == "U-EDITOR"

    assert client.post("/api/projects/T-RBAC/members",
                       json={"username": "editor"}).status_code == 409
    assert client.post("/api/projects/T-RBAC/members",
                       json={"username": "nobody"}).status_code == 404

    members = client.get("/api/projects/T-RBAC/members").json()["members"]
    assert {m["user_id"] for m in members} == {"U-MANAGER", "U-EDITOR"}

    assert client.delete("/api/projects/T-RBAC/members/U-MANAGER").status_code == 409
    assert client.delete("/api/projects/T-RBAC/members/U-EDITOR").status_code == 200
    assert client.delete("/api/projects/T-RBAC/members/U-EDITOR").status_code == 404


def test_permissions_endpoint(client, auth_user, tmp_env):
    """GET /api/projects/{id}/permissions：角色权限集 + 成员身份。"""
    db = Database()
    _seed_tender(db)
    auth_user("bid_editor")
    r = client.get("/api/projects/T-RBAC/permissions")
    assert r.status_code == 200
    body = r.json()
    assert body["user_id"] == "U-TEST-bid_editor"
    assert "bid:generate" in body["permissions"]
    assert "quality:confirm" not in body["permissions"]
    assert body["is_member"] is False

    auth_user("staff")
    r = client.get("/api/projects/T-RBAC/permissions")
    assert r.status_code == 403        # staff 无 project:view


# ═══════════════════════════════════════════════════════════════════════
# workbench 角色过滤（4.5）
# ═══════════════════════════════════════════════════════════════════════
def test_workbench_staff_delivery_only(client, auth_user, tmp_env):
    """staff：只见成员项目 + 交付裁剪；概览非成员 404。"""
    db = Database()
    _seed_tender(db, tender_id="T-A", owner_id="U-MANAGER")
    _seed_tender(db, tender_id="T-B", owner_id="U-MANAGER")
    db.insert("project_members", {"project_id": "T-A",
                                  "user_id": "U-STAFF", "role": "member",
                                  "created_at": "2026-01-02 00:00:00"})

    auth_user("bid_manager", user_id="U-MANAGER")
    body = client.get("/api/workbench/projects").json()
    assert body["delivery_only"] is False
    assert {p["id"] for p in body["projects"]} == {"T-A", "T-B"}
    assert "documents_detail" not in body["projects"][0]      # 全量视图本身不含（概览才有）

    auth_user("staff", user_id="U-STAFF")
    body = client.get("/api/workbench/projects").json()
    assert body["delivery_only"] is True
    assert [p["id"] for p in body["projects"]] == ["T-A"]
    trimmed = body["projects"][0]
    assert set(trimmed.keys()) == {"id", "name", "created_at",
                                   "delivery", "quality"}
    assert "stages" not in trimmed

    # 概览：成员项目 → 200（裁剪）；非成员 → 404 防存在性泄露
    r = client.get("/api/workbench/projects/T-A")
    assert r.status_code == 200
    assert "documents_detail" not in r.json()
    assert client.get("/api/workbench/projects/T-B").status_code == 404
