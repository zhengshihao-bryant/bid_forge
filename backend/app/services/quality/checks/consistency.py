# -*- coding: utf-8 -*-
"""
quality/checks/consistency.py —— M5-10/11/12 一致性检查

三组检查（确定性、无 LLM）：

1. 跨章节冲突（M5-10）
   复用 extract_claims 对事实区章节取值，按指标分组。同一指标两条声明
   区间不相交 且 至少一条不匹配注册表（KB 不一致）→ CONFLICT(ERROR)。
   KB 一致豁免：质保 2 年(CH-05-2/3) vs 3 年(CH-07-1/2) 均匹配注册表
   {2,3} → 不报冲突；2000→5000 变异（5000 不匹配注册表）→ 报。

2. 引用有效性（M5-11）
   事实区正文扫 EVD-\\d{3,} 引用；∉ 本招标证据池 → INVALID_REFERENCE(CRITICAL)。
   边界：系统无企业绑定字段，以整个证据池为企业池（M5 边界，见报告）。
   "证据在池但引用位置可疑"的误引判定需要需求↔章节映射推断，判据不可靠，
   本期不启用（注释留档）。

3. 待确认收集（M5-12）
   事实区扫【待确认】标记，每条 → PENDING_CONFIRMATION(INFO)（section + 上下文）。
   需求回显区（CH-08/CH-05-4）排除：回显原文不是标书自述待确认项。
"""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Optional

from ..models import CheckContext, IssueType, QualityIssue, Severity
from .facts import _to_implied, _values_match
from .scan import extract_claims

_EVD_RE = re.compile(r"EVD-\d{3,}")
_PENDING_RE = re.compile(r"【待确认】")


def check_consistency(ctx: CheckContext) -> list[QualityIssue]:
    issues: list[QualityIssue] = []
    issues += _conflicts(ctx)
    issues += _references(ctx)
    issues += _pending_markers(ctx)
    return issues


# ═══════════════════════════════════════════════════════════════════════
# 跨章节冲突（M5-10）
# ═══════════════════════════════════════════════════════════════════════
def _conflicts(ctx: CheckContext) -> list[QualityIssue]:
    issues: list[QualityIssue] = []
    claims: list = []
    for section in ctx.fact_zone_sections():
        claims += extract_claims(section.get("content_md") or "",
                                 section.get("section_id") or "",
                                 ctx.registry)
    by_metric: dict[str, list] = defaultdict(list)
    for c in claims:
        by_metric[c.metric].append(c)

    for metric, cs in by_metric.items():
        entries = ctx.registry.metric(metric)
        sections = {c.section_id for c in cs}
        if len(sections) < 2:
            continue
        involved: list[dict] = []
        found = False
        for i in range(len(cs)):
            for j in range(i + 1, len(cs)):
                a, b = cs[i], cs[j]
                if a.section_id == b.section_id:
                    continue
                norm_a = _to_implied(a.lo, a.hi, a.unit,
                                     entries[0].unit if entries else "count")
                norm_b = _to_implied(b.lo, b.hi, b.unit,
                                     entries[0].unit if entries else "count")
                if norm_a is None or norm_b is None:
                    continue
                if _values_match(norm_a[0], norm_a[1], norm_b[0], norm_b[1],
                                 entries[0].unit if entries else "count"):
                    continue                       # 相交 → 同一数值，非冲突
                if _all_kb_consistent(norm_a, norm_b, entries):
                    continue                       # 全部匹配注册表 → KB 一致豁免
                found = True
                involved.append({"section": a.section_id,
                                 "value": _fmt(a), "unit": a.unit})
                involved.append({"section": b.section_id,
                                 "value": _fmt(b), "unit": b.unit})
        if not found:
            continue
        first = cs[0]
        issues.append(QualityIssue(
            section_id=first.section_id,
            issue_type=IssueType.CONFLICT, severity=Severity.ERROR,
            message=f"指标「{metric}」在多个章节自相矛盾："
                    + "；".join(f"{x['section']} 为 {x['value']}{x['unit'] or ''}"
                               for x in involved),
            source_refs=involved,
            suggestion="统一该指标为与知识库一致的数值（以注册表为准）"))
    return issues


def _all_kb_consistent(norm_a: tuple, norm_b: tuple,
                       entries) -> bool:
    """两条声明都命中注册表任一条目 → 视为 KB 内部既存口径差异，豁免。"""
    return _matches_any(norm_a, entries) and _matches_any(norm_b, entries)


def _matches_any(norm: tuple, entries) -> bool:
    for e in entries:
        if _values_match(norm[0], norm[1], e.value, e.value_hi,
                         e.unit or "count"):
            return True
    return False


def _fmt(c) -> str:
    s = f"{c.lo}"
    if c.hi is not None:
        s = f"{c.lo}-{c.hi}"
    return s


# ═══════════════════════════════════════════════════════════════════════
# 引用有效性（M5-11）
# ═══════════════════════════════════════════════════════════════════════
def _references(ctx: CheckContext) -> list[QualityIssue]:
    issues: list[QualityIssue] = []
    pool = ctx.evidences
    for section in ctx.fact_zone_sections():
        sid = section.get("section_id") or ""
        content = section.get("content_md") or ""
        for m in _EVD_RE.finditer(content):
            ref = m.group(0)
            if ref in pool:
                continue
            issues.append(QualityIssue(
                section_id=sid, issue_type=IssueType.INVALID_REFERENCE,
                severity=Severity.CRITICAL,
                message=f"证据引用「{ref}」不存在或不属于本招标",
                source_refs=[{"section": sid, "reference": ref}],
                suggestion=f"核对「{ref}」引用来源，删除或更正为有效证据"))
    return issues


# ═══════════════════════════════════════════════════════════════════════
# 待确认收集（M5-12）
# ═══════════════════════════════════════════════════════════════════════
def _pending_markers(ctx: CheckContext) -> list[QualityIssue]:
    issues: list[QualityIssue] = []
    for section in ctx.fact_zone_sections():
        sid = section.get("section_id") or ""
        content = section.get("content_md") or ""
        for lineno, line in enumerate(content.split("\n")):
            if not _PENDING_RE.search(line):
                continue
            context = line.strip()[:80]
            issues.append(QualityIssue(
                section_id=sid, issue_type=IssueType.PENDING_CONFIRMATION,
                severity=Severity.INFO,
                message=f"章节「{sid}」存在待确认项：{context}",
                source_refs=[{"section": sid, "marker": "【待确认】",
                              "context": context}],
                suggestion="人工确认后回填具体数值/口径，再移除【待确认】标注"))
    return issues


__all__ = ["check_consistency"]
