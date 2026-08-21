# -*- coding: utf-8 -*-
"""
generation/models.py —— M4 标书生成引擎实体（M4-01/03/06/10）

数据流：

    M3 MatchResult ──outline──▶ BidSection（章节树）──mapping──▶ 需求清单
        ──context──▶ GenerationContext ──strategy──▶ SectionDraft（富结构稿）
        ──assembler──▶ BidDocument（Markdown / DOCX）

命名约定：
- 本模块的 SectionDraft 与 schemas.SectionDraft 同名但不同模块（M3 已有
  MatchResult 双类先例）。M4 代码一律 `from app.services.generation.models import
  SectionDraft`，禁止 import schemas.SectionDraft（老 drafts 表保持不动）。
- 中文枚举值直接入库（SQLite 存字符串，沿用项目约定）。
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field

from ...schemas import DraftStatus, now_str

# 为避免与 services.matching.models 重名歧义，M4 需要的 M3 实体在消费方显式导入。


class SectionType(str, Enum):
    """M4-08 内容类型 —— 决定生成策略（策略分派，非自由 LLM）。"""
    SOLUTION = "方案型"          # 技术/实施/售后方案：LLM + Evidence
    FACT = "事实型"              # 公司概况/资质/人员：模板 + Evidence 回填
    TABLE = "表格型"             # 指标响应表/资质表/业绩表：结构化数据 + 模板
    FIXED = "固定格式"           # 封面/目录/投标函/报价表：模板直接渲染


class FactClass(str, Enum):
    """M4-05 事实三分类 —— 生成时强制区分，防历史标书/推断混入企业事实。"""
    FACT = "FACT"                # 有证据支撑的企业事实（只能来自 Evidence/能力卡）
    WRITING_STYLE = "WRITING_STYLE"   # 历史标书借鉴的写法/语气，非企业事实
    INFERENCE = "INFERENCE"      # 承诺/改进措施/过渡句，不构成企业事实断言


class SectionStatus(str, Enum):
    """章节生成生命周期（M4-10，人工编辑另见 draft_status）。"""
    PENDING = "待生成"
    RUNNING = "生成中"
    DONE = "已完成"
    FAILED = "失败"
    SKIPPED = "跳过"


class BidSection(BaseModel):
    """M4-01 章节树节点 —— 每标书实例化的章节（规划 + 状态）。

    树形：children 仅供内存组织；持久化用 parent_id + ord 平铺（镜像 M1 Section）。
    """
    id: str = ""                           # CH-04-1 稳定 id
    tender_id: str = ""
    parent_id: str = ""
    title: str = ""
    level: int = 1
    ord: int = 0                           # 同父相对顺序（order 是 SQL 保留字）
    section_type: SectionType = SectionType.SOLUTION
    source_refs: list[str] = Field(default_factory=list)   # 关联原始招标章节 path
    requirement_types: list[str] = Field(default_factory=list)  # M1 中文类型串
    allowed_categories: list[str] = Field(default_factory=list) # CapabilityCategory.value
    generation_prompt: str = ""
    status: SectionStatus = SectionStatus.PENDING
    children: list[BidSection] = Field(default_factory=list)


class Paragraph(BaseModel):
    """M4-06 结构化段落 —— 非纯 Markdown 的生成产物。"""
    type: str = "paragraph"                # heading / paragraph / list_item / table
    text: str = ""
    level: int = 0                         # 仅 heading：1-6
    table: list[list[str]] = Field(default_factory=list)   # 仅 table：行×列
    fact_class: FactClass = FactClass.INFERENCE
    source_refs: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)  # 只引用真实 EVD-XXXX


class EvidenceRef(BaseModel):
    """M4-06 证据引用 —— 事实可溯源的最小锚点。"""
    evidence_id: str = ""                  # EVD-XXXX
    requirement_id: str = ""
    quote: str = ""                        # 引用片段
    source_document: str = ""
    page: Optional[int] = None
    section_path: str = ""
    block_id: str = ""
    fact_class: FactClass = FactClass.FACT


class CoverageItem(BaseModel):
    """M4-02 章节需求清单条目 —— 覆盖统计与响应策略。"""
    requirement_id: str = ""               # REQ-C-XXXX
    title: str = ""
    status: str = ""                       # FULL/PARTIAL/MISSING/UNKNOWN
    covered: bool = False                  # 该章节已产出对应响应
    note: str = ""


class CoverageStats(BaseModel):
    """M4-02 覆盖统计 —— 需求→章节映射的审计口径。"""
    total: int = 0
    mapped: int = 0
    unmapped: int = 0
    by_section: dict[str, int] = Field(default_factory=dict)
    unmapped_reqs: list[dict[str, Any]] = Field(default_factory=list)


class HistoricalExample(BaseModel):
    """M4-04 历史标书参考 —— 只作写作参考（WRITING_STYLE），不作企业事实。"""
    source_document: str = ""
    section_path: str = ""
    snippet: str = ""
    fact_class: FactClass = FactClass.WRITING_STYLE


class GenerationContext(BaseModel):
    """M4-03 生成上下文 —— 统一 Prompt 输入结构（事实约束的地基）。"""
    section: BidSection
    requirements: list[Any] = Field(default_factory=list)     # CanonicalRequirement[]
    evidences: list[Any] = Field(default_factory=list)        # Evidence[]（去重/排序/截断）
    capability_cards: list[Any] = Field(default_factory=list) # Capability[]
    historical_examples: list[HistoricalExample] = Field(default_factory=list)
    constraints: list[Any] = Field(default_factory=list)      # Constraint[]
    metadata: dict[str, Any] = Field(default_factory=dict)


class SectionDraft(BaseModel):
    """M4-06 富结构章节稿 —— 生成器的统一产物（替代 schemas.SectionDraft）。

    content_md 为 Markdown 渲染正文；paragraphs 为结构化来源（M4-09 组装用）；
    requirement_coverage 逐条需求响应状态；evidence_refs 事实溯源；
    warnings 记录事实约束校验告警（M5 一致性命中的输入）。
    """
    section_id: str = ""
    tender_id: str = ""
    generation_id: str = ""
    title: str = ""
    section_type: SectionType = SectionType.SOLUTION
    paragraphs: list[Paragraph] = Field(default_factory=list)
    requirement_coverage: list[CoverageItem] = Field(default_factory=list)
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    status: DraftStatus = DraftStatus.DRAFT
    content_md: str = ""
    generation_metadata: dict[str, Any] = Field(default_factory=dict)
    version: int = 1
    created_at: str = Field(default_factory=now_str)
    updated_at: str = Field(default_factory=now_str)


class GenerationJob(BaseModel):
    """M4-10 生成任务 —— 章节级进度 + 断点继续。"""
    id: str = ""                           # uuid4 hex 前 12 位
    tender_id: str = ""
    outline_id: str = ""
    status: str = "未生成"                 # 未生成/生成中/已完成/部分失败/失败
    progress: str = ""
    section_states: dict[str, str] = Field(default_factory=dict)  # section_id → SectionStatus.value
    total_sections: int = 0
    done_sections: int = 0
    failed_sections: int = 0
    error: str = ""
    created_at: str = Field(default_factory=now_str)
    updated_at: str = Field(default_factory=now_str)


__all__ = [
    "SectionType", "FactClass", "SectionStatus",
    "BidSection", "Paragraph", "EvidenceRef", "CoverageItem", "CoverageStats",
    "HistoricalExample", "GenerationContext", "SectionDraft", "GenerationJob",
]
