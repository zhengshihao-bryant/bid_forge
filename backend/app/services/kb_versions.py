# -*- coding: utf-8 -*-
"""
app/services/kb_versions.py —— M7-04 知识库版本管理（knowledge_versions 写通）

版本号约定：
- id    ：KV-{全局计数:04d}（如 KV-0001）
- label ：{今日}-v{当日序}（如 2026-08-18-v3）——当日第 n 次版本变更；
  生成任务快照（generation_jobs.kb_version）记最新 label，追溯链：
  标书 → generation_jobs.kb_version → knowledge_versions → capabilities
  .source_doc/source_page（章节级 EVD→材料→页码链 M3/M4 已有）。

写侧两个入口：能力卡人工修订（capability_edit）、资料重处理
（material_reprocess）；读侧 GET /api/knowledge/versions。
"""

from __future__ import annotations

from ..db import Database
from ..schemas import now_str


def next_version_id(db: Database) -> str:
    """KV-{n:04d} 全局顺序号。"""
    row = db.query_one("SELECT COUNT(*) AS n FROM knowledge_versions", ())
    return f"KV-{((row or {}).get('n') or 0) + 1:04d}"


def next_label(db: Database, day: str = "") -> str:
    """{今日}-v{当日序}：当天第 n 次版本变更。"""
    day = day or now_str()[:10]
    n = db.query_one(
        "SELECT COUNT(*) AS n FROM knowledge_versions WHERE label LIKE ?",
        (f"{day}-%",))
    return f"{day}-v{((n or {}).get('n') or 0) + 1}"


def record_version(db: Database, change_type: str, changed_by: str = "",
                   capability_id: str = "", material_id: str = "",
                   summary: str = "") -> dict:
    """写一条版本记录（id + label 自动计算），返回行 dict。"""
    row = {
        "id": next_version_id(db),
        "label": next_label(db),
        "material_id": material_id,
        "capability_id": capability_id,
        "change_type": change_type,
        "summary": summary,
        "changed_by": changed_by,
        "created_at": now_str(),
    }
    db.insert("knowledge_versions", row)
    return row


def latest_kb_label(db: Database) -> str:
    """最新版本 label（生成任务快照用；无任何版本时 "v0"）。"""
    row = db.query_one(
        "SELECT label FROM knowledge_versions ORDER BY created_at DESC, id DESC LIMIT 1")
    return (row or {}).get("label") or "v0"
