# -*- coding: utf-8 -*-
"""
tests/test_parsers.py —— 解析器单元测试（离线，不依赖 LLM）

覆盖：
- 文本 PDF（技术规格书）：章节树（TOC 标题）、页码、无 OCR 标记
- 扫描件 PDF（无文本层）：ocr_pages 检测 + IMAGE 块
- xlsx：sheet → 页语义 + TABLE 块
- docx：Heading 样式标题树 + 表格块（docx 样例未生成时 skip）
- build_section_tree：树构建规则（升降级、无标题兜底）
- parse_file 分发与未知扩展名拒绝
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.parsers import parse_file, build_section_tree, SUPPORTED_EXTENSIONS  # noqa: E402
from app.schemas import Block, BlockType  # noqa: E402


# ═══════════════════════════════════════════════════════════════════════
# 样例包四类文件
# ═══════════════════════════════════════════════════════════════════════
def test_pdf_spec_parses(sample_dir):
    """技术规格书：文本 PDF，应提取出 10 章目录 + 页码 + 无 OCR 页。"""
    doc = parse_file(sample_dir / "02_技术规格书.pdf")
    assert doc.file_type == "pdf"
    assert doc.total_pages >= 10
    assert doc.char_count > 4000
    assert doc.ocr_pages == []
    titles = [s.title for s in doc.sections if s.level == 1]
    assert len(titles) >= 9, f"一级章节不足: {titles}"
    assert any("第三章" in t or "安防" in t for t in titles)
    headings = [b for b in doc.blocks if b.type == BlockType.HEADING]
    assert len(headings) >= 9
    assert all(b.level is not None for b in headings)


def test_scan_pdf_detects_ocr_pages(sample_dir):
    """扫描件：无文本层，应检测出 ocr_pages == [1,2] 且生成 IMAGE 块。"""
    doc = parse_file(sample_dir / "04_补充通知(扫描件).pdf")
    assert doc.file_type == "pdf"
    assert doc.total_pages == 2
    assert doc.ocr_pages == [1, 2]
    assert any(b.type == BlockType.IMAGE for b in doc.blocks)
    # 扫描页不应有正文文本
    assert doc.char_count < 50


def test_xlsx_parses_sheets(sample_dir):
    """设备清单：3 个 sheet → total_pages=3，每 sheet 一个表格块，行数充足。"""
    doc = parse_file(sample_dir / "03_设备清单.xlsx")
    assert doc.file_type == "xlsx"
    assert doc.total_pages == 3
    tables = [b for b in doc.blocks if b.type == BlockType.TABLE]
    assert len(tables) == 3
    first = tables[0].table
    assert first is not None and len(first) > 20
    assert "设备名称" in first[0][0]
    # sheet 标题块（level 1）
    sheet_titles = [b.text for b in doc.blocks if b.type == BlockType.HEADING]
    assert any("硬件清单" in t for t in sheet_titles)


def test_docx_parses_sample(docx_sample):
    """招标文件正文：13 个一级章节 + 评分表表格块；docx 无页码 → total_pages=0。"""
    doc = parse_file(docx_sample)
    assert doc.file_type == "docx"
    assert doc.total_pages == 0  # docx 无页码信息，如实记录
    assert doc.char_count > 20000
    titles = [s.title for s in doc.sections if s.level == 1]
    assert len(titles) >= 13, f"一级章节不足: {titles}"
    assert any(t.startswith("第十一章") for t in titles)
    tables = [b for b in doc.blocks if b.type == BlockType.TABLE]
    assert len(tables) >= 2, "评分表未解析为表格块"
    # 表格块无页码（docx 限制）
    assert all(b.page is None for b in tables)


# ═══════════════════════════════════════════════════════════════════════
# build_section_tree 规则
# ═══════════════════════════════════════════════════════════════════════
def _b(block_id: str, type_: BlockType, text: str, level=None, page=None) -> Block:
    return Block(block_id=block_id, type=type_, text=text, level=level, page=page)


def test_build_section_tree_nesting():
    blocks = [
        _b("B1", BlockType.HEADING, "第一章 总则", level=1, page=1),
        _b("B2", BlockType.PARAGRAPH, "本章说明……", page=1),
        _b("B3", BlockType.HEADING, "1.1 目的", level=2, page=1),
        _b("B4", BlockType.PARAGRAPH, "目的正文……", page=1),
        _b("B5", BlockType.HEADING, "第二章 要求", level=1, page=2),
        _b("B6", BlockType.PARAGRAPH, "要求正文……", page=2),
    ]
    roots = build_section_tree(blocks)
    assert len(roots) == 2
    assert roots[0].title == "第一章 总则"
    assert len(roots[0].children) == 1
    assert roots[0].children[0].title == "1.1 目的"
    # B2 归属第一章，B4 归属 1.1 节
    assert "B2" in roots[0].block_ids
    assert "B4" in roots[0].children[0].block_ids
    # 页码范围
    assert roots[1].page_start == 2 and roots[1].page_end == 2


def test_build_section_tree_no_heading_fallback():
    blocks = [_b("B1", BlockType.PARAGRAPH, "只有正文，没有标题", page=1)]
    roots = build_section_tree(blocks)
    assert len(roots) == 1
    assert roots[0].title == "全文"
    assert roots[0].block_ids == ["B1"]


def test_build_section_tree_pre_heading_blocks_dropped():
    """首个标题之前的块（封面）不挂任何节点。"""
    blocks = [
        _b("B1", BlockType.PARAGRAPH, "封面文字", page=1),
        _b("B2", BlockType.HEADING, "第一章 总则", level=1, page=2),
        _b("B3", BlockType.PARAGRAPH, "正文", page=2),
    ]
    roots = build_section_tree(blocks)
    assert len(roots) == 1
    assert "B1" not in roots[0].block_ids
    assert "B3" in roots[0].block_ids


# ═══════════════════════════════════════════════════════════════════════
# 分发入口
# ═══════════════════════════════════════════════════════════════════════
def test_parse_file_unknown_extension(tmp_path):
    bad = tmp_path / "virus.exe"
    bad.write_bytes(b"MZ")
    with pytest.raises(ValueError, match="不支持的文件类型"):
        parse_file(bad)


def test_supported_extensions_whitelist():
    assert {".pdf", ".docx", ".xlsx", ".png", ".jpg", ".jpeg", ".tif", ".tiff"} <= SUPPORTED_EXTENSIONS
    assert ".exe" not in SUPPORTED_EXTENSIONS
