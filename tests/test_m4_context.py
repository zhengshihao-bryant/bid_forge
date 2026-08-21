# -*- coding: utf-8 -*-
"""
tests/test_m4_context.py —— M4-03/04 生成上下文 + 历史标书参考（批次 2）

覆盖：
- 技术章节上下文：需求 + FACT 证据白名单（历史标书剔除）+ 能力卡 + 置信度降序
- 证据去重 / 引用截断
- 工期需求（仅历史标书证据）→ FACT 白名单为空（历史标书不当企业事实）
- HistoricalExampleRetriever：恒 WRITING_STYLE，source_document 溯源
- 无映射章节（封面）→ 空上下文不报错
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.schemas import SearchHit, SearchResult  # noqa: E402
from app.services.generation import (EVIDENCE_QUOTE_LIMIT,  # noqa: E402
                                     GenerationContextBuilder,
                                     HistoricalExampleRetriever,
                                     dedupe_evidences, trim_evidence)
from app.services.generation.models import FactClass  # noqa: E402


def _section(data, sid):
    return next(s for s in data["sections"] if s.id == sid)


# ═══════════════════════════════════════════════════════════════════════
# 上下文构建
# ═══════════════════════════════════════════════════════════════════════
def test_context_for_tech_section(seed_m4):
    """技术章节：需求 + 证据（FACT）+ 能力卡 + 置信度降序。"""
    data = seed_m4
    ctx = GenerationContextBuilder(data["db"]).build(_section(data, "CH-05-2"))
    assert ctx.requirements, "技术章节应有需求"
    assert ctx.evidences, "技术章节应有产品证据"
    # 事实白名单不含历史标书
    assert all(e.category != "历史标书" for e in ctx.evidences)
    # 能力卡：allowed_categories=[技术方案,产品] → 产品卡在列
    assert ctx.capability_cards
    assert all(c.category.value == "产品" for c in ctx.capability_cards)
    # 置信度降序
    confs = [e.confidence for e in ctx.evidences]
    assert confs == sorted(confs, reverse=True), "证据应按 confidence 降序"
    assert ctx.metadata["evidence_count"] == len(ctx.evidences)


def test_context_historical_bid_excluded_from_fact(seed_m4):
    """工期需求仅历史标书证据 → FACT 白名单为空（历史标书不当企业事实）。"""
    data = seed_m4
    ctx = GenerationContextBuilder(data["db"]).build(_section(data, "CH-06-1"))
    assert any("工期" in r.title for r in ctx.requirements)
    assert ctx.evidences == [], "历史标书证据不得进入 FACT 白名单"
    # 但需求仍在，供策略按状态响应（非 FULL → 待确认/如实写）
    assert ctx.metadata["evidence_count"] == 0


def test_context_unmapped_section_empty(seed_m4):
    """无类型声明的章节（封面）→ 空上下文不报错。"""
    data = seed_m4
    ctx = GenerationContextBuilder(data["db"]).build(_section(data, "CH-01"))
    assert ctx.requirements == [] and ctx.evidences == []
    assert ctx.capability_cards == []
    assert ctx.historical_examples == []


# ═══════════════════════════════════════════════════════════════════════
# 证据工具
# ═══════════════════════════════════════════════════════════════════════
def test_dedupe_evidences():
    from types import SimpleNamespace

    evs = [SimpleNamespace(evidence_id="EVD-1"),
           SimpleNamespace(evidence_id="EVD-2"),
           SimpleNamespace(evidence_id="EVD-1")]
    out = dedupe_evidences(evs)
    assert [e.evidence_id for e in out] == ["EVD-1", "EVD-2"]


def test_trim_evidence():
    from app.services.matching.models import Evidence, EvidenceSourceType

    ev = Evidence(evidence_id="EVD-1", tender_id="T-M3",
                  source_type=EvidenceSourceType.CHUNK,
                  content="长" * (EVIDENCE_QUOTE_LIMIT + 50))
    trim_evidence(ev)
    assert len(ev.content) == EVIDENCE_QUOTE_LIMIT + 1  # +1 省略号
    assert ev.content.endswith("…")


# ═══════════════════════════════════════════════════════════════════════
# M4-04 历史标书检索
# ═══════════════════════════════════════════════════════════════════════
class _FakeSearch:
    """最小 SearchService 替身：按 category 过滤返回命中。"""

    def __init__(self, hits: list[dict]):
        self.hits = hits

    def search(self, query, top_k=8, category=None):
        matched = [h for h in self.hits if not category or h["category"] == category]
        return SearchResult(hits=[SearchHit(**h) for h in matched[:top_k]])


def test_historical_retriever_writing_style_only(seed_m4):
    """历史标书检索 → 恒 WRITING_STYLE，source_document 溯源。"""
    from app.services.matching.models import (CanonicalRequirement,
                                              RequirementSourceRef,
                                              RequirementTypeM3)
    fake = _FakeSearch([{
        "chunk_id": "m6_C0001", "material_id": "m6",
        "file_name": "08_历史标书.docx", "category": "历史标书",
        "section_path": "第四章 实施方案",
        "content": "本项目工期预计10个月完成。", "score": 0.9,
    }])
    retriever = HistoricalExampleRetriever(search_service=fake, top_k=2)
    req = CanonicalRequirement(
        id="REQ-C-008", tender_id=seed_m4["tender_id"],
        req_type=RequirementTypeM3.IMPLEMENTATION, title="项目工期不超过12个月",
        text="项目工期不超过12个月。",
        sources=[RequirementSourceRef(id="REQ-0016", type="实施要求",
                                       title="项目工期不超过12个月",
                                       original_text="项目工期不超过 12 个月。")])
    examples = retriever.retrieve(req)
    assert examples
    assert examples[0].fact_class == FactClass.WRITING_STYLE
    assert examples[0].source_document == "08_历史标书.docx"
    assert examples[0].section_path == "第四章 实施方案"
    assert "10个月" in examples[0].snippet


def test_historical_retriever_filters_category(seed_m4):
    """检索限定 category=历史标书：非历史标书命中不返回。"""
    fake = _FakeSearch([{
        "chunk_id": "m1_C0001", "material_id": "m1",
        "file_name": "01_产品介绍.pdf", "category": "产品",
        "content": "设备接入支持不少于2000台。", "score": 0.8,
    }])
    retriever = HistoricalExampleRetriever(search_service=fake, top_k=2)
    # 无历史标书命中 → 空列表
    from app.services.matching.models import (CanonicalRequirement,
                                              RequirementSourceRef,
                                              RequirementTypeM3)
    req = CanonicalRequirement(
        id="REQ-C-009", tender_id=seed_m4["tender_id"],
        req_type=RequirementTypeM3.TECHNICAL, title="设备接入",
        sources=[RequirementSourceRef(id="REQ-0001", type="技术要求",
                                       title="设备接入不少于1000台",
                                       original_text="平台应支持不少于 1000 台。")])
    assert retriever.retrieve(req) == []
