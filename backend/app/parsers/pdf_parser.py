# -*- coding: utf-8 -*-
"""
app/parsers/pdf_parser.py —— PDF 解析器（PyMuPDF 1.28.0，钉版）

标题检测策略（按优先级）：
  1. 文档目录 get_toc()（样例 PDF 与多数正式招标文件都有书签目录）
  2. 字号启发式：行字号 ≥ 页内主流字号 × 1.15，短行（≤40 字）且无句末标点
  3. 编号正则：第X章 / X.Y / 一、 /（一）/ 附件X

扫描页检测（技术校验阈值）：
  页文本 < 20 字符 且 有图片 且 词数 ≤ 2 → 记入 ocr_pages + IMAGE 块。
  OCR 是独立步骤（app/parsers/ocr.py 按需触发），不装 PaddleOCR 也能跑通全链路。

表格 page.find_tables() 已知坑：可能返回 None、无边框表漏检、隐形矩形误检
  → None 守卫 + 行列数一致性校验（单列表降级为正文文本）。

段落合并：页内行按 (y, x) 阅读顺序排序；垂直间距 < 1.5 倍行高的相邻正文行并为一段。
"""

from __future__ import annotations

import re
from pathlib import Path

import fitz

from ..schemas import Block, BlockType, ParsedDocument
from .base import build_section_tree

_HEADING_PATTERNS = [
    re.compile(r"^第[一二三四五六七八九十百0-9]+[章节篇部分]"),      # 第X章/第X节
    re.compile(r"^[一二三四五六七八九十]+、"),                        # 一、
    re.compile(r"^[（(][一二三四五六七八九十0-9]+[）)]"),             # （一）
    re.compile(r"^\d+(\.\d+){0,3}\s*\S"),                            # 1. / 1.2 / 1.2.3
    re.compile(r"^附件\s*[一二三四五六七八九十0-9]*[:：]?"),          # 附件X
]
_END_PUNCT = set("。；，、：！？…""''）)】]》")
_MAX_HEADING_LEN = 40


def _guess_level(text: str) -> int:
    """按编号形态猜测标题层级（无 TOC 时的启发式）。"""
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


class _Line:
    """页内文本行（标题判定与段落合并的中间结构）。"""

    __slots__ = ("text", "size", "bbox")

    def __init__(self, text: str, size: float, bbox: tuple):
        self.text = text
        self.size = size
        self.bbox = bbox  # (x0, y0, x1, y1)


