# -*- coding: utf-8 -*-
"""
matching/models/evidence.py —— M3 证据实体（M3-04）

Evidence 是匹配结论的唯一依据：三种来源（能力卡 / 知识块 / 企业资料文档），
统一四元溯源（document_id / section_id / page / section_path）。

M3-05 原文回验：LLM/检索产生的证据必须回原文精确匹配（Source Validator），
VALID 才可进入高可信证据；INVALID 禁入高可信、不得单独支撑 FULL。
"""
from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field

from ....schemas import now_str


class EvidenceSourceType(str, Enum):
    """证据来源三型（M3-04：CapabilityCard / Chunk / Document）。"""
    CAPABILITY_CARD = "capability_card"    # M2 能力卡（结构化事实）
    CHUNK = "chunk"                        # 知识库内容块（RAG 命中）
    DOCUMENT = "document"                  # 企业资料文档（整份/章节级）


class EvidenceValidation(str, Enum):
    """原文回验结果（M3-05）。"""
    VALID = "VALID"                        # 原文精确匹配命中
    INVALID = "INVALID"                    # 原文找不到 → 禁入高可信证据
    UNCHECKED = "UNCHECKED"               # 无法回验（如资料未切块）


class Evidence(BaseModel):
    """统一证据对象 —— 证据链的最小单元（EVD-XXXX）。"""
    evidence_id: str = ""                  # EVD-0001 起（tender 内全局自增）
    tender_id: str = ""
    requirement_id: str = ""               # 归属的规范需求（REQ-C-XXXX）
    source_type: EvidenceSourceType
    source_id: str = ""                    # CAP-0007 / chunk id / material id
    content: str = ""                      # 证据文本（引用注入 + 回验对象）
    category: str = ""                     # 资料类别（来源档位判定：正式资料/案例/历史标书）
    document_id: str = ""                  # kb_materials 主键（capability 经 file_name 反查）
    section_id: str = ""                   # 章节 id（有则填）
    page: Optional[int] = None
    section_path: str = ""
    block_id: str = ""                     # 块号（chunk.block_ids 首个）
    confidence: float = 0.0                # 证据可信度（rank 后赋值）
    retrieval_score: float = 0.0           # RAG 余弦分（能力卡为关键词分）
    validation: EvidenceValidation = EvidenceValidation.UNCHECKED
    matched_text: str = ""                 # 原文精确匹配命中的片段（回验器回填）
    created_at: str = Field(default_factory=now_str)
