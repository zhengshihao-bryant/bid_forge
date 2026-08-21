# -*- coding: utf-8 -*-
"""
quality/checks/__init__.py —— M5 检查器编排

all_checks(ctx, include_llm=False, llm=None) → list[QualityIssue]

顺序：事实（数字/人员/资质/项目）→ 完整性（需求/评分/章节/项目名）→
一致性（冲突/引用/待确认）→ 格式。全部确定性、无 LLM；LLM 语义覆盖
审查为可选 include_llm 参数（离线 FakeLLM 空返回 → 不新增 issue）。
"""
from __future__ import annotations

from typing import Optional

from ..models import CheckContext, QualityIssue
from .completeness import check_completeness
from .consistency import check_consistency
from .facts import check_facts
from .format_check import check_format


def all_checks(ctx: CheckContext, include_llm: bool = False,
               llm=None) -> list[QualityIssue]:
    """跑全部分类检查，返回问题列表（未排序，report 阶段按 severity 分组）。"""
    issues: list[QualityIssue] = []
    issues += check_facts(ctx)
    issues += check_completeness(ctx)
    issues += check_consistency(ctx)
    issues += check_format(ctx)
    if include_llm and llm is not None:
        from .llm_judge import check_semantic_coverage
        issues += check_semantic_coverage(ctx, llm)
    return issues


__all__ = ["all_checks"]
