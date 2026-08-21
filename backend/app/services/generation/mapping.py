# -*- coding: utf-8 -*-
"""
generation/mapping.py —— M4-02 需求→章节映射（确定性，非 LLM）

类型桥接（关键）：CanonicalRequirement.sources[].type 由 M1 写入 RequirementType.value
（中文串），ChapterSpec.requirement_types 同样声明 M1 中文串 —— 直接做集合交集，
无需 M1↔M3 枚举互转。sources 为空的规范需求用 req_type 兜底表。

一对多：一条需求可映射到所有声明了对应类型的章节（技术→CH-05-2/3/4/5）。
映射结果落 requirement_section_maps（幂等：重跑先清表，镜像 matcher）。
覆盖统计供 /coverage 端点与 M4-11 验收：total == mapped + unmapped。
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from ... import config
from ...db import Database
from ...schemas import now_str
from ..matching.models import CanonicalRequirement, RequirementTypeM3
from .models import CoverageStats

logger = logging.getLogger(__name__)

# M3 需求类型（英文枚举）→ M1 中文类型兜底（仅 sources 为空的规范需求）
_M3_TO_M1_FALLBACK: dict[RequirementTypeM3, str] = {
    RequirementTypeM3.QUALIFICATION: "资质要求",
    RequirementTypeM3.PERSONNEL: "人员要求",
    RequirementTypeM3.PROJECT_EXPERIENCE: "商务要求",
    RequirementTypeM3.PRODUCT_CAPABILITY: "技术要求",
    RequirementTypeM3.TECHNICAL: "技术要求",
    RequirementTypeM3.IMPLEMENTATION: "实施要求",
    RequirementTypeM3.SERVICE: "售后服务",
    RequirementTypeM3.COMMERCIAL: "商务要求",
    RequirementTypeM3.DOCUMENT: "投标文件格式",
    RequirementTypeM3.OTHER: "项目背景",
}


class RequirementSectionMapper:
    """需求→章节映射（M4-02）。"""

    def __init__(self, db: Optional[Database] = None):
        self.db = db or Database(config.DB_PATH)

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------
    def map_all(self, tender_id: str) -> CoverageStats:
        """全部非评分规范需求 → 章节映射并落库，返回覆盖统计（幂等）。"""
        sections = self._flat_sections(tender_id)
        reqs = self._canonical_reqs(tender_id)
        # 章节类型集合预计算
        sec_types = [(s["section_id"], set(json.loads(s["requirement_types"] or "[]")))
                     for s in sections]

        self.db.execute("DELETE FROM requirement_section_maps WHERE tender_id = ?",
                        (tender_id,))
        now = now_str()
        mapped_ids: set[str] = set()
        for c in reqs:
            if c.is_scoring:
                continue
            m1_types, basis = self._req_m1_types(c)
            if not m1_types:
                continue
            for sid, s_types in sec_types:
                hit = m1_types & s_types
                if not hit:
                    continue
                self.db.insert("requirement_section_maps", {
                    "tender_id": tender_id,
                    "requirement_id": c.id,
                    "section_id": sid,
                    "basis": f"{basis}:{','.join(sorted(hit))}",
                    "created_at": now,
                })
                mapped_ids.add(c.id)
        logger.info("需求映射完成 tender=%s mapped=%d/%d",
                    tender_id, len(mapped_ids),
                    sum(1 for c in reqs if not c.is_scoring))
        return self._stats(reqs, mapped_ids)

    def coverage(self, tender_id: str) -> CoverageStats:
        """从 requirement_section_maps 重算覆盖统计（不写库，供 /coverage）。"""
        reqs = self._canonical_reqs(tender_id)
        rows = self.db.query("SELECT DISTINCT requirement_id FROM "
                             "requirement_section_maps WHERE tender_id = ?",
                             (tender_id,))
        return self._stats(reqs, {r["requirement_id"] for r in rows})

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------
    @staticmethod
    def _req_m1_types(c: CanonicalRequirement) -> tuple[set[str], str]:
        """规范需求 → (M1 类型集合, 来源标签)。sources 优先，兜底表次之。"""
        m1_types = {s.type for s in c.sources if s.type}
        if m1_types:
            return m1_types, "sources"
        fb = _M3_TO_M1_FALLBACK.get(c.req_type)
        if fb:
            return {fb}, "fallback"
        return set(), "none"

    def _stats(self, reqs: list[CanonicalRequirement],
               mapped_ids: set[str]) -> CoverageStats:
        non_scoring = [c for c in reqs if not c.is_scoring]
        by_section_rows = self.db.query(
            "SELECT section_id, COUNT(DISTINCT requirement_id) AS n "
            "FROM requirement_section_maps WHERE tender_id = ? "
            "GROUP BY section_id ORDER BY section_id",
            (reqs[0].tender_id if reqs else "",))
        by_section = {r["section_id"]: r["n"] for r in by_section_rows}
        unmapped = [c for c in non_scoring if c.id not in mapped_ids]
        return CoverageStats(
            total=len(non_scoring),
            mapped=len(mapped_ids),
            unmapped=len(unmapped),
            by_section=by_section,
            unmapped_reqs=[{
                "requirement_id": c.id,
                "title": c.title,
                "reason": self._unmapped_reason(c),
            } for c in unmapped],
        )

    def _unmapped_reason(self, c: CanonicalRequirement) -> str:
        m1_types, basis = self._req_m1_types(c)
        if not m1_types:
            return "需求无来源类型且 req_type 无兜底类型"
        types = "、".join(sorted(m1_types))
        return f"需求类型「{types}」（{basis}）未被大纲任何章节声明"

    def _flat_sections(self, tender_id: str) -> list[dict]:
        return self.db.query(
            "SELECT * FROM generation_sections WHERE tender_id = ? "
            "ORDER BY level, ord", (tender_id,))

    def _canonical_reqs(self, tender_id: str) -> list[CanonicalRequirement]:
        rows = self.db.query(
            "SELECT * FROM canonical_requirements WHERE tender_id = ? "
            "ORDER BY id", (tender_id,))
        return [Database.row_to_canonical(r) for r in rows]


__all__ = ["RequirementSectionMapper"]
