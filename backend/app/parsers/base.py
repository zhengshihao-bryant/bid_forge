# -*- coding: utf-8 -*-
"""
app/parsers/base.py —— 解析层公共模型与分发入口

统一产物 ParsedDocument：章节树(sections) + 平铺内容块(blocks) + 扫描页标记(ocr_pages)。
上层（需求提取/知识库）只认这一结构，不关心文件格式。

页码/章节路径/块号是可追溯性的地基：
需求要能回溯到"哪份文件、哪一页、哪一章、哪一块"（四元溯源）。
"""

from __future__ import annotations

from pathlib import Path

from ..schemas import Block, BlockType, ParsedDocument, Section

_PDF_EXTS = {".pdf"}
_DOCX_EXTS = {".docx"}
_XLSX_EXTS = {".xlsx"}
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".tif", ".tiff"}

SUPPORTED_EXTENSIONS = _PDF_EXTS | _DOCX_EXTS | _XLSX_EXTS | _IMAGE_EXTS


def parse_file(path: Path | str) -> ParsedDocument:
    """按扩展名分发到对应解析器（图片走 OCR 模块，延迟导入避免加载 paddle）。"""
    path = Path(path)
    ext = path.suffix.lower()
    if ext in _PDF_EXTS:
        from .pdf_parser import PdfParser
        return PdfParser().parse(path)
    if ext in _DOCX_EXTS:
        from .docx_parser import DocxParser
        return DocxParser().parse(path)
    if ext in _XLSX_EXTS:
        from .xlsx_parser import XlsxParser
        return XlsxParser().parse(path)
    if ext in _IMAGE_EXTS:
        from .ocr import parse_image
        return parse_image(path)
    raise ValueError(f"不支持的文件类型: {ext}（支持 {sorted(SUPPORTED_EXTENSIONS)}）")


def build_section_tree(blocks: list[Block]) -> list[Section]:
    """由平铺块构建章节树。

    - 标题块（level 1-6）创建 Section 节点，按 level 出入栈成树
    - 非标题块挂到当前最内层 Section 的 block_ids，并维护起止页码
    - 首个标题之前的块（如封面）不挂任何节点（封面无需求，可接受）
    - 无标题的文档退化为单节点"全文"
    """
    roots: list[Section] = []
    stack: list[Section] = []
    sec_counter = [0]
    order_counter = [0]

    def new_section(title: str, level: int, page) -> Section:
        sec_counter[0] += 1
        order_counter[0] += 1
        return Section(
            id=f"S{sec_counter[0]:04d}",
            title=title,
            level=level,
            order=order_counter[0],
            page_start=page,
            page_end=page,
        )

    for b in blocks:
        if b.type == BlockType.HEADING and b.level is not None:
            while stack and stack[-1].level >= b.level:
                stack.pop()
            sec = new_section(b.text, b.level, b.page)
            if stack:
                stack[-1].children.append(sec)
            else:
                roots.append(sec)
            stack.append(sec)
        elif stack:
            sec = stack[-1]
            sec.block_ids.append(b.block_id)
            if b.page is not None:
                if sec.page_start is None:
                    sec.page_start = b.page
                sec.page_end = b.page

    if not roots and blocks:
        # 无标题文档：单节点兜底
        sec = new_section("全文", 1, blocks[0].page)
        sec.block_ids = [b.block_id for b in blocks]
        if blocks:
            sec.page_start = blocks[0].page
            sec.page_end = blocks[-1].page
        roots.append(sec)

    return roots
