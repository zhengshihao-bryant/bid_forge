# -*- coding: utf-8 -*-
"""
quality/checks/completeness.py —— M5-07/08/09 完整性检查 + 项目名一致性

四组检查（确定性、无 LLM）：

1. 需求完整性（M5-07，33 条非评分规范需求）
   响应表按 canonical 实时生成，任何现存 canonical 必在表中 —— 仅凭响应表
   无法发现"需求被删"。故以 **M1 招标需求（requirements 表）为权威清单**
   做反向覆盖校验：
   - M1 需求没有对应 canonical（canonical 行被删，连带 matches/maps）→
     REQUIREMENT_MISSING（★ 或高重要 → CRITICAL，否则 ERROR）。
   - 现存 canonical 按匹配状态分类：UNKNOWN → PENDING_CONFIRMATION(INFO)；
     MISSING（如实披露不满足）→ 不产生 issue（商务决策范畴，非质量缺陷，
     口径与验收基线 PENDING=9 一致）；FULL 但无证据 → PENDING(WARNING)；
     match 缺失 → PENDING(INFO)。

2. 评分项覆盖（M5-08）：score_points 每条 item/criteria 与正文
   key_overlap ≥0.2 → 覆盖；否则 SCORE_MISSING(WARNING)。空表自然通过。

3. 章节完整性（M5-09）：build_default_outline 26 个预期 id 逐对检查——
   行缺失 / content_md 为空 / status ∉ {已完成, 跳过} → SECTION_MISSING(CRITICAL)。

4. 项目名一致性：封面 CH-01 内容必须含 tenders.name（空白归一）→
   否则 PROJECT_MISMATCH(ERROR)。
"""
from __future__ import annotations

from typing import Any, Optional

from ...generation.outline import build_default_outline
from ...generation.response_table import BidResponseTableBuilder
from ...matching.similarity import key_overlap, normalize_text
from ..models import CheckContext, IssueType, QualityIssue, Severity

_SECTION_OK_STATUS = {"已完成", "跳过"}


def check_completeness(ctx: CheckContext) -> list[QualityIssue]:
    issues: list[QualityIssue] = []
    issues += _requirement_completeness(ctx)
    issues += _score_point_coverage(ctx)
    issues += _section_completeness(ctx)
    issues += _project_name(ctx)
    return issues


# ═══════════════════════════════════════════════════════════════════════
# 需求完整性（M5-07）
# ═══════════════════════════════════════════════════════════════════════
def _requirement_completeness(ctx: CheckContext) -> list[QualityIssue]:
    issues: list[QualityIssue] = []
    # ── 1) M1 招标需求 → canonical 反向覆盖 ──────────────────────────
    # 响应表按现存 canonical 实时生成，删行即消失；以 M1 需求表为权威
    # 清单，任何无 canonical 承接的 M1 需求都是"需求未在标书响应"。
    canonical_by_m1 = _canonical_by_m1(ctx.canonicals)
    for req in ctx.requirements:
        rid = req.get("id") or ""
        if rid in canonical_by_m1:
            continue
        critical = bool(req.get("is_star")) or (req.get("importance") or "中") in ("高", "★")
        issues.append(_issue(
            requirement_id=rid, issue_type=IssueType.REQUIREMENT_MISSING,
            severity=Severity.CRITICAL if critical else Severity.ERROR,
            message=f"需求「{req.get('title')}」未在标书任何章节响应"
                    f"（响应表行缺失）",
            source_refs=[{"requirement": rid}],
            suggestion="在对应章节补充响应，或在响应表如实标注不满足/待确认"))

    # ── 2) 现存 canonical 响应状态分类 ──────────────────────────────
    table = BidResponseTableBuilder(ctx.db).build(ctx.tender_id)
    in_table = {r["requirement_id"] for r in table["rows"]}
    for c in ctx.canonicals:
        if c.get("is_scoring"):
            continue
        rid = c.get("id") or ""
        match = ctx.matches.get(rid) or {}
        status = match.get("status") or ""
        if rid not in in_table:
            critical = bool(c.get("is_star")) or (c.get("importance") or "中") in ("高", "★")
            issues.append(_issue(
                requirement_id=rid, issue_type=IssueType.REQUIREMENT_MISSING,
                severity=Severity.CRITICAL if critical else Severity.ERROR,
                message=f"需求「{c.get('title')}」未在响应表中生成行",
                source_refs=[{"requirement": rid}],
                suggestion="重新生成响应表，或如实标注不满足/待确认"))
            continue
        if status == "UNKNOWN":
            issues.append(_issue(
                requirement_id=rid, issue_type=IssueType.PENDING_CONFIRMATION,
                severity=Severity.INFO,
                message=f"需求「{c.get('title')}」为 UNKNOWN（资料不足），"
                        f"响应列以【待确认】如实标注，需人工确认",
                source_refs=[{"requirement": rid, "status": status}]))
        elif status == "MISSING":
            # 如实披露不满足属商务决策，非质量缺陷 → 不产生 issue
            continue
        elif status == "FULL":
            ev_ids = _json_list(match.get("evidence_ids"))
            if not ev_ids:
                issues.append(_issue(
                    requirement_id=rid, issue_type=IssueType.PENDING_CONFIRMATION,
                    severity=Severity.WARNING,
                    message=f"需求「{c.get('title')}」响应为 FULL 但无证据"
                            f"支撑，需补充 EVD 引用",
                    source_refs=[{"requirement": rid}]))
        elif not status:
            issues.append(_issue(
                requirement_id=rid, issue_type=IssueType.PENDING_CONFIRMATION,
                severity=Severity.INFO,
                message=f"需求「{c.get('title')}」无匹配记录，需人工确认",
                source_refs=[{"requirement": rid}]))
    return issues


