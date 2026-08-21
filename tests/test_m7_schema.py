# -*- coding: utf-8 -*-
"""M7 步骤2：DB 层——12 张新表 + 幂等迁移 + RBAC 种子。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.db import Database, M7_PERMISSIONS, M7_ROLE_PERMISSIONS, seed_rbac  # noqa: E402
from app.services.generation.models import GenerationJob  # noqa: E402
from app.schemas import Capability, CapabilityCategory  # noqa: E402

M7_TABLES = [
    "users", "roles", "permissions", "user_roles", "role_permissions",
    "project_members", "audit_logs", "knowledge_versions", "tasks",
    "llm_calls", "agent_traces", "agent_spans",
]


def _columns(db: Database, table: str) -> set:
    return {r["name"] for r in db.query(f"PRAGMA table_info({table})")}


def test_init_schema_creates_m7_tables(tmp_env):
    db = Database()
    db.init_schema()
    tables = {r["name"] for r in db.query(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    for t in M7_TABLES:
        assert t in tables, f"缺表 {t}"


def test_migrate_adds_columns_idempotent(tmp_env):
    db = Database()
    db.init_schema()
    assert "owner_id" in _columns(db, "tenders")
    assert "version" in _columns(db, "capabilities")
    assert "updated_at" in _columns(db, "capabilities")
    assert "kb_version" in _columns(db, "generation_jobs")
    # 第二次 init_schema 不报错（幂等）
    db.init_schema()
    assert "owner_id" in _columns(db, "tenders")


def test_seed_rbac_idempotent(tmp_env):
    db = Database()
    db.init_schema()
    seed_rbac(db)
    seed_rbac(db)  # 幂等：不重复插

    roles = db.query("SELECT * FROM roles")
    assert len(roles) == 5
    perms = db.query("SELECT * FROM permissions")
    assert len(perms) == len(M7_PERMISSIONS) == 17
    rp = db.query("SELECT * FROM role_permissions")
    assert len(rp) == sum(len(v) for v in M7_ROLE_PERMISSIONS.values())
    users = db.query("SELECT * FROM users")
    assert len(users) == 5  # admin + 4 演示用户
    assert db.query_one("SELECT id FROM users WHERE username='admin'") is not None
    # 密码哈希格式：pbkdf2_sha256$600000$salt$hash
    admin = db.query_one("SELECT password_hash FROM users WHERE username='admin'")
    assert admin["password_hash"].startswith("pbkdf2_sha256$600000$")
    # 普通员工仅 final:* 权限
    staff = db.query_one("SELECT id FROM users WHERE username='staff'")
    sp = db.query(
        "SELECT rp.permission_id FROM role_permissions rp "
        "JOIN user_roles ur ON ur.role_id = rp.role_id WHERE ur.user_id = ?",
        (staff["id"],))
    assert {p["permission_id"] for p in sp} == {"final:view", "final:export"}


def test_capability_row_roundtrip_with_version(tmp_env):
    db = Database()
    db.init_schema()
    cap = Capability(
        id="CAP-0001", category=CapabilityCategory("人员资质"), name="项目经理张伟",
        attributes={"experience_years": 6, "pmp": True},
        source_doc="04_人员资质.docx", source_page=1,
        version=2, updated_at="2026-08-18 10:00:00")
    db.insert("capabilities", Database.capability_to_row(cap))
    row = db.query_one("SELECT * FROM capabilities WHERE id='CAP-0001'")
    back = Database.row_to_capability(row)
    assert back.version == 2
    assert back.updated_at == "2026-08-18 10:00:00"
    assert back.attributes["experience_years"] == 6


def test_job_row_roundtrip_with_kb_version(tmp_env):
    db = Database()
    db.init_schema()
    job = GenerationJob(id="job-m7", tender_id="T-M7", status="未生成",
                        kb_version="2026-08-18-v3")
    db.insert("generation_jobs", Database.job_to_row(job))
    row = db.query_one("SELECT * FROM generation_jobs WHERE id='job-m7'")
    back = Database.row_to_job(row)
    assert back.kb_version == "2026-08-18-v3"
