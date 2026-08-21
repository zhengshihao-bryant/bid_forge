# -*- coding: utf-8 -*-
"""matching.models —— M3 数据模型统一出口。"""
from .evidence import Evidence, EvidenceSourceType, EvidenceValidation
from .match_result import (Conflict, MatchMethod, MatchReport, MatchResult,
                           MatchStatus, TraceLink)
from .requirement import (CanonicalRequirement, Constraint,
                          RequirementSourceRef, RequirementTypeM3, TYPE_LABELS)

__all__ = [
    "CanonicalRequirement", "Constraint", "RequirementSourceRef",
    "RequirementTypeM3", "TYPE_LABELS",
    "Evidence", "EvidenceSourceType", "EvidenceValidation",
    "Conflict", "MatchMethod", "MatchReport", "MatchResult",
    "MatchStatus", "TraceLink",
]
