# -*- coding: utf-8 -*-
"""
tests/test_m4_job.py —— M4-10 生成任务状态机（批次 4）

覆盖（对照 M4-11）：
- 全量生成：job 未生成→生成中→已完成；26 章节全部 content_md 非空
- 断点继续：重跑跳过已完成章节，只处理失败/待生成
- 单章节重新生成：version+1，其他章节不动
- 部分失败：单章节异常 → job.status=部分失败 + section_states 失败；错误落库
- run_generation_task 后台入口：终态 + progress
- API：POST /jobs → 轮询 GET /jobs/{job_id}；regenerate；document 409 前置
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.services.generation import (GenerationJobRunner,  # noqa: E402
                                     run_generation_task)
from app.services.generation.models import SectionStatus  # noqa: E402


def _row(db, section_id: str) -> dict:
    return db.query_one("SELECT * FROM generation_sections WHERE section_id = ?",
                        (section_id,))


# ═══════════════════════════════════════════════════════════════════════
# 状态机
# ═══════════════════════════════════════════════════════════════════════
def test_job_full_run_all_sections_done(seed_m4):
    """全量生成：job 终态已完成，26 章节 content_md 全部非空。"""
    data = seed_m4
    runner = GenerationJobRunner(db=data["db"])
    job = runner.create_job(data["tender_id"])
    assert job.status == "未生成" and job.total_sections == 26
    runner.run(job)
    assert job.status == "已完成", job.status
    assert job.done_sections == 26 and job.failed_sections == 0
    assert "全部 26" in job.progress
    n = data["db"].query_one(
        "SELECT COUNT(*) AS n FROM generation_sections "
        "WHERE tender_id = ? AND content_md != ''", (data["tender_id"],))["n"]
    assert n == 26, "所有章节都应产出 content_md"
    # 章节状态全部落库为已完成
    rows = data["db"].query("SELECT status FROM generation_sections "
                            "WHERE tender_id = ?", (data["tender_id"],))
    assert all(r["status"] == "已完成" for r in rows)


def test_job_resume_skips_completed(seed_m4):
    """断点继续：重跑跳过已完成章节，只重做失败/待生成。"""
    data = seed_m4
    runner = GenerationJobRunner(db=data["db"])
    job = runner.create_job(data["tender_id"])
    runner.run(job)
    # 人为把 CH-06-1 复位为失败，其余保持已完成
    data["db"].update("generation_sections", "section_id", "CH-06-1", {
        "status": SectionStatus.FAILED.value, "content_md": ""})
    before = _row(data["db"], "CH-05-2")["version"]
    job2 = runner.create_job(data["tender_id"])
    runner.run(job2)
    assert job2.status == "已完成"
    assert job2.section_states["CH-06-1"] == "已完成"
    # 未失败章节不被重写：version 不变
    assert _row(data["db"], "CH-05-2")["version"] == before


def test_job_regenerate_single_section_version(seed_m4):
    """单章节重生成：只重跑该章节，version+1，其他章节版本不动。"""
    data = seed_m4
    runner = GenerationJobRunner(db=data["db"])
    job = runner.create_job(data["tender_id"])
    runner.run(job)
    v1 = _row(data["db"], "CH-04-1")["version"]
    v2 = _row(data["db"], "CH-05-2")["version"]
    # 单章节重生成
    jobr = runner.create_job(data["tender_id"], section_id="CH-04-1")
    assert jobr.total_sections == 1
    runner.run(jobr, section_id="CH-04-1")
    assert jobr.status == "已完成"
    assert _row(data["db"], "CH-04-1")["version"] == v1 + 1
    assert _row(data["db"], "CH-05-2")["version"] == v2, "其他章节不得被重写"


def test_job_partial_failure(seed_m4, monkeypatch):
    """单章节异常 → job 部分失败；section_states 标记失败 + 错误落库。"""
    import app.services.generation.job as job_mod

    data = seed_m4
    real = job_mod.SectionGenerator

    class FailingGen(real):
        def generate_section(self, section, *a, **kw):
            if section.id == "CH-06-1":
                raise RuntimeError("模拟章节生成失败")
            return super().generate_section(section, *a, **kw)

    monkeypatch.setattr(job_mod, "SectionGenerator", FailingGen)
    runner = GenerationJobRunner(db=data["db"])
    job = runner.create_job(data["tender_id"])
    runner.run(job)
    assert job.status == "部分失败", job.status
    assert job.failed_sections == 1 and job.done_sections == 25
    assert job.section_states["CH-06-1"] == "失败"
    row = _row(data["db"], "CH-06-1")
    assert row["status"] == "失败" and "模拟章节生成失败" in row["error"]
    # 失败不阻断其他章节
    assert _row(data["db"], "CH-05-2")["content_md"]


def test_run_generation_task_wrapper(seed_m4):
    """后台入口 run_generation_task：终态 + progress + 日志落库。"""
    data = seed_m4
    from app.services.generation import new_job_id
    job_id = new_job_id()
    result = run_generation_task(data["tender_id"], job_id)
    assert result["status"] == "已完成"
    job = GenerationJobRunner(db=data["db"]).get_job(job_id)
    assert job.status == "已完成" and job.done_sections == 26
    logs = data["db"].query_one(
        "SELECT COUNT(*) AS n FROM generation_logs WHERE generation_id = ?",
        (job_id,))
    assert logs["n"] > 0


# ═══════════════════════════════════════════════════════════════════════
# API
# ═══════════════════════════════════════════════════════════════════════
@pytest.fixture()
def job_client(seed_m4):
    from fastapi.testclient import TestClient
    from app.api.main import app

    data = seed_m4
    data["db"].insert("tenders", {"id": data["tender_id"], "name": "M4任务测试项目",
                                  "created_at": "2026-01-01 00:00:00"})
    with TestClient(app) as c:
        yield data, c


def _wait_job(c, tender_id, job_id, timeout=60):
    import time
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = c.get(f"/api/generation/tenders/{tender_id}/jobs/{job_id}")
        j = r.json()
        if j["status"] not in ("未生成", "生成中"):
            return j
        time.sleep(0.2)
    raise AssertionError(f"job 未在 {timeout}s 内完成: {j}")


def test_jobs_endpoint_full_flow(job_client):
    """POST /jobs → 轮询完成 → GET /jobs 最新任务。"""
    data, c = job_client
    r = c.post(f"/api/generation/tenders/{data['tender_id']}/jobs")
    assert r.status_code == 202
    job_id = r.json()["job_id"]
    job = _wait_job(c, data["tender_id"], job_id)
    assert job["status"] == "已完成"
    assert job["total_sections"] == 26 and job["done_sections"] == 26
    # 章节 GET 从 409 → 200（草稿可读）
    r = c.get(f"/api/generation/tenders/{data['tender_id']}/sections/CH-05-2")
    assert r.status_code == 200
    sec = r.json()
    assert sec["section_id"] == "CH-05-2" and sec["content_md"]
    # 最新任务列表
    r = c.get(f"/api/generation/tenders/{data['tender_id']}/jobs")
    assert r.json()["jobs"][0]["id"] == job_id


def test_regenerate_endpoint_version_plus_one(job_client):
    data, c = job_client
    c.post(f"/api/generation/tenders/{data['tender_id']}/jobs")
    v0 = data["db"].query_one("SELECT version FROM generation_sections "
                              "WHERE section_id='CH-04-1'")["version"]
    r = c.post(f"/api/generation/tenders/{data['tender_id']}"
               "/sections/CH-04-1/regenerate")
    assert r.status_code == 202
    job = _wait_job(c, data["tender_id"], r.json()["job_id"])
    assert job["status"] == "已完成" and job["total_sections"] == 1
    v1 = data["db"].query_one("SELECT version FROM generation_sections "
                              "WHERE section_id='CH-04-1'")["version"]
    assert v1 == v0 + 1


def test_jobs_endpoint_conflict_while_running(job_client):
    """生成中再 POST /jobs → 409。"""
    data, c = job_client
    c.post(f"/api/generation/tenders/{data['tender_id']}/jobs")   # 完成首个任务
    # 手工伪造一个生成中任务（后台同步执行无法停在中间态）
    from app.services.generation import GenerationJobRunner
    runner = GenerationJobRunner(db=data["db"])
    job = runner.create_job(data["tender_id"])
    data["db"].update("generation_jobs", "id", job.id, {"status": "生成中"})
    r = c.post(f"/api/generation/tenders/{data['tender_id']}/jobs")
    assert r.status_code == 409


def test_document_endpoint_409_without_generation(job_client):
    """未生成任何章节时 GET /document → 409。"""
    data, c = job_client
    r = c.get(f"/api/generation/tenders/{data['tender_id']}/document")
    assert r.status_code == 409
