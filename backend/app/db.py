# -*- coding: utf-8 -*-
"""
app/db.py —— SQLite 存储层

线程安全策略（技术校验结论）：
- **每操作独立连接**：connect() 每次新建连接，执行完即关 ——
  后台任务与请求线程各自持有自己的连接，杜绝共享连接跨线程并发损坏
- 勿用 check_same_thread=False（只是移除守卫，共享连接跨线程并发仍有实证损坏案例）
- PRAGMA journal_mode=WAL + busy_timeout=5000：读写并发不互锁

表清单（M2/M3 表现在预建，向前兼容）：
  M1：tenders / documents / requirements / score_points
  M2：kb_materials / kb_chunks（M2 新增）/ capabilities / matches（matches 实际 M3 使用）
  M3：outlines / drafts

requirements 行与 Requirement 模型的映射在 requirement_to_row / row_to_requirement，
保持 schema ↔ 持久化单一出处。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Optional

from . import config
from .schemas import Requirement, SourceAnchor, now_str


class Database:
    """SQLite 封装：每次操作独立连接（见模块 docstring）。"""

    def __init__(self, path: Path | str | None = None):
        self._path = str(path or config.DB_PATH)

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    # ------------------------------------------------------------------
    # 基础操作
    # ------------------------------------------------------------------
    def execute(self, sql: str, params: tuple = ()) -> int:
        """执行写语句，返回 lastrowid。"""
        with self.connect() as conn:
            cur = conn.execute(sql, params)
            conn.commit()
            return cur.lastrowid or 0

    def executemany(self, sql: str, rows: list[tuple]) -> None:
        with self.connect() as conn:
            conn.executemany(sql, rows)
            conn.commit()

    def query(self, sql: str, params: tuple = ()) -> list[dict]:
        with self.connect() as conn:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]

    def query_one(self, sql: str, params: tuple = ()) -> Optional[dict]:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def insert(self, table: str, row: dict) -> None:
        cols = ", ".join(row.keys())
        marks = ", ".join("?" for _ in row)
        self.execute(f"INSERT INTO {table} ({cols}) VALUES ({marks})", tuple(row.values()))

    def update(self, table: str, id_col: str, row_id: str, values: dict) -> int:
        """按主键更新，返回受影响行数。"""
        if not values:
            return 0
        sets = ", ".join(f"{k} = ?" for k in values)
        with self.connect() as conn:
            cur = conn.execute(
                f"UPDATE {table} SET {sets} WHERE {id_col} = ?",
                (*values.values(), row_id),
            )
            conn.commit()
            return cur.rowcount

    # ------------------------------------------------------------------
    # 建表
    # ------------------------------------------------------------------
    def init_schema(self) -> None:
        with self.connect() as conn:
            conn.executescript(_DDL)
            conn.commit()

    # ------------------------------------------------------------------
    # Requirement ↔ 行 映射（schema ↔ 持久化单一出处）
    # ------------------------------------------------------------------
    @staticmethod
    def requirement_to_row(req: Requirement) -> dict:
        src = req.source or SourceAnchor()
        return {
            "id": req.id,
            "tender_id": req.tender_id,
            "type": req.type.value,
            "title": req.title,
            "original_text": req.original_text,
            "quantitative": json.dumps([q.model_dump() for q in req.quantitative], ensure_ascii=False),
            "importance": req.importance,
            "is_star": int(req.is_star),
            "source_document": src.document,
            "source_doc_id": src.doc_id,
            "source_page": src.page,
            "source_section_path": src.section_path,
            "source_block_id": src.block_id,
            "source_snippet": src.snippet,
            "status": req.status,
            "response": req.response,
            "human_confirmed": int(req.human_confirmed),
            "created_at": req.created_at,
            "updated_at": req.updated_at,
        }

    @staticmethod
    def row_to_requirement(row: dict) -> Requirement:
        from .schemas import RequirementType

        return Requirement(
            id=row["id"],
            tender_id=row["tender_id"],
            type=RequirementType(row["type"]),
            title=row["title"],
            original_text=row["original_text"],
            quantitative=json.loads(row.get("quantitative") or "[]"),
            importance=row.get("importance") or "中",
            is_star=bool(row.get("is_star")),
            source=SourceAnchor(
                document=row.get("source_document") or "",
                doc_id=row.get("source_doc_id") or "",
                page=row.get("source_page"),
                section_path=row.get("source_section_path") or "",
                block_id=row.get("source_block_id") or "",
                snippet=row.get("source_snippet") or "",
            ),
            status=row.get("status") or "待响应",
            response=row.get("response") or "",
            human_confirmed=bool(row.get("human_confirmed")),
            created_at=row.get("created_at") or now_str(),
            updated_at=row.get("updated_at") or now_str(),
        )

    # ------------------------------------------------------------------
    # KbMaterial / KbChunk ↔ 行 映射（schema ↔ 持久化单一出处）
    # ------------------------------------------------------------------
    @staticmethod
    def material_to_row(m: "KbMaterial") -> dict:
        return {
            "id": m.id,
            "category": m.category.value,
            "file_name": m.file_name,
            "stored_name": m.stored_name,
            "file_type": m.file_type,
            "total_pages": m.total_pages,
            "char_count": m.char_count,
            "ocr_pages": json.dumps(m.ocr_pages, ensure_ascii=False),
            "raw_hash": m.raw_hash,
            "parser_version": m.parser_version,
            "parse_error": m.parse_error,
            "parsed_file": m.parsed_file,
            "process_status": m.process_status.value,
            "process_progress": m.process_progress,
            "chunk_count": m.chunk_count,
            "capability_count": m.capability_count,
            "index_status": m.index_status,
            "created_at": m.created_at,
        }

    @staticmethod
    def row_to_material(row: dict) -> "KbMaterial":
        from .schemas import CapabilityCategory, KbMaterial, KbProcessStatus

        return KbMaterial(
            id=row["id"],
            category=CapabilityCategory(row["category"]),
            file_name=row["file_name"],
            stored_name=row.get("stored_name") or "",
            file_type=row.get("file_type") or "",
            total_pages=row.get("total_pages") or 0,
            char_count=row.get("char_count") or 0,
            ocr_pages=json.loads(row.get("ocr_pages") or "[]"),
            raw_hash=row.get("raw_hash") or "",
            parser_version=row.get("parser_version") or "",
            parse_error=row.get("parse_error") or "",
            parsed_file=row.get("parsed_file") or "",
            process_status=KbProcessStatus(row.get("process_status") or "未处理"),
            process_progress=row.get("process_progress") or "",
            chunk_count=row.get("chunk_count") or 0,
            capability_count=row.get("capability_count") or 0,
            index_status=row.get("index_status") or "none",
            created_at=row.get("created_at") or now_str(),
        )

    @staticmethod
    def chunk_to_row(c: "KbChunk", embedding: Optional[list] = None) -> dict:
        return {
            "id": c.id,
            "material_id": c.material_id,
            "category": c.category.value,
            "file_name": c.file_name,
            "file_type": getattr(c, "file_type", "") or "",
            "content": c.content,
            "section_path": c.section_path,
            "page_start": c.page_start,
            "page_end": c.page_end,
            "block_ids": json.dumps(c.block_ids, ensure_ascii=False),
            "embedding": json.dumps(embedding or [], ensure_ascii=False),
            "seq": c.seq,
            "created_at": c.created_at,
        }

    @staticmethod
    def row_to_chunk(row: dict) -> "KbChunk":
        from .schemas import CapabilityCategory, KbChunk

        return KbChunk(
            id=row["id"],
            material_id=row["material_id"],
            category=CapabilityCategory(row["category"]),
            file_name=row.get("file_name") or "",
            content=row["content"],
            section_path=row.get("section_path") or "",
            page_start=row.get("page_start"),
            page_end=row.get("page_end"),
            block_ids=json.loads(row.get("block_ids") or "[]"),
            seq=row.get("seq") or 0,
            created_at=row.get("created_at") or now_str(),
        )

    # ------------------------------------------------------------------
    # Capability ↔ 行 映射（M1 预建表，M2 起写入）
    # ------------------------------------------------------------------
    @staticmethod
    def capability_to_row(cap: "Capability") -> dict:
        return {
            "id": cap.id,
            "category": cap.category.value,
            "name": cap.name,
            "attributes": json.dumps(cap.attributes, ensure_ascii=False),
            "description": cap.description,
            "source_doc": cap.source_doc,
            "source_page": cap.source_page,
            "created_at": cap.created_at,
        }

    @staticmethod
    def row_to_capability(row: dict) -> "Capability":
        from .schemas import Capability, CapabilityCategory

        return Capability(
            id=row["id"],
            category=CapabilityCategory(row["category"]),
            name=row["name"],
            attributes=json.loads(row.get("attributes") or "{}"),
            description=row.get("description") or "",
            source_doc=row.get("source_doc") or "",
            source_page=row.get("source_page"),
            created_at=row.get("created_at") or now_str(),
        )


# ═══════════════════════════════════════════════════════════════════════
# DDL（M2/M3 表预建，向前兼容）
# ═══════════════════════════════════════════════════════════════════════
_DDL = """
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
"""


def get_db() -> Database:
    """获取 Database 实例（每次操作仍独立连接）。"""
    return Database(config.DB_PATH)
