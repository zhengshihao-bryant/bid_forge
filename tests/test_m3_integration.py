# -*- coding: utf-8 -*-
"""
tests/test_m3_integration.py —— M3 真实 LLM / 真实 Milvus 集成测试

默认跳过；需要真实依赖时分别运行：

    pytest tests/test_m3_integration.py -m llm -v      # 需 LLM_API_KEY
    pytest tests/test_m3_integration.py -m milvus -v   # 需本机运行中的 Milvus

LLM 测试验收对象：
    - 真实 LLM 归一化合并（merge_method == "llm"，成员约束不丢）
    - 真实 LLM Judge 判定（method == "LLM_JUDGE"，证据编号白名单铁律）
    - 无证据 → UNKNOWN（没有证据 ≠ 不满足，空池不调 LLM 也恒 UNKNOWN）

Milvus 测试验收对象：
    - Milvus 检索路径（engine == "milvus"）→ SemanticRetriever → Rerank
    - 相关 chunk 排第一、类别过滤生效；临时集合 _m3_test_{pid} 测后即删，
      绝不触碰生产集合 bid_chunks
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app import config  # noqa: E402
from app.db import Database  # noqa: E402
from app.services.llm import create_llm_client  # noqa: E402
from app.services.matching.judge import LLMJudge  # noqa: E402
from app.services.matching.models import (CanonicalRequirement,  # noqa: E402
                                          RequirementTypeM3)
from app.services.matching.normalize import RequirementNormalizer  # noqa: E402
from app.services.matching.pipeline import Matcher  # noqa: E402
from app.services.matching.retrieve import SemanticRetriever  # noqa: E402
from app.services.vector_store import (MilvusVectorStore, SearchService,  # noqa: E402
                                       SqliteVectorStore)
from conftest import m3_req, seed_m3_kb  # noqa: E402

# ═══════════════════════════════════════════════════════════════════════
# 真实 LLM：归一化合并 + Judge 判定 + 证据白名单铁律
# ═══════════════════════════════════════════════════════════════════════
LLM_TENDER = "T-M3-LLM"


def _seed_llm_db(db, emb):
    """最小企业包：产品 chunk（设备接入证据）+ 项目案例 chunk（业绩证据）。"""
    seed_m3_kb(db, emb,
               materials=[
                   {"id": "llm-m1", "category": "产品",
                    "file_name": "01_产品介绍.pdf"},
                   {"id": "llm-m2", "category": "项目案例",
                    "file_name": "02_项目案例.docx"},
               ],
               chunks=[
                   {"id": "llm-c1", "material_id": "llm-m1", "category": "产品",
                    "file_name": "01_产品介绍.pdf",
                    "content": "智慧园区综合管理平台V3.2，设备接入支持不少于2000台，"
                               "并发1000用户，系统可用性99.95%。",
                    "section_path": "1.3 技术指标", "page_start": 3},
                   {"id": "llm-c2", "material_id": "llm-m2", "category": "项目案例",
                    "file_name": "02_项目案例.docx",
                    "content": "我司承建XX市智慧园区综合运营平台项目，具备大型智慧园区"
                               "综合运营平台建设业绩，项目已于2025年验收通过。",
                    "section_path": "2.1 案例列表", "page_start": 1},
               ])


def _seed_llm_reqs(db):
    reqs = [
        m3_req(tender_id=LLM_TENDER, type_="技术要求",
               title="设备接入能力不低于1000台",
               text="平台应支持不少于 1000 台（个）设备的接入管理。",
               quantitative=[{"metric": "设备接入", "op": "不少于",
                              "value": "1000", "unit": "台"}],
               importance="高"),
        # 与上一条同簇（标题 bigram Jaccard ≈ 0.73 > 0.45 同类型阈值），
        # 走 LLM 合并路径 —— M3-01 的"同一需求不同写法"场景
        m3_req(tender_id=LLM_TENDER, type_="技术要求",
               title="设备接入能力不少于1000台",
               text="系统接入规模不得少于 1000 台，支持多类型设备接入。",
               quantitative=[{"metric": "设备接入", "op": "不少于",
                              "value": "1000", "unit": "台"}],
               importance="高"),
        m3_req(tender_id=LLM_TENDER, type_="技术要求",
               title="智慧园区运营平台建设业绩",
               text="投标人须具备大型智慧园区综合运营平台的建设业绩。",
               importance="中"),
        m3_req(tender_id=LLM_TENDER, type_="商务要求",
               title="投标报价要求",
               text="投标人应按招标文件要求提交投标报价。",
               importance="中"),
    ]
    for r in reqs:
        db.insert("requirements", Database.requirement_to_row(r))
    return reqs


@pytest.mark.llm
@pytest.mark.skipif(not config.LLM_API_KEY,
                    reason="未配置 LLM_API_KEY，跳过真实 LLM 集成测试")
def test_llm_normalize_merge_and_judge(m3_env):
    """真实 LLM：同簇合并（llm）+ Judge 判定（LLM_JUDGE）+ 证据白名单 + 空池 UNKNOWN。"""
    db = Database(config.DB_PATH)
    _seed_llm_db(db, m3_env)
    _seed_llm_reqs(db)

    matcher = Matcher(
        db,
        normalizer=RequirementNormalizer(client=create_llm_client()),
        llm_judge=LLMJudge(client=create_llm_client()))
    report = matcher.match(LLM_TENDER)

    # ① 归一化：设备接入两条同簇 → 真实 LLM 合并为一条规范需求
    canonicals = [db.row_to_canonical(r) for r in db.query(
        "SELECT * FROM canonical_requirements WHERE tender_id = ?",
        (LLM_TENDER,))]
    merged = [c for c in canonicals if len(c.source_requirement_ids) == 2]
    assert len(merged) == 1, f"设备接入两条应合并为一条: {[c.title for c in canonicals]}"
    c = merged[0]
    assert c.merge_method == "llm", f"合并路径应为 llm，实际 {c.merge_method}"
    # 成员约束不丢（逐成员提取并集）：1000 台原样保留
    device = [x for x in c.constraints if x.attribute == "device_count"]
    assert device and float(device[0].value) == 1000.0

    # ② 判定分派：业绩走 LLM Judge，证据编号白名单（只能引用池内 EVD）
    by_id = {m.requirement_id: m for m in report.matches}
    assert by_id[c.id].method.value == "llm_judge"
    evd_ids = {r["id"] for r in db.query(
        "SELECT id FROM evidences WHERE tender_id = ?", (LLM_TENDER,))}
    perf = [m for m in report.matches if "业绩" in
            next(x.title for x in canonicals if x.id == m.requirement_id)]
    assert perf and perf[0].method.value == "llm_judge"
    assert perf[0].evidence_ids, "有直接证据的判定必须给出证据编号"
    assert set(perf[0].evidence_ids) <= evd_ids, \
        f"证据编号白名单被突破: {perf[0].evidence_ids}"
    assert perf[0].status.value in ("FULL", "PARTIAL"), \
        f"证据直接覆盖的业绩要求不应 UNKNOWN/MISSING: {perf[0].status.value}"

    # ③ 铁律：报价类无证据 → UNKNOWN（没有证据 ≠ 不满足）
    quote = next(m for m in report.matches
                 if next(x.title for x in canonicals if x.id == m.requirement_id)
                 == "投标报价要求")
    assert quote.status.value == "UNKNOWN"
    assert quote.evidence_ids == []

    # ④ 全量白名单兜底：任何判定引用的编号都必须真实存在
    for m in report.matches:
        assert set(m.evidence_ids) <= evd_ids


# ═══════════════════════════════════════════════════════════════════════
# 真实 Milvus：检索路径 → SemanticRetriever → Rerank
# ═══════════════════════════════════════════════════════════════════════
def _canonical(tender_id: str, rid: str, title: str, text: str,
               req_type: RequirementTypeM3) -> CanonicalRequirement:
    return CanonicalRequirement(
        id=rid, tender_id=tender_id, req_type=req_type,
        title=title, text=text, source_requirement_ids=[],
        importance="高", is_star=False, is_scoring=False,
        constraints=[], sources=[], merge_method="exact")


@pytest.mark.milvus
def test_milvus_semantic_retrieval_path(tmp_env):
    """真实 Milvus：建临时集合 → SearchService(milvus) → Rerank 排序。

    前置：docker start milvus-etcd milvus-minio milvus-standalone。
    命中回 SQLite 取权威元数据（事实源铁律），故 kb_chunks 需同步落库。
    """
    from app.services.embedding import FakeEmbedding

    store = MilvusVectorStore(collection=f"_m3_test_{os.getpid()}")
    if not store.info().get("reachable"):
        pytest.skip("本机 Milvus 未运行（docker start milvus-standalone 后重跑）")

    emb = FakeEmbedding(dimension=config.EMBEDDING_DIM)
    db = Database(config.DB_PATH)
    seed_m3_kb(db, emb,
               materials=[
                   {"id": "mv-m1", "category": "产品",
                    "file_name": "01_产品介绍.pdf"},
                   {"id": "mv-m2", "category": "项目案例",
                    "file_name": "02_项目案例.docx"},
               ],
               chunks=[
                   {"id": "mv-c1", "material_id": "mv-m1", "category": "产品",
                    "file_name": "01_产品介绍.pdf",
                    "content": "智慧园区综合管理平台V3.2，设备接入支持不少于2000台。",
                    "section_path": "1.3 技术指标", "page_start": 3},
                   {"id": "mv-c2", "material_id": "mv-m1", "category": "产品",
                    "file_name": "01_产品介绍.pdf",
                    "content": "平台支持人脸识别考勤与访客管理功能。",
                    "section_path": "2.1 功能列表", "page_start": 8},
                   {"id": "mv-c3", "material_id": "mv-m2", "category": "项目案例",
                    "file_name": "02_项目案例.docx",
                    "content": "我司承建智慧园区平台项目三个，单个合同额均超500万元。",
                    "section_path": "2.1 案例列表", "page_start": 1},
               ])
    try:
        store.ensure()
        rows = []
        for ch in db.query("SELECT * FROM kb_chunks"):
            import json
            rows.append({
                "chunk_id": ch["id"], "material_id": ch["material_id"],
                "category": ch["category"], "file_name": ch["file_name"],
                "section_path": ch["section_path"],
                "page_start": ch["page_start"] or 0,
                "page_end": ch["page_end"] or 0,
                "block_ids": json.loads(ch["block_ids"] or "[]"),
                "content": ch["content"],
                "embedding": json.loads(ch["embedding"]),
            })
        store.upsert(rows)

        # engine 透明标识：Milvus 路径不打折扣
        svc = SearchService(milvus_store=store,
                            sqlite_store=SqliteVectorStore(db),
                            embedding=emb, db=db)
        assert svc.search("设备接入", top_k=2).engine == "milvus"

        retriever = SemanticRetriever(search_service=svc)
        req = _canonical("T-M3-MV", "REQ-C-T", "设备接入能力不低于1000台",
                         "平台应支持不少于 1000 台设备的接入管理。",
                         RequirementTypeM3.PRODUCT_CAPABILITY)
        hits = retriever.retrieve(req)
        assert hits, "Milvus 语义检索返回空"
        # Rerank：相关 chunk 排第一，且过双下限（总分 + 关键词重叠）
        assert hits[0][0].chunk_id == "mv-c1", \
            f"设备接入证据应排第一: {[h.chunk_id for h, _ in hits]}"
        assert hits[0][1] >= config.M3_RAG_MIN_SCORE

        # 类别过滤：项目案例类需求 → 案例 chunk 排第一（产品类被类别亲和压制）
        req2 = _canonical("T-M3-MV", "REQ-C-C", "智慧园区平台建设业绩",
                          "投标人须具备智慧园区平台建设业绩，单个合同额不低于500万元。",
                          RequirementTypeM3.PROJECT_EXPERIENCE)
        hits2 = retriever.retrieve(req2)
        assert hits2 and hits2[0][0].chunk_id == "mv-c3", \
            f"案例证据应排第一: {[h.chunk_id for h, _ in hits2]}"
    finally:
        try:
            from pymilvus import MilvusClient
            MilvusClient(uri=store.uri).drop_collection(store.collection)
        except Exception:
            pass
