# -*- coding: utf-8 -*-
"""
tests/test_m7_kb_version.py —— M7-04 知识库版本管理

覆盖：
- 能力卡版本化：PATCH capabilities → version+1 + knowledge_versions 行
  （change_type=capability_edit，summary=before/after JSON，
  label={今日}-v{当日序}，changed_by 快照）
- 资料重处理：run_kb_task 完成 → material_reprocess 版本行（HTTP 全链路）
- 生成快照：GenerationJobRunner.create_job.kb_version = 最新 label（无则 v0）
  → 追溯链：标书 → generation_jobs.kb_version → knowledge_versions →
  capabilities.source_doc/source_page
- GET /api/knowledge/versions：列表 + capability_id/label 过滤 + 权限
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app import config
from app.api.main import app
from app.db import Database
from app.schemas import Capability, CapabilityCategory, now_str


@pytest.fixture()
def client(tmp_env, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    with TestClient(app) as c:   # lifespan：init_schema + seed_rbac
        yield c


def _seed_capability(db: Database) -> None:
    cap = Capability(
        id="CAP-0001", category=CapabilityCategory("人员资质"),
        name="项目经理张伟", attributes={"年限": "5年"},
        description="", source_doc="04_人员资质.docx", source_page=3)
    db.insert("capabilities", Database.capability_to_row(cap))


def test_capability_versioning_api(client, auth_user, tmp_env):
    """PATCH 能力卡：version 1→2→3 + 版本行 + 列表/过滤 + 权限。"""
    db = Database()
    _seed_capability(db)
    auth_user("bid_manager", user_id="U-MANAGER")

    # v1→v2：张伟 5年→6年（规格示例）
    r = client.patch("/api/knowledge/capabilities/CAP-0001",
                     json={"attributes": {"年限": "6年"}})
    assert r.status_code == 200, r.text
    assert r.json()["version"] == 2
    # v2→v3：+PMP
    r = client.patch("/api/knowledge/capabilities/CAP-0001",
                     json={"attributes": {"年限": "6年", "证书": "PMP"}})
    assert r.json()["version"] == 3

    rows = db.query("SELECT * FROM knowledge_versions "
                    "WHERE capability_id = 'CAP-0001' ORDER BY id")
    assert len(rows) == 2
    assert all(r["change_type"] == "capability_edit" for r in rows)
    assert all(r["changed_by"] == "u-manager" for r in rows)
    today = now_str()[:10]
    assert rows[0]["label"] == f"{today}-v1"
    assert rows[1]["label"] == f"{today}-v2"
    s1 = json.loads(rows[0]["summary"])
    assert json.loads(s1["before"]["attributes"]) == {"年限": "5年"}
    assert json.loads(s1["after"]["attributes"]) == {"年限": "6年"}

    # 列表 + 过滤
    body = client.get("/api/knowledge/versions").json()
    assert body["total"] == 2
    assert body["versions"][0]["id"] == "KV-0002"       # 最新在前
    body = client.get("/api/knowledge/versions",
                      params={"capability_id": "CAP-0001"}).json()
    assert body["total"] == 2
    body = client.get("/api/knowledge/versions",
                      params={"label": rows[1]["label"]}).json()
    assert body["total"] == 1
    assert body["versions"][0]["summary"] == rows[1]["summary"]

    # 权限：staff 无 knowledge:view → 403
    auth_user("staff")
    assert client.get("/api/knowledge/versions").status_code == 403


def test_material_reprocess_version(client, auth_user, tmp_env, m3_env,
                                    kb_sample_dir):
    """资料重处理完成 → material_reprocess 版本行（HTTP 全链路，Mock LLM）。"""
    auth_user("bid_manager", user_id="U-MANAGER")
    kb = kb_sample_dir / "07_公司介绍.pdf"
    r = client.post("/api/knowledge/materials", data={"category": "公司介绍"},
                    files=[("files", (kb.name, kb.read_bytes(),
                                      "application/pdf"))])
    assert r.status_code == 201, r.text
    mid = r.json()["results"][0]["material_id"]
    r = client.post(f"/api/knowledge/materials/{mid}/process")
    assert r.status_code == 202, r.text

    db = Database()
    row = db.query_one(
        "SELECT * FROM knowledge_versions WHERE material_id = ?", (mid,))
    assert row, "重处理完成后应有 material_reprocess 版本行"
    assert row["change_type"] == "material_reprocess"
    assert row["label"].startswith(now_str()[:10])
    body = client.get(f"/api/knowledge/versions?label={row['label']}").json()
    assert body["total"] == 1
    assert body["versions"][0]["material_id"] == mid


def test_generation_job_kb_version_snapshot(tmp_env):
    """create_job.kb_version = 最新 label（无版本时 v0）→ 追溯链逐跳可查。"""
    from app.services.generation import GenerationJobRunner
    from app.services.kb_versions import latest_kb_label, record_version

    db = Database()
    db.init_schema()
    assert latest_kb_label(db) == "v0"

    runner = GenerationJobRunner(db)
    job0 = runner.create_job("T-KBV")
    assert job0.kb_version == "v0"                  # 尚无版本事件

    v = record_version(db, change_type="capability_edit",
                       capability_id="CAP-0001",
                       summary='{"before": {}, "after": {}}')
    job1 = runner.create_job("T-KBV")
    assert job1.kb_version == v["label"]            # 生成快照最新 label

    # 追溯链：标书 → generation_jobs.kb_version → knowledge_versions → 能力卡
    row = db.query_one(
        "SELECT * FROM knowledge_versions WHERE label = ?", (job1.kb_version,))
    assert row["capability_id"] == "CAP-0001"
    v2 = record_version(db, "material_reprocess", material_id="M1")
    assert v2["label"] == f"{now_str()[:10]}-v2"    # 当日序递增

    job2 = runner.create_job("T-KBV")
    assert job2.kb_version == v2["label"]
