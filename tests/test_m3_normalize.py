# -*- coding: utf-8 -*-
"""
tests/test_m3_normalize.py —— M3-01/02/03 需求标准化 / 分类 / 约束结构化（离线）

覆盖：
- 去重：精确键 + bigram Jaccard 相似去重（REQ-001/127/278 跨类型归并示例）
- 聚类：同类型阈值 0.45 / 跨类型阈值 0.6
- ID 映射：source_requirement_ids 全保留 + merge_method 口径
- 评分细则：is_scoring=True + parent_requirement_id 挂靠实体需求
- 出处保留：document/page/section_path/snippet 逐条不丢
- LLM 归并路径（脚本化真实模型名客户端）+ 无 LLM 确定性回退
- 分类：10 类关键词规则 + M1 类型亲和兜底
- 约束提取：quantitative / 正则扫描 / 存在性（ISO9001）/ 单位归一
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.services.matching.classify import RequirementClassifier  # noqa: E402
from app.services.matching.extract import ConstraintExtractor  # noqa: E402
from app.services.matching.models import RequirementTypeM3  # noqa: E402
from app.services.matching.normalize import (  # noqa: E402
    Deduplicator, RequirementClusterer, RequirementNormalizer)
from app.services.matching.normalize.normalizer import _is_scoring_member  # noqa: E402

from conftest import m3_req  # noqa: E402


# ── 用户 M3-01 示例：REQ-001/127/278 同一需求三种写法（跨类型归并） ──
# 标题两两 Jaccard ≥ 0.6（跨类型阈值）——同一需求的三种章节写法
def _device_reqs():
    return [
        m3_req(rid="REQ-0001", type_="技术要求",
               title="设备接入不少于1000台",
               text="平台应支持不少于 1000 台（个）设备的接入管理。",
               quantitative=[{"metric": "设备接入", "op": "不少于",
                              "value": "1000", "unit": "台"}],
               importance="高", page=12,
               section_path="第三章 技术要求 > 3.1 接入能力"),
        m3_req(rid="REQ-0127", type_="功能要求",
               title="设备接入支持不少于1000台",
               text="系统须支持 1000 台以上设备同时接入，满足园区设备统一管理需求。",
               quantitative=[{"metric": "设备接入", "op": "不少于",
                              "value": "1000", "unit": "台"}],
               importance="中", page=31,
               section_path="第四章 功能要求 > 4.2 平台功能"),
        m3_req(rid="REQ-0278", type_="实施要求",
               title="设备接入能力不少于1000台",
               text="本项目验收时平台设备接入能力应不低于 1000 台。",
               quantitative=[{"metric": "设备接入", "op": "不低于",
                              "value": "1000", "unit": "台"}],
               importance="中", page=78,
               section_path="第七章 实施要求 > 7.3 验收标准"),
    ]


def test_example_req_001_127_278_merge():
    """用户示例：三条跨类型需求 → 1 条规范需求，ID 映射全保留。"""
    reqs = _device_reqs()
    norm = RequirementNormalizer()
    canonicals, stats = norm.normalize("T-M3", reqs)
    assert stats["input"] == 3
    assert len(canonicals) == 1, "三条跨类型同义需求应归并为 1 条"
    c = canonicals[0]
    assert c.id == "REQ-C-0001"
    assert c.source_requirement_ids == ["REQ-0001", "REQ-0127", "REQ-0278"]
    assert c.merge_method == "similarity"
    assert c.is_star is False
    # 出处逐条保留（document/page/section_path 不丢）
    assert len(c.sources) == 3
    assert {(s.id, s.document) for s in c.sources} == {
        ("REQ-0001", "01_招标文件.docx"), ("REQ-0127", "01_招标文件.docx"),
        ("REQ-0278", "01_招标文件.docx")}
    assert c.sources[0].page == 12 and c.sources[1].page == 31
    assert c.sources[0].section_path.startswith("第三章")


def test_dedup_exact_and_similar():
    """精确重复 + 相似重复（Jaccard ≥ 0.85）都收敛；不同需求保留。"""
    reqs = _device_reqs() + [
        m3_req(type_="技术要求", title="设备接入不少于1000台",
               text="平台应支持不少于 1000 台（个）设备的接入管理。"),  # 与 REQ-0001 完全相同
        m3_req(type_="技术要求", title="设备接入不少于1000台的",
               text="平台应支持不少于 1000 台的设备接入管理。"),       # 近义改写（Jaccard ≈0.92）
        m3_req(type_="人员要求", title="项目经理5年经验",
               text="★项目经理须具有 5 年以上智慧园区类项目管理经验。",
               is_star=True, page=45),
    ]
    groups, stats = Deduplicator().dedupe(reqs)
    assert stats["exact_dupes"] == 1, stats
    assert stats["sim_dupes"] == 1, stats
    assert len(groups) == 4, stats          # 设备×1 + 项目经理×1 两簇语义主体


def test_cluster_thresholds_cross_type():
    """同类型低阈值（0.45）成簇；跨类型高阈值（0.6）——语义近才合并。"""
    clusterer = RequirementClusterer(same_type_threshold=0.45,
                                     cross_type_threshold=0.60)
    reqs = _device_reqs() + [
        m3_req(type_="技术要求", title="项目经理经验不少于5年",
               text="项目经理应具有 5 年以上经验。", page=40),
    ]
    clusters = clusterer.cluster([[r] for r in reqs])
    # 三条设备需求跨类型同簇；项目经理单独成簇
    device_cluster = [c for c in clusters
                      if any("设备" in r.title for r in c)]
    assert len(device_cluster) == 1
    assert len(device_cluster[0]) == 3
    assert len(clusters) == 2


def test_scoring_rules_marked_and_linked():
    """评分细则：is_scoring=True，parent_requirement_id 挂靠实体需求。"""
    reqs = [
        m3_req(rid="REQ-0100", type_="技术要求",
               title="平台设备接入能力",
               text="平台应支持不少于 1000 台设备接入。",
               quantitative=[{"metric": "设备接入", "op": "不少于",
                              "value": "1000", "unit": "台"}]),
        m3_req(rid="REQ-0101", type_="评分标准",
               title="平台设备接入能力评分",
               text="平台设备接入能力评分：满足 1000 台得满分 10 分，不满足不得分。",
               importance="低"),
    ]
    canonicals, stats = RequirementNormalizer().normalize("T-M3", reqs)
    scoring = [c for c in canonicals if c.is_scoring]
    entity = [c for c in canonicals if not c.is_scoring]
    assert len(scoring) == 1 and len(entity) == 1
    assert scoring[0].parent_requirement_id == entity[0].id
    assert stats["scoring_linked"] == 1
    assert _is_scoring_member(reqs[1]) is True
    assert _is_scoring_member(reqs[0]) is False


def test_llm_merge_path_scripted():
    """LLM 归并路径：脚本化真实模型名客户端 → llm 扩写标题/正文。"""
    class FakeRealLLM:
        model = "deepseek-chat"

        def chat_json(self, system, user, temperature=None, max_tokens=None):
            return {"data": {"results": [
                {"index": 0, "title": "设备接入能力（不少于1000台）",
                 "text": "平台应支持不少于 1000 台设备的接入管理，满足园区统一管理需求。",
                 "should_merge": True}]},
                "finish_reason": "stop", "usage": {}}

    reqs = _device_reqs()
    norm = RequirementNormalizer(client=FakeRealLLM())
    canonicals, stats = norm.normalize("T-M3", reqs)
    assert stats["llm_merged"] == 1 and stats["llm_calls"] >= 1
    c = canonicals[0]
    assert c.merge_method == "llm"
    assert "1000" in c.title and "1000" in c.text
    assert c.source_requirement_ids == ["REQ-0001", "REQ-0127", "REQ-0278"]


def test_no_llm_deterministic_fallback():
    """无 LLM：取代表成员（★/高/长文本）标题 —— 数字绝不改写。"""
    canonicals, stats = RequirementNormalizer().normalize(
        "T-M3", _device_reqs())
    assert stats["llm_calls"] == 0
    assert canonicals[0].merge_method == "similarity"
    assert canonicals[0].title == "设备接入不少于1000台"   # importance 高者


# ═══════════════════════════════════════════════════════════════════════
# M3-02 分类
# ═══════════════════════════════════════════════════════════════════════
_CLASSIFY_CASES = [
    ("投标人须具有ISO9001质量管理体系认证", RequirementTypeM3.QUALIFICATION),
    ("项目经理须具有5年以上项目管理经验", RequirementTypeM3.PERSONNEL),
    ("近三年承担过3个类似项目业绩", RequirementTypeM3.PROJECT_EXPERIENCE),
    ("平台应支持视频监控与门禁管理功能", RequirementTypeM3.PRODUCT_CAPABILITY),
    ("系统架构须支持信创国产化适配", RequirementTypeM3.TECHNICAL),
    ("项目工期不超过12个月", RequirementTypeM3.IMPLEMENTATION),
    ("质保期不少于2年并驻场2人", RequirementTypeM3.SERVICE),
    ("投标保证金金额不超过50万元", RequirementTypeM3.COMMERCIAL),
    ("投标文件正本1份副本4份并胶装", RequirementTypeM3.DOCUMENT),
    ("本章为项目背景介绍", RequirementTypeM3.OTHER),
]


def test_classify_ten_types_keyword_rules():
    classifier = RequirementClassifier()
    for title, expected in _CLASSIFY_CASES:
        got = classifier.classify(title)
        assert got == expected, f"{title!r} → {got}，期望 {expected}"


def test_classify_affinity_fallback():
    """关键词无命中 → M1 类型亲和映射兜底。"""
    classifier = RequirementClassifier()
    assert classifier.classify("本项目建设意义", m1_types=["技术要求"]) \
        == RequirementTypeM3.TECHNICAL
    assert classifier.classify("未知要求") == RequirementTypeM3.OTHER


# ═══════════════════════════════════════════════════════════════════════
# M3-03 约束结构化
# ═══════════════════════════════════════════════════════════════════════
def test_extract_from_quantitative():
    cs = ConstraintExtractor().extract(
        "设备接入不少于1000台",
        quantitative=[{"metric": "设备接入", "op": "不少于",
                       "value": "1000", "unit": "台"}])
    assert len(cs) == 1
    c = cs[0]
    assert (c.attribute, c.operator, c.value, c.unit) == \
        ("device_count", ">=", 1000.0, "count")
    assert c.raw_value == "1000"          # 原文原样


def test_extract_regex_scan_and_operators():
    """正则扫描：操作符归一（不少于→>=、不超过→<=）；语境窗口判定属性。

    数值邻近时 22 字符语境窗口可能错归属性（设计限制），故逐项孤立验证。
    """
    extractor = ConstraintExtractor()
    attrs = set()
    for title in ("质保期不少于2年", "响应时间不超过2小时",
                  "系统年可用性不低于99.95%"):
        for c in extractor.extract(title):
            attrs.add((c.attribute, c.operator, c.value, c.unit))
    assert ("warranty_years", ">=", 2.0, "year") in attrs
    assert ("response_time", "<=", 2.0, "hour") in attrs
    assert ("availability", ">=", 99.95, "percent") in attrs


def test_extract_existence_constraints():
    cs = ConstraintExtractor().extract(
        "投标人须具有ISO9001质量管理体系认证和CMMI3级证书。")
    certs = {(c.subject, c.exists) for c in cs if c.attribute == "certification"}
    assert ("ISO9001", True) in certs
    assert ("CMMI", True) in certs


def test_extract_unit_normalization():
    """万元 → money_wan、个月 → month：规则引擎可比口径。"""
    extractor = ConstraintExtractor()
    attrs = set()
    for title in ("单个合同额不少于500万元", "工期不超过12个月"):
        for c in extractor.extract(title):
            attrs.add((c.attribute, c.unit, c.value))
    assert ("contract_amount", "money_wan", 500.0) in attrs
    assert ("duration_months", "month", 12.0) in attrs


def test_normalizer_inline_classify_and_extract():
    """normalizer 注入 classifier+extractor：规范需求带类型与约束。"""
    reqs = _device_reqs()
    norm = RequirementNormalizer()
    canonicals, _ = norm.normalize(
        "T-M3", reqs, classifier=RequirementClassifier(),
        extractor=ConstraintExtractor())
    c = canonicals[0]
    assert c.req_type in (RequirementTypeM3.PRODUCT_CAPABILITY,
                          RequirementTypeM3.TECHNICAL)
    assert any(x.attribute == "device_count" and x.value == 1000.0
               for x in c.constraints)
