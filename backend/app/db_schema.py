# -*- coding: utf-8 -*-
"""
app/db_schema.py —— 数据库 Schema 与初始化

从 app/db.py 拆分：DDL 建表脚本、RBAC 权限矩阵常量、seed_rbac 种子、
get_db 工厂。migrate 由 Database.init_schema 调用（db.py 内保留实现）。
"""

from __future__ import annotations

from . import config
from .schemas import now_str


# ═══════════════════════════════════════════════════════════════════════
# DDL（M2/M3 表预建，向前兼容）
# ═══════════════════════════════════════════════════════════════════════
DDL = """
CREATE TABLE IF NOT EXISTS tenders (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL,
    extraction_status TEXT NOT NULL DEFAULT '未提取',
    extraction_progress TEXT NOT NULL DEFAULT '',
    requirement_count INTEGER NOT NULL DEFAULT 0,
    score_point_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY,
    tender_id TEXT NOT NULL,
    file_name TEXT NOT NULL,
    stored_name TEXT NOT NULL,
    file_type TEXT NOT NULL,
    total_pages INTEGER NOT NULL DEFAULT 0,
    char_count INTEGER NOT NULL DEFAULT 0,
    ocr_pages TEXT NOT NULL DEFAULT '[]',
    raw_hash TEXT NOT NULL DEFAULT '',
    parser_version TEXT NOT NULL DEFAULT '',
    parse_error TEXT NOT NULL DEFAULT '',
    parsed_file TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_documents_tender ON documents(tender_id);

CREATE TABLE IF NOT EXISTS requirements (
    id TEXT PRIMARY KEY,
    tender_id TEXT NOT NULL,
    type TEXT NOT NULL,
    title TEXT NOT NULL,
    original_text TEXT NOT NULL,
    quantitative TEXT NOT NULL DEFAULT '[]',
    importance TEXT NOT NULL DEFAULT '中',
    is_star INTEGER NOT NULL DEFAULT 0,
    source_document TEXT NOT NULL DEFAULT '',
    source_doc_id TEXT NOT NULL DEFAULT '',
    source_page INTEGER,
    source_section_path TEXT NOT NULL DEFAULT '',
    source_block_id TEXT NOT NULL DEFAULT '',
    source_snippet TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT '待响应',
    response TEXT NOT NULL DEFAULT '',
    human_confirmed INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_requirements_tender ON requirements(tender_id);

CREATE TABLE IF NOT EXISTS score_points (
    id TEXT PRIMARY KEY,
    tender_id TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT '',
    item TEXT NOT NULL,
    max_score REAL,
    criteria TEXT NOT NULL DEFAULT '',
    rule_id TEXT NOT NULL DEFAULT '',
    weight REAL NOT NULL DEFAULT 0,
    source_ref TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_score_points_tender ON score_points(tender_id);

-- M2：企业知识库
CREATE TABLE IF NOT EXISTS kb_materials (
    id TEXT PRIMARY KEY,
    category TEXT NOT NULL,
    file_name TEXT NOT NULL,
    stored_name TEXT NOT NULL,
    file_type TEXT NOT NULL,
    total_pages INTEGER NOT NULL DEFAULT 0,
    char_count INTEGER NOT NULL DEFAULT 0,
    ocr_pages TEXT NOT NULL DEFAULT '[]',
    raw_hash TEXT NOT NULL DEFAULT '',
    parser_version TEXT NOT NULL DEFAULT '',
    parse_error TEXT NOT NULL DEFAULT '',
    parsed_file TEXT NOT NULL DEFAULT '',
    process_status TEXT NOT NULL DEFAULT '未处理',
    process_progress TEXT NOT NULL DEFAULT '',
    chunk_count INTEGER NOT NULL DEFAULT 0,
    capability_count INTEGER NOT NULL DEFAULT 0,
    index_status TEXT NOT NULL DEFAULT 'none',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_kb_materials_category ON kb_materials(category);

-- 内容块：SQLite 为事实源（全文 + 四元溯源 + 向量 JSON）；Milvus 为可重建索引
CREATE TABLE IF NOT EXISTS kb_chunks (
    id TEXT PRIMARY KEY,
    material_id TEXT NOT NULL,
    category TEXT NOT NULL,
    file_name TEXT NOT NULL,
    file_type TEXT NOT NULL,
    content TEXT NOT NULL,
    section_path TEXT NOT NULL DEFAULT '',
    page_start INTEGER,
    page_end INTEGER,
    block_ids TEXT NOT NULL DEFAULT '[]',
    embedding TEXT NOT NULL DEFAULT '[]',
    seq INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_kb_chunks_material ON kb_chunks(material_id);

CREATE TABLE IF NOT EXISTS capabilities (
    id TEXT PRIMARY KEY,
    category TEXT NOT NULL,
    name TEXT NOT NULL,
    attributes TEXT NOT NULL DEFAULT '{}',
    description TEXT NOT NULL DEFAULT '',
    source_doc TEXT NOT NULL DEFAULT '',
    source_page INTEGER,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_capabilities_category ON capabilities(category);

-- M3：需求-能力匹配
CREATE TABLE IF NOT EXISTS matches (
    id TEXT PRIMARY KEY,
    requirement_id TEXT NOT NULL,
    capability_id TEXT,
    verdict TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 0,
    evidence TEXT NOT NULL DEFAULT '',
    reason TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_matches_requirement ON matches(requirement_id);

-- M3（正式版）：需求标准化 / 证据 / 匹配结果
-- matches 表为 M1 预建的旧版形状（verdict 中文枚举），M3 采用 requirement_matches
CREATE TABLE IF NOT EXISTS canonical_requirements (
    id TEXT PRIMARY KEY,
    tender_id TEXT NOT NULL,
    req_type TEXT NOT NULL DEFAULT 'OTHER',
    title TEXT NOT NULL,
    text TEXT NOT NULL DEFAULT '',
    constraints TEXT NOT NULL DEFAULT '[]',
    source_requirement_ids TEXT NOT NULL DEFAULT '[]',
    parent_requirement_id TEXT NOT NULL DEFAULT '',
    importance TEXT NOT NULL DEFAULT '中',
    is_star INTEGER NOT NULL DEFAULT 0,
    is_scoring INTEGER NOT NULL DEFAULT 0,
    merge_method TEXT NOT NULL DEFAULT '',
    sources TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_canonical_tender ON canonical_requirements(tender_id);

CREATE TABLE IF NOT EXISTS evidences (
    id TEXT PRIMARY KEY,
    tender_id TEXT NOT NULL,
    requirement_id TEXT NOT NULL DEFAULT '',
    source_type TEXT NOT NULL,
    source_id TEXT NOT NULL,
    content TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL DEFAULT '',
    document_id TEXT NOT NULL DEFAULT '',
    section_id TEXT NOT NULL DEFAULT '',
    page INTEGER,
    section_path TEXT NOT NULL DEFAULT '',
    block_id TEXT NOT NULL DEFAULT '',
    confidence REAL NOT NULL DEFAULT 0,
    retrieval_score REAL NOT NULL DEFAULT 0,
    validation TEXT NOT NULL DEFAULT 'UNCHECKED',
    matched_text TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_evidences_requirement ON evidences(requirement_id);
CREATE INDEX IF NOT EXISTS idx_evidences_tender ON evidences(tender_id);

CREATE TABLE IF NOT EXISTS requirement_matches (
    id TEXT PRIMARY KEY,
    tender_id TEXT NOT NULL,
    requirement_id TEXT NOT NULL,
    status TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 0,
    reason TEXT NOT NULL DEFAULT '',
    method TEXT NOT NULL DEFAULT 'heuristic',
    evidence_ids TEXT NOT NULL DEFAULT '[]',
    conflicts TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_requirement_matches_tender ON requirement_matches(tender_id);
CREATE INDEX IF NOT EXISTS idx_requirement_matches_req ON requirement_matches(requirement_id);

CREATE TABLE IF NOT EXISTS matching_runs (
    tender_id TEXT PRIMARY KEY,
    status TEXT NOT NULL DEFAULT '未匹配',
    progress TEXT NOT NULL DEFAULT '',
    canonical_count INTEGER NOT NULL DEFAULT 0,
    match_count INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);

-- M3：标书模板 + 章节稿
CREATE TABLE IF NOT EXISTS outlines (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    chapters TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS drafts (
    id TEXT PRIMARY KEY,
    tender_id TEXT NOT NULL,
    chapter_id TEXT NOT NULL,
    title TEXT NOT NULL,
    content TEXT NOT NULL DEFAULT '',
    citations TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT '草稿',
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_drafts_tender ON drafts(tender_id);

-- M4：标书生成引擎（规划 + 生成 + 任务）
-- generation_jobs：生成任务（uuid 主键，一个 tender 可多次生成；镜像 matching_runs 状态机）
CREATE TABLE IF NOT EXISTS generation_jobs (
    id TEXT PRIMARY KEY,
    tender_id TEXT NOT NULL,
    outline_id TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT '未生成',
    progress TEXT NOT NULL DEFAULT '',
    section_states TEXT NOT NULL DEFAULT '{}',
    total_sections INTEGER NOT NULL DEFAULT 0,
    done_sections INTEGER NOT NULL DEFAULT 0,
    failed_sections INTEGER NOT NULL DEFAULT 0,
    error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_generation_jobs_tender ON generation_jobs(tender_id);

-- generation_sections：章节实例 = 规划 + 草稿单表（parent_id + ord 前序重组章节树）
-- status 为生成生命周期（待生成/生成中/已完成/失败/跳过），draft_status 为人工编辑
-- 生命周期（草稿/已编辑/已确认）；M4-06 富结构稿的 JSON 列装不下 drafts 老表故另立。
CREATE TABLE IF NOT EXISTS generation_sections (
    section_id TEXT PRIMARY KEY,
    generation_id TEXT NOT NULL DEFAULT '',
    tender_id TEXT NOT NULL,
    chapter_id TEXT NOT NULL DEFAULT '',
    parent_id TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL,
    section_type TEXT NOT NULL DEFAULT '方案型',
    ord INTEGER NOT NULL DEFAULT 0,          -- order 是 SQL 保留字
    level INTEGER NOT NULL DEFAULT 1,
    requirement_types TEXT NOT NULL DEFAULT '[]',
    allowed_categories TEXT NOT NULL DEFAULT '[]',
    source_refs TEXT NOT NULL DEFAULT '[]',
    coverage TEXT NOT NULL DEFAULT '[]',
    evidence_refs TEXT NOT NULL DEFAULT '[]',
    paragraphs TEXT NOT NULL DEFAULT '[]',
    warnings TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT '待生成',
    draft_status TEXT NOT NULL DEFAULT '草稿',
    attempt INTEGER NOT NULL DEFAULT 0,
    error TEXT NOT NULL DEFAULT '',
    content_md TEXT NOT NULL DEFAULT '',
    metadata TEXT NOT NULL DEFAULT '{}',
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_generation_sections_tender ON generation_sections(tender_id);
CREATE INDEX IF NOT EXISTS idx_generation_sections_job ON generation_sections(generation_id);

-- requirement_section_maps：需求→章节映射（一对多）
CREATE TABLE IF NOT EXISTS requirement_section_maps (
    tender_id TEXT NOT NULL,
    requirement_id TEXT NOT NULL,
    section_id TEXT NOT NULL,
    basis TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    PRIMARY KEY (requirement_id, section_id)
);
CREATE INDEX IF NOT EXISTS idx_req_section_map_section ON requirement_section_maps(section_id);

-- generation_logs：章节级生成日志（SSE tail / 断点诊断）
CREATE TABLE IF NOT EXISTS generation_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    generation_id TEXT NOT NULL,
    section_id TEXT NOT NULL DEFAULT '',
    level TEXT NOT NULL DEFAULT 'info',
    message TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_generation_logs_job ON generation_logs(generation_id);

-- M5：质量检查引擎（检查 + 报告 + 人工审核闭环）
-- quality_reports：一次检查的报告快照（score 为 5 维内部质量指标，非"准确率"）
CREATE TABLE IF NOT EXISTS quality_reports (
    id TEXT PRIMARY KEY,
    tender_id TEXT NOT NULL,
    document_version TEXT NOT NULL DEFAULT '',
    score REAL NOT NULL DEFAULT 0,
    dimensions TEXT NOT NULL DEFAULT '[]',
    counts TEXT NOT NULL DEFAULT '{}',
    issue_counts TEXT NOT NULL DEFAULT '{}',
    summary TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT '草稿',
    reviewer TEXT NOT NULL DEFAULT '',
    review_time TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_quality_reports_tender ON quality_reports(tender_id);

-- quality_issues：报告内的问题明细（M5-16 状态机：待处理→已确认/已忽略/已修复）
CREATE TABLE IF NOT EXISTS quality_issues (
    id TEXT PRIMARY KEY,
    report_id TEXT NOT NULL,
    tender_id TEXT NOT NULL,
    document_version TEXT NOT NULL DEFAULT '',
    section_id TEXT NOT NULL DEFAULT '',
    requirement_id TEXT NOT NULL DEFAULT '',
    issue_type TEXT NOT NULL,
    severity TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT '待处理',
    message TEXT NOT NULL DEFAULT '',
    source_refs TEXT NOT NULL DEFAULT '[]',
    suggestion TEXT NOT NULL DEFAULT '',
    autofixable INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_quality_issues_report ON quality_issues(report_id);
CREATE INDEX IF NOT EXISTS idx_quality_issues_tender ON quality_issues(tender_id);

-- review_records：人工审核留痕（问题处理 + finalize 批准审计）
CREATE TABLE IF NOT EXISTS review_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    issue_id TEXT NOT NULL,
    action TEXT NOT NULL,
    reviewer TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_review_records_issue ON review_records(issue_id);

-- ═════ M7：企业级能力（认证 / RBAC / 审计 / 版本 / 任务 / 监控）═════
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    email TEXT NOT NULL DEFAULT '',
    password_hash TEXT NOT NULL,          -- pbkdf2_sha256$600000$<salt hex>$<hash hex>
    display_name TEXT NOT NULL DEFAULT '',
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS roles (
    id TEXT PRIMARY KEY,                  -- admin / bid_manager / bid_editor / reviewer / staff
    name TEXT NOT NULL UNIQUE,            -- 管理员/投标经理/标书编辑/审核人员/普通员工
    description TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS permissions (
    id TEXT PRIMARY KEY,                  -- 形如 "project:view"（见 seed_rbac 常量）
    resource TEXT NOT NULL,
    action TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_permissions_res ON permissions(resource);

CREATE TABLE IF NOT EXISTS user_roles (
    user_id TEXT NOT NULL,
    role_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (user_id, role_id)
);
CREATE INDEX IF NOT EXISTS idx_user_roles_role ON user_roles(role_id);

CREATE TABLE IF NOT EXISTS role_permissions (
    role_id TEXT NOT NULL,
    permission_id TEXT NOT NULL,
    PRIMARY KEY (role_id, permission_id)
);

-- project_members：项目级成员（owner = 建单人自动写入；final:* 资源强制成员校验）
CREATE TABLE IF NOT EXISTS project_members (
    project_id TEXT NOT NULL,             -- tenders.id
    user_id TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'member',  -- owner / member
    created_at TEXT NOT NULL,
    PRIMARY KEY (project_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_project_members_user ON project_members(user_id);

-- audit_logs：操作审计（username 冗余快照，用户改名/删除仍可审计）
CREATE TABLE IF NOT EXISTS audit_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL DEFAULT '',
    username TEXT NOT NULL DEFAULT '',
    action TEXT NOT NULL,
    resource_type TEXT NOT NULL DEFAULT '',
    resource_id TEXT NOT NULL DEFAULT '',
    detail TEXT NOT NULL DEFAULT '',
    ip TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_logs_user ON audit_logs(user_id);
CREATE INDEX IF NOT EXISTS idx_audit_logs_res ON audit_logs(resource_type, resource_id);
CREATE INDEX IF NOT EXISTS idx_audit_logs_time ON audit_logs(created_at);

-- knowledge_versions：知识库版本（能力卡修订 / 资料重处理）
-- label = "{日期}-v{当日序}"（如 2026-08-18-v3）；生成任务快照 kb_version 引用
CREATE TABLE IF NOT EXISTS knowledge_versions (
    id TEXT PRIMARY KEY,                  -- KV-0001 顺序号
    label TEXT NOT NULL,
    material_id TEXT NOT NULL DEFAULT '',
    capability_id TEXT NOT NULL DEFAULT '',
    change_type TEXT NOT NULL DEFAULT '', -- capability_edit / material_reprocess
    summary TEXT NOT NULL DEFAULT '',     -- 改动摘要 JSON
    changed_by TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_kv_label ON knowledge_versions(label);
CREATE INDEX IF NOT EXISTS idx_kv_cap ON knowledge_versions(capability_id);

-- tasks：统一任务中心（extract/kb_process/match/generate/quality_check）
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    task_type TEXT NOT NULL,
    target_id TEXT NOT NULL DEFAULT '',   -- tender_id / material_id
    ref_id TEXT NOT NULL DEFAULT '',      -- generate = generation_jobs.id
    status TEXT NOT NULL DEFAULT 'pending',  -- pending/running/success/failed/cancelled
    progress TEXT NOT NULL DEFAULT '',
    progress_pct INTEGER NOT NULL DEFAULT 0,
    total INTEGER NOT NULL DEFAULT 0,
    done INTEGER NOT NULL DEFAULT 0,
    error TEXT NOT NULL DEFAULT '',
    started_by TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    started_at TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_type ON tasks(task_type);

-- llm_calls：LLM 调用指标（model/tokens/耗时/失败原因；Mock 客户端不记录）
CREATE TABLE IF NOT EXISTS llm_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    caller TEXT NOT NULL DEFAULT '',      -- extraction/kb_extract/llm_judge/generator/quality_judge
    model TEXT NOT NULL DEFAULT '',
    prompt_tokens INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    total_tokens INTEGER NOT NULL DEFAULT 0,
    duration_ms INTEGER NOT NULL DEFAULT 0,
    retries INTEGER NOT NULL DEFAULT 0,
    success INTEGER NOT NULL DEFAULT 1,
    finish_reason TEXT NOT NULL DEFAULT '',
    error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_llm_calls_time ON llm_calls(created_at);

-- agent_traces / agent_spans：Agent 链路（用户请求→需求分析→知识检索→生成章节→质量检查）
CREATE TABLE IF NOT EXISTS agent_traces (
    id TEXT PRIMARY KEY,
    task_type TEXT NOT NULL,              -- extract/kb_process/match/generate/quality_check
    target_id TEXT NOT NULL DEFAULT '',
    user_id TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'running',  -- running/success/failed
    started_at TEXT NOT NULL,
    finished_at TEXT NOT NULL DEFAULT '',
    error TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_traces_type ON agent_traces(task_type);

CREATE TABLE IF NOT EXISTS agent_spans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trace_id TEXT NOT NULL,
    stage TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'running',  -- running/success/failed
    detail TEXT NOT NULL DEFAULT '',
    started_at TEXT NOT NULL,
    finished_at TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_spans_trace ON agent_spans(trace_id);
"""


