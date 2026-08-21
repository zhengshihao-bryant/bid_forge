# -*- coding: utf-8 -*-
"""
tests/test_capability_extractor.py —— 能力卡提取单元测试（离线，FakeLLM）

覆盖：
- 基本提取：类别分派、attributes 归一、quantitative 字符串化、页码兜底
- 坏条丢弃计数（坏类别/缺名称/attributes 非 dict）
- 去重键 (category, 去空白 name) + 全局编号 CAP-XXXX
- finish_reason=length → 半窗递归
- 历史标书跳过提取（只切块嵌入）
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.parsers import build_section_tree  # noqa: E402
from app.schemas import Block, BlockType, CapabilityCategory, ParsedDocument  # noqa: E402
from app.services.capability_extractor import CapabilityExtractor  # noqa: E402

from conftest import FakeLLM  # noqa: E402


# ═══════════════════════════════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════════════════════════════
def _b(block_id: str, type_: BlockType, text: str = "", page=None,
       table=None, level=None) -> Block:
    return Block(block_id=block_id, type=type_, text=text, page=page,
                 table=table, level=level)


def _doc(file_name="04_人员资质.docx", file_type="docx", blocks=None) -> ParsedDocument:
    doc = ParsedDocument(file_name=file_name, file_type=file_type)
    doc.blocks = blocks or []
    doc.sections = build_section_tree(doc.blocks)
    return doc


_ZHANGWEI = {
    "category": "人员资质", "name": "张伟-项目经理",
    "description": "PMP、信息系统项目管理师",
    "attributes": {"person_name": "张伟", "role": "项目经理",
                   "experience_years": "6", "certs": ["PMP", "信息系统项目管理师"],
                   "projects": ["智慧园区一期"]},
    "page": 2,
}

_LIHUA = {
    "category": "人员资质", "name": "李华-技术负责人",
    "description": "高级工程师",
    "attributes": {"person_name": "李华", "role": "技术负责人",
                   "experience_years": "10", "certs": ["高级工程师"]},
    "page": 2,
}


# ═══════════════════════════════════════════════════════════════════════
# 基本提取与编号
# ═══════════════════════════════════════════════════════════════════════
def test_extract_basic_and_numbering():
    doc = _doc(blocks=[
        _b("B1", BlockType.HEADING, "4.2 项目经理详情", level=3, page=2),
        _b("B2", BlockType.PARAGRAPH, "张伟具有 6 年智慧园区类项目管理经验。", page=2),
    ])
    fake = FakeLLM(responses=[[dict(_ZHANGWEI), dict(_LIHUA)]], data_key="capabilities")
    extractor = CapabilityExtractor(client=fake)
    caps, stats = extractor.extract(doc.file_name, CapabilityCategory.PERSONNEL,
                                    doc, start_no=3)

    assert len(caps) == 2
    assert [c.id for c in caps] == ["CAP-0003", "CAP-0004"]   # 全局编号起点
    assert stats["windows"] == 1 and stats["llm_calls"] == 1
    assert stats["dropped_items"] == 0
    # 溯源：文件名 + 页码原样保留
    assert caps[0].source_doc == "04_人员资质.docx"
    assert caps[0].source_page == 2
    assert caps[0].category == CapabilityCategory.PERSONNEL
    assert caps[0].attributes["experience_years"] == "6"
    assert caps[0].attributes["certs"] == ["PMP", "信息系统项目管理师"]


def test_drop_invalid_items_and_dedupe():
    doc = _doc(blocks=[
        _b("B1", BlockType.HEADING, "4.1 核心项目团队", level=3),
        _b("B2", BlockType.PARAGRAPH, "核心团队成员情况如下。"),
    ])
    bad_category = {"category": "外星类别", "name": "某卡", "attributes": {}, "page": 1}
    no_name = {"category": "人员资质", "name": "   ", "attributes": {}, "page": 1}
    no_attrs = {"category": "人员资质", "name": "缺属性卡", "page": 1}
    duplicate = dict(_ZHANGWEI)
    duplicate["name"] = "张伟 - 项目经理"     # 与 _ZHANGWEI 去空白后同 key
    fake = FakeLLM(responses=[[
        dict(_ZHANGWEI), bad_category, no_name, no_attrs, duplicate,
    ]], data_key="capabilities")
    caps, stats = CapabilityExtractor(client=fake).extract(
        doc.file_name, CapabilityCategory.PERSONNEL, doc, start_no=1)

    assert len(caps) == 1, "坏条全丢 + 去重后只剩 1 张"
    assert caps[0].id == "CAP-0001"
    assert stats["dropped_items"] == 3


def test_quantitative_normalized_to_strings():
    doc = _doc(blocks=[
        _b("B1", BlockType.HEADING, "1.3 技术指标", level=2, page=2),
        _b("B2", BlockType.PARAGRAPH, "平台支持不少于 2000 台设备接入。", page=2),
    ])
    card = {
        "category": "产品", "name": "智慧园区综合管理平台", "description": "园区管理软件",
        "attributes": {
            "product": "智慧园区综合管理平台", "version": "V3.2",
            "key_capabilities": ["视频监控"],
            "quantitative": [
                {"metric": "设备接入", "op": "不少于", "value": 2000, "unit": "台"},  # 数字原样→字符串
                "不是字典会被丢弃",
            ],
        },
        "page": 2,
    }
    fake = FakeLLM(responses=[[card]], data_key="capabilities")
    caps, _stats = CapabilityExtractor(client=fake).extract(
        doc.file_name, CapabilityCategory.PRODUCT, doc, start_no=1)
    assert len(caps) == 1
    q = caps[0].attributes["quantitative"]
    assert q == [{"metric": "设备接入", "op": "不少于", "value": "2000", "unit": "台"}]


def test_invalid_page_falls_back_to_window_start():
    doc = _doc(blocks=[
        _b("B1", BlockType.HEADING, "4.2 项目经理详情", level=3, page=5),
        _b("B2", BlockType.PARAGRAPH, "张伟具有 6 年经验。", page=5),
    ])
    card = dict(_ZHANGWEI)
    card["page"] = 0          # 非法页码 → 回退窗口起始页
    fake = FakeLLM(responses=[[card]], data_key="capabilities")
    caps, _stats = CapabilityExtractor(client=fake).extract(
        doc.file_name, CapabilityCategory.PERSONNEL, doc, start_no=1)
    assert caps[0].source_page == 5


def test_length_finish_reason_half_window_split():
    """输出截断 → 半窗递归，两个半窗分别提取。"""
    doc = _doc(blocks=[
        _b("B1", BlockType.HEADING, "4.2 项目经理详情", level=3, page=2),
        _b("B2", BlockType.PARAGRAPH, "张伟具有 6 年经验。", page=2),
        _b("B3", BlockType.PARAGRAPH, "李华具有 10 年经验。", page=2),
        _b("B4", BlockType.PARAGRAPH, "王芳具有 5 年经验。", page=2),
        _b("B5", BlockType.PARAGRAPH, "团队情况如上。", page=2),
    ])
    fake = FakeLLM(responses=[
        {"data": {"capabilities": []}, "finish_reason": "length", "usage": {}},
        [dict(_ZHANGWEI)],                      # 前半窗
        [dict(_LIHUA)],                         # 后半窗
    ], data_key="capabilities")
    caps, stats = CapabilityExtractor(client=fake).extract(
        doc.file_name, CapabilityCategory.PERSONNEL, doc, start_no=1)
    assert len(caps) == 2
    assert stats["llm_calls"] >= 3              # 1 次截断 + 两个半窗
    assert stats["windows_failed"] == 0


def test_historical_bid_skips_extraction():
    """历史标书只切块嵌入，不提取能力卡（0 次 LLM 调用）。"""
    doc = _doc(file_name="08_历史标书.docx", blocks=[
        _b("B1", BlockType.HEADING, "8.1 投标函", level=3),
        _b("B2", BlockType.PARAGRAPH, "我方愿意承接本项目。"),
    ])
    fake = FakeLLM(data_key="capabilities")
    caps, stats = CapabilityExtractor(client=fake).extract(
        doc.file_name, CapabilityCategory.HISTORICAL_BID, doc, start_no=1)
    assert caps == []
    assert stats.get("skipped") is True
    assert fake.calls == 0


def test_three_retries_then_window_failed():
    """LLM 连续 3 次返回非列表 → 窗口失败计数，不抛异常。"""
    doc = _doc(blocks=[
        _b("B1", BlockType.HEADING, "4.2 项目经理详情", level=3),
        _b("B2", BlockType.PARAGRAPH, "张伟具有 6 年经验。"),
    ])
    fake = FakeLLM(responses=[{"oops": 1}, {"oops": 2}, {"oops": 3}],
                   data_key="capabilities")
    caps, stats = CapabilityExtractor(client=fake).extract(
        doc.file_name, CapabilityCategory.PERSONNEL, doc, start_no=1)
    assert caps == []
    assert stats["windows_failed"] == 1
    assert stats["llm_calls"] == 3
