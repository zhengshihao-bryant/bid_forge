# -*- coding: utf-8 -*-
"""
tests/test_extraction.py —— 需求提取层单元测试（离线，FakeLLM）

覆盖：
- 窗口切分：≤窗口上限、【第p页】标记只存在于窗口临时文本（不写回 Block）
- 页边界回退切分
- 提取端到端：类型归一、量化解析、坏条丢弃、去重编号、doc_id 映射
- ★条款规则补扫
- 评分表规则解析（不走 LLM）：列检测优先级、类别判定、权重
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.parsers import build_section_tree  # noqa: E402
from app.schemas import Block, BlockType, ParsedDocument  # noqa: E402
from app.services.extraction import (  # noqa: E402
    RequirementExtractor,
    _build_windows,
    _chunk_blocks,
    _coerce_type,
    _detect_columns,
    parse_score_tables,
)

from conftest import BASELINE_ITEMS, FakeLLM  # noqa: E402


# ═══════════════════════════════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════════════════════════════
def _b(block_id: str, type_: BlockType, text: str = "", page=None,
       table=None, level=None) -> Block:
    return Block(block_id=block_id, type=type_, text=text, page=page,
                 table=table, level=level)


def _doc(file_name="01_正文.pdf", file_type="pdf", blocks=None, sections=None) -> ParsedDocument:
    doc = ParsedDocument(file_name=file_name, file_type=file_type)
    if blocks is not None:
        doc.blocks = blocks
        doc.sections = sections if sections is not None else build_section_tree(blocks)
    return doc


# ═══════════════════════════════════════════════════════════════════════
# 窗口切分
# ═══════════════════════════════════════════════════════════════════════
def test_window_chunking_respects_max_chars():
    blocks = [
        _b("B1", BlockType.HEADING, "第四章 技术要求", level=1, page=10),
        _b("B2", BlockType.PARAGRAPH, "很长的正文" * 200, page=10),   # 1000 字
        _b("B3", BlockType.PARAGRAPH, "另一段正文" * 200, page=10),   # 1000 字
        _b("B4", BlockType.PARAGRAPH, "第三段正文" * 200, page=11),   # 1000 字
    ]
    doc = _doc(blocks=blocks)
    windows = _build_windows(doc, max_chars=1500)
    assert len(windows) == 3
    assert all(len(w.text) <= 1500 + 50 for w in windows)  # 页码标记不计入块长


def test_page_markers_in_window_not_in_block():
    """【第p页】标记只出现在窗口文本，绝不写回 Block.text。"""
    blocks = [
        _b("B1", BlockType.HEADING, "第一章 总则", level=1, page=1),
        _b("B2", BlockType.PARAGRAPH, "第一页的正文内容。", page=1),
        _b("B3", BlockType.PARAGRAPH, "第二页的正文内容。", page=2),
    ]
    doc = _doc(blocks=blocks)
    windows = _build_windows(doc, max_chars=4000)
    assert len(windows) == 1
    assert "【第1页】" in windows[0].text
    assert "【第2页】" in windows[0].text
    # 原块未被污染
    assert "【第" not in doc.blocks[1].text
    assert "【第" not in doc.blocks[2].text
    assert windows[0].page_start == 1 and windows[0].page_end == 2
    assert windows[0].section_path == "第一章 总则"


def test_chunk_blocks_page_boundary_cut():
    """超限时优先回退到最近页边界切分。"""
    blocks = [
        _b("B1", BlockType.PARAGRAPH, "一" * 100, page=1),
        _b("B2", BlockType.PARAGRAPH, "二" * 100, page=1),
        _b("B3", BlockType.PARAGRAPH, "三" * 100, page=2),
        _b("B4", BlockType.PARAGRAPH, "四" * 100, page=2),
    ]
    chunks = _chunk_blocks(blocks, max_chars=250)
    assert len(chunks) == 2
    # 切在页边界：第一组全在 page 1
    assert all(b.page == 1 for b in chunks[0])
    assert all(b.page == 2 for b in chunks[1])


def test_docx_no_page_uses_section_path():
    """docx 无页码：窗口 page_start 为 None，section_path 兜底。"""
    blocks = [
        _b("B1", BlockType.HEADING, "第九章 售后服务要求", level=1),
        _b("B2", BlockType.PARAGRAPH, "质保期不少于 2 年。"),
    ]
    doc = _doc(file_name="01_正文.docx", file_type="docx", blocks=blocks)
    windows = _build_windows(doc, max_chars=4000)
    assert windows[0].page_start is None
    assert windows[0].section_path == "第九章 售后服务要求"
    assert "【第" not in windows[0].text


# ═══════════════════════════════════════════════════════════════════════
# 提取端到端（FakeLLM）
# ═══════════════════════════════════════════════════════════════════════
def test_extract_end_to_end_fake_llm():
    blocks = [
        _b("B1", BlockType.HEADING, "第四章 技术要求", level=1, page=12),
        _b("B2", BlockType.PARAGRAPH, "平台应支持不少于 1000 台（个）设备的接入管理。", page=12),
        _b("B3", BlockType.HEADING, "第七章 人员要求", level=1, page=45),
        _b("B4", BlockType.PARAGRAPH, "★项目经理须具有 5 年以上智慧园区类项目管理经验。", page=45),
    ]
    doc = _doc(blocks=blocks)
    # 两个窗口各返回一条 + 一条坏条（无 type）+ 一条重复（第二窗口再来一条同标题）
    fake = FakeLLM(responses=[
        [BASELINE_ITEMS[0], {"title": "缺类型条目", "original_text": "将被丢弃"}],
        [dict(BASELINE_ITEMS[1]), dict(BASELINE_ITEMS[0]), {"type": "技术要求", "title": "缺原文"}],
    ])
    extractor = RequirementExtractor(fake)
    reqs, stats = extractor.extract(
        "t1", "XX智慧园区", [doc], doc_id_map={"01_正文.pdf": "doc-1"})

    assert len(reqs) == 2, f"期望 2 条（去重+丢弃后），实际 {len(reqs)}"
    assert stats["windows"] == 2
    assert stats["dropped_items"] == 2        # 缺 type + 缺原文
    assert stats["llm_calls"] == 2

    r1 = next(r for r in reqs if "设备接入" in r.title)
    assert r1.id == "REQ-0001"
    assert r1.type.value == "技术要求"
    assert r1.quantitative[0].value == "1000" and r1.quantitative[0].op == "不少于"
    assert r1.source.page == 12
    assert r1.source.doc_id == "doc-1"
    assert r1.source.document == "01_正文.pdf"
    assert r1.source.section_path == "第四章 技术要求"
    assert r1.source.block_id == "B2"         # snippet 与块文本双向匹配
    assert r1.is_star is False

    r2 = next(r for r in reqs if "项目经理" in r.title)
    assert r2.id == "REQ-0002"
    # ★补扫：LLM 未标 is_star，规则强制补上
    assert r2.is_star is True
    assert r2.importance == "高"


def test_extract_window_failure_retries_then_fails_soft():
    """三次失败（None 响应）→ 窗口计入 windows_failed，不整批失败。"""
    class NoneLLM:
        def chat_json(self, *a, **k):
            return None

    blocks = [
        _b("B1", BlockType.HEADING, "第一章 总则", level=1, page=1),
        _b("B2", BlockType.PARAGRAPH, "正文", page=1),
    ]
    doc = _doc(blocks=blocks)
    reqs, stats = RequirementExtractor(NoneLLM()).extract("t1", "T", [doc])
    assert reqs == []
    assert stats["retries"] == 3
    assert stats["windows_failed"] == 1


def test_extract_length_truncation_splits_window():
    """finish_reason=="length" → 半窗切分递归提取（≥4 块才可切）。"""
    blocks = [
        _b("B1", BlockType.HEADING, "第四章 技术要求", level=1, page=12),
        _b("B2", BlockType.PARAGRAPH, "平台应支持不少于 1000 台设备的接入管理。", page=12),
        _b("B3", BlockType.PARAGRAPH, "并发用户不少于 500 个。", page=12),
        _b("B4", BlockType.PARAGRAPH, "可用性不低于 99.9%。", page=12),
        _b("B5", BlockType.PARAGRAPH, "支持信创环境部署。", page=12),
    ]
    doc = _doc(blocks=blocks)
    fake = FakeLLM(responses=[
        {"data": {"requirements": []}, "finish_reason": "length", "usage": {}},
        [dict(BASELINE_ITEMS[0])],
        [],
    ])
    reqs, stats = RequirementExtractor(fake).extract("t1", "T", [doc])
    assert len(reqs) == 1
    assert stats["llm_calls"] == 3  # 原窗口 + 两个半窗


# ═══════════════════════════════════════════════════════════════════════
# 类型归一 / 列检测
# ═══════════════════════════════════════════════════════════════════════
def test_coerce_type_exact_and_fuzzy():
    assert _coerce_type("技术要求").value == "技术要求"
    assert _coerce_type("资质").value == "资质要求"      # 子串包含
    assert _coerce_type("评分标准") is not None
    assert _coerce_type("") is None
    assert _coerce_type("外星要求") is None


def test_detect_columns_priority():
    """“评分标准”应判为细则列而非分值列；“评价项”优先于两者。"""
    m = _detect_columns(["序号", "评价项", "分值", "评分标准"])
    assert m["item"] == 1
    assert m["score"] == 2
    assert m["criteria"] == 3


# ═══════════════════════════════════════════════════════════════════════
# 评分表规则解析（不走 LLM）
# ═══════════════════════════════════════════════════════════════════════
SCORE_TABLE_BLOCK = Block(
    block_id="B10", type=BlockType.TABLE, page=61,
    table=[
        ["序号", "评价项", "分值", "评分细则"],
        ["1", "总体技术方案", "10", "方案完整得 8-10 分"],
        ["2", "企业资质", "6", "具备 ISO9001、ISO27001、CMMI3 得 6 分"],
        ["3", "类似业绩", "4", "每提供 1 个类似项目得 2 分"],
    ],
)


def test_parse_score_tables_basic():
    doc = _doc(blocks=[SCORE_TABLE_BLOCK])
    points, warnings = parse_score_tables("t1", [doc])
    assert warnings == []
    assert len(points) == 3
    assert [p.id for p in points] == ["SC-0001", "SC-0002", "SC-0003"]
    assert points[0].item == "总体技术方案"
    assert points[0].max_score == 10.0 and points[0].weight == 10.0
    assert points[0].rule_id == "RULE-B10"
    assert points[0].source_ref == "01_正文.pdf#B10"
    # 类别按 表头+评价项 判定：方案行 → 技术；资质/业绩行 → 商务
    assert points[0].category == "技术"
    assert points[1].category == "商务"
    assert points[2].category == "商务"


def test_parse_score_tables_category_detection():
    biz = Block(block_id="B11", type=BlockType.TABLE, page=62, table=[
        ["序号", "评审内容", "分数", "评分办法"],
        ["1", "企业信誉", "2", "无失信记录得 2 分"],
    ])
    doc = _doc(blocks=[biz])
    points, _ = parse_score_tables("t1", [doc])
    assert len(points) == 1
    assert points[0].category == "商务"


def test_parse_score_tables_category_from_section_context():
    """行内关键词识别不出类别时，靠章节标题兜底（"11.2 技术评分表" → 技术）。"""
    blocks = [
        _b("B20", BlockType.HEADING, "11.2 技术评分表（50 分）", level=2, page=60),
        Block(block_id="B21", type=BlockType.TABLE, page=60, table=[
            ["序号", "评价项", "分值", "评分细则"],
            ["1", "安全性设计", "5", "安全设计完整得 4-5 分"],
            ["2", "国产化适配", "5", "信创方案完整得 4-5 分"],
        ]),
    ]
    doc = _doc(blocks=blocks)
    points, _ = parse_score_tables("t1", [doc])
    assert len(points) == 2
    assert all(p.category == "技术" for p in points)


def test_parse_score_tables_unrecognized_warns():
    """含评分关键词但识别不出评价项/分值列的表 → 告警（人工兜底）。"""
    weird = Block(block_id="B12", type=BlockType.TABLE, page=63, table=[
        ["评审办法", "说明", "备注"], ["a", "b", "c"],
    ])
    doc = _doc(blocks=[weird])
    points, warnings = parse_score_tables("t1", [doc])
    assert points == []
    assert len(warnings) == 1
    assert warnings[0]["block"] == "B12"


def test_parse_score_tables_skips_non_score_tables():
    """无评分关键词的表不参与解析。"""
    price = Block(block_id="B13", type=BlockType.TABLE, page=70, table=[
        ["设备名称", "规格", "数量", "单价"], ["摄像机", "IPC", "10", "900"],
    ])
    doc = _doc(blocks=[price])
    points, warnings = parse_score_tables("t1", [doc])
    assert points == [] and warnings == []
