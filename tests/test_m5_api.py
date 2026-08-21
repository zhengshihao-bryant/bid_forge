# -*- coding: utf-8 -*-
"""
tests/test_m5_api.py —— M5 API 层（批次 4，/api/quality 全端点离线测试）

覆盖：
- POST /check：基线 QR-0001 / 9 待确认 / score 99.1 / 无 CRITICAL/ERROR；
  无章节 → 409；未知项目 → 404
- GET /tenders/{id}/reports + GET /reports/{id}：列表 + 详情 + 404
- GET /tenders/{id}/issues?status= 过滤；PATCH 人工处理（已确认→审计留痕）
  + 非法状态 422
- POST /issues/{id}/autofix：格式注入 → 修复章节 → 重查归零；非格式问题 422
- POST /tenders/{id}/finalize：干净基线通过 + 三格式产物；未检查 409；
  有未清 CRITICAL → 409 → 清状态 → 通过
"""
from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app import config  # noqa: E402
from app.api.main import app  # noqa: E402
from app.db import Database  # noqa: E402


def _set_content(db, tid: str, sid: str, content: str) -> None:
    db.execute("UPDATE generation_sections SET content_md = ? "
               "WHERE section_id = ? AND tender_id = ?", (content, sid, tid))


# ═══════════════════════════════════════════════════════════════════════
# POST /check
# ═══════════════════════════════════════════════════════════════════════
def test_check_baseline(m5_api):
    """基线检查：QR-0001、9 条待确认、score=99.1、无 CRITICAL/ERROR。"""
    data, client = m5_api
    tid = data["tender_id"]
    r = client.post(f"/api/quality/tenders/{tid}/check")
    assert r.status_code == 200
    body = r.json()
    assert body["report"]["id"] == "QR-0001"
    assert body["report"]["score"] == 99.1
    assert body["report"]["counts"]["pending"] == 9
    assert body["report"]["counts"]["critical"] == 0
    assert body["report"]["counts"]["error"] == 0
    assert len(body["issues"]) == 9
    assert {i["issue_type"] for i in body["issues"]} == {"PENDING_CONFIRMATION"}


def test_check_409_no_sections(tmp_env):
    """tender 存在但无已生成章节 → 409。"""
    db = Database(config.DB_PATH)
    db.init_schema()
    db.insert("tenders", {"id": "T-EMPTY", "name": "空项目",
                          "created_at": "2026-01-01 00:00:00"})
    with TestClient(app) as c:
        r = c.post("/api/quality/tenders/T-EMPTY/check")
    assert r.status_code == 409


def test_404_unknown_tender(m5_api):
    """未知项目 → 各端点 404。"""
    _, client = m5_api
    assert client.post("/api/quality/tenders/T-NOPE/check").status_code == 404
    assert client.get("/api/quality/tenders/T-NOPE/reports").status_code == 404
    assert client.get("/api/quality/tenders/T-NOPE/issues").status_code == 404
    assert client.post("/api/quality/tenders/T-NOPE/finalize",
                       json={}).status_code == 404
    assert client.get("/api/quality/tenders/T-NOPE/final").status_code == 404


# ═══════════════════════════════════════════════════════════════════════
# GET /reports
# ═══════════════════════════════════════════════════════════════════════
def test_reports_endpoints(m5_api):
    """报告列表 + 详情（含 issues）+ 未知报告 404。"""
    data, client = m5_api
    tid = data["tender_id"]
    client.post(f"/api/quality/tenders/{tid}/check")

    r = client.get(f"/api/quality/tenders/{tid}/reports")
    assert r.status_code == 200
    reports = r.json()["reports"]
    assert len(reports) == 1 and reports[0]["id"] == "QR-0001"

    d = client.get(f"/api/quality/reports/QR-0001")
    assert d.status_code == 200
    assert d.json()["report"]["score"] == 99.1
    assert len(d.json()["issues"]) == 9

    assert client.get("/api/quality/reports/QR-9999").status_code == 404


# ═══════════════════════════════════════════════════════════════════════
# GET /issues + PATCH /issues
# ═══════════════════════════════════════════════════════════════════════
def test_issues_list_and_patch(m5_api):
    """问题列表过滤 + 人工处理（已确认 → 审计留痕）+ 非法状态 422。"""
    data, client = m5_api
    tid = data["tender_id"]
    client.post(f"/api/quality/tenders/{tid}/check")

    issues = client.get(f"/api/quality/tenders/{tid}/issues").json()["issues"]
    assert len(issues) == 9
    iid = issues[0]["id"]

    r = client.patch(f"/api/quality/issues/{iid}",
                     json={"status": "已确认", "reviewer": "验收员",
                           "note": "核实无误"})
    assert r.status_code == 200
    assert r.json()["action"] == "确认"

    confirmed = client.get(
        f"/api/quality/tenders/{tid}/issues?status=已确认").json()["issues"]
    assert len(confirmed) == 1 and confirmed[0]["id"] == iid

    audit = data["db"].query("SELECT * FROM review_records WHERE issue_id = ?",
                             (iid,))
    assert len(audit) == 1 and audit[0]["reviewer"] == "验收员"

    assert client.patch(f"/api/quality/issues/{iid}",
                        json={"status": "不存在"}).status_code == 422
    assert client.patch(f"/api/quality/issues/QR-0001-9999",
                        json={"status": "已确认"}).status_code == 404