# ═══════════════════════════════════════════════════════════════════════
# M7：RBAC 种子（5 角色 + 17 权限 + 矩阵 + 初始用户）
# ═══════════════════════════════════════════════════════════════════════
# 权限枚举：resource:action —— 资源 = 项目(招标文件/企业知识库/标书/质量报告/最终版本)
M7_PERMISSIONS = [
    ("project:view", "project", "view", "查看项目（列表/详情/需求）"),
    ("project:edit", "project", "edit", "修订需求/启动匹配等项目中台操作"),
    ("project:manage", "project", "manage", "项目成员管理"),
    ("tender_doc:view", "tender_doc", "view", "查看招标文件"),
    ("tender_doc:upload", "tender_doc", "upload", "上传招标文件/触发提取"),
    ("knowledge:view", "knowledge", "view", "查看企业知识库"),
    ("knowledge:upload", "knowledge", "upload", "上传/处理知识库资料"),
    ("knowledge:edit", "knowledge", "edit", "修改/删除能力卡与资料"),
    ("bid:view", "bid", "view", "查看章节/大纲/响应表/日志"),
    ("bid:edit", "bid", "edit", "编辑章节"),
    ("bid:generate", "bid", "generate", "生成标书（大纲+启动任务）"),
    ("bid:regenerate", "bid", "regenerate", "单章节重新生成"),
    ("quality:view", "quality", "view", "查看质量报告/问题"),
    ("quality:check", "quality", "check", "执行质量检查"),
    ("quality:confirm", "quality", "confirm", "确认/忽略/修复问题、终版批准"),
    ("final:view", "final", "view", "查看最终版本（需项目成员）"),
    ("final:export", "final", "export", "导出终版 docx（需项目成员）"),
]

