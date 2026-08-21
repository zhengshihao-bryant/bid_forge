# -*- coding: utf-8 -*-
"""
tests/test_m3_retrieval.py —— M3-07/08/10 检索三件套（离线，确定性）

覆盖：
- 能力卡检索：需求类型 → 类别映射过滤 + 关键词打分 + 约束主体加分
- 语义检索：注入 FakeSearchService（确定性命中）+ 规则 Rerank
  （余弦 ×0.5 + 关键词 ×0.3 + 类别亲和 ×0.2）
- 证据排序 M3-10 口径：正式企业资料 > 项目案例 > 能力卡 > 历史标书 > 普通文本
  + 验证状态系数 + INVALID 封顶 0.5（禁入高可信）
  + 历史标书不能覆盖正式项目资料
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app import config  # noqa: E402
from app.db import Database  # noqa: E402
from app.schemas import SearchHit, SearchResult  # noqa: E402
from app.services.matching.models import (  # noqa: E402
    CanonicalRequirement, Constraint, Evidence, EvidenceSourceType,
    EvidenceValidation, RequirementTypeM3)
from app.services.matching.retrieve import (  # noqa: E402
    CapabilityRetriever, EvidenceRanker, Reranker, SemanticRetriever,
    source_tier)

from conftest import seed_m3_kb  # noqa: E402


def _req(title, req_type=RequirementTypeM3.TECHNICAL, constraints=None):
    return CanonicalRequirement(
        id="REQ-C-0001", tender_id="T-M3", req_type=req_type,
        title=title, text=title, constraints=constraints or [])


def _ev(source_type=EvidenceSourceType.CHUNK, category="", validation=None,
        retrieval_score=0.0):
    return Evidence(
        evidence_id="EVD-0001", tender_id="T-M3", requirement_id="REQ-C-0001",
        source_type=source_type, source_id="s1", content="证据内容",
        category=category,
        validation=validation or EvidenceValidation.UNCHECKED,
        retrieval_score=retrieval_score)


# ═══════════════════════════════════════════════════════════════════════
# M3-07 能力卡检索
# ═══════════════════════════════════════════════════════════════════════
def test_capability_retriever_category_mapping(tmp_env, m3_env):
    db = Database(config.DB_PATH)
    db.init_schema()
    seed_m3_kb(db, m3_env, capabilities=[
        {"id": "CAP-0001", "category": "公司资质", "name": "ISO9001证书",
         "attributes": {"certs": ["ISO9001"]}},
        {"id": "CAP-0002", "category": "人员资质", "name": "张伟-项目经理",
         "attributes": {"experience_years": "6"}},
        {"id": "CAP-0003", "category": "产品", "name": "智慧园区平台",
         "attributes": {"max_devices": "2000"}},
    ])
    retriever = CapabilityRetriever(db)
    # 资质类需求 → 只扫公司资质卡
    hits = retriever.retrieve(_req("ISO9001质量管理体系认证",
                                   RequirementTypeM3.QUALIFICATION))
    assert hits and all(h[0].category.value == "公司资质" for h in hits)
    # 人员类需求 → 只扫人员资质卡
    hits = retriever.retrieve(_req("项目经理经验",
                                   RequirementTypeM3.PERSONNEL))
    assert hits and all(h[0].category.value == "人员资质" for h in hits)
    # OTHER 类型全类别扫描：只有语义相关的卡得分 > 0
    hits = retriever.retrieve(_req("智慧园区平台", RequirementTypeM3.OTHER),
                              all_categories=True)
    assert hits and hits[0][0].id == "CAP-0003"


def test_capability_retriever_keyword_and_subject_bonus(tmp_env, m3_env):
    db = Database(config.DB_PATH)
    db.init_schema()
    seed_m3_kb(db, m3_env, capabilities=[
        {"id": "CAP-0001", "category": "产品", "name": "智慧园区平台",
         "description": "支持设备接入与视频监控",
         "attributes": {"max_devices": "2000"}},
        {"id": "CAP-0002", "category": "产品", "name": "能耗管理系统",
         "description": "水电气能耗采集", "attributes": {}},
    ])
    retriever = CapabilityRetriever(db)
    # 关键词命中（设备接入）→ 产品卡 CAP-0001 高分
    req = _req("平台应支持不少于1000台设备接入管理",
               RequirementTypeM3.PRODUCT_CAPABILITY,
               constraints=[Constraint(subject="设备接入",
                                       attribute="device_count",
                                       operator=">=", value=1000.0,
                                       unit="count")])
    hits = retriever.retrieve(req)
    assert hits[0][0].id == "CAP-0001"
    # 约束主体命中 → 加分后仍是 CAP-0001 居首
    assert hits[0][1] >= 0.25


# ═══════════════════════════════════════════════════════════════════════
# M3-08 语义检索 + Rerank
# ═══════════════════════════════════════════════════════════════════════
class _FakeSearchService:
    """脚本化检索服务：按类别过滤命中；无类别时返回全部。"""

    def __init__(self, hits_by_category: dict):
        self.hits_by_category = hits_by_category
        self.all_hits = [h for hs in hits_by_category.values() for h in hs]
        self.calls: list = []

    def search(self, query, top_k=10, category=None):
        self.calls.append(category)
        hits = (self.hits_by_category.get(category, []) if category
                else self.all_hits)
        return SearchResult(engine="fake", hits=hits[:top_k])


def _hit(chunk_id, category, content, score):
    return SearchHit(chunk_id=chunk_id, material_id=f"m-{chunk_id}",
                     file_name=f"{chunk_id}.pdf", category=category,
                     section_path="第一章", page=1, score=score, content=content)


def test_reranker_composition():
    """Rerank：类别亲和与关键词重叠改变纯余弦排序。"""
    reranker = Reranker()
    req = _req("平台支持设备接入", RequirementTypeM3.PRODUCT_CAPABILITY)
    hits = [
        _hit("c1", "公司介绍", "平台支持设备接入管理的全部功能说明", 0.9),
        _hit("c2", "产品", "无关内容", 0.9),
    ]
    ranked = reranker.rerank(req, hits)
    # 同余弦：c1 关键词重叠更高（0.5*0.9 + 0.3*kw + 0.2*cat）
    assert ranked[0][0].chunk_id == "c1"
    hits2 = [_hit("c3", "产品", "设备接入", 0.5),
             _hit("c4", "历史标书", "设备接入", 0.9)]
    ranked2 = reranker.rerank(req, hits2)
    # 产品：0.5*0.5+0.3*1+0.2*1=0.75；历史标书：0.5*0.9+0.3*1+0.2*0.2=0.79
    # 历史标书仍靠余弦领先（Rerank 不做来源档位，档位交给 EvidenceRanker）
    assert ranked2[0][0].chunk_id == "c4"


def test_semantic_retriever_category_dispatch():
    service = _FakeSearchService({
        "产品": [_hit("c1", "产品", "平台支持2000台设备接入", 0.9)],
        "技术方案": [_hit("c2", "技术方案", "高可用架构方案", 0.8)],
        "历史标书": [_hit("c3", "历史标书", "投标函", 0.4)],
    })
    sr = SemanticRetriever(search_service=service, top_k=2)
    req = _req("平台支持设备接入", RequirementTypeM3.PRODUCT_CAPABILITY)
    results = sr.retrieve(req)
    ids = {h.chunk_id for h, _ in results}
    assert "c1" in ids and "c2" in ids          # 映射类别：产品 + 技术方案
    assert "c3" not in ids                      # 历史标书不在映射类别
    # 映射类别命中 ≥ top_k//2 时不放开全库兜底
    assert service.calls == ["产品", "技术方案"]


# ═══════════════════════════════════════════════════════════════════════
# M3-10 证据排序（来源档位 + 验证系数）
# ═══════════════════════════════════════════════════════════════════════
def test_source_tier_classification():
    assert source_tier(_ev(EvidenceSourceType.CAPABILITY_CARD,
                           category="产品")) == "card"
    assert source_tier(_ev(category="产品")) == "formal"
    assert source_tier(_ev(category="公司资质")) == "formal"
    assert source_tier(_ev(category="项目案例")) == "case"
    assert source_tier(_ev(category="历史标书")) == "historical"
    assert source_tier(_ev(category="")) == "plain"


def test_ranker_source_weight_order():
    """正式企业资料 > 项目案例 > 能力卡 > 历史标书 > 普通文本。"""
    ranker = EvidenceRanker()
    evs = [
        _ev(category="历史标书", retrieval_score=1.0),   # 检索分满分也压不过正式资料
        _ev(category="产品", retrieval_score=0.1),       # 正式资料
        _ev(category="项目案例", retrieval_score=0.5),
        _ev(EvidenceSourceType.CAPABILITY_CARD, category="产品", retrieval_score=0.5),
        _ev(category="", retrieval_score=0.9),
    ]
    ranked = ranker.rank(evs)
    assert [source_tier(e) for e in ranked] == \
        ["formal", "case", "card", "historical", "plain"]
    # 历史标书 confidence 必须低于正式资料（历史标书不能覆盖正式项目资料）
    conf = {source_tier(e): e.confidence for e in ranked}
    assert conf["historical"] < conf["formal"]
    assert conf["historical"] < conf["case"]


def test_ranker_validation_multipliers():
    """VALID ×1.0 > UNCHECKED ×0.8 > INVALID ×0.4 且 INVALID 封顶 0.5。"""
    ranker = EvidenceRanker()
    evs = [
        _ev(category="产品", validation=EvidenceValidation.INVALID),
        _ev(category="产品", validation=EvidenceValidation.UNCHECKED),
        _ev(category="产品", validation=EvidenceValidation.VALID),
    ]
    ranked = ranker.rank(evs)
    assert [e.validation.value for e in ranked] == \
        ["VALID", "UNCHECKED", "INVALID"]
    invalid = ranked[2]
    assert invalid.confidence <= 0.5        # 禁入高可信
    assert ranked[0].confidence == 0.9      # 1.0 × 1.0 × (0.9+0.1×0)


def test_ranker_top_cap():
    ranker = EvidenceRanker()
    evs = [_ev(category="产品", retrieval_score=float(i) / 10)
           for i in range(10)]
    assert len(ranker.top(evs, k=5)) == 5
    assert [e.retrieval_score for e in ranker.top(evs, k=5)] == \
        [0.9, 0.8, 0.7, 0.6, 0.5]
