# -*- coding: utf-8 -*-
"""
quality/scoring.py —— M5-14 五维质量评分（确定性公式）

    score_report(issues) → (总分, [DimensionScore])

五维（口径声明：内部质量指标，非"准确率"；扣分按问题严重度权重累加）：
    完整性     REQUIREMENT_MISSING / SCORE_MISSING / SECTION_MISSING / SEMANTIC_COVERAGE
    事实准确性 NUMBER|PERSON|CERTIFICATE|PROJECT_MISMATCH / PENDING_CONFIRMATION
    证据覆盖   INVALID_REFERENCE
    一致性     CONFLICT
    格式完整性 FORMAT_ERROR

每维 = clamp(100 − Σ 严重度权重, 0, 100)（权重见 models.SEVERITY_WEIGHT：
CRITICAL 20 / ERROR 10 / WARNING 3 / INFO 0.5）；总分 = 5 维均值 round(,1)。

口径边界：
- PENDING_CONFIRMATION（INFO 0.5）计入事实准确性扣分——待确认是事实未定，
  提示性强但仍是质量损耗；与验收基线（9 条 PENDING → 事实准确性 95.5、
  总分 99.1）一致。
- 同一问题只在其所属维度扣一次（DIMENSION_ISSUE_MAP 逐维过滤，无重叠）。
"""
from __future__ import annotations

from .models import (
    DimensionScore, IssueType, QualityIssue, SEVERITY_WEIGHT, Severity)

DIMENSION_ISSUE_MAP: dict[str, frozenset[IssueType]] = {
    "完整性": frozenset({
        IssueType.REQUIREMENT_MISSING, IssueType.SCORE_MISSING,
        IssueType.SECTION_MISSING, IssueType.SEMANTIC_COVERAGE,
    }),
    "事实准确性": frozenset({
        IssueType.NUMBER_MISMATCH, IssueType.PERSON_MISMATCH,
        IssueType.CERTIFICATE_MISMATCH, IssueType.PROJECT_MISMATCH,
        IssueType.PENDING_CONFIRMATION,
    }),
    "证据覆盖": frozenset({IssueType.INVALID_REFERENCE}),
    "一致性": frozenset({IssueType.CONFLICT}),
    "格式完整性": frozenset({IssueType.FORMAT_ERROR}),
}

_DIMENSION_NAMES = list(DIMENSION_ISSUE_MAP)


def score_report(issues: list[QualityIssue],
                 ) -> tuple[float, list[DimensionScore]]:
    """按 5 维扣分公式计分。返回 (总分, 各维明细)。"""
    dimensions: list[DimensionScore] = []
    for name in _DIMENSION_NAMES:
        types = DIMENSION_ISSUE_MAP[name]
        related = [i for i in issues if i.issue_type in types]
        deduction = sum(SEVERITY_WEIGHT[i.severity] for i in related)
        score = max(0.0, min(100.0, 100.0 - deduction))
        dimensions.append(DimensionScore(
            name=name, score=round(score, 1),
            deductions=[_short(i) for i in related]))
    total = round(sum(d.score for d in dimensions) / len(dimensions), 1)
    return total, dimensions


def _short(issue: QualityIssue) -> str:
    return f"[{issue.severity.value}] {issue.message[:60]}"


__all__ = ["score_report", "DIMENSION_ISSUE_MAP"]
