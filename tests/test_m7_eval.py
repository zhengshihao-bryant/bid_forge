# -*- coding: utf-8 -*-
"""
tests/test_m7_eval.py —— M7-07 评估体系（离线确定性，无 LLM）

覆盖：
- 检索基线：FakeEmbedding 下 8 条 golden 查询全命中（种子块内容含查询
  原句 → 双字 bigram 重叠最大 → 排序第 1，确定性）
- 引用准确率：注入 EVD-9999（证据池外）→ 准确率下降
- 引用完整率：金标引用缺一条 → 完整率下降 + missing_refs 列出
- 生成评估：seed_m5 全生成后三指标可算（事实声明数 > 0）
- 趋势：相邻报告 delta（score 差 + issue_counts 逐类差）
- disclaimer 铁律：所有结果带口径声明
- API：eval router 挂载 scratch app（admin override）→ 200 + disclaimer
"""

from __future__ import annotations

import json

import pytest

from conftest import seed_m3_kb  # noqa: E402 —— tests 目录在 sys.path（同 seed_m4 惯例）

from app.db import Database  # noqa: E402
from app.evaluation import golden, metrics, runner  # noqa: E402


# ═══════════════════════════════════════════════════════════════════════
# 检索基线
# ═══════════════════════════════════════════════════════════════════════
def _seed_baseline_kb(db, emb) -> None:
    """按 golden.RETRIEVAL_QUERIES 种子 8 材料 8 块（内容 = 查询 + 事实）。"""
    materials = [{"id": f"MAT-{i:02d}", "category": q["expect_category"],
                  "file_name": q["expect_file"]}
                 for i, q in enumerate(golden.RETRIEVAL_QUERIES, 1)]
    chunks = [{"id": f"CH-{i:02d}", "material_id": f"MAT-{i:02d}",
               "category": q["expect_category"],
               "file_name": q["expect_file"],
               "content": f"{q['query']} {q['expect_fact']}"}
              for i, q in enumerate(golden.RETRIEVAL_QUERIES, 1)]
    seed_m3_kb(db, emb, materials=materials, chunks=chunks)


def test_retrieval_baseline_all_hit(tmp_env, m3_env):
    """8 条基线 + 需求子集：FakeEmbedding 下确定性全命中（Recall@K=1）。"""
    db = Database()
    db.init_schema()
    _seed_baseline_kb(db, m3_env)
    result = runner.run_retrieval(k=10)

    assert result["disclaimer"] == "基于项目内离线评估集，不代表通用准确率"
    kb = result["kb_queries"]
    assert kb["evaluated"] == 8
    assert kb["recall_at_k"] == 1.0
    assert kb["mrr"] == 1.0
    for row in kb["rows"]:
        assert row["hit"], f"查询未命中：{row['query']}"
        assert row["rank"] == 1
        assert row["expect_file"] == row["top_files"][0]

    # 需求子集：5 条有 expect_file，其余 10 条在 excluded 中明示
    req = result["requirement_queries"]
    assert req["evaluated"] == 5
    assert len(req["excluded_queries"]) == 10
    # 种子块内容含对应需求关键词（设备接入/ISO9001/质保/项目经理/业绩）→ 全命中
    assert req["recall_at_k"] == 1.0


def test_requirement_rag_subset():
    """golden 需求查询集：15 条基线中 5 条有已核实 KB 映射，10 条排除。"""
    assert len(golden.REQUIREMENT_BASELINE) == 15
    queries = golden.requirement_rag_queries()
    assert len(queries) == 15
    assert {r["expect_file"] for r in queries if r["expect_file"]} == {
        "01_产品介绍.pdf", "04_人员资质.docx", "02_项目案例.docx",
        "03_公司资质.docx", "06_售后服务.docx"}


# ═══════════════════════════════════════════════════════════════════════
# 引用指标（纯函数，无 DB）
# ═══════════════════════════════════════════════════════════════════════
def test_citation_accuracy_invalid_ref_drops():
    """EVD-9999 不在证据池 → 准确率下降且被点名。"""
    content = "本平台接入 EVD-001 设备，质保 EVD-002，案例 EVD-9999。"
    r = metrics.citation_accuracy(content, {"EVD-001", "EVD-002"})
    assert r["total_refs"] == 3
    assert r["invalid_refs"] == ["EVD-9999"]
    assert r["citation_accuracy"] == round(2 / 3, 4)

    # 无引用 → 1.0（宁缺勿假）
    assert metrics.citation_accuracy("无引用正文", set())["citation_accuracy"] == 1.0


def test_citation_completeness_missing_ref_drops():
    """金标引用集缺一条在正文出现 → 完整率下降 + 章节级 missing_refs。"""
    sections = [
        {"section_id": "CH-05-1", "content_md": "内容 EVD-001。",
         "evidence_refs": json.dumps(["EVD-001", "EVD-002"])},
        {"section_id": "CH-05-2", "content_md": "内容 EVD-003。",
         "evidence_refs": json.dumps(["EVD-003"])},
    ]
    r = metrics.citation_completeness(sections)
    assert r["gold_refs"] == 3
    assert r["present_refs"] == 2
    assert r["citation_completeness"] == round(2 / 3, 4)
    assert r["per_section"][0]["missing_refs"] == ["EVD-002"]


