# -*- coding: utf-8 -*-
"""
app.parsers —— 解析层：四类格式统一产出 ParsedDocument。

    PDF   → pdf_parser.PdfParser（文本层；扫描页检测记入 ocr_pages）
    扫描  → ocr.ocr_pdf_pages 按需识别（PaddleOCR 3.x，可插拔）
    Word  → docx_parser.DocxParser（iter_inner_content，style_id 判标题）
    Excel → xlsx_parser.XlsxParser（sheet 序号 = page 语义）
    图片  → ocr.parse_image

使用：

    from app.parsers import parse_file
    parsed = parse_file("招标文件.pdf")
"""

from .base import build_section_tree, parse_file, SUPPORTED_EXTENSIONS  # noqa: F401

__all__ = ["parse_file", "build_section_tree", "SUPPORTED_EXTENSIONS"]
