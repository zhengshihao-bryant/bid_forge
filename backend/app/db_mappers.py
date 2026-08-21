# -*- coding: utf-8 -*-
"""
app/db_mappers.py —— ORM 映射层（schema ↔ Pydantic 模型双向转换）

从 app/db.py 拆分：全部 *to_row / row_to_* 静态方法集中在 MappersMixin，
Database(MappersMixin) 继承后对外 API 不变（Database.row_to_x / db.row_to_x 均可用）。
"""

from __future__ import annotations

import json
from typing import Optional

from .schemas import Requirement, SourceAnchor, now_str


class MappersMixin:
    """行 ↔ 模型映射（静态方法，供 Database 继承）。"""
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
