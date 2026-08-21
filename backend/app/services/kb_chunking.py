# -*- coding: utf-8 -*-
"""
app/services/kb_chunking.py —— 企业资料切块（M2 知识库）

切块目标：检索粒度。~600 字一块（KB_CHUNK_CHARS 可配），与 M1 需求提取窗口
（≤4000 字喂 LLM）是两个概念——检索块要小，才能命中精准、出处清晰。

铁律（沿用 M1 extraction.py 模块 docstring 的约束）：
    【第p页】页码标记只存在于 LLM 提取窗口的临时文本；本模块产出的 chunk.content
    是干净块文本，绝不掺页码标记——页码进 page_start/page_end 元数据，
    否则污染 M2 向量与 M3 引用。

切块规则：
- 按顶层章节顺序遍历块（含子章节），块组贪心合并至 ≤max_chars
- 表格块整块保留；超长表格按行二次切分（每块仍 ≤max_chars）
- 溢出时优先回退到最近页边界切分（复用 extraction._chunk_blocks 思路）
- 单段超长按句边界硬切
- docx 无页码 → page_start/page_end 恒 None，以 section_path + block_ids 溯源

chunk_id：{material_id}_C{n:04d}（600 字切块会跨块合并，块级 ID 语义失真，
故不用块号命名；块级出处由 block_ids 完整保留）。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Optional

from .. import config
from ..schemas import Block, BlockType, CapabilityCategory, KbChunk, ParsedDocument, now_str
from .extraction import _block_text

logger = logging.getLogger(__name__)


@dataclass
class _Item:
    """切块中间项：块 + 其所在章节路径（前序遍历带出的祖先路径）。"""
    block: Block
    path: str


def _walk(sec, ancestors: list[str], block_map: dict[str, Block], out: list[_Item]) -> None:
    """前序遍历章节树，收集 (block, 完整章节路径)。"""
    path = ancestors + [sec.title]
    for bid in sec.block_ids:
        block = block_map.get(bid)
        if block is not None:
            out.append(_Item(block=block, path=" > ".join(path)))
    for child in sec.children:
        _walk(child, path, block_map, out)


def _split_long_text(text: str, max_chars: int) -> list[str]:
    """单段超长 → 按句边界硬切成 ≤max_chars 的片段。"""
    if len(text) <= max_chars:
        return [text]
    sentences = re.split(r"(?<=[。；;！？!?])", text)
    parts: list[str] = []
    cur = ""
    for s in sentences:
        if cur and len(cur) + len(s) > max_chars:
            parts.append(cur)
            cur = s
            # 单句本身超长：硬切
            while len(cur) > max_chars:
                parts.append(cur[:max_chars])
                cur = cur[max_chars:]
        else:
            cur += s
    if cur:
        parts.append(cur)
    return [p for p in parts if p.strip()]


def _split_table(block: Block, max_chars: int) -> list[Block]:
    """超长表格按行二次切分（每子块仍 ≤max_chars，行边界完整）。"""
    if not block.table:
        return [block]
    out: list[Block] = []
    rows: list[str] = []
    chars = 0
    for row in block.table:
        line = " | ".join(row)
        # 子块最终文本按 _block_text 口径 = 各行之和 + (行数-1) 个换行符；
        # 预判换行开销（再并入一行将新增 len(rows) 个换行），保证子块 ≤max_chars
        if rows and chars + len(line) + len(rows) > max_chars:
            out.append(Block(
                block_id=block.block_id, type=BlockType.TABLE,
                text="\n".join(rows), page=block.page, table=[r.split(" | ") for r in rows]))
            rows, chars = [], 0
        rows.append(line)
        chars += len(line)
    if rows:
        out.append(Block(
            block_id=block.block_id, type=BlockType.TABLE,
            text="\n".join(rows), page=block.page, table=[r.split(" | ") for r in rows]))
    return out


def _group_items(items: list[_Item], max_chars: int) -> list[list[_Item]]:
    """块序列 → 若干 ≤max_chars 的块组；溢出优先回退最近页边界切分。"""
    groups: list[list[_Item]] = []
    current: list[_Item] = []
    chars = 0
    for item in items:
        b = item.block
        # 超长表格：先按行切分子块
        if b.type == BlockType.TABLE and len(_block_text(b)) > max_chars:
            for sub in _split_table(b, max_chars):
                if current:
                    groups.append(current)
                    current, chars = [], 0
                groups.append([_Item(block=sub, path=item.path)])
            continue
        length = len(_block_text(b))
        if current and chars + length > max_chars:
            cut = None
            for i in range(len(current) - 1, 0, -1):
                if (current[i].block.page is not None
                        and current[i].block.page != current[i - 1].block.page):
                    cut = i
                    break
            if cut is not None:
                groups.append(current[:cut])
                current = current[cut:]
                chars = sum(len(_block_text(x.block)) for x in current)
            else:
                groups.append(current)
                current, chars = [], 0
        current.append(item)
        chars += length
    if current:
        groups.append(current)
    return groups


def build_chunks(doc: ParsedDocument, material_id: str, file_name: str,
                 category: CapabilityCategory, max_chars: Optional[int] = None) -> list[KbChunk]:
    """ParsedDocument → KbChunk 列表（干净文本，页码/章节路径进元数据）。"""
    max_chars = max_chars or config.KB_CHUNK_CHARS
    block_map = {b.block_id: b for b in doc.blocks}

    items: list[_Item] = []
    for sec in doc.sections:
        _walk(sec, [], block_map, items)

    chunks: list[KbChunk] = []
    for group in _group_items(items, max_chars):
        # 单段超长硬切（_group_items 只处理了表格，普通段在 group 内仍可能超长）
        pieces: list[list[_Item]] = [[]]
        for item in group:
            text = _block_text(item.block)
            if text and len(text) > max_chars:
                for part in _split_long_text(text, max_chars):
                    sub = Block(block_id=item.block.block_id, type=item.block.type,
                                text=part, page=item.block.page)
                    pieces.append([_Item(block=sub, path=item.path)])
                    pieces.append([])
            else:
                pieces[-1].append(item)
        for piece in pieces:
            if not piece:
                continue
            pages = [x.block.page for x in piece if x.block.page is not None]
            chunks.append(KbChunk(
                id="",  # 编号后回填
                material_id=material_id,
                category=category,
                file_name=file_name,
                content="\n".join(_block_text(x.block) for x in piece),
                section_path=piece[0].path,
                page_start=min(pages) if pages else None,
                page_end=max(pages) if pages else None,
                block_ids=[x.block.block_id for x in piece],
                seq=0,
                created_at=now_str(),
            ))

    # 编号：{material_id}_C{n:04d}，资料内递增
    for i, c in enumerate(chunks):
        c.id = f"{material_id}_C{i + 1:04d}"
        c.seq = i + 1
    logger.info("切块完成：%s → %d 块（max_chars=%d）", file_name, len(chunks), max_chars)
    return chunks


__all__ = ["build_chunks"]
