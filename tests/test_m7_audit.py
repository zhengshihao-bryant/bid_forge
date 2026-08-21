# -*- coding: utf-8 -*-
"""
tests/test_m7_audit.py —— M7-03 操作审计埋点

覆盖规格 10 类操作 + 补充项（login/成员管理/知识库上传删除/大纲规划/
质量检查/报告查看/自动修复/终版闭环/查看终版），全部走真实 HTTP 端点
（seed_m5 提供 M3-M5 全链路数据，鉴权依赖真实执行）：

- 10 类：上传招标文件、查看招标文件、上传/查看/删除知识库、查看证据、
  编辑章节、确认问题、修改能力卡、生成标书、重新生成章节、导出终版
- 成功路径落审计（action + username 快照正确）；失败路径不落（404 无痕）
- reviewer 兼容：显式传优先、缺省取当前登录用户（review_records/report）
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import config
from app.api.main import app
from app.db import Database


@pytest.fixture()
def client(tmp_env, monkeypatch, tmp_path):
    """TestClient + DATA_DIR 隔离（finalize 产物写入 tmp，不污染真实 out）。"""
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    with TestClient(app) as c:   # lifespan：init_schema + seed_rbac
        yield c


def _count(db: Database, action: str) -> int:
    return db.query_one(
        "SELECT COUNT(*) AS n FROM audit_logs WHERE action = ?", (action,))["n"]


def _seed_issue(db: Database, issue_id: str, tender_id: str, report_id: str,
                section_id: str, issue_type: str = "NUMBER_MISMATCH",
                autofixable: int = 0) -> None:
    db.insert("quality_issues", {
        "id": issue_id, "tender_id": tender_id, "report_id": report_id,
        "section_id": section_id, "requirement_id": "",
        "issue_type": issue_type, "severity": "WARNING",
        "message": "测试问题", "status": "待处理",
        "source_refs": "[]", "suggestion": "", "autofixable": autofixable,
        "created_at": "2026-01-01 00:00:00",
    })


def test_audit_spec_operations(client, auth_user, seed_m5, tmp_env, monkeypatch):
    """规格 10 类 + 补充项全链路：成功路径全部落审计且 username 快照正确。"""
    data = seed_m5
    tender_id = data["tender_id"]          # T-M3（M3-M5 全链路数据）
    db = Database()
    # reviewer 成员行（final:* 强制成员校验）
    db.insert("project_members", {
        "project_id": tender_id, "user_id": "U-REVIEWER", "role": "member",
        "created_at": "2026-01-02 00:00:00"})
    # 防后台生成真跑（审计在 handler 内、add_task 之前已落库）
    monkeypatch.setattr("app.api.routes_generation.run_generation_task",
                        lambda *a, **k: None)

    # ── 阶段 A：投标经理（U-MANAGER）──
    auth_user("bid_manager", user_id="U-MANAGER")
    # 1 上传招标文件（新项目 + owner 成员行）
    r = client.post("/api/tenders", data={"name": "审计上传项目"},
                    files=[("files", ("t.pdf", b"xx", "application/pdf"))])
    assert r.status_code == 201, r.text
    # 2 查看招标文件
    assert client.get(f"/api/tenders/{tender_id}").status_code == 200
    # 3 上传知识库
    r = client.post("/api/knowledge/materials", data={"category": "公司资质"},
                    files=[("files", ("kb.pdf", b"xx", "application/pdf"))])
    assert r.status_code == 201, r.text
    mid = r.json()["results"][0]["material_id"]
    # 4 查看知识库
    assert client.get(f"/api/knowledge/materials/{mid}").status_code == 200
    # 5 修订需求（edit_requirement）
    req = db.query_one("SELECT id FROM requirements WHERE tender_id = ? LIMIT 1",
                       (tender_id,))
    r = client.patch(f"/api/tenders/{tender_id}/requirements/{req['id']}",
                     json={"response": "已响应"})
    assert r.status_code == 200, r.text
    # 6 修改能力卡（edit_capability + 版本化）
    cap = db.query_one("SELECT id FROM capabilities LIMIT 1")
    r = client.patch(f"/api/knowledge/capabilities/{cap['id']}",
                     json={"attributes": {"年限": "6年"}})
    assert r.status_code == 200, r.text
    assert r.json()["version"] == 2          # 版本化随审计同批验证
    # 7 查看证据（match_detail）
    m = db.query_one("SELECT id FROM requirement_matches WHERE tender_id = ? LIMIT 1",
                     (tender_id,))
    assert client.get(f"/api/matching/tenders/{tender_id}/matches/{m['id']}"
                      ).status_code == 200
    # 8 编辑章节
    sec = db.query_one("SELECT section_id FROM generation_sections "
                       "WHERE tender_id = ? AND content_md != '' LIMIT 1",
                       (tender_id,))
    sec_id = sec["section_id"]
    r = client.patch(f"/api/generation/tenders/{tender_id}/sections/{sec_id}",
                     json={"content_md": "# 审计编辑"})
    assert r.status_code == 200, r.text
    # 补充：member_add / member_remove
    r = client.post(f"/api/projects/{tender_id}/members",
                    json={"username": "staff"})
    assert r.status_code == 201, r.text
    assert client.delete(f"/api/projects/{tender_id}/members/U-STAFF"
                         ).status_code == 200

    # ── 阶段 B：审核人员（U-REVIEWER）──
    reviewer = auth_user("reviewer", user_id="U-REVIEWER")
    # 9 quality_check（补充项；成功后才写审计）
    r = client.post(f"/api/quality/tenders/{tender_id}/check")
    assert r.status_code == 200, r.text
    report_id = r.json()["report"]["id"]
    # 补充：查看质量报告
    assert client.get(f"/api/quality/reports/{report_id}").status_code == 200
    # 10 确认问题（不传 reviewer → 缺省当前登录用户）
    _seed_issue(db, "QI-AUD", tender_id, report_id, sec_id)
    r = client.patch("/api/quality/issues/QI-AUD", json={"status": "已确认"})
    assert r.status_code == 200, r.text
    rr = db.query_one("SELECT reviewer FROM review_records WHERE issue_id = 'QI-AUD'")
    assert rr["reviewer"] == reviewer["display_name"]
    # 补充：autofix_issue
    _seed_issue(db, "QI-FMT", tender_id, report_id, sec_id,
                issue_type="FORMAT_ERROR", autofixable=1)
    r = client.post("/api/quality/issues/QI-FMT/autofix")
    assert r.status_code == 200, r.text
    # 补充：finalize（不传 reviewer → 缺省当前登录用户；force 跳过未清问题）
    r = client.post(f"/api/quality/tenders/{tender_id}/finalize",
                    json={"force": True})
    assert r.status_code == 200, r.text
    report = db.query_one("SELECT reviewer FROM quality_reports WHERE id = ?",
                          (report_id,))
    assert report["reviewer"] == reviewer["display_name"]
    # 补充：view_final / export_final（final:* 成员校验 + 审计）
    r = client.get(f"/api/quality/tenders/{tender_id}/final")
    assert r.status_code == 200, r.text
    r = client.get(f"/api/quality/tenders/{tender_id}/final?format=docx")
    assert r.status_code == 200, r.text

    # ── 阶段 C：投标经理（生成链路）──
    auth_user("bid_manager", user_id="U-MANAGER")
    # 补充：generate_outline
    r = client.post(f"/api/generation/tenders/{tender_id}/outline")
    assert r.status_code == 200, r.text
    sid = r.json()["sections"][0]["id"]
    # 11 生成标书
    r = client.post(f"/api/generation/tenders/{tender_id}/jobs")
    assert r.status_code == 202, r.text
    # 12 重新生成章节
    r = client.post(f"/api/generation/tenders/{tender_id}/sections/{sid}/regenerate")
    assert r.status_code == 202, r.text
    # 补充：delete_knowledge
    assert client.delete(f"/api/knowledge/materials/{mid}").status_code == 200

    # ── 阶段 D：登录（真实凭证路径，不依赖 override）──
    r = client.post("/api/auth/login",
                    json={"username": "nobody", "password": "x"})
    assert r.status_code == 401
    r = client.post("/api/auth/login",
                    json={"username": "manager", "password": "manager123"})
    assert r.status_code == 200, r.text

    # ═══ 断言：全部 action 落审计 + username 快照 ═══
    rows = Database().query("SELECT action, username FROM audit_logs")
    by_action: dict[str, list[str]] = {}
    for row in rows:
        by_action.setdefault(row["action"], []).append(row["username"])
    expected = {
        "upload_tender", "view_tender_doc", "upload_knowledge", "view_knowledge",
        "edit_requirement", "edit_capability", "view_evidence", "edit_section",
        "quality_check", "view_quality_report", "confirm_issue",
        "autofix_issue", "finalize_bid", "view_final", "export_final",
        "member_add", "member_remove", "generate_outline", "generate_bid",
        "regenerate_section", "delete_knowledge", "login", "login_failed",
    }
    missing = expected - set(by_action)
    assert not missing, f"缺少审计 action: {sorted(missing)}"
    # username 快照（record_audit 冗余列）
    assert "u-manager" in by_action["edit_capability"]
    assert "u-manager" in by_action["generate_bid"]
    assert "u-reviewer" in by_action["confirm_issue"]
    assert "u-reviewer" in by_action["export_final"]
    assert by_action["login_failed"][0] == "nobody"
    assert "manager" in by_action["login"]


def test_audit_failure_not_recorded(client, auth_user, tmp_env):
    """失败路径不落审计：404 的修改类操作无痕。"""
    auth_user("bid_manager")
    db = Database()
    before_cap = _count(db, "edit_capability")
    r = client.patch("/api/knowledge/capabilities/NOPE", json={"name": "x"})
    assert r.status_code == 404
    assert _count(db, "edit_capability") == before_cap

    auth_user("reviewer")
    before_issue = _count(db, "confirm_issue")
    r = client.patch("/api/quality/issues/NOPE", json={"status": "已确认"})
    assert r.status_code == 404
    assert _count(db, "confirm_issue") == before_issue
