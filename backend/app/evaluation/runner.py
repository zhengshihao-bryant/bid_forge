# -*- coding: utf-8 -*-
"""
app/evaluation/runner.py —— M7-07 评估执行器

三个执行入口（供 api.py 与 verify_m7 复用）：
- run_retrieval：RAG 检索评估（KB 8 条基线 + 需求子集），确定性、无 LLM
- run_generation：生成评估（引用完整率 / 事实一致率 / 需求覆盖率）
- run_trends：质量趋势（报告序列 + 相邻 delta）

每个结果 dict 都带 disclaimer（口径声明铁律）。
"""

from __future__ import annotations

from ..db import Database
from ..services.quality.context import build_check_context
from ..services.vector_store import create_search_service
from . import golden, metrics

DISCLAIMER = "基于项目内离线评估集，不代表通用准确率"


def run_retrieval(svc=None, k: int = 10) -> dict:
    """RAG 检索评估：KB 基线 + 需求→证据子集（expect_file 非空者）。"""
    svc = svc or create_search_service()
    kb = metrics.run_retrieval_eval(svc, golden.RETRIEVAL_QUERIES, k=k)
    req = metrics.run_retrieval_eval(
        svc, golden.requirement_rag_queries(), k=k)
    return {
        "disclaimer": DISCLAIMER,
        "k": k,
        "kb_queries": kb,
        "requirement_queries": req,
        "combined": {
            "recall_at_k": round(
                (kb["recall_at_k"] * kb["evaluated"]
                 + req["recall_at_k"] * req["evaluated"])
                / max(1, kb["evaluated"] + req["evaluated"]), 4),
            "mrr": round(
                (kb["mrr"] * kb["evaluated"]
                 + req["mrr"] * req["evaluated"])
                / max(1, kb["evaluated"] + req["evaluated"]), 4),
            "evaluated": kb["evaluated"] + req["evaluated"],
        },
    }


def run_generation(db: Database, tender_id: str) -> dict:
    """生成评估：引用完整率 + 引用准确率 + 事实一致率 + 需求覆盖率。

    无生成内容时置 no_content 标志，指标记 1.0 空集口径（语义：无产出
    不算产出错误；调用方应提示"请先完成生成"）。
    """
    ctx = build_check_context(db, tender_id)
    sections = ctx.sections
    full_text = "\n".join(s.get("content_md") or "" for s in sections)
    evidence_ids = set(ctx.evidences.keys())
    coverage = metrics.requirement_coverage(db, tender_id, ctx=ctx)
    return {
        "disclaimer": DISCLAIMER,
        "tender_id": tender_id,
        "no_content": not any((s.get("content_md") or "").strip()
                              for s in sections),
        "citation_completeness": metrics.citation_completeness(sections),
        "citation_accuracy": metrics.citation_accuracy(full_text,
                                                       evidence_ids),
        "fact_consistency": metrics.fact_consistency(ctx),
        "requirement_coverage": coverage,
    }


def run_trends(db: Database, tender_id: str) -> dict:
    """质量趋势（报告序列 + 相邻 delta）。"""
    trends = metrics.quality_trends(db, tender_id)
    return {"disclaimer": DISCLAIMER, "tender_id": tender_id, **trends}


def run_summary(db: Database, tender_id: str = "", k: int = 10) -> dict:
    """汇总：检索（RAG）+ 生成 + 趋势（tender_id 空则趋势返回空序列）。"""
    result: dict = {
        "disclaimer": DISCLAIMER,
        "retrieval": run_retrieval(k=k),
    }
    if tender_id:
        result["generation"] = run_generation(db, tender_id)
        result["trends"] = run_trends(db, tender_id)
    else:
        result["note"] = "未提供 tender_id，跳过生成与趋势评估"
    return result
