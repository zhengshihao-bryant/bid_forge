# -*- coding: utf-8 -*-
"""
matching/retrieve/evidence_ranker.py —— 证据排序（M3-10）

同一需求可能命中多条证据（企业介绍/项目案例/能力卡/历史标书/普通文本），
排序口径（用户 M3-10 指定，权重常量可调）：

    正式企业资料（产品/资质/人员/方案/售后/介绍）1.0
        > 项目案例 0.9
        > 结构化能力卡 0.8
        > 历史标书 0.5
        > 普通文本 0.3

再乘验证状态系数：VALID ×1.0 / UNCHECKED ×0.8 / INVALID ×0.4；
**INVALID 禁入高可信证据**：confidence 封顶 0.5，且不得单独支撑 FULL。
"""
from __future__ import annotations

from ..models import Evidence, EvidenceSourceType, EvidenceValidation

# 类别 → 来源档位（正式企业资料 6 类 / 项目案例 / 历史标书 / 其他）
_FORMAL_CATEGORIES = ("产品", "公司资质", "人员资质", "技术方案", "售后服务", "公司介绍")
_CASE_CATEGORY = "项目案例"
_HISTORICAL_CATEGORY = "历史标书"

# 来源基础权重（M3-10；可调整）
BASE_WEIGHTS = {
    "formal": 1.0,      # 正式企业资料
    "case": 0.9,        # 项目案例
    "card": 0.8,        # 结构化能力卡
    "historical": 0.5,  # 历史标书
    "plain": 0.3,       # 普通文本
}

# 验证状态系数（M3-05：INVALID 禁入高可信）
_VALIDATION_MULT = {
    EvidenceValidation.VALID: 1.0,
    EvidenceValidation.UNCHECKED: 0.8,
    EvidenceValidation.INVALID: 0.4,
}

# INVALID 证据的置信度上限（禁入高可信）
_INVALID_CONFIDENCE_CAP = 0.5


def source_tier(evidence: Evidence) -> str:
    """证据 → 来源档位（formal/case/card/historical/plain）。"""
    if evidence.source_type == EvidenceSourceType.CAPABILITY_CARD:
        return "card"
    category = (evidence.category or "")
    if category == _CASE_CATEGORY:
        return "case"
    if category == _HISTORICAL_CATEGORY:
        return "historical"
    if category in _FORMAL_CATEGORIES:
        return "formal"
    return "plain"


class EvidenceRanker:
    """证据排序器（M3-10）：来源权重 × 验证系数 → confidence，降序。"""

    def __init__(self, base_weights: dict | None = None):
        self.base_weights = base_weights or BASE_WEIGHTS

    # ------------------------------------------------------------------
    def rank(self, evidences: list[Evidence]) -> list[Evidence]:
        """就地赋 confidence 并按降序返回。

        排序键：confidence 降序 → 来源权威降序 → 检索分数降序。
        """
        for e in evidences:
            tier = source_tier(e)
            base = self.base_weights.get(tier, self.base_weights["plain"])
            mult = _VALIDATION_MULT.get(e.validation, 0.8)
            confidence = base * mult
            if e.validation == EvidenceValidation.INVALID:
                confidence = min(confidence, _INVALID_CONFIDENCE_CAP)
            # 相关性微调（检索分数/能力卡分 0-1）：confidence × (0.9 + 0.1×score)
            e.confidence = round(confidence * (0.9 + 0.1 * max(0.0, e.retrieval_score)), 4)
        tier_order = {"formal": 0, "case": 1, "card": 2, "historical": 3, "plain": 4}
        return sorted(
            evidences,
            key=lambda e: (-e.confidence,
                           tier_order.get(source_tier(e), 9),
                           -e.retrieval_score))

    # ------------------------------------------------------------------
    def top(self, evidences: list[Evidence], k: int = 5) -> list[Evidence]:
        """排序后取前 k（送入 LLM Judge 的证据池上限）。"""
        return self.rank(evidences)[:k]


__all__ = ["EvidenceRanker", "source_tier", "BASE_WEIGHTS"]
