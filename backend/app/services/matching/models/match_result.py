# -*- coding: utf-8 -*-
"""
matching/models/match_result.py —— M3 匹配判定结果（M3-12/13/14）

四种最终状态恒保留（M3-12）：
    FULL     明确满足（有 VALID 证据支撑）
    PARTIAL  有能力但未完全达到要求
    MISSING  资料明确显示不满足（有明确相反证据）
    UNKNOWN  现有资料不足以判断（没有证据 ≠ 不满足）

Conflict 记录证据冲突及仲裁（M3-13）；TraceLink 是需求 → 证据的可追溯链（M3-14）。
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field

from ....schemas import now_str


class MatchStatus(str, Enum):
    """四种最终状态（M3-12，口径铁律：没有证据 ≠ 不满足）。"""
    FULL = "FULL"
    PARTIAL = "PARTIAL"
    MISSING = "MISSING"
    UNKNOWN = "UNKNOWN"

    @property
    def label(self) -> str:
        return _STATUS_LABELS[self]


_STATUS_LABELS = {
    MatchStatus.FULL: "满足",
    MatchStatus.PARTIAL: "部分满足",
    MatchStatus.MISSING: "不满足",
    MatchStatus.UNKNOWN: "待确认",
}


class MatchMethod(str, Enum):
    """判定路径（混合匹配策略 M3-09，method 落库可追溯）。"""
    RULE = "rule"                  # 规则引擎（结构化约束 × 能力卡）
    CARD = "card"                  # 能力卡匹配（非数值/关键词）
    RAG = "rag"                    # 语义检索证据
    LLM_JUDGE = "llm_judge"        # LLM 依据证据判定
    HEURISTIC = "heuristic"        # 离线确定性判定（无 LLM 回退）


class Conflict(BaseModel):
    """证据冲突（M3-13）—— 同指标多证据数值不一致。"""
    metric: str = ""                       # 冲突指标（设备接入/质保期/…）
    claim_a: dict[str, Any] = Field(default_factory=dict)   # {evidence_id, value, unit, source_type, document, authority}
    claim_b: dict[str, Any] = Field(default_factory=dict)
    resolution: str = ""                   # authority（来源权威）/ time（文档新旧）/ unresolved
    winner_evidence_id: str = ""           # 仲裁胜出证据
    note: str = ""


class MatchResult(BaseModel):
    """匹配记录（MAT-XXXX）—— 需求 × 证据的判定关系。"""
    id: str = ""
    tender_id: str
    requirement_id: str                    # REQ-C-XXXX
    status: MatchStatus
    confidence: float = 0.0                # 0-1
    reason: str = ""                       # 判定理由（人可读、可复核）
    method: MatchMethod = MatchMethod.HEURISTIC
    evidence_ids: list[str] = Field(default_factory=list)
    conflicts: list[Conflict] = Field(default_factory=list)
    created_at: str = Field(default_factory=now_str)


class TraceLink(BaseModel):
    """可追溯链（M3-14）：REQ-C → MATCH → EVD → CAP/chunk → DOC → 章节 → 原文。"""
    requirement_id: str
    match_id: str
    evidence_id: str
    source_type: str
    source_id: str
    document: str
    section_path: str = ""
    page: Optional[int] = None
    block_id: str = ""
    snippet: str = ""


class MatchReport(BaseModel):
    """tender 级匹配报告（响应表的数据源）。"""
    tender_id: str
    total: int = 0
    counts: dict[str, int] = Field(default_factory=dict)   # status → n
    matches: list[MatchResult] = Field(default_factory=list)