# ═══════════════════════════════════════════════════════════════════════
# 生成评估（seed_m5 全生成）
# ═══════════════════════════════════════════════════════════════════════
def test_generation_eval_on_seed_m5(seed_m5):
    """全生成后三指标可算：事实声明数 > 0，覆盖总量 > 0，disclaimer 在。"""
    data = seed_m5
    r = runner.run_generation(data["db"], data["tender_id"])
    assert r["disclaimer"] == "基于项目内离线评估集，不代表通用准确率"
    assert r["no_content"] is False
    assert 0.0 <= r["citation_completeness"]["citation_completeness"] <= 1.0
    assert 0.0 <= r["citation_accuracy"]["citation_accuracy"] <= 1.0
    fc = r["fact_consistency"]
    assert fc["fact_claims"] > 0, "事实区应提取到声明"
    assert 0.0 <= fc["fact_consistency"] <= 1.0
    cov = r["requirement_coverage"]
    assert cov["forward"]["total"] > 0
    assert cov["forward"]["mapped"] == cov["forward"]["total"]


# ═══════════════════════════════════════════════════════════════════════
# 趋势
# ═══════════════════════════════════════════════════════════════════════
def test_trends_deltas(tmp_env):
    """相邻报告 delta：score 差 + issue_counts 逐类差（8→2 等）。"""
    db = Database()
    db.init_schema()
    db.insert("tenders", {"id": "T-TREND", "name": "趋势测试",
                          "created_at": "2026-01-01 00:00:00"})
    rows = [
        ("QR-0001", 82.0, {"FACT_MISMATCH": 8, "INVALID_REFERENCE": 3},
         "2026-01-01 10:00:00"),
        ("QR-0002", 90.0, {"FACT_MISMATCH": 2, "INVALID_REFERENCE": 1},
         "2026-01-02 10:00:00"),
        ("QR-0003", 95.0, {"FACT_MISMATCH": 1, "INVALID_REFERENCE": 0},
         "2026-01-03 10:00:00"),
    ]
    for rid, score, counts, ts in rows:
        db.insert("quality_reports", {
            "id": rid, "tender_id": "T-TREND", "document_version": "v1",
            "score": score, "dimensions": "[]",
            "counts": "{}", "issue_counts": json.dumps(counts),
            "summary": "", "status": "草稿", "reviewer": "",
            "review_time": "", "created_at": ts,
        })
    r = runner.run_trends(db, "T-TREND")
    assert r["disclaimer"] == "基于项目内离线评估集，不代表通用准确率"
    assert [x["report_id"] for x in r["reports"]] == ["QR-0001", "QR-0002", "QR-0003"]
    assert r["deltas"][0] == {
        "from": "QR-0001", "to": "QR-0002",
        "score_delta": 8.0,
        "issue_deltas": {"FACT_MISMATCH": -6, "INVALID_REFERENCE": -2},
    }
    assert r["deltas"][1]["issue_deltas"] == {"FACT_MISMATCH": -1,
                                              "INVALID_REFERENCE": -1}


# ═══════════════════════════════════════════════════════════════════════
# API（scratch app 挂 eval router；正式注册在 main.py 步骤 4）
# ═══════════════════════════════════════════════════════════════════════
def test_eval_api_retrieval_disclaimer(tmp_env, m3_env):
    """GET /api/eval/retrieval → 200 + disclaimer（admin override 旁路权限）。"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.auth.deps import get_current_user
    from app.evaluation.api import router

    db = Database()
    db.init_schema()
    _seed_baseline_kb(db, m3_env)

    scratch = FastAPI()
    scratch.include_router(router)
    admin = {"id": "U-ADMIN", "username": "admin", "email": "",
             "display_name": "管理员", "roles": ["admin"], "permissions": set()}
    scratch.dependency_overrides[get_current_user] = lambda: admin
    with TestClient(scratch) as c:
        r = c.get("/api/eval/retrieval?k=10")
    assert r.status_code == 200
    body = r.json()
    assert body["disclaimer"] == "基于项目内离线评估集，不代表通用准确率"
    assert body["kb_queries"]["recall_at_k"] == 1.0


@pytest.mark.parametrize("endpoint", ["/api/eval/generation?tender_id=T-NOEXIST",
                                      "/api/eval/trends?tender_id=T-NOEXIST"])
def test_eval_api_404(tmp_env, m3_env, endpoint):
    """生成/趋势对不存在项目 → 404。"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.auth.deps import get_current_user
    from app.evaluation.api import router

    Database().init_schema()
    scratch = FastAPI()
    scratch.include_router(router)
    admin = {"id": "U-ADMIN", "username": "admin", "email": "",
             "display_name": "管理员", "roles": ["admin"], "permissions": set()}
    scratch.dependency_overrides[get_current_user] = lambda: admin
    with TestClient(scratch) as c:
        assert c.get(endpoint).status_code == 404