def _canonical_by_m1(canonicals: list[dict]) -> dict[str, str]:
    """M1 requirement_id → canonical id 反向映射（含评分细则 canonical）。

    依据 canonical_requirements.source_requirement_ids（JSON 数组）。
    """
    import json
    m1: dict[str, str] = {}
    for c in canonicals:
        raw = c.get("source_requirement_ids") or "[]"
        try:
            ids = json.loads(raw) if isinstance(raw, str) else raw
        except (TypeError, ValueError):
            ids = []
        for rid in ids if isinstance(ids, list) else []:
            m1[str(rid)] = c.get("id") or ""
    return m1


# ═══════════════════════════════════════════════════════════════════════
# 评分项覆盖（M5-08）
# ═══════════════════════════════════════════════════════════════════════
def _score_point_coverage(ctx: CheckContext) -> list[QualityIssue]:
    issues: list[QualityIssue] = []
    if not ctx.score_points:
        return issues
    texts = [s.get("content_md") or "" for s in ctx.sections]
    for sp in ctx.score_points:
        item = sp.get("item") or ""
        if not item:
            continue
        if any(_covers(item, t) for t in texts):
            continue
        issues.append(_issue(
            issue_type=IssueType.SCORE_MISSING, severity=Severity.WARNING,
            message=f"评分项「{item}」在标书正文无对应响应内容",
            source_refs=[{"score_point": sp.get("id") or ""}],
            suggestion="在对应章节补充该评分项的实质响应"))
    return issues


def _covers(item: str, content: str) -> bool:
    return item in content or key_overlap(item, content) >= 0.2


# ═══════════════════════════════════════════════════════════════════════
# 章节完整性（M5-09）
# ═══════════════════════════════════════════════════════════════════════
def _section_completeness(ctx: CheckContext) -> list[QualityIssue]:
    issues: list[QualityIssue] = []
    by_id = {s.get("section_id"): s for s in ctx.sections}
    for sid in _outline_section_ids():
        row = by_id.get(sid)
        if row is None:
            issues.append(_issue(
                issue_type=IssueType.SECTION_MISSING,
                severity=Severity.CRITICAL,
                message=f"章节「{sid}」在大纲中存在但未生成",
                source_refs=[{"section": sid}],
                suggestion="重新生成该章节"))
            continue
        content = (row.get("content_md") or "").strip()
        if not content:
            issues.append(_issue(
                section_id=sid, issue_type=IssueType.SECTION_MISSING,
                severity=Severity.CRITICAL,
                message=f"章节「{sid}」内容为空",
                source_refs=[{"section": sid}],
                suggestion="重新生成该章节"))
            continue
        if (row.get("status") or "") not in _SECTION_OK_STATUS:
            issues.append(_issue(
                section_id=sid, issue_type=IssueType.SECTION_MISSING,
                severity=Severity.CRITICAL,
                message=f"章节「{sid}」状态为 {row.get('status')}，未完成生成",
                source_refs=[{"section": sid}],
                suggestion="重新生成该章节"))
    return issues


def _outline_section_ids() -> list[str]:
    ids: list[str] = []

    def walk(ch) -> None:
        ids.append(ch.id)
        for c in getattr(ch, "children", []) or []:
            walk(c)

    for ch in build_default_outline().chapters:
        walk(ch)
    return ids


# ═══════════════════════════════════════════════════════════════════════
# 项目名一致性
# ═══════════════════════════════════════════════════════════════════════
def _project_name(ctx: CheckContext) -> list[QualityIssue]:
    name = ctx.tender.get("name") or ""
    if not name:
        return []
    ch01 = next((s for s in ctx.sections
                 if s.get("section_id") == "CH-01"), None)
    content = (ch01 or {}).get("content_md") or ""
    if not content:
        return []
    if normalize_text(name) in normalize_text(content):
        return []
    return [_issue(
        section_id="CH-01", issue_type=IssueType.PROJECT_MISMATCH,
        severity=Severity.ERROR,
        message=f"封面项目名与招标项目「{name}」不一致",
        source_refs=[{"section": "CH-01", "expected": name}],
        suggestion=f"将封面项目名改为「{name}」")]


# ═══════════════════════════════════════════════════════════════════════
def _json_list(raw: Any) -> list:
    import json
    if not raw:
        return []
    if isinstance(raw, list):
        return raw
    try:
        v = json.loads(raw)
        return v if isinstance(v, list) else []
    except (TypeError, ValueError):
        return []


def _issue(section_id: str = "", requirement_id: str = "",
           issue_type: IssueType = IssueType.FORMAT_ERROR,
           severity: Severity = Severity.WARNING,
           message: str = "", source_refs: Optional[list[dict]] = None,
           suggestion: str = "") -> QualityIssue:
    return QualityIssue(
        section_id=section_id, requirement_id=requirement_id,
        issue_type=issue_type, severity=severity, message=message,
        source_refs=source_refs or [], suggestion=suggestion)


__all__ = ["check_completeness"]