# ═══════════════════════════════════════════════════════════════════════
# POST /issues/{id}/autofix
# ═══════════════════════════════════════════════════════════════════════
def test_autofix_endpoint(m5_api):
    """注入行尾空白 → check 报 FORMAT_ERROR → autofix 修复章节 → 重查归零。"""
    data, client = m5_api
    db, tid = data["db"], data["tender_id"]
    content = db.query_one(
        "SELECT content_md FROM generation_sections "
        "WHERE section_id = 'CH-04-1' AND tender_id = ?", (tid,))["content_md"]
    _set_content(db, tid, "CH-04-1", content + "\n尾部空白行  ")

    r = client.post(f"/api/quality/tenders/{tid}/check")
    assert r.status_code == 200
    fmt = [i for i in r.json()["issues"] if i["issue_type"] == "FORMAT_ERROR"]
    assert len(fmt) == 1 and fmt[0]["autofixable"] is True
    iid = fmt[0]["id"]

    r2 = client.post(f"/api/quality/issues/{iid}/autofix")
    assert r2.status_code == 200
    body = r2.json()
    assert body["fixed"] is True
    assert body["remaining_format_issues"] == []

    new_content = db.query_one(
        "SELECT content_md FROM generation_sections "
        "WHERE section_id = 'CH-04-1' AND tender_id = ?", (tid,))["content_md"]
    assert "尾部空白行  " not in new_content
    assert "尾部空白行" in new_content

    row = db.query_one("SELECT status FROM quality_issues WHERE id = ?", (iid,))
    assert row["status"] == "已修复"


def test_autofix_non_format_422(m5_api):
    """非格式问题（待确认）→ 422。"""
    data, client = m5_api
    tid = data["tender_id"]
    client.post(f"/api/quality/tenders/{tid}/check")
    issues = client.get(f"/api/quality/tenders/{tid}/issues").json()["issues"]
    pending = issues[0]
    assert pending["issue_type"] == "PENDING_CONFIRMATION"
    r = client.post(f"/api/quality/issues/{pending['id']}/autofix")
    assert r.status_code == 422


# ═══════════════════════════════════════════════════════════════════════
# POST /tenders/{id}/finalize + GET /final
# ═══════════════════════════════════════════════════════════════════════
def test_finalize_clean_flow(m5_api):
    """干净基线 → finalize 通过 + 三格式终版产物可读。"""
    data, client = m5_api
    tid = data["tender_id"]
    client.post(f"/api/quality/tenders/{tid}/check")

    r = client.post(f"/api/quality/tenders/{tid}/finalize",
                    json={"reviewer": "验收员"})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "已批准"
    assert body["reviewer"] == "验收员"

    rj = client.get(f"/api/quality/tenders/{tid}/final?format=json")
    assert rj.status_code == 200 and rj.json()["score"] == 99.1
    rm = client.get(f"/api/quality/tenders/{tid}/final?format=markdown")
    # final.md = 组装终版标书（质量报告以结构化 JSON 落盘）
    assert rm.status_code == 200
    assert "M5质量检查测试项目" in rm.json()["content"]
    rd = client.get(f"/api/quality/tenders/{tid}/final?format=docx")
    assert rd.status_code == 200 and rd.content[:2] == b"PK"


def test_finalize_409_no_check(m5_api):
    """未执行过检查 → finalize 409。"""
    data, client = m5_api
    tid = data["tender_id"]
    r = client.post(f"/api/quality/tenders/{tid}/finalize", json={})
    assert r.status_code == 409
    assert "尚未执行质量检查" in r.json()["detail"]


def test_finalize_blocked_then_cleared(m5_api):
    """有未清 CRITICAL → 409；人工确认后 → 通过。"""
    data, client = m5_api
    db, tid = data["db"], data["tender_id"]
    _set_content(db, tid, "CH-06-1", "")
    client.post(f"/api/quality/tenders/{tid}/check")

    r = client.post(f"/api/quality/tenders/{tid}/finalize",
                    json={"reviewer": "验收员"})
    assert r.status_code == 409
    assert "未处理问题" in r.json()["detail"]

    for i in client.get(f"/api/quality/tenders/{tid}/issues").json()["issues"]:
        if i["severity"] in ("CRITICAL", "ERROR"):
            assert client.patch(
                f"/api/quality/issues/{i['id']}",
                json={"status": "已确认", "reviewer": "验收员"}
            ).status_code == 200

    r2 = client.post(f"/api/quality/tenders/{tid}/finalize",
                     json={"reviewer": "验收员"})
    assert r2.status_code == 200
    assert r2.json()["status"] == "已批准"
