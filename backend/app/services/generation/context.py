# -*- coding: utf-8 -*-
"""
generation/context.py —— M4-03 生成上下文 + M4-04 历史标书参考

    GenerationContextBuilder.build(section) → GenerationContext
        需求（映射到本章节）＋证据（FACT 白名单）＋能力卡＋历史标书示例＋约束

事实约束地基（M4-05）：
- GenerationContext.evidences 即 FACT 白名单 —— 只含非「历史标书」类证据，
  category=="历史标书" 的证据在收集时剔除（进 historical_examples 当 WRITING_STYLE）。
- 能力卡 attributes 也算事实源（strategy 拼入事实语料，数字校验用）。
- 证据去重 / 按 confidence 降序 / 保留四元溯源 / 引用截断 ≤300 字。

历史标书检索（M4-04）：SemanticRetriever 显式限定 category=历史标书，
只产出 HistoricalExample（恒 fact_class=WRITING_STYLE），严禁当企业事实。
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

from ... import config
from ...db import Database
from ...schemas import CapabilityCategory, now_str
from ..matching.models import CanonicalRequirement
from .models import GenerationContext, HistoricalExample
from .models import BidSection

logger = logging.getLogger(__name__)

EVIDENCE_QUOTE_LIMIT = 300     # 证据引用片段上限
HISTORICAL_MAX = 3             # 每章节历史标书示例上限
HISTORICAL_TOP_K = 2           # 每条需求检索的历史标书 top_k


# ---------------------------------------------------------------------------
# 证据工具（可直接单测）
# ---------------------------------------------------------------------------
def dedupe_evidences(evidences: list) -> list:
    """按 evidence_id 去重（保留首次出现）。"""
    seen: set[str] = set()
    out = []
    for e in evidences:
        if e.evidence_id in seen:
            continue
        seen.add(e.evidence_id)
        out.append(e)
    return out


def trim_evidence(evidence, limit: int = EVIDENCE_QUOTE_LIMIT):
    """引用片段截断（超长丢弃尾部 + 省略号）。"""
    if len(evidence.content) > limit:
        evidence.content = evidence.content[:limit] + "…"
    return evidence


# ---------------------------------------------------------------------------
# M4-04 历史标书检索
# ---------------------------------------------------------------------------
class HistoricalExampleRetriever:
    """历史标书 → 写作参考（WRITING_STYLE），不做企业事实。"""

    def __init__(self, search_service=None, top_k: int = HISTORICAL_TOP_K):
        self.search_service = search_service
        self.top_k = top_k

    def retrieve(self, req: CanonicalRequirement,
                 top_k: Optional[int] = None) -> list[HistoricalExample]:
        """单条规范需求 → 历史标书示例列表（检索失败返回空，不阻断）。

        SemanticRetriever 在类别命中稀疏时会放开检索（回退无类别查询），
        可能带回非历史标书命中 —— 这里按 category=="历史标书" 硬过滤，
        保住 M4-04 边界：历史示例恒来自历史标书，只作 WRITING_STYLE。
        """
        from ..matching.retrieve import SemanticRetriever

        try:
            retriever = SemanticRetriever(search_service=self.search_service,
                                          top_k=top_k or self.top_k)
            hits = retriever.retrieve(req, categories=[CapabilityCategory.HISTORICAL_BID])
        except Exception as e:  # noqa: BLE001 —— 离线/无索引环境静默降级
            logger.warning("历史标书检索失败 %s: %s", req.id, str(e)[:150])
            return []
        out: list[HistoricalExample] = []
        for hit, _score in hits:
            if hit.category != CapabilityCategory.HISTORICAL_BID.value:
                continue                  # 放开检索兜底可能带回其他类别 → 硬过滤
            out.append(HistoricalExample(
                source_document=hit.file_name or hit.material_id or "",
                section_path=hit.section_path or "",
                snippet=(hit.content or "")[:EVIDENCE_QUOTE_LIMIT],
            ))
        return out


# ---------------------------------------------------------------------------
# M4-03 上下文构建
# ---------------------------------------------------------------------------
class GenerationContextBuilder:
    """按章节组装生成上下文（事实白名单 + 能力卡 + 历史参考 + 约束）。"""

    def __init__(self, db: Optional[Database] = None):
        self.db = db or Database(config.DB_PATH)

    def build(self, section: BidSection, retriever: Optional[HistoricalExampleRetriever] = None,
              ) -> GenerationContext:
        tender_id = section.tender_id
        reqs = self._mapped_requirements(section.id, tender_id)
        evidences = self._collect_evidences(reqs)
        cards = self._capability_cards(section.allowed_categories)
        hist = self._historical_examples(reqs, retriever)
        constraints = [c for r in reqs for c in r.constraints]
        metadata = {
            "tender_id": tender_id,
            "section_id": section.id,
            "section_type": section.section_type.value,
            "requirement_count": len(reqs),
            "evidence_count": len(evidences),
            "card_count": len(cards),
            "historical_count": len(hist),
            "built_at": now_str(),
        }
        return GenerationContext(
            section=section, requirements=reqs, evidences=evidences,
            capability_cards=cards, historical_examples=hist,
            constraints=constraints, metadata=metadata)

    # ------------------------------------------------------------------
    def _mapped_requirements(self, section_id: str, tender_id: str,
                             ) -> list[CanonicalRequirement]:
        """映射到本章节的非评分规范需求（按 id 序）。"""
        rows = self.db.query(
            "SELECT c.* FROM canonical_requirements c "
            "JOIN requirement_section_maps m ON m.requirement_id = c.id "
            "WHERE m.tender_id = ? AND m.section_id = ? AND c.is_scoring = 0 "
            "ORDER BY c.id", (tender_id, section_id))
        return [Database.row_to_canonical(r) for r in rows]

    def _collect_evidences(self, reqs: list[CanonicalRequirement]) -> list:
        """需求匹配的 evidence_ids → 证据（FACT 白名单）。

        剔除 category=="历史标书"（历史标书不能当企业事实）；去重；按
        confidence 降序；引用截断。只写真编号（校验器在 generator 兜底）。
        """
        ids: list[str] = []
        for r in reqs:
            match = self.db.query_one(
                "SELECT * FROM requirement_matches WHERE requirement_id = ?",
                (r.id,))
            if match:
                ids.extend(json.loads(match.get("evidence_ids") or "[]"))

        evidences = []
        seen_ids: set[str] = set()
        for eid in ids:
            if eid in seen_ids:
                continue
            seen_ids.add(eid)
            row = self.db.query_one("SELECT * FROM evidences WHERE id = ?", (eid,))
            if not row:
                continue
            ev = Database.row_to_evidence(row)
            if ev.category == "历史标书":
                continue                     # 历史标书 → WRITING_STYLE，非 FACT
            evidences.append(ev)
        evidences.sort(key=lambda e: (e.confidence, e.retrieval_score), reverse=True)
        return [trim_evidence(e) for e in evidences]

    def _capability_cards(self, allowed_categories: list[str]) -> list:
        if not allowed_categories:
            return []
        placeholders = ",".join("?" * len(allowed_categories))
        rows = self.db.query(
            f"SELECT * FROM capabilities WHERE category IN ({placeholders}) "
            "ORDER BY id", allowed_categories)
        return [Database.row_to_capability(r) for r in rows]

    def _historical_examples(self, reqs: list[CanonicalRequirement],
                             retriever: Optional[HistoricalExampleRetriever],
                             ) -> list[HistoricalExample]:
        if not reqs or retriever is None:
            return []
        out: list[HistoricalExample] = []
        seen: set[str] = set()
        for r in reqs:
            for ex in retriever.retrieve(r):
                if ex.snippet in seen:
                    continue
                seen.add(ex.snippet)
                out.append(ex)
            if len(out) >= HISTORICAL_MAX:
                break
        return out[:HISTORICAL_MAX]


__all__ = ["GenerationContextBuilder", "HistoricalExampleRetriever",
           "dedupe_evidences", "trim_evidence", "EVIDENCE_QUOTE_LIMIT"]
