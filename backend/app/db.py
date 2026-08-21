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
            self._migrate(conn)
            conn.commit()

    @staticmethod
    def _migrate(conn: sqlite3.Connection) -> None:
        """幂等 ALTER（M7 起）：PRAGMA table_info 检查列存在再加列。

        项目无迁移框架（M1–M6 均只追加新表）；M7 给已有表加列用此
        检查法（比依赖异常驱动的判断可靠）。
        """
        def add_col(table: str, column: str, ddl: str) -> None:
            cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
            if column not in cols:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")

        add_col("tenders", "owner_id", "owner_id TEXT NOT NULL DEFAULT ''")
        add_col("capabilities", "version", "version INTEGER NOT NULL DEFAULT 1")
        add_col("capabilities", "updated_at", "updated_at TEXT NOT NULL DEFAULT ''")
        add_col("generation_jobs", "kb_version", "kb_version TEXT NOT NULL DEFAULT ''")

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
            "version": getattr(cap, "version", 1) or 1,
            "updated_at": getattr(cap, "updated_at", "") or "",
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
            version=row.get("version") or 1,
            updated_at=row.get("updated_at") or "",
            created_at=row.get("created_at") or now_str(),
        )

    # ------------------------------------------------------------------
    # M3：CanonicalRequirement / Evidence / MatchResult ↔ 行 映射
    # ------------------------------------------------------------------
    @staticmethod
    def canonical_to_row(c: "CanonicalRequirement") -> dict:
        return {
            "id": c.id,
            "tender_id": c.tender_id,
            "req_type": c.req_type.value,
            "title": c.title,
            "text": c.text,
            "constraints": json.dumps(
                [x.model_dump(mode="json") for x in c.constraints], ensure_ascii=False),
            "source_requirement_ids": json.dumps(c.source_requirement_ids, ensure_ascii=False),
            "parent_requirement_id": c.parent_requirement_id,
            "importance": c.importance,
            "is_star": int(c.is_star),
            "is_scoring": int(c.is_scoring),
            "merge_method": c.merge_method,
            "sources": json.dumps(
                [s.model_dump(mode="json") for s in c.sources], ensure_ascii=False),
            "created_at": c.created_at,
        }

    @staticmethod
    def row_to_canonical(row: dict) -> "CanonicalRequirement":
        from .services.matching.models import (CanonicalRequirement, Constraint,
                                               RequirementSourceRef, RequirementTypeM3)

        return CanonicalRequirement(
            id=row["id"],
            tender_id=row["tender_id"],
            req_type=RequirementTypeM3(row.get("req_type") or "OTHER"),
            title=row.get("title") or "",
            text=row.get("text") or "",
            constraints=[Constraint(**x) for x in json.loads(row.get("constraints") or "[]")],
            source_requirement_ids=json.loads(row.get("source_requirement_ids") or "[]"),
            parent_requirement_id=row.get("parent_requirement_id") or "",
            importance=row.get("importance") or "中",
            is_star=bool(row.get("is_star")),
            is_scoring=bool(row.get("is_scoring")),
            merge_method=row.get("merge_method") or "",
            sources=[RequirementSourceRef(**s) for s in json.loads(row.get("sources") or "[]")],
            created_at=row.get("created_at") or now_str(),
        )

    @staticmethod
    def evidence_to_row(e: "Evidence") -> dict:
        return {
            "id": e.evidence_id,
            "tender_id": e.tender_id,
            "requirement_id": e.requirement_id,
            "source_type": e.source_type.value,
            "source_id": e.source_id,
            "content": e.content,
            "category": e.category,
            "document_id": e.document_id,
            "section_id": e.section_id,
            "page": e.page,
            "section_path": e.section_path,
            "block_id": e.block_id,
            "confidence": e.confidence,
            "retrieval_score": e.retrieval_score,
            "validation": e.validation.value,
            "matched_text": e.matched_text,
            "created_at": e.created_at,
        }

    @staticmethod
    def row_to_evidence(row: dict) -> "Evidence":
        from .services.matching.models import (Evidence, EvidenceSourceType,
                                               EvidenceValidation)

        return Evidence(
            evidence_id=row["id"],
            tender_id=row.get("tender_id") or "",
            requirement_id=row.get("requirement_id") or "",
            source_type=EvidenceSourceType(row["source_type"]),
            source_id=row.get("source_id") or "",
            content=row.get("content") or "",
            category=row.get("category") or "",
            document_id=row.get("document_id") or "",
            section_id=row.get("section_id") or "",
            page=row.get("page"),
            section_path=row.get("section_path") or "",
            block_id=row.get("block_id") or "",
            confidence=row.get("confidence") or 0.0,
            retrieval_score=row.get("retrieval_score") or 0.0,
            validation=EvidenceValidation(row.get("validation") or "UNCHECKED"),
            matched_text=row.get("matched_text") or "",
            created_at=row.get("created_at") or now_str(),
        )

    @staticmethod
    def match_to_row(m: "MatchResult") -> dict:
        return {
            "id": m.id,
            "tender_id": m.tender_id,
            "requirement_id": m.requirement_id,
            "status": m.status.value,
            "confidence": m.confidence,
            "reason": m.reason,
            "method": m.method.value,
            "evidence_ids": json.dumps(m.evidence_ids, ensure_ascii=False),
            "conflicts": json.dumps(
                [c.model_dump(mode="json") for c in m.conflicts], ensure_ascii=False),
            "created_at": m.created_at,
        }

    @staticmethod
    def row_to_match(row: dict) -> "MatchResult":
        from .services.matching.models import Conflict, MatchMethod, MatchResult, MatchStatus

        return MatchResult(
            id=row["id"],
            tender_id=row["tender_id"],
            requirement_id=row["requirement_id"],
            status=MatchStatus(row["status"]),
            confidence=row.get("confidence") or 0.0,
            reason=row.get("reason") or "",
            method=MatchMethod(row.get("method") or "heuristic"),
            evidence_ids=json.loads(row.get("evidence_ids") or "[]"),
            conflicts=[Conflict(**c) for c in json.loads(row.get("conflicts") or "[]")],
            created_at=row.get("created_at") or now_str(),
        )


    # ------------------------------------------------------------------
    # M4：Outline / SectionDraft / BidSection / GenerationJob ↔ 行 映射
    # generation_sections 单表 = 规划 + 草稿：写入用 planning_to_row（插入）/
    # draft_to_row（更新），读出用 row_to_bid_section（规划）/ row_to_section（草稿）
    # ------------------------------------------------------------------
    @staticmethod
    def outline_to_row(o: "OutlineTemplate") -> dict:
        return {
            "id": o.id, "name": o.name, "description": o.description,
            "chapters": json.dumps(o.chapters, ensure_ascii=False,
                                   default=lambda x: x.model_dump(mode="json")),
            "created_at": o.created_at,
        }

    @staticmethod
    def row_to_outline(row: dict) -> "OutlineTemplate":
        from .schemas import ChapterSpec, OutlineTemplate

        chapters = json.loads(row.get("chapters") or "[]")
        return OutlineTemplate(
            id=row["id"], name=row.get("name") or "通用标书结构",
            description=row.get("description") or "",
            chapters=[ChapterSpec(**c) for c in chapters],
            created_at=row.get("created_at") or now_str(),
        )

    @staticmethod
    def planning_to_row(s: "BidSection", generation_id: str = "",
                        tender_id: str = "") -> dict:
        """章节规划 → generation_sections 全行（草稿列为空）。"""
        return {
            "section_id": s.id, "generation_id": generation_id,
            "tender_id": tender_id or s.tender_id,
            "chapter_id": getattr(s, "chapter_id", "") or "",
            "parent_id": s.parent_id, "title": s.title,
            "section_type": s.section_type.value,
            "ord": s.ord, "level": s.level,
            "requirement_types": json.dumps(s.requirement_types, ensure_ascii=False),
            "allowed_categories": json.dumps(s.allowed_categories, ensure_ascii=False),
            "source_refs": json.dumps(s.source_refs, ensure_ascii=False),
            "coverage": "[]", "evidence_refs": "[]", "paragraphs": "[]",
            "warnings": "[]", "status": s.status.value,
            "draft_status": "草稿", "attempt": 0, "error": "",
            "content_md": "", "metadata": "{}", "version": 1,
            "created_at": now_str(), "updated_at": now_str(),
        }

    @staticmethod
    def draft_to_row(d: "SectionDraft") -> dict:
        """章节草稿 → generation_sections 草稿列（用于 update，不含规划列）。"""
        return {
            "generation_id": d.generation_id, "title": d.title,
            "section_type": d.section_type.value,
            "coverage": json.dumps(
                [c.model_dump(mode="json") for c in d.requirement_coverage],
                ensure_ascii=False),
            "evidence_refs": json.dumps(
                [e.model_dump(mode="json") for e in d.evidence_refs],
                ensure_ascii=False),
            "paragraphs": json.dumps(
                [p.model_dump(mode="json") for p in d.paragraphs],
                ensure_ascii=False),
            "warnings": json.dumps(d.warnings, ensure_ascii=False),
            "draft_status": d.status.value,
            "content_md": d.content_md,
            "metadata": json.dumps(d.generation_metadata, ensure_ascii=False),
            "version": d.version, "updated_at": now_str(),
        }

    @staticmethod
    def row_to_bid_section(row: dict) -> "BidSection":
        from .services.generation.models import BidSection, SectionStatus, SectionType

        return BidSection(
            id=row["section_id"], tender_id=row["tender_id"],
            parent_id=row.get("parent_id") or "",
            title=row["title"], level=row.get("level") or 1,
            ord=row.get("ord") or 0,
            section_type=SectionType(row.get("section_type") or "方案型"),
            source_refs=json.loads(row.get("source_refs") or "[]"),
            requirement_types=json.loads(row.get("requirement_types") or "[]"),
            allowed_categories=json.loads(row.get("allowed_categories") or "[]"),
            generation_prompt="",
            status=SectionStatus(row.get("status") or "待生成"),
        )

    @staticmethod
    def row_to_section(row: dict) -> "SectionDraft":
        from .schemas import DraftStatus
        from .services.generation.models import (CoverageItem, EvidenceRef,
                                                 Paragraph, SectionDraft,
                                                 SectionType)

        return SectionDraft(
            section_id=row["section_id"], tender_id=row["tender_id"],
            generation_id=row.get("generation_id") or "",
            title=row["title"],
            section_type=SectionType(row.get("section_type") or "方案型"),
            paragraphs=[Paragraph(**p) for p in json.loads(row.get("paragraphs") or "[]")],
            requirement_coverage=[CoverageItem(**c) for c in
                                  json.loads(row.get("coverage") or "[]")],
            evidence_refs=[EvidenceRef(**e) for e in
                           json.loads(row.get("evidence_refs") or "[]")],
            warnings=json.loads(row.get("warnings") or "[]"),
            status=DraftStatus(row.get("draft_status") or "草稿"),
            content_md=row.get("content_md") or "",
            generation_metadata=json.loads(row.get("metadata") or "{}"),
            version=row.get("version") or 1,
            created_at=row.get("created_at") or now_str(),
            updated_at=row.get("updated_at") or now_str(),
        )

    @staticmethod
    def job_to_row(j: "GenerationJob") -> dict:
        return {
            "id": j.id, "tender_id": j.tender_id, "outline_id": j.outline_id,
            "status": j.status, "progress": j.progress,
            "section_states": json.dumps(j.section_states, ensure_ascii=False),
            "total_sections": j.total_sections, "done_sections": j.done_sections,
            "failed_sections": j.failed_sections, "error": j.error,
            "kb_version": getattr(j, "kb_version", "") or "",
            "created_at": j.created_at, "updated_at": j.updated_at,
        }

    @staticmethod
    def row_to_job(row: dict) -> "GenerationJob":
        from .services.generation.models import GenerationJob

        return GenerationJob(
            id=row["id"], tender_id=row["tender_id"],
            outline_id=row.get("outline_id") or "",
            status=row.get("status") or "未生成",
            progress=row.get("progress") or "",
            section_states=json.loads(row.get("section_states") or "{}"),
            total_sections=row.get("total_sections") or 0,
            done_sections=row.get("done_sections") or 0,
            failed_sections=row.get("failed_sections") or 0,
            error=row.get("error") or "",
            kb_version=row.get("kb_version") or "",
            created_at=row.get("created_at") or now_str(),
            updated_at=row.get("updated_at") or now_str(),
        )

    # ── M5 质量检查 ─────────────────────────────────────────────────────
    @staticmethod
    def report_to_row(r: "QualityReport") -> dict:
        """质量报告 → quality_reports 行（JSON 列 ensure_ascii=False 保中文）。"""
        return {
            "id": r.id, "tender_id": r.tender_id,
            "document_version": r.document_version, "score": r.score,
            "dimensions": json.dumps(
                [d.model_dump(mode="json") for d in r.dimensions],
                ensure_ascii=False),
            "counts": json.dumps(r.counts, ensure_ascii=False),
            "issue_counts": json.dumps(r.issue_counts, ensure_ascii=False),
            "summary": r.summary, "status": r.status,
            "reviewer": r.reviewer, "review_time": r.review_time,
            "created_at": r.created_at,
        }

    @staticmethod
    def row_to_report(row: dict) -> "QualityReport":
        from .services.quality.models import DimensionScore, QualityReport

        return QualityReport(
            id=row["id"], tender_id=row["tender_id"],
            document_version=row.get("document_version") or "",
            score=row.get("score") or 0.0,
            dimensions=[DimensionScore(**d) for d in
                        json.loads(row.get("dimensions") or "[]")],
            counts=json.loads(row.get("counts") or "{}"),
            issue_counts=json.loads(row.get("issue_counts") or "{}"),
            summary=row.get("summary") or "",
            status=row.get("status") or "草稿",
            reviewer=row.get("reviewer") or "",
            review_time=row.get("review_time") or "",
            created_at=row.get("created_at") or now_str(),
        )

    @staticmethod
    def issue_to_row(i: "QualityIssue") -> dict:
        """问题 → quality_issues 行。"""
        return {
            "id": i.id, "report_id": i.report_id, "tender_id": i.tender_id,
            "document_version": i.document_version,
            "section_id": i.section_id, "requirement_id": i.requirement_id,
            "issue_type": i.issue_type.value, "severity": i.severity.value,
            "status": i.status.value, "message": i.message,
            "source_refs": json.dumps(i.source_refs, ensure_ascii=False),
            "suggestion": i.suggestion,
            "autofixable": 1 if i.autofixable else 0,
            "created_at": i.created_at,
        }

    @staticmethod
    def row_to_issue(row: dict) -> "QualityIssue":
        from .services.quality.models import (IssueStatus, IssueType,
                                              QualityIssue, Severity)

        return QualityIssue(
            id=row["id"], report_id=row["report_id"],
            tender_id=row["tender_id"],
            document_version=row.get("document_version") or "",
            section_id=row.get("section_id") or "",
            requirement_id=row.get("requirement_id") or "",
            issue_type=IssueType(row.get("issue_type") or IssueType.FORMAT_ERROR),
            severity=Severity(row.get("severity") or "WARNING"),
            status=IssueStatus(row.get("status") or "待处理"),
            message=row.get("message") or "",
            source_refs=json.loads(row.get("source_refs") or "[]"),
            suggestion=row.get("suggestion") or "",
            autofixable=bool(row.get("autofixable")),
            created_at=row.get("created_at") or now_str(),
        )

    @staticmethod
    def review_to_row(rv: "ReviewRecord") -> dict:
        """审核留痕 → review_records 行（不含自增 id）。"""
        return {
            "issue_id": rv.issue_id, "action": rv.action,
            "reviewer": rv.reviewer, "note": rv.note,
            "created_at": rv.created_at,
        }

    @staticmethod
    def row_to_review(row: dict) -> "ReviewRecord":
        from .services.quality.models import ReviewRecord

        return ReviewRecord(
            id=row.get("id") or 0, issue_id=row.get("issue_id") or "",
            action=row.get("action") or "",
            reviewer=row.get("reviewer") or "",
            note=row.get("note") or "",
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
    """获取 Database 实例（每次操作仍独立连接）。"""
    return Database(config.DB_PATH)
