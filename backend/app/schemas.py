# -*- coding: utf-8 -*-
"""
app/schemas.py —— 核心数据模型（本项目与"又一个 RAG"的分水岭）

数据流设计：

    招标文件 ──解析/提取──▶ 需求实体 (Requirement)        "我要满足什么？"
    企业资料 ──入库/向量化─▶ 能力实体 (Capability)        "我能提供什么？"
    匹配      = 需求 × 能力的【关系】(MatchResult)
    生成      = 带引用的【产物】(SectionDraft)

RAG 只负责"检索"这一段；事实约束靠 SourceAnchor 四元溯源贯穿全链路：
document / doc_id / page / section_path / block_id —— M3 生成时每个关键数字
都能回溯到原文位置，这是"不能写错"的工程地基。

约定：
- 所有模型 Pydantic v2（fastapi 0.111 配套）
- 中文枚举值直接入库（SQLite 存字符串）
- 需求/能力/匹配/章节稿均有 created_at/updated_at，M4 版本管理依赖于此
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field

# ═══════════════════════════════════════════════════════════════════════
# 时间工具
# ═══════════════════════════════════════════════════════════════════════
def now_str() -> str:
    """ISO 时间字符串（本地时区，秒级精度）。"""
    return datetime.now().astimezone().isoformat(timespec="seconds")


# ═══════════════════════════════════════════════════════════════════════
# 枚举
# ═══════════════════════════════════════════════════════════════════════
class BlockType(str, Enum):
    HEADING = "heading"        # 标题块（level 1-6）
    PARAGRAPH = "paragraph"    # 正文段落
    TABLE = "table"            # 表格（table 字段存行数据）
    LIST_ITEM = "list_item"    # 列表项
    IMAGE = "image"            # 图片/扫描页（需 OCR）


class RequirementType(str, Enum):
    """招标要求 12 类 —— 标书模板章节按类型映射响应。"""
    BACKGROUND = "项目背景"
    GOAL = "建设目标"
    TECHNICAL = "技术要求"
    FUNCTIONAL = "功能要求"
    IMPLEMENTATION = "实施要求"
    PERSONNEL = "人员要求"
    QUALIFICATION = "资质要求"
    AFTERSALES = "售后服务"
    SCORING = "评分标准"
    FORMAT = "投标文件格式"
    COMMERCIAL = "商务要求"
    PRICING = "报价要求"


class CapabilityCategory(str, Enum):
    """企业能力 8 类 —— 对应企业资料分散的部门。"""
    PRODUCT = "产品"
    CASE = "项目案例"
    QUALIFICATION = "公司资质"
    PERSONNEL = "人员资质"
    SOLUTION = "技术方案"
    AFTERSALES = "售后服务"
    INTRO = "公司介绍"
    HISTORICAL_BID = "历史标书"


class MatchVerdict(str, Enum):
    FULL = "满足"
    PARTIAL = "部分满足"
    NOT_MET = "不满足"
    NOT_FOUND = "未找到"


class ExtractionStatus(str, Enum):
    """招标项目的需求提取状态（落库，服务重启不丢状态）。"""
    NONE = "未提取"
    RUNNING = "提取中"
    DONE = "已完成"
    FAILED = "失败"


class DraftStatus(str, Enum):
    DRAFT = "草稿"
    EDITED = "已编辑"
    CONFIRMED = "已确认"


# ═══════════════════════════════════════════════════════════════════════
# 解析层产物（parsers/ 统一结构）
# ═══════════════════════════════════════════════════════════════════════
class Block(BaseModel):
    """平铺内容块 —— 解析的最小单元。

    block_id 块内自增（B0001 起），与 Section.block_ids 关联；
    OCR 块附 ocr=True + confidence，供 M5 质量检查。
    """
    block_id: str
    type: BlockType
    text: str = ""
    page: Optional[int] = None            # PDF 1 基页码；docx 无页码恒为 None
    level: Optional[int] = None           # 仅 heading：1-6
    table: Optional[list[list[str]]] = None   # 仅 table：行×列
    ocr: bool = False
    confidence: Optional[float] = None


class Section(BaseModel):
    """章节树节点 —— 由标题块构建，树形组织。"""
    id: str                                # S0001 起
    title: str
    level: int
    order: int                             # 树内前序序号
    page_start: Optional[int] = None
    page_end: Optional[int] = None
    children: list[Section] = Field(default_factory=list)
    block_ids: list[str] = Field(default_factory=list)   # 直接归属本节的块


class ParsedDocument(BaseModel):
    """所有解析器的统一产物 —— 上层（提取/知识库）不关心文件格式。"""
    schema_version: str = "1.0.0"          # M5 重解析可追溯
    file_name: str
    file_type: str                         # pdf / docx / xlsx / image
    total_pages: int = 0                   # xlsx 语义 = sheet 数；docx 恒为 0
    char_count: int = 0
    ocr_pages: list[int] = Field(default_factory=list)   # 检测出的扫描页（1 基）
    sections: list[Section] = Field(default_factory=list)
    blocks: list[Block] = Field(default_factory=list)


# ═══════════════════════════════════════════════════════════════════════
# 出处锚点与需求实体
# ═══════════════════════════════════════════════════════════════════════
class SourceAnchor(BaseModel):
    """四元溯源 —— 事实约束的地基。

    docx 无页码信息（Word 页面属于渲染层），以 section_path + block_id 为准；
    PDF 才锚定 page。snippet 为原文片段，供人工核对与 M3 引用注入。
    """
    document: str = ""                     # 文件名（展示用）
    doc_id: str = ""                       # documents 表主键
    page: Optional[int] = None
    section_path: str = ""                 # 如 "第四章 技术要求 > 4.2 平台功能要求"
    block_id: str = ""
    snippet: str = ""


class QuantitativeItem(BaseModel):
    """量化指标 —— 数值/比较符/单位原样保留，绝不改写。

    例：招标原文"支持不少于1000个设备接入" →
        {"metric": "设备接入", "op": "不少于", "value": "1000", "unit": "个"}
    """
    metric: str = ""
    op: str = ""                           # ≥/≤/不少于/不高于/...（原文用词）
    value: str = ""
    unit: str = ""


class Requirement(BaseModel):
    """需求实体 —— "甲方到底要求我们提供什么"的结构化答案。"""
    id: str = ""                           # REQ-0001 起，tender 内自增
    tender_id: str
    type: RequirementType
    title: str                             # 一句话概括（≤30 字）
    original_text: str                     # 原文逐字摘录
    quantitative: list[QuantitativeItem] = Field(default_factory=list)
    importance: str = "中"                 # 高 / 中 / 低
    is_star: bool = False                  # ★/※/否决项/实质性要求
    source: Optional[SourceAnchor] = None
    status: str = "待响应"                 # 待响应 → 已匹配 → 已确认 / 不适用
    response: str = ""                     # 响应内容（M3 生成后回填）
    human_confirmed: bool = False          # 人工修订后置 True（锁定）
    created_at: str = Field(default_factory=now_str)
    updated_at: str = Field(default_factory=now_str)


class ScorePoint(BaseModel):
    """评分点 —— 评分标准表的规则解析产物（不走 LLM，规则更准）。

    rule_id/weight/source_ref 供 M3 需求响应表复用：
    每条评分点对应一个必答项，直接映射到标书章节。
    """
    id: str = ""                           # SC-0001 起
    tender_id: str
    category: str = ""                     # 技术 / 商务 / 价格 / 其他
    item: str                              # 评价项
    max_score: Optional[float] = None
    criteria: str = ""                     # 评分细则
    rule_id: str = ""                      # 解析规则标识（RULE-<block_id>）
    weight: float = 0.0                    # 分值权重（= max_score，M3 复用）
    source_ref: str = ""                   # "文件名#块号"
    created_at: str = Field(default_factory=now_str)


# ═══════════════════════════════════════════════════════════════════════
# 招标项目（聚合根）
# ═══════════════════════════════════════════════════════════════════════
class Tender(BaseModel):
    id: str                                # uuid4 hex 前 12 位
    name: str
    created_at: str = Field(default_factory=now_str)
    extraction_status: ExtractionStatus = ExtractionStatus.NONE
    extraction_progress: str = ""          # 进度描述（后台任务回写）
    requirement_count: int = 0
    score_point_count: int = 0


class DocumentMeta(BaseModel):
    """documents 表行 —— 文件元数据 + 解析统计。"""
    id: str
    tender_id: str
    file_name: str                         # 原名（展示用）
    stored_name: str                       # uuid 落盘名
    file_type: str
    total_pages: int = 0
    char_count: int = 0
    ocr_pages: list[int] = Field(default_factory=list)
    raw_hash: str = ""                     # 原文 SHA-256，M5 重解析可追溯
    parser_version: str = ""
    parse_error: str = ""                  # 解析失败原因（空 = 成功）
    parsed_file: str = ""                  # data/parsed/{tender_id}/{stored_name}.json
    created_at: str = Field(default_factory=now_str)


# ═══════════════════════════════════════════════════════════════════════
# M2：企业知识库（资料/内容块/检索结果）
# 注意：capabilities 表在 M1 已预建、字段定死（无 material_id、无人工锁定标记），
# 卡片↔资料用 source_doc=file_name 关联；人工锁定/版本 M3 随匹配表一起做。
# ═══════════════════════════════════════════════════════════════════════
class KbProcessStatus(str, Enum):
    """知识库资料处理状态（落库，服务重启不丢状态）。"""
    NONE = "未处理"
    RUNNING = "处理中"
    DONE = "已完成"
    FAILED = "失败"


class KbMaterial(BaseModel):
    """kb_materials 表行 —— 1 个上传文件 = 1 行。

    process_status 状态机：未处理 → 处理中 → 已完成/失败；
    index_status：none / done / degraded（Milvus 写入失败仅降级，不整任务失败）。
    """
    id: str                                 # uuid4 hex 前 12 位
    category: CapabilityCategory
    file_name: str                          # 原名（展示用；能力卡 source_doc 关联键）
    stored_name: str = ""
    file_type: str = ""
    total_pages: int = 0
    char_count: int = 0
    ocr_pages: list[int] = Field(default_factory=list)
    raw_hash: str = ""                      # 原文 SHA-256，M5 重处理可追溯
    parser_version: str = ""
    parse_error: str = ""
    parsed_file: str = ""                   # KB_PARSED_DIR/{id}/{stored_name}.json
    process_status: KbProcessStatus = KbProcessStatus.NONE
    process_progress: str = ""
    chunk_count: int = 0
    capability_count: int = 0
    index_status: str = "none"              # none / done / degraded
    created_at: str = Field(default_factory=now_str)


class KbChunk(BaseModel):
    """kb_chunks 表行 —— SQLite 为事实源（全文 + 四元溯源）。

    embedding 列只在降级检索/重建索引时使用，不出 API；
    content 是干净块文本——【第p页】标记绝不写入（页码进 page_start/page_end）。
    """
    id: str                                 # {material_id}_C{n:04d}
    material_id: str
    category: CapabilityCategory
    file_name: str
    content: str
    section_path: str = ""
    page_start: Optional[int] = None        # PDF 1 基；docx 恒 None
    page_end: Optional[int] = None
    block_ids: list[str] = Field(default_factory=list)  # 合并入本块的 Block.block_id
    seq: int = 0                            # 资料内块序号
    created_at: str = Field(default_factory=now_str)


class CapabilityPatch(BaseModel):
    """能力卡人工修订请求体（全可选，任一非空即修订）。"""
    category: Optional[str] = None
    name: Optional[str] = None
    description: Optional[str] = None
    attributes: Optional[dict[str, Any]] = None


class SearchHit(BaseModel):
    """语义检索命中 —— 四元溯源 + 引擎分数。"""
    chunk_id: str
    material_id: str
    file_name: str
    category: str
    section_path: str = ""
    page: Optional[int] = None
    score: float = 0.0
    content: str = ""
    anchor: Optional[SourceAnchor] = None   # 完整四元溯源（M3 引用注入依赖）


class SearchResult(BaseModel):
    """检索结果 —— engine 标识降级路径，透明可查。"""
    engine: str = "milvus"                  # milvus / sqlite（降级）
    hits: list[SearchHit] = Field(default_factory=list)


# ═══════════════════════════════════════════════════════════════════════
# M2/M3 实体（结构现在定死，向前兼容）
# ═══════════════════════════════════════════════════════════════════════
class Capability(BaseModel):
    """能力实体 —— 企业"我能提供什么"的结构化答案。

    与 chunk 并存：卡片管事实（attributes 结构化字段），向量管检索。
    例：产品能力卡 {"category": 产品, "name": "智慧园区平台 V3.2",
    "attributes": {"max_devices": 2000, "face_recognition": true}, ...}
    """
    id: str = ""                           # CAP-0001 起
    category: CapabilityCategory
    name: str
    attributes: dict[str, Any] = Field(default_factory=dict)
    description: str = ""
    source_doc: str = ""                   # 来源文件
    source_page: Optional[int] = None
    created_at: str = Field(default_factory=now_str)


class MatchResult(BaseModel):
    """匹配记录 —— 需求 × 能力的【关系】。

    检索给候选，LLM 做判定；verdict 与 evidence/reason 同时落库，
    供人工复核（AI 不做最终决策）。
    """
    id: str = ""                           # MAT-0001 起
    requirement_id: str
    capability_id: Optional[str] = None    # 未找到时为 None
    verdict: MatchVerdict
    confidence: float = 0.0                # 0-1
    evidence: str = ""                     # 证据原文（能力卡/资料片段）
    reason: str = ""                       # 判定理由
    created_at: str = Field(default_factory=now_str)


class ChapterSpec(BaseModel):
    """标书模板章节 —— 每章声明：响应哪些需求类型、允许引用哪些资料类别。

    M4 新增 section_type（方案型/事实型/表格型/固定格式，对应 M4-08 内容类型）
    与 source_refs（关联原始招标文件章节路径）；section_type 用中文串
    （与 SectionType 枚举值一致，见 services/generation/models.py）。
    """
    id: str = ""                           # CH-01 起
    order: int
    title: str
    level: int = 1
    requirement_types: list[RequirementType] = Field(default_factory=list)
    allowed_categories: list[CapabilityCategory] = Field(default_factory=list)
    generation_prompt: str = ""            # 本章生成提示词（可覆盖默认）
    section_type: str = "方案型"           # 方案型 / 事实型 / 表格型 / 固定格式（M4-08）
    source_refs: list[str] = Field(default_factory=list)   # 关联原始招标章节 path
    children: list[ChapterSpec] = Field(default_factory=list)


class OutlineTemplate(BaseModel):
    """标书模板 —— 结构化内容生成的核心（M3 使用）。"""
    id: str = ""
    name: str = "通用标书结构"
    description: str = ""
    chapters: list[ChapterSpec] = Field(default_factory=list)
    created_at: str = Field(default_factory=now_str)


class SectionDraft(BaseModel):
    """章节稿 —— 带引用的生成产物。

    content 内含 [证据:...] 引用标记；status 流转：草稿 → 已编辑 → 已确认；
    version 自增，M4 版本管理依赖。
    """
    id: str = ""                           # DRAFT-0001 起
    tender_id: str
    chapter_id: str
    title: str
    content: str = ""
    citations: list[SourceAnchor] = Field(default_factory=list)
    status: DraftStatus = DraftStatus.DRAFT
    version: int = 1
    created_at: str = Field(default_factory=now_str)
    updated_at: str = Field(default_factory=now_str)
