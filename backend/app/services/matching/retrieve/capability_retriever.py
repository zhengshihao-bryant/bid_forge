# -*- coding: utf-8 -*-
"""
matching/retrieve/capability_retriever.py —— 能力卡匹配检索（M3-07）

对 M2 的 Capability Card 优先匹配（结构化程度高：资质/人员/案例/数值/产品）。
类型 → 类别映射决定候选集；打分 = 关键词重叠（标题/约束主体 × 卡名/描述/属性）。

卡片与资料的关联键是 source_doc（文件名，M2 定版），document_id 由调用方
经 kb_materials 反查（本模块不负责）。
"""
from __future__ import annotations

import logging
from typing import Optional

from ....db import Database
from ....schemas import CapabilityCategory
from ..models import CanonicalRequirement, RequirementTypeM3
from ..similarity import key_overlap

logger = logging.getLogger(__name__)

# M3 需求类型 → M2 能力卡类别候选（结构化匹配优先路径）
TYPE_CATEGORY_MAP: dict[RequirementTypeM3, list[CapabilityCategory]] = {
    RequirementTypeM3.QUALIFICATION: [CapabilityCategory.QUALIFICATION],
    RequirementTypeM3.PERSONNEL: [CapabilityCategory.PERSONNEL],
    RequirementTypeM3.PROJECT_EXPERIENCE: [CapabilityCategory.CASE],
    RequirementTypeM3.PRODUCT_CAPABILITY: [CapabilityCategory.PRODUCT,
                                           CapabilityCategory.SOLUTION],
    RequirementTypeM3.TECHNICAL: [CapabilityCategory.SOLUTION,
                                  CapabilityCategory.PRODUCT],
    RequirementTypeM3.IMPLEMENTATION: [CapabilityCategory.SOLUTION,
                                       CapabilityCategory.AFTERSALES],
    RequirementTypeM3.SERVICE: [CapabilityCategory.AFTERSALES],
    RequirementTypeM3.COMMERCIAL: [CapabilityCategory.INTRO],
    RequirementTypeM3.DOCUMENT: [],
    RequirementTypeM3.OTHER: [],
}


def _card_text(card) -> str:
    """能力卡 → 打分文本（名称 + 描述 + 属性键值）。"""
    attrs = getattr(card, "attributes", None) or {}
    parts = [getattr(card, "name", "") or "", getattr(card, "description", "") or ""]
    for k, v in attrs.items():
        if isinstance(v, (str, int, float)):
            parts.append(f"{k}{v}")
        elif isinstance(v, list):
            parts.append(" ".join(str(x) for x in v))
    return " ".join(parts)


class CapabilityRetriever:
    """能力卡检索器（M3-07）：类别过滤 + 关键词打分。"""

    def __init__(self, db: Optional[Database] = None):
        self.db = db or Database()

    # ------------------------------------------------------------------
    def retrieve(self, req: CanonicalRequirement, top_k: int = 5,
                 all_categories: bool = False) -> list[tuple]:
        """规范需求 → [(Capability, score)] 按分数降序。

        all_categories=True 时全类别扫描（OTHER 类型/兜底用）。
        """
        categories = TYPE_CATEGORY_MAP.get(req.req_type, [])
        rows = self.db.query("SELECT * FROM capabilities", ())
        caps = [self.db.row_to_capability(r) for r in rows]
        if not all_categories and categories:
            allowed = {c.value for c in categories}
            caps = [c for c in caps if c.category.value in allowed]

        # 查询文本：标题 + 约束主体（结构化匹配的锚点）
        query_parts = [req.title, req.text]
        query_parts.extend(c.subject for c in req.constraints if c.subject)
        query = " ".join(query_parts)

        scored: list[tuple] = []
        for card in caps:
            score = key_overlap(query, _card_text(card))
            # 约束主体命中卡内文本 → 加分（结构化字段是硬锚点）
            for c in req.constraints:
                if c.subject and c.subject in _card_text(card):
                    score += 0.25
            if score > 0:
                scored.append((card, min(score, 1.0)))
        scored.sort(key=lambda x: -x[1])
        return scored[:top_k]


__all__ = ["CapabilityRetriever", "TYPE_CATEGORY_MAP", "_card_text"]
