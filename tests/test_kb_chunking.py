# -*- coding: utf-8 -*-
"""
tests/test_kb_chunking.py —— 企业资料切块单元测试（离线）

覆盖：
- ≤max_chars 约束、干净文本（【第p页】标记绝不进入 chunk.content）
- 页码元数据（page_start/page_end）、章节路径（section_path）、block_ids 保留
- docx 无页码 → page None，以章节路径溯源
- 超长表格按行二次切分；超长单段按句边界硬切
- chunk_id 编号 {material_id}_C{n:04d}、seq 递增
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.parsers import build_section_tree  # noqa: E402
from app.schemas import Block, BlockType, CapabilityCategory, ParsedDocument  # noqa: E402
from app.services.kb_chunking import build_chunks  # noqa: E402


# ═══════════════════════════════════════════════════════════════════════
# 工具函数（与 test_extraction.py 同款）
# ═══════════════════════════════════════════════════════════════════════
def _b(block_id: str, type_: BlockType, text: str = "", page=None,
       table=None, level=None) -> Block:
    return Block(block_id=block_id, type=type_, text=text, page=page,
                 table=table, level=level)


def _doc(file_name="01_产品介绍.pdf", file_type="pdf", blocks=None,
         sections=None) -> ParsedDocument:
    doc = ParsedDocument(file_name=file_name, file_type=file_type)
    if blocks is not None:
        doc.blocks = blocks
        doc.sections = sections if sections is not None else build_section_tree(blocks)
    return doc


# ═══════════════════════════════════════════════════════════════════════
# 基本切块
# ═══════════════════════════════════════════════════════════════════════
def test_chunks_respect_max_chars_and_carry_metadata():
    blocks = [
        _b("B1", BlockType.HEADING, "1.1 产品概述", level=2, page=1),
        _b("B2", BlockType.PARAGRAPH, "产品介绍正文。" * 50, page=1),   # 350 字
        _b("B3", BlockType.PARAGRAPH, "功能说明正文。" * 50, page=1),   # 350 字
        _b("B4", BlockType.HEADING, "1.2 核心功能", level=2, page=2),
        _b("B5", BlockType.PARAGRAPH, "视频监控管理功能。" * 30, page=2),  # 270 字
    ]
    doc = _doc(blocks=blocks)
    chunks = build_chunks(doc, "mat001", "01_产品介绍.pdf",
                          CapabilityCategory.PRODUCT, max_chars=600)

    # 标题块是章节树节点（标题文本进 section_path 元数据，不作为内容块发射），
    # 故内容块 = 段落：[B2]（350）、[B3]（350）、[B5]（270）
    assert len(chunks) == 3
    assert all(len(c.content) <= 600 for c in chunks)
    assert all("【第" not in c.content for c in chunks)          # 干净文本铁律
    assert [c.id for c in chunks] == ["mat001_C0001", "mat001_C0002", "mat001_C0003"]
    assert [c.seq for c in chunks] == [1, 2, 3]
    # 页码元数据：不跨页
    assert chunks[0].page_start == 1 and chunks[0].page_end == 1
    assert chunks[1].page_start == 1 and chunks[1].page_end == 1
    assert chunks[2].page_start == 2 and chunks[2].page_end == 2
    # 章节路径与块级出处
    assert chunks[0].section_path == "1.1 产品概述"
    assert chunks[0].block_ids == ["B2"]
    assert chunks[1].block_ids == ["B3"]
    assert chunks[2].section_path == "1.2 核心功能"
    assert chunks[2].block_ids == ["B5"]
    assert chunks[0].category == CapabilityCategory.PRODUCT
    assert chunks[0].file_name == "01_产品介绍.pdf"


def test_docx_chunks_have_no_page_and_nested_section_path():
    """docx 无页码：page_start/page_end 恒 None，section_path 带祖先路径。"""
    blocks = [
        _b("B1", BlockType.HEADING, "公司资质证书", level=1),
        _b("B2", BlockType.HEADING, "3.1 资质证书一览", level=3),
        _b("B3", BlockType.PARAGRAPH, "公司持有质量管理体系认证。"),
    ]
    doc = _doc(file_name="03_公司资质.docx", file_type="docx", blocks=blocks)
    chunks = build_chunks(doc, "mat003", "03_公司资质.docx",
                          CapabilityCategory.QUALIFICATION, max_chars=600)
    assert len(chunks) == 1
    assert chunks[0].page_start is None and chunks[0].page_end is None
    assert chunks[0].section_path == "公司资质证书 > 3.1 资质证书一览"


# ═══════════════════════════════════════════════════════════════════════
# 超长内容二次切分
# ═══════════════════════════════════════════════════════════════════════
def test_long_table_split_by_rows():
    """超长表格按行二次切分，每子块 ≤max_chars 且行边界完整。"""
    rows = [[f"列一内容{i}", f"列二内容{i}"] for i in range(40)]
    blocks = [
        _b("B1", BlockType.HEADING, "资质证书一览", level=2, page=1),
        _b("B2", BlockType.TABLE, text="", page=1, table=rows),
    ]
    doc = _doc(blocks=blocks)
    chunks = build_chunks(doc, "mat003", "03_公司资质.docx",
                          CapabilityCategory.QUALIFICATION, max_chars=200)
    # 标题是章节节点不进内容；表格 40 行按行二次切分，每子块 ≤max_chars
    assert len(chunks) >= 2
    assert all(len(c.content) <= 200 for c in chunks)
    assert all(c.block_ids == ["B2"] for c in chunks)          # 行切分不换块号
    assert all(c.page_start == 1 for c in chunks)
    assert all(c.section_path == "资质证书一览" for c in chunks)
    # 每行文本完整保留（行边界不截断，换行开销计入子块长度）
    joined = "\n".join(c.content for c in chunks)
    assert joined.count("列一内容") == 40
    assert joined.count("列二内容") == 40
    for i in range(40):
        assert f"列一内容{i} | 列二内容{i}" in joined


def test_long_paragraph_split_by_sentence():
    """超长单段按句边界硬切，每段 ≤max_chars。"""
    blocks = [
        _b("B1", BlockType.HEADING, "1.3 技术指标", level=2, page=1),
        _b("B2", BlockType.PARAGRAPH, "这是一句超过上限的长句。" * 60, page=1),
    ]
    doc = _doc(blocks=blocks)
    chunks = build_chunks(doc, "mat001", "01_产品介绍.pdf",
                          CapabilityCategory.PRODUCT, max_chars=300)
    assert len(chunks) >= 3
    assert all(len(c.content) <= 300 for c in chunks)
    assert all(c.section_path == "1.3 技术指标" for c in chunks)


def test_page_boundary_cut_preferred():
    """溢出时优先回退到最近页边界切分（复用 M1 _chunk_blocks 思路）。"""
    blocks = [
        _b("B1", BlockType.PARAGRAPH, "一" * 200, page=1),
        _b("B2", BlockType.PARAGRAPH, "二" * 200, page=1),
        _b("B3", BlockType.PARAGRAPH, "三" * 200, page=2),
        _b("B4", BlockType.PARAGRAPH, "四" * 200, page=2),
    ]
    doc = _doc(blocks=blocks)
    chunks = build_chunks(doc, "mat005", "05_技术方案.pdf",
                          CapabilityCategory.SOLUTION, max_chars=500)
    assert len(chunks) == 2
    assert chunks[0].page_start == 1 and chunks[0].page_end == 1
    assert chunks[1].page_start == 2 and chunks[1].page_end == 2
