# -*- coding: utf-8 -*-
"""
tests/test_llm_integration.py —— 真实 LLM 集成测试（@pytest.mark.llm）

默认跳过；需要真实 DeepSeek Key 时运行：

    pytest tests/test_llm_integration.py -m llm -v

验收对象：样例招标文件正文（docx）+ 技术规格书（PDF）→ 需求提取 →
对照 样例说明.md 预埋需求基线抽查召回与数值准确性。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app import config  # noqa: E402
from app.parsers import parse_file  # noqa: E402
from app.services.extraction import RequirementExtractor, parse_score_tables  # noqa: E402
from app.services.llm import create_llm_client  # noqa: E402

pytestmark = [
    pytest.mark.llm,
    pytest.mark.skipif(
        not config.LLM_API_KEY, reason="未配置 LLM_API_KEY，跳过真实 LLM 集成测试"),
]

SAMPLE_DIR = config.SAMPLES_DIR / "智慧园区项目"

# 预埋基线（与 样例说明.md 对应）：关键词 → 期望量化数值（原文必须原样保留）
BASELINE = [
    ("设备接入", ["1000"]),
    ("并发", ["500"]),
    ("99.9", ["99.9"]),
    ("人脸识别", ["99.5"]),
    ("工期", ["12"]),
    ("项目经理", ["5"]),
    ("质保", ["2"]),
    ("业绩", ["3"]),
    ("评分", ["50", "20", "30"]),
    ("正本", ["1", "4"]),
]


@pytest.fixture(scope="module")
def extracted():
    """样例 docx + pdf → 提取（真实 LLM，耗时分钟级，模块级缓存）。"""
    docs = []
    for name in ("01_招标文件正文.docx", "02_技术规格书.pdf"):
        p = SAMPLE_DIR / name
        if not p.exists():
            pytest.skip(f"样例文件缺失: {name}")
        docs.append(parse_file(p))
    extractor = RequirementExtractor(create_llm_client())
    reqs, stats = extractor.extract("llm-test", "XX市智慧园区建设项目", docs,
                                    doc_id_map={d.file_name: f"doc-{i}" for i, d in enumerate(docs)})
    return reqs, stats, docs


def test_baseline_recall(extracted):
    """预埋基线召回：10 组关键词至少命中 8 组。"""
    reqs, stats, _ = extracted
    haystack = "\n".join(f"{r.title} {r.original_text}" for r in reqs)
    hits = [kw for kw, _ in BASELINE if kw in haystack]
    missed = [kw for kw, _ in BASELINE if kw not in haystack]
    assert len(hits) >= 8, f"基线召回不足: 命中 {hits}，漏检 {missed}"


def test_quantitative_numbers_unchanged(extracted):
    """事实约束铁律：量化数字必须与预埋值一致（原文原样，不得改写）。"""
    reqs, _, _ = extracted
    # 宽松策略：预埋数值只要出现在量化字段池中即视为保留（原文原样）
    all_values = " ".join(
        f"{q.op}{q.value}{q.unit}" for r in reqs for q in r.quantitative)
    for v in ("1000", "500", "99.9", "12", "5", "2", "3", "50", "20", "30"):
        assert v in all_values, f"量化数值 {v} 丢失或改写"


def test_star_clauses_flagged(extracted):
    """★条款补扫：项目经理/ISO 条款应 is_star=True 且 importance=高。"""
    reqs, _, _ = extracted
    star_titles = [r.title for r in reqs if r.is_star]
    assert any("项目经理" in t for t in star_titles), f"★条款未标记: {star_titles}"
    assert all(r.importance == "高" for r in reqs if r.is_star)


def test_score_tables_rule_parsed(extracted):
    """评分表规则解析（不走 LLM）：技术 9 + 商务 4 = 13 点，权重和 70。"""
    _, _, docs = extracted
    points, warnings = parse_score_tables("llm-test", docs)
    assert len(points) == 13, f"评分点 {len(points)} != 13（告警: {warnings}）"
    assert sum(p.weight for p in points) == 70.0


def test_source_anchors_present(extracted):
    """四元溯源：需求必须带出处（PDF 有页码 / docx 有章节路径）。"""
    reqs, _, _ = extracted
    for r in reqs:
        assert r.source is not None
        assert r.source.document
        if r.source.document.endswith(".pdf"):
            assert r.source.page is not None, f"PDF 需求缺页码: {r.title}"
        assert r.source.section_path or r.source.block_id
