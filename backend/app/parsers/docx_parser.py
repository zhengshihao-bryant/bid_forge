# -*- coding: utf-8 -*-
"""
app/parsers/docx_parser.py —— Word 解析器（python-docx 1.2.0）

- document.iter_inner_content()（1.1.0 原生引入）按序产出 Paragraph|Table，
  段落与表格交错保序，无需底层 CT 技巧
- 不递归表格单元格内段落（必要时 cell.iter_inner_content() 手动递归，
  招标文件嵌套表格罕见，先不实现）
- 标题检测判 **style_id 前缀 "Heading"**（语言无关，勿匹配显示名"标题 1"），
  outlineLvl → 列表 numPr → 编号正则逐级兜底

已知限制（如实记录）：docx 无页码信息（Word 页面属于渲染层，XML 中不存在），
page 恒为 None；出处锚点以 section_path + block_id 为准，见 README「已知限制」。
"""

from __future__ import annotations

import re
from pathlib import Path

from docx import Document
from docx.table import Table
from docx.text.paragraph import Paragraph

from ..schemas import Block, BlockType, ParsedDocument
from .base import build_section_tree

_HEADING_PATTERNS = [
    re.compile(r"^第[一二三四五六七八九十百0-9]+[章节篇部分]"),
    re.compile(r"^[一二三四五六七八九十]+、"),
    re.compile(r"^[（(][一二三四五六七八九十0-9]+[）)]"),
    re.compile(r"^\d+(\.\d+){0,3}\s*\S"),
    re.compile(r"^附件\s*[一二三四五六七八九十0-9]*[:：]?"),
]
_END_PUNCT = set("。；，、：！？…""''）)】]》")
_MAX_HEADING_LEN = 40


def _guess_level(text: str) -> int:
    m = re.match(r"^(\d+(?:\.\d+){0,3})", text)
    if m:
        return m.group(1).count(".") + 1
    if re.match(r"^第[一二三四五六七八九十百0-9]+[章节]", text):
        return 1
    if re.match(r"^[一二三四五六七八九十]+、", text):
        return 2
    if re.match(r"^[（(][一二三四五六七八九十0-9]+[）)]", text):
        return 3
    return 1


class DocxParser:
    file_type = "docx"

    def parse(self, path: Path | str) -> ParsedDocument:
        path = Path(path)
        doc = Document(str(path))

        blocks: list[Block] = []
        char_count = 0
        bid = [0]

        def new_block(type_, text, level=None, table=None) -> Block:
            bid[0] += 1
            return Block(block_id=f"B{bid[0]:04d}", type=type_, text=text,
                         level=level, table=table)

        for item in doc.iter_inner_content():
            if isinstance(item, Paragraph):
                text = item.text.strip()
                if not text:
                    continue
                level = self._heading_level(item, text)
                if level:
                    blocks.append(new_block(BlockType.HEADING, text, level=level))
                elif self._is_list(item):
                    blocks.append(new_block(BlockType.LIST_ITEM, text))
                else:
                    blocks.append(new_block(BlockType.PARAGRAPH, text))
                char_count += len(text)
            elif isinstance(item, Table):
                rows = [[cell.text.strip() for cell in row.cells] for row in item.rows]
                rows = [r for r in rows if any(c for c in r)]  # 去空行
                if not rows:
                    continue
                text = "\n".join(" | ".join(r) for r in rows)
                blocks.append(new_block(BlockType.TABLE, text, table=rows))
                char_count += len(text)

        sections = build_section_tree(blocks)
        return ParsedDocument(
            schema_version="1.0.0",
            file_name=path.name,
            file_type="docx",
            total_pages=0,          # docx 无页码（渲染层信息），如实记录
            char_count=char_count,
            sections=sections,
            blocks=blocks,
        )

    # ------------------------------------------------------------------
    @staticmethod
    def _heading_level(p: Paragraph, text: str) -> int:
        """标题层级判定：style_id "Heading" 前缀 > outlineLvl > 编号正则。"""
        style = p.style
        if style is not None:
            sid = style.style_id or ""
            if sid.startswith("Heading"):
                digits = sid[len("Heading"):]
                return int(digits) if digits.isdigit() else 1
        try:
            pPr = p._p.pPr
            if pPr is not None and pPr.outlineLvl is not None:
                return pPr.outlineLvl.val + 1  # 0 基 → 1 基
        except Exception:
            pass
        if (len(text) <= _MAX_HEADING_LEN and text[-1] not in _END_PUNCT
                and any(pat.match(text) for pat in _HEADING_PATTERNS)):
            return _guess_level(text)
        return 0

    @staticmethod
    def _is_list(p: Paragraph) -> bool:
        """是否列表项（numPr 存在 = 编号/项目符号列表）。"""
        try:
            pPr = p._p.pPr
            return pPr is not None and pPr.numPr is not None
        except Exception:
            return False
