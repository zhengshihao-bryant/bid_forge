# -*- coding: utf-8 -*-
"""
matching/models/requirement.py —— M3 需求实体

    RawRequirement（= M1 Requirement，M3 不做拷贝，直接引用）
         ↓ RequirementNormalizer
    CanonicalRequirement（REQ-C-XXXX，规范化/去重/聚类后的核心需求）

Constraint 是需求的结构化约束（M3-03）：自然语言 → 规则引擎可处理的形式。
"""
from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field

from ....schemas import now_str


class RequirementTypeM3(str, Enum):
    """M3 需求类型（10 类）—— 决定后续用什么方式匹配（M3-02/09）。"""
    QUALIFICATION = "QUALIFICATION"                  # 资质认证：ISO/CMMI/等保/信用
    PERSONNEL = "PERSONNEL"                          # 人员：项目经理/职称/经验年限
    PROJECT_EXPERIENCE = "PROJECT_EXPERIENCE"        # 业绩案例：合同额/案例数量
    PRODUCT_CAPABILITY = "PRODUCT_CAPABILITY"        # 产品/平台能力：设备接入/功能模块
    TECHNICAL = "TECHNICAL"                          # 技术：架构/性能/接口/信创
    IMPLEMENTATION = "IMPLEMENTATION"                # 实施：工期/部署/培训/验收
    SERVICE = "SERVICE"                              # 售后：质保/响应/驻场
    COMMERCIAL = "COMMERCIAL"                        # 商务：报价/付款/保证金
    DOCUMENT = "DOCUMENT"                            # 文件格式：装订/份数/签署
    OTHER = "OTHER"                                  # 背景/目标等无匹配对象的需求


# M3 类型 → 中文标签（响应表展示用）
TYPE_LABELS: dict[RequirementTypeM3, str] = {
    RequirementTypeM3.QUALIFICATION: "资质认证",
    RequirementTypeM3.PERSONNEL: "人员要求",
    RequirementTypeM3.PROJECT_EXPERIENCE: "项目业绩",
    RequirementTypeM3.PRODUCT_CAPABILITY: "产品能力",
    RequirementTypeM3.TECHNICAL: "技术要求",
    RequirementTypeM3.IMPLEMENTATION: "实施要求",
    RequirementTypeM3.SERVICE: "售后服务",
    RequirementTypeM3.COMMERCIAL: "商务要求",
    RequirementTypeM3.DOCUMENT: "文件格式",
    RequirementTypeM3.OTHER: "其他",
}


class Constraint(BaseModel):
    """结构化约束（M3-03）—— 自然语言需求的规则引擎可处理形式。

    例："项目经理应具有5年以上相关项目经验" →
        {subject: "项目经理", attribute: "experience_years",
         operator: ">=", value: 5, unit: "年"}
    "投标人应具有1000台以上设备接入经验" →
        {subject: "设备接入", attribute: "device_count",
         operator: ">=", value: 1000, unit: "台"}
    "投标人须具有ISO9001认证" →
        {subject: "ISO9001", attribute: "certification",
         exists: True, value: None}
    """
    subject: str = ""                    # 约束主体（项目经理/设备接入/质保…）
    attribute: str = ""                  # 归一属性键（experience_years/device_count/…）
    metric: str = ""                     # 指标名（量化项原文，如"设备接入"）
    operator: str = ""                   # >= / <= / > / < / = / exists
    value: Optional[float] = None        # 归一数值
    unit: str = ""                       # 归一单位（年/台/人/%/万元/小时/分钟/…）
    raw_value: str = ""                  # 原文数值（"5"、"1000"、"99.9%"）
    exists: bool = False                 # 存在性约束（具有/通过某资质）
    source_text: str = ""                # 约束出处原文片段


class RequirementSourceRef(BaseModel):
    """原始需求出处（M3-01：保留原文出处 + 原始需求 ID 映射）。"""
    id: str = ""                          # REQ-0001
    title: str = ""
    original_text: str = ""
    type: str = ""                        # M1 RequirementType.value
    importance: str = "中"
    is_star: bool = False
    document: str = ""                    # 四元溯源（沿用 M1 SourceAnchor）
    doc_id: str = ""
    page: Optional[int] = None
    section_path: str = ""
    block_id: str = ""
    snippet: str = ""


class CanonicalRequirement(BaseModel):
    """规范需求（REQ-C-XXXX）—— 聚类去重后的一条核心需求。

    source_requirement_ids 恒保留原始需求映射（REQ-001/REQ-127/REQ-278 → REQ-C-001）；
    parent_requirement_id 用于评分细则/LLM 扩写合并的子需求挂靠；
    is_scoring 标记评分细则，不参与能力匹配（区分"评分细则与真正需求"）。
    """
    id: str = ""                          # REQ-C-0001 起，tender 内自增
    tender_id: str
    req_type: RequirementTypeM3 = RequirementTypeM3.OTHER
    title: str = ""                       # 一句话概括
    text: str = ""                        # 规范化需求陈述（LLM 扩写或成员原文）
    source_requirement_ids: list[str] = Field(default_factory=list)
    parent_requirement_id: str = ""       # 挂靠父需求（评分细则/扩写子项）
    importance: str = "中"                # 取成员最高：高 > 中 > 低
    is_star: bool = False                 # 任一成员为★条款即 True
    is_scoring: bool = False              # 评分细则 → 不参与能力匹配
    constraints: list[Constraint] = Field(default_factory=list)
    sources: list[RequirementSourceRef] = Field(default_factory=list)
    merge_method: str = ""                # exact / similarity / llm
    created_at: str = Field(default_factory=now_str)
