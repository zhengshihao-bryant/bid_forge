# -*- coding: utf-8 -*-
"""
app/services/generation/ —— M4 标书生成引擎

主线（对应 M4-01～M4-10）：

    M3 MatchResult ──outline──▶ BidSection（M4-01 章节树）
        ──mapping──▶ 需求→章节（M4-02 覆盖统计）
        ──context──▶ GenerationContext（M4-03/04：证据 + 能力卡 + 历史标书）
        ──generator──▶ SectionDraft（M4-05/06/08：事实约束 + 策略分派）
        ──response_table──▶ 三列响应表（M4-07）
        ──assembler──▶ BidDocument（M4-09 Markdown/DOCX）
        ──job──▶ 任务状态机（M4-10 断点继续 / 单章节重生成）

边界（守住）：M4 只负责"把正确的需求 + 正确的企业证据组织成一份完整标书"；
一致性/事实核验/完整性/质量评估留给 M5。
"""
from .assembler import BidDocumentAssembler
from .context import (EVIDENCE_QUOTE_LIMIT, GenerationContextBuilder,
                      HistoricalExampleRetriever, dedupe_evidences,
                      trim_evidence)
from .generator import SectionGenerator, render_markdown
from .job import GenerationJobRunner, new_job_id, run_generation_task
from .mapping import RequirementSectionMapper
from .outline import (DEFAULT_OUTLINE_ID, OutlineBuilder, build_default_outline,
                      tree_from_flat)
from .response_table import BidResponseTableBuilder
from .strategies import strategy_for

__all__ = [
    "OutlineBuilder", "build_default_outline", "DEFAULT_OUTLINE_ID",
    "tree_from_flat",
    "RequirementSectionMapper",
    "GenerationContextBuilder", "HistoricalExampleRetriever",
    "dedupe_evidences", "trim_evidence", "EVIDENCE_QUOTE_LIMIT",
    "SectionGenerator", "render_markdown", "strategy_for",
    "BidResponseTableBuilder",
    "GenerationJobRunner", "run_generation_task", "new_job_id",
    "BidDocumentAssembler",
]
