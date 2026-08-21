# -*- coding: utf-8 -*-
"""
matching/classify/requirement_classifier.py —— 需求类型分类（M3-02）

不是所有需求都走 RAG —— 类型决定匹配方式（M3-09）：

    QUALIFICATION / PERSONNEL / PROJECT_EXPERIENCE / PRODUCT_CAPABILITY
        → 结构化强，规则引擎 + 能力卡优先
    TECHNICAL / IMPLEMENTATION / SERVICE
        → 部分结构化，规则 + RAG
    COMMERCIAL / DOCUMENT / OTHER
        → 无能力卡对应，RAG / 不参与匹配

实现：关键词规则优先（确定性、可解释），M1 type 亲和映射兜底；
LLM 分类仅作为可选兜底（默认不启用 —— 离线测试可断言）。
"""
from __future__ import annotations

from typing import Optional

from ..models import RequirementTypeM3

# 关键词规则表：类型 → 命中词（按 title + text 计数打分）
_KEYWORD_RULES: dict[RequirementTypeM3, tuple[str, ...]] = {
    RequirementTypeM3.QUALIFICATION: (
        "资质", "认证", "证书", "ISO", "CMMI", "等保", "等级保护", "高新技术",
        "软件企业", "信用", "许可证", "备案"),
    RequirementTypeM3.PERSONNEL: (
        "项目经理", "项目负责人", "技术负责人", "工程师", "人员", "职称",
        "经验", "PMP", "建造师", "项目经理证书", "从业"),
    RequirementTypeM3.PROJECT_EXPERIENCE: (
        "业绩", "案例", "项目经验", "合同额", "类似项目", "交付", "承担过",
        "完成过", "实施经验"),
    RequirementTypeM3.PRODUCT_CAPABILITY: (
        "平台", "系统", "产品", "设备接入", "功能", "模块", "人脸识别",
        "门禁", "停车", "能耗", "一卡通", "视频监控", "软件", "APP", "小程序"),
    RequirementTypeM3.TECHNICAL: (
        "架构", "性能", "指标", "接口", "协议", "安全", "可用性", "响应时间",
        "并发", "数据", "信创", "国产化", "标准", "扩展", "冗余", "备份"),
    RequirementTypeM3.IMPLEMENTATION: (
        "工期", "实施", "部署", "培训", "验收", "调试", "进度", "施工",
        "安装", "试运行", "上线"),
    RequirementTypeM3.SERVICE: (
        "售后", "响应", "质保", "保修", "维护", "服务", "驻场", "热线",
        "巡检", "运维", "到场"),
    RequirementTypeM3.COMMERCIAL: (
        "报价", "价格", "付款", "保证金", "保函", "履约", "发票", "总价",
        "税率", "结算", "成本", "员工", "注册资本", "注册资本金"),
    RequirementTypeM3.DOCUMENT: (
        "格式", "装订", "盖章", "签署", "份数", "正本", "副本", "密封",
        "目录", "签字", "电子版", "胶装"),
}

# M1 RequirementType → M3 类型亲和映射（关键词均不命中时的兜底）
_TYPE_AFFINITY: dict[str, RequirementTypeM3] = {
    "人员要求": RequirementTypeM3.PERSONNEL,
    "资质要求": RequirementTypeM3.QUALIFICATION,
    "技术要求": RequirementTypeM3.TECHNICAL,
    "功能要求": RequirementTypeM3.PRODUCT_CAPABILITY,
    "实施要求": RequirementTypeM3.IMPLEMENTATION,
    "售后服务": RequirementTypeM3.SERVICE,
    "商务要求": RequirementTypeM3.COMMERCIAL,
    "报价要求": RequirementTypeM3.COMMERCIAL,
    "投标文件格式": RequirementTypeM3.DOCUMENT,
    # 项目背景/建设目标/评分标准 → 无匹配对象
}


class RequirementClassifier:
    """规则优先的需求类型分类器（M3-02）。"""

    def __init__(self, rules: Optional[dict[RequirementTypeM3, tuple[str, ...]]] = None):
        self.rules = rules or _KEYWORD_RULES

    # ------------------------------------------------------------------
    def classify(self, title: str, text: str = "",
                 m1_types: Optional[list[str]] = None) -> RequirementTypeM3:
        """title/text → M3 类型。m1_types 为簇内成员的原 M1 类型（亲和兜底）。"""
        haystack = f"{title} {text}"
        scores: dict[RequirementTypeM3, int] = {}
        for rtype, keywords in self.rules.items():
            scores[rtype] = sum(haystack.count(kw) for kw in keywords if kw in haystack)

        best_type, best_score = RequirementTypeM3.OTHER, 0
        for rtype, score in scores.items():
            if score > best_score:
                best_type, best_score = rtype, score
        if best_score > 0:
            return best_type

        # 关键词无命中 → M1 类型亲和映射（多数票）
        if m1_types:
            votes: dict[RequirementTypeM3, int] = {}
            for t in m1_types:
                aff = _TYPE_AFFINITY.get(t)
                if aff is not None:
                    votes[aff] = votes.get(aff, 0) + 1
            if votes:
                return max(votes, key=lambda k: votes[k])
        return RequirementTypeM3.OTHER


__all__ = ["RequirementClassifier", "_KEYWORD_RULES", "_TYPE_AFFINITY"]
