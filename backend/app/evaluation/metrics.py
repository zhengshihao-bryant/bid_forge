# -*- coding: utf-8 -*-
"""
app/evaluation/metrics.py —— M7-07 指标实现

全部复用 M3/M5 现成件，不引入新算法：

RAG 评估
- recall_at_k / mrr：对检索结果序列做命中/排名统计
  （SearchService.search，vector_store.py:356-377）
- run_retrieval_eval：逐条查询跑检索 → 期望文件是否进 top-k
- citation_accuracy：正文 EVD 引用是否全部属于证据池
  （_EVD_RE 同款正则，consistency.py:131-141 的反向口径）
生成评估
- citation_completeness：章节 evidence_refs（生成时的金标引用集）在正文
  中的实际出现比例
- fact_consistency：事实区声明数（extract_claims，scan.py:145）vs
  事实类 ERROR/CRITICAL 问题数（check_facts，facts.py:56）
- requirement_coverage：RequirementSectionMapper.coverage（mapping.py:86）
  + REQUIREMENT_MISSING 反向口径（completeness.py:39）
趋势
- quality_trends：quality_reports 序列 + 相邻 delta（score 差 +
  issue_counts 逐类差）。EVD 编号跨版本会重置，只比 issue_counts 类别数
  （口径见 ROADMAP M7）。

口径声明（铁律）：基于项目内离线评估集，不代表通用准确率。
"""

from __future__ import annotations

import json
import re

from ..db import Database
from ..services.generation.mapping import RequirementSectionMapper
from ..services.quality.checks.completeness import check_completeness
from ..services.quality.checks.facts import check_facts
from ..services.quality.checks.scan import extract_claims
from ..services.quality.context import build_check_context
from ..services.quality.models import IssueType, Severity

# 与 consistency.py:33 同款证据引用正则（EVD-后至少 3 位数字）
_EVD_RE = re.compile(r"EVD-\d{3,}")

# 事实类问题判定：check_facts 产出的 ERROR/CRITICAL 才算不一致
# （WARNING/INFO 是提示级，不进"事实一致率"分子）
_FACT_SEVERITIES = {Severity.CRITICAL, Severity.ERROR}

# 完整性反向口径：这三类 issue 都属于"需求未响应"
_MISSING_TYPES = {IssueType.REQUIREMENT_MISSING}


# ═══════════════════════════════════════════════════════════════════════
# RAG 评估
# ═══════════════════════════════════════════════════════════════════════
def recall_at_k(rows: list[dict]) -> float:
    """rows: [{hit: bool}] → 命中率（0/0 视为 1.0，避免空集除零）。"""
    if not rows:
        return 1.0
    return sum(1 for r in rows if r["hit"]) / len(rows)


def mrr(rows: list[dict]) -> float:
    """rows: [{rank: int|None}] → 平均倒数排名（未命中记 0）。"""
    if not rows:
        return 1.0
    return sum(1.0 / r["rank"] for r in rows if r.get("rank")) / len(rows)


def run_retrieval_eval(svc, queries: list[dict], k: int = 10) -> dict:
    """逐条查询 → top-k 检索 → 期望文件命中判定。

    svc: SearchService（有 search(query, top_k) 方法）；k: top-k。
    返回 {rows, recall_at_k, mrr, k, excluded_notes}——
    rows 含 category 命中作为诊断字段，指标只认 expect_file。
    """
    rows: list[dict] = []
    excluded: list[str] = []
    for q in queries:
        if not q.get("expect_file"):
            excluded.append(q.get("query") or q.get("requirement_title") or "")
            continue
        result = svc.search(q["query"], top_k=k)
        hits = result.hits if hasattr(result, "hits") else result
        file_names = [h.file_name for h in hits]
        rank = None
        for i, name in enumerate(file_names):
            if name == q["expect_file"]:
                rank = i + 1
                break
        rows.append({
            "query": q["query"],
            "expect_file": q["expect_file"],
            "hit": rank is not None,
            "rank": rank,
            "top_files": file_names[:k],
            "category_hit": q["expect_category"] in (
                [h.category for h in hits] if q.get("expect_category") else []),
        })
    return {
        "k": k,
        "rows": rows,
        "recall_at_k": round(recall_at_k(rows), 4),
        "mrr": round(mrr(rows), 4),
        "evaluated": len(rows),
        "excluded_queries": excluded,
    }


def citation_accuracy(content: str, evidence_ids: set[str]) -> dict:
    """正文 EVD 引用是否全部属于证据池（consistency.py:131 反向口径）。

    无引用 → 1.0（宁缺勿假；引用完整性由 citation_completeness 管）。
    """
    refs = _EVD_RE.findall(content or "")
    invalid = [r for r in refs if r not in evidence_ids]
    return {
        "total_refs": len(refs),
        "invalid_refs": invalid,
        "citation_accuracy": round(
            1.0 - len(invalid) / len(refs), 4) if refs else 1.0,
    }