class PdfParser:
    file_type = "pdf"

    def parse(self, path: Path | str) -> ParsedDocument:
        path = Path(path)
        doc = fitz.open(str(path))
        try:
            return self._parse(doc, path.name)
        finally:
            doc.close()

    # ------------------------------------------------------------------
    def _parse(self, doc: fitz.Document, file_name: str) -> ParsedDocument:
        toc = doc.get_toc() or []
        toc_map = {(t[2], t[1].strip()): t[0] for t in toc}  # (页码, 标题) → 层级

        blocks: list[Block] = []
        ocr_pages: list[int] = []
        char_count = 0
        bid = [0]

        def new_block(type_, text, page, level=None, table=None) -> Block:
            bid[0] += 1
            return Block(
                block_id=f"B{bid[0]:04d}", type=type_, text=text,
                page=page, level=level, table=table,
            )

        for page_no in range(len(doc)):
            page = doc.load_page(page_no)
            pno = page_no + 1
            text = page.get_text("text").strip()
            words = page.get_text("words")
            images = page.get_images(full=True)

            # ── 扫描页检测 ──
            if len(text) < 20 and images and len(words) <= 2:
                ocr_pages.append(pno)
                blocks.append(new_block(
                    BlockType.IMAGE, f"[扫描页 第{pno}页 · 需 OCR]", pno))
                continue

            char_count += len(text)
            blocks.extend(self._parse_page(page, pno, toc_map, new_block))

        sections = build_section_tree(blocks)
        return ParsedDocument(
            schema_version="1.0.0",
            file_name=file_name,
            file_type="pdf",
            total_pages=len(doc),
            char_count=char_count,
            ocr_pages=ocr_pages,
            sections=sections,
            blocks=blocks,
        )

    # ------------------------------------------------------------------
    def _parse_page(self, page: fitz.Page, pno: int, toc_map: dict,
                    new_block) -> list[Block]:
        """单页 → 块列表（标题 / 表格 / 段落，按阅读顺序）。"""
        # ── 表格（先抽，正文行要避开表格区域）──
        tables: list[tuple[tuple, list[list[str]]]] = []  # (bbox, rows)
        try:
            tabs = page.find_tables()
            tlist = tabs.tables if tabs is not None else []
        except Exception:
            tlist = []
        for t in tlist:
            try:
                raw = t.extract()
            except Exception:
                continue
            if not raw or not raw[0]:
                continue
            ncols = max(len(r) for r in raw)
            if ncols < 2:  # 单列表降级为正文（find_tables 误检）
                continue
            rows = [[(c or "").strip().replace("\n", " ") for c in r] for r in raw]
            tables.append((t.bbox, rows))

        def in_table(bbox) -> bool:
            x0, y0, x1, y1 = bbox
            cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
            return any(tb[0] <= cx <= tb[2] and tb[1] <= cy <= tb[3] for tb, _ in tables)

        # ── 文本行 ──
        lines: list[_Line] = []
        sizes: dict[float, int] = {}
        for blk in page.get_text("dict")["blocks"]:
            if blk.get("type") != 0:
                continue
            for line in blk.get("lines", []):
                text = "".join(s["text"] for s in line["spans"]).strip()
                if not text or in_table(line["bbox"]):
                    continue
                size = max((s["size"] for s in line["spans"]), default=0)
                sizes[round(size, 1)] = sizes.get(round(size, 1), 0) + len(text)
                lines.append(_Line(text, size, line["bbox"]))
        # 页内主流字号（按字符数加权）
        body_size = max(sizes.items(), key=lambda kv: kv[1])[0] if sizes else 10.0

        # ── 组装 items 并按 (y, x) 排序 ──
        items: list[tuple[float, float, object]] = []
        for tb, rows in tables:
            text = "\n".join(" | ".join(r) for r in rows)
            items.append((tb[1], tb[0],
                          new_block(BlockType.TABLE, text, pno, table=rows)))
        for ln in lines:
            kind = self._heading_kind(ln.text, ln.size, body_size, pno, toc_map)
            if kind == "heading":
                items.append((ln.bbox[1], ln.bbox[0],
                              new_block(BlockType.HEADING, ln.text, pno,
                                        level=_guess_level(ln.text))))
            else:
                items.append((ln.bbox[1], ln.bbox[0], ("line", ln)))
        items.sort(key=lambda it: (it[0], it[1]))

        # ── 正文行合并为段落 ──
        blocks: list[Block] = []
        para: list[_Line] = []
        for _, _, it in items:
            if isinstance(it, Block):
                if para:
                    blocks.append(self._flush_para(para, pno, new_block))
                    para = []
                blocks.append(it)
            else:  # ("line", ln)
                ln = it[1]
                if para:
                    prev = para[-1]
                    line_h = prev.bbox[3] - prev.bbox[1]
                    gap = ln.bbox[1] - prev.bbox[3]
                    if gap > 1.5 * max(line_h, 1):
                        blocks.append(self._flush_para(para, pno, new_block))
                        para = []
                para.append(ln)
        if para:
            blocks.append(self._flush_para(para, pno, new_block))
        return blocks

    @staticmethod
    def _flush_para(para: list[_Line], pno: int, new_block) -> Block:
        return new_block(BlockType.PARAGRAPH, "".join(l.text for l in para), pno)

    # ------------------------------------------------------------------
    def _heading_kind(self, text: str, size: float, body_size: float,
                      pno: int, toc_map: dict) -> str:
        """返回 'heading' 或 ''。TOC 精确匹配 > 字号 > 编号正则。"""
        if (pno, text) in toc_map:
            return "heading"
        if len(text) > _MAX_HEADING_LEN:
            return ""
        if text[-1] in _END_PUNCT:
            return ""
        if size >= body_size * 1.15:
            return "heading"
        if any(p.match(text) for p in _HEADING_PATTERNS):
            return "heading"
        return ""
