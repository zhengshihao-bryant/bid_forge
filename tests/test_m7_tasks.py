# -*- coding: utf-8 -*-
"""
tests/test_m7_tasks.py —— M7-05 任务中心

覆盖：
- 状态机单元：create(pending)→start(running)→progress(pct)→succeed/fail；
  cancel 仅 pending（running 409 语义）
- 4+1 类任务全链路（真实 HTTP + 真实后台执行，Mock LLM 离线）：
  extract / kb_process / match / generate / quality_check → 全部 success
  且 started_by 正确、generate 带 ref_id=generation_jobs.id
- 失败路径：run_extraction_task 无解析产物 → failed + error
- cancel 语义：own pending→cancelled；他人 403；running 409；不存在 404
- GET /api/tasks：type/status 过滤 + 非 admin 只见自己启动的任务
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import config
from app.api.main import app
from app.db import Database
from app.services.task_tracker import (cancel_task, create_task, fail_task,
                                       start_task, succeed_task,
                                       update_progress)


@pytest.fixture()
def client(tmp_env, monkeypatch, tmp_path):
    """TestClient + DATA_DIR 隔离（finalize 产物写入 tmp，不污染真实 out）。"""
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    with TestClient(app) as c:   # lifespan：init_schema + seed_rbac
        yield c


# ═══════════════════════════════════════════════════════════════════════
# 状态机单元
# ═══════════════════════════════════════════════════════════════════════
def test_tracker_state_machine(tmp_env):
    """create→start→progress→succeed 与 create→fail、create→cancel。"""
    db = Database()
    db.init_schema()
    t = create_task(db, "extract", target_id="T-1", started_by="u1", total=10)
    assert t["status"] == "pending" and t["progress_pct"] == 0

    start_task(db, t["id"])
    update_progress(db, t["id"], 3, 10, "窗口 3")
    row = db.query_one("SELECT * FROM tasks WHERE id = ?", (t["id"],))
    assert row["status"] == "running" and row["progress_pct"] == 30
    assert row["progress"] == "窗口 3"

    succeed_task(db, t["id"], done=10, total=10, progress="完成")
    row = db.query_one("SELECT * FROM tasks WHERE id = ?", (t["id"],))
    assert row["status"] == "success" and row["progress_pct"] == 100

    t2 = create_task(db, "match", target_id="T-1")
    fail_task(db, t2["id"], error="x" * 600)
    row = db.query_one("SELECT * FROM tasks WHERE id = ?", (t2["id"],))
    assert row["status"] == "failed" and len(row["error"]) <= 500

    t3 = create_task(db, "generate", target_id="T-1")
    cancel_task(db, t3["id"])
    row = db.query_one("SELECT * FROM tasks WHERE id = ?", (t3["id"],))
    assert row["status"] == "cancelled"


# ═══════════════════════════════════════════════════════════════════════
# 4+1 类任务全链路（HTTP + 真实后台执行）
# ═══════════════════════════════════════════════════════════════════════
def test_task_center_full_chain(client, auth_user, seed_m5, tmp_env,
                                sample_dir, kb_sample_dir):
    """extract/kb_process/match/generate/quality_check 全部 pending→success。"""
    tender_id = seed_m5["tender_id"]          # T-M3（requirements/matches/sections 齐备）
    manager = auth_user("bid_manager", user_id="U-MANAGER")

    # 1 extract：上传真实样例 → 提取（Mock LLM 离线）
    pdf = sample_dir / "02_技术规格书.pdf"
    r = client.post("/api/tenders", data={"name": "任务中心提取项目"},
                    files=[("files", (pdf.name, pdf.read_bytes(),
                                      "application/pdf"))])
    assert r.status_code == 201, r.text
    new_tid = r.json()["id"]
    r = client.post(f"/api/tenders/{new_tid}/extract")
    assert r.status_code == 202, r.text
    assert r.json()["task_id"].startswith("TSK-")

    # 2 kb_process：上传企业资料 → 处理（切块/嵌入/卡片，Mock LLM）
    kb = kb_sample_dir / "07_公司介绍.pdf"
    r = client.post("/api/knowledge/materials", data={"category": "公司介绍"},
                    files=[("files", (kb.name, kb.read_bytes(),
                                      "application/pdf"))])
    assert r.status_code == 201, r.text
    mid = r.json()["results"][0]["material_id"]
    r = client.post(f"/api/knowledge/materials/{mid}/process")
    assert r.status_code == 202, r.text
    assert r.json()["task_id"].startswith("TSK-")

    # 3 match：T-M3 已有需求（seed_m5 基线）
    r = client.post(f"/api/matching/tenders/{tender_id}/match")
    assert r.status_code == 202, r.text
    assert r.json()["task_id"].startswith("TSK-")

    # 4 generate：章节全部已完成 → 断点继续跳过，任务快速 success
    r = client.post(f"/api/generation/tenders/{tender_id}/jobs")
    assert r.status_code == 202, r.text
    assert r.json()["task_id"].startswith("TSK-")

    # 5 quality_check（同步包一层）：reviewer 权限
    auth_user("reviewer", user_id="U-REVIEWER")
    r = client.post(f"/api/quality/tenders/{tender_id}/check")
    assert r.status_code == 200, r.text
    assert r.json()["task_id"].startswith("TSK-")

    # ═══ 断言：5 类任务全部 success ═══
    db = Database()
    rows = db.query("SELECT * FROM tasks")
    by_type = {r["task_type"]: r for r in rows}
    assert set(by_type) >= {"extract", "kb_process", "match", "generate",
                            "quality_check"}, f"缺任务类型: {sorted(by_type)}"
    for r in by_type.values():
        assert r["status"] == "success", f"{r['task_type']}: {r['status']} {r['error']}"
        assert r["progress_pct"] == 100
    assert by_type["extract"]["target_id"] == new_tid
    assert by_type["kb_process"]["target_id"] == mid
    assert by_type["extract"]["started_by"] == manager["id"]
    assert by_type["quality_check"]["started_by"] == "U-REVIEWER"
    # generate 的 ref_id 指向 generation_jobs.id（任务中心可跳转生成详情）
    job_row = db.query_one(
        "SELECT id FROM generation_jobs WHERE tender_id = ? "
        "ORDER BY created_at DESC, id DESC LIMIT 1", (tender_id,))
    assert by_type["generate"]["ref_id"] == job_row["id"]
    assert by_type["generate"]["target_id"] == tender_id


# ═══════════════════════════════════════════════════════════════════════
# 失败路径
# ═══════════════════════════════════════════════════════════════════════
def test_task_failure_path(tmp_env):
    """run_extraction_task：无解析产物 → 任务 failed + error 非空。"""
    from app.services.extraction import run_extraction_task

    db = Database()
    db.init_schema()
    db.insert("tenders", {"id": "T-FAIL-TASK", "name": "失败任务项目",
                          "created_at": "2026-01-01 00:00:00"})
    task = create_task(db, "extract", target_id="T-FAIL-TASK")
    run_extraction_task("T-FAIL-TASK", task["id"])
    row = db.query_one("SELECT * FROM tasks WHERE id = ?", (task["id"],))
    assert row["status"] == "failed"
    assert "解析产物" in row["error"] or "可用" in row["error"]


# ═══════════════════════════════════════════════════════════════════════
# cancel 语义 + 列表过滤/可见性
# ═══════════════════════════════════════════════════════════════════════
def test_task_cancel_and_visibility(client, auth_user, tmp_env):
    """取消：own pending→cancelled；他人 403；running/success 409；不存在 404。

    GET /api/tasks：admin 全量；非 admin 只见自己启动的（started_by 过滤）。
    """
    db = Database()
    db.insert("tasks", {"id": "TSK-P1", "task_type": "extract",
                        "target_id": "", "ref_id": "", "status": "pending",
                        "progress": "", "progress_pct": 0, "total": 0,
                        "done": 0, "error": "", "started_by": "U-STAFF",
                        "created_at": "2026-01-01 00:00:00",
                        "started_at": "", "updated_at": "2026-01-01 00:00:00"})
    db.insert("tasks", {"id": "TSK-P2", "task_type": "match",
                        "target_id": "", "ref_id": "", "status": "pending",
                        "progress": "", "progress_pct": 0, "total": 0,
                        "done": 0, "error": "", "started_by": "U-MANAGER",
                        "created_at": "2026-01-01 00:00:00",
                        "started_at": "", "updated_at": "2026-01-01 00:00:00"})
    db.insert("tasks", {"id": "TSK-R1", "task_type": "generate",
                        "target_id": "", "ref_id": "j1", "status": "running",
                        "progress": "3/26", "progress_pct": 11, "total": 26,
                        "done": 3, "error": "", "started_by": "U-STAFF",
                        "created_at": "2026-01-01 00:00:00",
                        "started_at": "2026-01-01 00:00:00",
                        "updated_at": "2026-01-01 00:00:00"})
    db.insert("tasks", {"id": "TSK-S1", "task_type": "quality_check",
                        "target_id": "", "ref_id": "", "status": "success",
                        "progress": "score=99.1", "progress_pct": 100,
                        "total": 1, "done": 1, "error": "",
                        "started_by": "U-STAFF",
                        "created_at": "2026-01-01 00:00:00",
                        "started_at": "2026-01-01 00:00:00",
                        "updated_at": "2026-01-01 00:00:00"})

    staff = auth_user("staff", user_id="U-STAFF")
    # 自己 pending → cancelled（+ 审计 task_cancel）
    r = client.post("/api/tasks/TSK-P1/cancel")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "cancelled"
    audit = db.query_one(
        "SELECT action FROM audit_logs WHERE action = 'task_cancel' "
        "AND resource_id = 'TSK-P1'")
    assert audit
    # 他人任务 → 403
    assert client.post("/api/tasks/TSK-P2/cancel").status_code == 403
    # running → 409（BackgroundTasks 不可杀，ROADMAP 声明）
    assert client.post("/api/tasks/TSK-R1/cancel").status_code == 409
    # 已终态 → 409；不存在 → 404
    assert client.post("/api/tasks/TSK-S1/cancel").status_code == 409
    assert client.post("/api/tasks/TSK-NOPE/cancel").status_code == 404

    # 非 admin 只看到自己的任务
    body = client.get("/api/tasks").json()
    assert {t["id"] for t in body["tasks"]} == {"TSK-P1", "TSK-R1", "TSK-S1"}
    # admin 全量 + 过滤
    auth_user("admin", user_id="U-ADMIN")
    body = client.get("/api/tasks").json()
    assert {t["id"] for t in body["tasks"]} == {"TSK-P1", "TSK-P2",
                                                "TSK-R1", "TSK-S1"}
    body = client.get("/api/tasks", params={"status": "success"}).json()
    assert [t["id"] for t in body["tasks"]] == ["TSK-S1"]
    body = client.get("/api/tasks", params={"task_type": "generate"}).json()
    assert [t["id"] for t in body["tasks"]] == ["TSK-R1"]