# ═══════════════════════════════════════════════════════════════════════
# 生成评估
# ═══════════════════════════════════════════════════════════════════════
def citation_completeness(sections: list[dict]) -> dict:
    """引用完整率 = 金标引用集（evidence_refs）在正文中的实际出现比例。

    sections: generation_sections 行列表（evidence_refs/content_md 列为 JSON/文本）。
    """
    total, present = 0, 0
    per_section: list[dict] = []
    for s in sections:
        gold = json.loads(s.get("evidence_refs") or "[]")
        content = s.get("content_md") or ""
        found = [r for r in gold if r in content]
        total += len(gold)
        present += len(found)
        if gold:
            per_section.append({
                "section_id": s.get("section_id", ""),
                "gold_refs": gold,
                "missing_refs": [r for r in gold if r not in found],
            })
    return {
        "gold_refs": total,
        "present_refs": present,
        "citation_completeness": round(present / total, 4) if total else 1.0,
        "per_section": per_section,
    }


def fact_consistency(ctx) -> dict:
    """事实一致率 = 1 − 事实类 ERROR/CRITICAL 问题数 / 事实区声明数。

    声明数 = 事实区各章节 extract_claims 的 claim 总数（含冲突申领）；
    事实类问题 = check_facts 产出中 severity ∈ {CRITICAL, ERROR} 的条数
    （LAYER_A/B/CERT 三类事实检查的失败都是不一致）。声明数为 0 时记 1.0
    并置 no_fact_zone 标志——"没写事实"不算"写错了事实"。
    """
    claims = 0
    for section in ctx.fact_zone_sections():
        claims += len(extract_claims(section.get("content_md") or "",
                                     section.get("section_id") or "",
                                     ctx.registry))
    issues = check_facts(ctx)
    fact_issues = [i for i in issues
                   if getattr(i, "severity", None) in _FACT_SEVERITIES]
    return {
        "fact_claims": claims,
        "fact_issues": len(fact_issues),
        "fact_consistency": round(
            1.0 - len(fact_issues) / claims, 4) if claims else 1.0,
        "no_fact_zone": claims == 0,
        "issue_detail": [
            {"section_id": i.section_id, "issue_type": i.issue_type,
             "severity": i.severity, "message": i.message[:200]}
            for i in fact_issues[:20]],
    }


def requirement_coverage(db: Database, tender_id: str,
                         ctx=None) -> dict:
    """需求覆盖率双口径：正向（规范需求→章节映射）+ 反向（MISSING 检查）。

    正向 = CoverageStats.mapped/total（mapping.py:86）；
    反向 = 1 − REQUIREMENT_MISSING 数 / 需求总数（completeness.py:39）。
    ctx 不传时用 build_check_context 现装（纯 DB 读，无 LLM）。
    """
    stats = RequirementSectionMapper(db).coverage(tender_id)
    ctx = ctx or build_check_context(db, tender_id)
    missing = sum(1 for i in check_completeness(ctx)
                  if getattr(i, "issue_type", None) == IssueType.REQUIREMENT_MISSING)
    total_reqs = len(ctx.requirements) or stats.total
    return {
        "forward": {
            "total": stats.total, "mapped": stats.mapped,
            "unmapped": stats.unmapped,
            "requirement_coverage": round(
                stats.mapped / stats.total, 4) if stats.total else 1.0,
        },
        "reverse": {
            "total_requirements": total_reqs,
            "missing_count": missing,
            "requirement_coverage": round(
                1.0 - missing / total_reqs, 4) if total_reqs else 1.0,
        },
    }


# ═══════════════════════════════════════════════════════════════════════
# 趋势
# ═══════════════════════════════════════════════════════════════════════
def quality_trends(db: Database, tender_id: str) -> dict:
    """质量趋势：报告序列 + 相邻 delta（score 差 + issue_counts 逐类差）。

    EVD 编号跨版本重置（ROADMAP 已知限制）——趋势只比 issue_counts 类别数，
    不比编号。
    """
    rows = db.query(
        "SELECT id, score, issue_counts, status, reviewer, review_time, "
        "created_at FROM quality_reports WHERE tender_id = ? "
        "ORDER BY created_at, id", (tender_id,))
    series = [{
        "report_id": r["id"],
        "score": r["score"],
        "issue_counts": json.loads(r["issue_counts"] or "{}"),
        "status": r["status"],
        "reviewer": r["reviewer"],
        "created_at": r["created_at"],
    } for r in rows]
    deltas: list[dict] = []
    for prev, cur in zip(series, series[1:]):
        keys = set(prev["issue_counts"]) | set(cur["issue_counts"])
        deltas.append({
            "from": prev["report_id"], "to": cur["report_id"],
            "score_delta": round(cur["score"] - prev["score"], 2),
            "issue_deltas": {
                k: int(cur["issue_counts"].get(k, 0))
                   - int(prev["issue_counts"].get(k, 0)) for k in sorted(keys)},
        })
    return {"reports": series, "deltas": deltas}
