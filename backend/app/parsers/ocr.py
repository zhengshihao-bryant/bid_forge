# -*- coding: utf-8 -*-
"""
app/parsers/ocr.py —— 扫描件 OCR（PaddleOCR 3.x，可插拔）

3.x API 与 2.x 完全不同：`PaddleOCR(lang="ch").predict(path)` 返回结果对象，
文本在 `res.rec_texts`；旧 `ocr.ocr()` 已弃用。
CPU 需设环境变量 PADDLE_PDX_ENABLE_MKLDNN_BYDEFAULT=0（规避 oneDNN NotImplementedError）。
首次调用会下载模型（~10MB 级），已加载提示。

设计：扫描页先"检测标记"（pdf_parser 记入 ocr_pages），OCR 是**独立步骤**，
由调用方按需触发 —— 不装 PaddleOCR 也能跑通全链路（优雅降级）。
"""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path
from typing import Optional

from ..schemas import Block, BlockType, ParsedDocument

logger = logging.getLogger(__name__)


class OCRNotAvailableError(RuntimeError):
    """PaddleOCR 未安装（可 pip install paddlepaddle paddleocr）。"""


_ocr: Optional[object] = None


def is_ocr_available() -> bool:
    try:
        import paddleocr  # noqa: F401
        return True
    except ImportError:
        return False


def get_ocr():
    """PaddleOCR 单例（延迟导入 + 环境变量规避 oneDNN 问题）。"""
    global _ocr
    if _ocr is None:
        try:
            import paddleocr
            os.environ.setdefault("PADDLE_PDX_ENABLE_MKLDNN_BYDEFAULT", "0")
            _ocr = paddleocr.PaddleOCR(lang="ch")
            logger.info("PaddleOCR 已加载（首次调用可能下载识别模型）")
        except ImportError as e:
            raise OCRNotAvailableError(
                "PaddleOCR 未安装：pip install paddlepaddle paddleocr") from e
    return _ocr


def _collect_texts(res) -> list[str]:
    """3.x 结果对象 → 文本列表（兼容 list[result] 形态）。"""
    items = res if isinstance(res, list) else [res]
    texts: list[str] = []
    for r in items:
        for t in (getattr(r, "rec_texts", None) or []):
            if t:
                texts.append(t)
    return texts


def ocr_pdf_pages(pdf_path: Path | str, pages: list[int]) -> dict[int, str]:
    """渲染指定页位图 → OCR → {页码(1 基): 文本}。

    未安装 PaddleOCR 时抛 OCRNotAvailableError，调用方降级处理。
    """
    import fitz

    ocr = get_ocr()
    result: dict[int, str] = {}
    doc = fitz.open(str(pdf_path))
    try:
        for pno in pages:
            page = doc.load_page(pno - 1)
            pix = page.get_pixmap(dpi=200)
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
                pix.save(f.name)
                tmp = Path(f.name)
            try:
                res = ocr.predict(str(tmp))
                result[pno] = "\n".join(_collect_texts(res))
            finally:
                tmp.unlink(missing_ok=True)
    finally:
        doc.close()
    return result


def parse_image(path: Path | str) -> ParsedDocument:
    """图片文件 → ParsedDocument（OCR 可用时直接识别，否则 IMAGE 块待处理）。"""
    path = Path(path)
    block = Block(block_id="B0001", type=BlockType.IMAGE,
                  text="[图片 · 待 OCR]", page=1)
    if is_ocr_available():
        try:
            res = get_ocr().predict(str(path))
            texts = _collect_texts(res)
            if texts:
                scores = []
                items = res if isinstance(res, list) else [res]
                for r in items:
                    for s in (getattr(r, "rec_scores", None) or []):
                        if isinstance(s, (int, float)):
                            scores.append(float(s))
                conf = (sum(scores) / len(scores)) if scores else None
                block = Block(block_id="B0001", type=BlockType.PARAGRAPH,
                              text="\n".join(texts), page=1,
                              ocr=True, confidence=conf)
        except OCRNotAvailableError:
            pass
    return ParsedDocument(
        schema_version="1.0.0",
        file_name=path.name,
        file_type="image",
        total_pages=1,
        char_count=len(block.text),
        ocr_pages=[] if block.type == BlockType.PARAGRAPH else [1],
        blocks=[block],
    )
