# -*- coding: utf-8 -*-
"""
quality/models.py —— M5 标书一致性与质量检查引擎实体（M5-01）

数据流：

    BidDocument + Requirements + Evidence/Capability + MatchResult
        --QualityRunner.run--> QualityReport（含 QualityIssue 列表）
        --scoring--> 5 维评分 → QualityReport.score
        --finalize--> final.docx + final.md + quality-report.json + 审计快照

命名约定：
- IssueType 12 类对齐 M5-01 建议清单；规格的 EVIDENCE_INVALID 由
  INVALID_REFERENCE 承载，FACT_ERROR / FACT_UNSUPPORTED 由各锚定 mismatch
  类型承载（NUMBER/PERSON/CERTIFICATE/PROJECT_MISMATCH）。
- 中文枚举值直接入库（SQLite 存字符串，沿用项目约定）。
- FactRegistryEntry 为临时构建产物（M5-10 事实注册表，不落库）。
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field

from ...schemas import now_str


class IssueType(str, Enum):
    """M5-01 问题类型 —— 12 类。"""

    NUMBER_MISMATCH = "NUMBER_MISMATCH"             # 数字与证据/能力卡不一致或不可溯源
    PERSON_MISMATCH = "PERSON_MISMATCH"             # 人员姓名/职位/年限/证书与能力卡不一致
    CERTIFICATE_MISMATCH = "CERTIFICATE_MISMATCH"   # 资质证书名称/编号/有效期与知识库不一致
    PROJECT_MISMATCH = "PROJECT_MISMATCH"           # 项目业绩（名称/金额/规模）与知识库不一致
    REQUIREMENT_MISSING = "REQUIREMENT_MISSING"     # 需求未在标书响应
    SCORE_MISSING = "SCORE_MISSING"                 # 评分项无对应响应
    SECTION_MISSING = "SECTION_MISSING"             # 章节缺失/未生成
    CONFLICT = "CONFLICT"                           # 跨章节同一事实自相矛盾
    INVALID_REFERENCE = "INVALID_REFERENCE"         # 证据引用无效/不属于本企业本招标
    PENDING_CONFIRMATION = "PENDING_CONFIRMATION"   # 待确认项（【待确认】）
    SEMANTIC_COVERAGE = "SEMANTIC_COVERAGE"         # LLM 语义覆盖不足
    FORMAT_ERROR = "FORMAT_ERROR"                   # 格式问题（可自动修复）


class Severity(str, Enum):
    """M5-01 严重程度 —— 评分扣分权重见 scoring.SEVERITY_WEIGHT。"""

    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


SEVERITY_WEIGHT: dict[Severity, float] = {
    Severity.CRITICAL: 20.0,
    Severity.ERROR: 10.0,
    Severity.WARNING: 3.0,
    Severity.INFO: 0.5,
}


class IssueStatus(str, Enum):
    """M5-16 人工审核闭环 —— 问题处理生命周期。"""

    PENDING = "待处理"
    CONFIRMED = "已确认"
    IGNORED = "已忽略"
    FIXED = "已修复"


class QualityIssue(BaseModel):
    """M5-01 统一问题模型 —— 一次检查产出的单条问题。"""

    id: str = ""                                   # {report_id}-{i:04d}
    report_id: str = ""
    tender_id: str = ""
    document_version: str = ""
    section_id: str = ""
    requirement_id: str = ""
    issue_type: IssueType = IssueType.FORMAT_ERROR
    severity: Severity = Severity.WARNING
    status: IssueStatus = IssueStatus.PENDING
    message: str = ""
    source_refs: list[dict[str, Any]] = Field(default_factory=list)  # EVD/CAP/章节溯源
    suggestion: str = ""
    autofixable: bool = False
    created_at: str = Field(default_factory=now_str)


class DimensionScore(BaseModel):
    """M5-14 单维质量得分（完整性/事实准确性/证据覆盖/一致性/格式完整性）。"""

    name: str = ""
    score: float = 100.0
    deductions: list[str] = Field(default_factory=list)   # 扣分明细（issue 摘要）


class QualityReport(BaseModel):
    """M5-15 质量报告 —— 检查总产物。"""

    id: str = "QR-0001"
    tender_id: str = ""
    document_version: str = ""
    score: float = 0.0
    dimensions: list[DimensionScore] = Field(default_factory=list)
    counts: dict[str, int] = Field(default_factory=dict)         # critical/error/warning/info/pending
    issue_counts: dict[str, int] = Field(default_factory=dict)   # by IssueType
    summary: str = ""
    status: str = "草稿"                              # 草稿 → 已批准（finalize）
    reviewer: str = ""
    review_time: str = ""
    created_at: str = Field(default_factory=now_str)


class FactRegistryEntry(BaseModel):
    """M5-10 事实注册表条目 —— 知识库结构化事实的规范化锚点。"""

    metric: str = ""                                 # 指标名（如 "设备接入"/"注册资本"）
    kind: str = ""                                   # person|certificate|project|metric|company
    anchor_keywords: list[str] = Field(default_factory=list)
    require_all: bool = False                        # True=窗口须含全部关键词（防跨事实串线）
    value: Optional[float] = None                    # 点值
    value_hi: Optional[float] = None                 # 区间右端（None=点值）
    unit: str = ""                                   # count/year/money_wan/percent/hour...
    name: str = ""                                   # 实体名（张伟/ISO9001/项目名）
    source_ref: str = ""                             # CAP-XXXX / EVD-XXXX
    source_category: str = ""
    extra: dict[str, Any] = Field(default_factory=dict)  # cert_no/valid_until/role/certs...


class FactRegistry(BaseModel):
    """M5-10 事实注册表 —— 单次检查的规范化事实集合（不落库）。"""

    entries: list[FactRegistryEntry] = Field(default_factory=list)

    def metric(self, metric: str) -> list[FactRegistryEntry]:
        """同指标全部条目（多卡并存：2000 与 1500-2500 同组，命中任一即过）。"""
        return [e for e in self.entries if e.metric == metric]

    def persons(self) -> list[FactRegistryEntry]:
        return [e for e in self.entries if e.kind == "person"]

    def certs(self) -> list[FactRegistryEntry]:
        return [e for e in self.entries if e.kind == "certificate"]

    def projects(self) -> list[FactRegistryEntry]:
        return [e for e in self.entries if e.kind == "project"]

    def of_kind(self, kind: str) -> list[FactRegistryEntry]:
        return [e for e in self.entries if e.kind == kind]


class ReviewRecord(BaseModel):
    """M5-16/19 人工审核留痕 —— 问题处理与最终批准审计。"""

    id: int = 0
    issue_id: str = ""
    action: str = ""          # 确认/忽略/修复/批准
    reviewer: str = ""
    note: str = ""
    created_at: str = Field(default_factory=now_str)


class CheckContext(BaseModel):
    """M5 检查上下文 —— QualityRunner 一次性装载的检查输入。

    读的都是 raw 行（dict），与 db.py row_to_* 同构；evidences/matches 以 id 为键。
    """

    db: Any = None
    tender_id: str = ""
    as_of: str = ""                       # 证书有效期基准日（默认今日，测试传固定日期）
    sections: list[dict[str, Any]] = Field(default_factory=list)     # generation_sections 行
    canonicals: list[dict[str, Any]] = Field(default_factory=list)   # canonical_requirements 行
    requirements: list[dict[str, Any]] = Field(default_factory=list) # requirements（M1 招标需求）行
    matches: dict[str, dict[str, Any]] = Field(default_factory=dict) # requirement_id → requirement_matches 行
    section_maps: list[dict[str, Any]] = Field(default_factory=list) # requirement_section_maps 行
    score_points: list[dict[str, Any]] = Field(default_factory=list)
    evidences: dict[str, dict[str, Any]] = Field(default_factory=dict)  # id → evidences 行
    capabilities: list[dict[str, Any]] = Field(default_factory=list)
    tender: dict[str, Any] = Field(default_factory=dict)
    registry: FactRegistry = Field(default_factory=FactRegistry)
    assembled_md: str = ""
    fact_zone_ids: list[str] = Field(default_factory=list)   # 事实区章节 id（排除回显区）

    def fact_zone_sections(self) -> list[dict[str, Any]]:
        """事实区章节（排除 CH-08/CH-05-4 等需求回显区）。"""
        return [s for s in self.sections if s.get("section_id") in self.fact_zone_ids]


__all__ = [
    "IssueType", "Severity", "SEVERITY_WEIGHT", "IssueStatus",
    "QualityIssue", "DimensionScore", "QualityReport",
    "FactRegistryEntry", "FactRegistry", "ReviewRecord", "CheckContext",
]