_ALL_PERMS = [p[0] for p in M7_PERMISSIONS]

# 5 角色默认权限矩阵（admin 全量；普通员工仅 final:*，且需项目成员——
# 体现"不同角色看到不同内容"验收点）
M7_ROLE_PERMISSIONS = {
    "admin": _ALL_PERMS,
    "bid_manager": [
        "project:view", "project:edit", "project:manage",
        "tender_doc:view", "tender_doc:upload",
        "knowledge:view", "knowledge:upload", "knowledge:edit",
        "bid:view", "bid:edit", "bid:generate", "bid:regenerate",
        "quality:view", "quality:check",
        "final:view", "final:export",
    ],
    "bid_editor": [
        "project:view", "project:edit",
        "tender_doc:view",
        "knowledge:view",
        "bid:view", "bid:edit", "bid:generate", "bid:regenerate",
        "quality:view",
        "final:view", "final:export",
    ],
    "reviewer": [
        "project:view", "project:edit",
        "tender_doc:view",
        "knowledge:view",
        "bid:view",
        "quality:view", "quality:check", "quality:confirm",
        "final:view", "final:export",
    ],
    "staff": ["final:view", "final:export"],
}


def seed_rbac(db: "Database") -> None:
    """M7 RBAC 种子（幂等）：roles 空才插角色/权限/矩阵；users 空才建初始用户。

    初始用户：admin（口令 config.ADMIN_PASSWORD，默认 admin123）+ 4 演示用户
    （manager/editor/reviewer/staff，口令 同名+123，README 记录）。
    lifespan 在 init_schema 后调用。
    """
    if not db.query_one("SELECT 1 AS x FROM roles LIMIT 1"):
        roles = [
            ("admin", "管理员", "系统管理员：全部权限"),
            ("bid_manager", "投标经理", "项目全流程管理：招标文件/知识库/生成/成员"),
            ("bid_editor", "标书编辑", "标书编写：查看/编辑/生成章节"),
            ("reviewer", "审核人员", "质量审核：查看报告/确认问题/终版批准"),
            ("staff", "普通员工", "仅查看最终交付版本（需为项目成员）"),
        ]
        for rid, name, desc in roles:
            db.insert("roles", {"id": rid, "name": name, "description": desc})
        for pid, resource, action, desc in M7_PERMISSIONS:
            db.insert("permissions",
                      {"id": pid, "resource": resource, "action": action,
                       "description": desc})
        for rid, perms in M7_ROLE_PERMISSIONS.items():
            for pid in perms:
                db.insert("role_permissions",
                          {"role_id": rid, "permission_id": pid})

    if not db.query_one("SELECT 1 AS x FROM users LIMIT 1"):
        from .auth.security import hash_password  # 惰性导入防环

        demo = [
            ("U-ADMIN", config.ADMIN_USERNAME, "管理员", "admin", config.ADMIN_PASSWORD),
            ("U-MANAGER", "manager", "投标经理", "bid_manager", "manager123"),
            ("U-EDITOR", "editor", "标书编辑", "bid_editor", "editor123"),
            ("U-REVIEWER", "reviewer", "审核人员", "reviewer", "reviewer123"),
            ("U-STAFF", "staff", "普通员工", "staff", "staff123"),
        ]
        for uid, uname, dname, rid, pwd in demo:
            db.insert("users", {
                "id": uid, "username": uname, "email": "",
                "password_hash": hash_password(pwd),
                "display_name": dname, "is_active": 1,
                "created_at": now_str(), "updated_at": now_str(),
            })
            db.insert("user_roles",
                      {"user_id": uid, "role_id": rid, "created_at": now_str()})


def get_db() -> Database:
    """获取 Database 实例（每次操作仍独立连接）。

    惰性导入 Database：db.py 底部 re-export 本模块，模块级互相导入会成环。
    """
    from .db import Database  # noqa: F401  （惰性，避免 db ⇄ db_schema 循环导入）

    return Database(config.DB_PATH)

