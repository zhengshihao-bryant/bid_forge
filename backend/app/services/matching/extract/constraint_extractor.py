# -*- coding: utf-8 -*-
"""
matching/extract/constraint_extractor.py —— 需求结构化（M3-03）

自然语言需求 → 规则引擎可处理的 Constraint：

    "项目经理应具有5年以上相关项目经验" →
        {subject: 项目经理, attribute: experience_years, operator: ">=",
         value: 5, unit: "year"}
    "投标人应具有1000台以上设备接入经验" →
        {subject: 设备接入, attribute: device_count, operator: ">=",
         value: 1000, unit: "台"}
    "投标人须具有ISO9001质量管理体系认证" →
        {subject: ISO9001, attribute: certification, operator: "exists",
         exists: True}

提取源（确定性，零 LLM —— 数字铁律：绝不改写原文数值）：
  1. M1 quantitative 数组（LLM 提取时已原样保留的量化项，metric 映射属性）
  2. 正则扫描 title + text：操作符词 × 数字 × 单位，上下文窗口判定属性
  3. 资质/证书存在性约束（ISO/CMMI/等保/PMP/建造师…）

单位归一为规则引擎可比口径：year/month/count/percent/money_wan/
money_yuan/hour/minute/second/day —— 原文数值存 raw_value 原样保留。
"""
from __future__ import annotations

import re
from typing import Optional

from ..models import Constraint, RequirementTypeM3

# 操作符归一（原文用词 → 规则引擎符号）
_OP_MAP = {
    ">=": ">=", "≥": ">=", ">": ">", "＞": ">",
    "<=": "<=", "≤": "<=", "<": "<", "＜": "<",
    "不少于": ">=", "不低于": ">=", "不小于": ">=", "至少": ">=", "以上": ">=",
    "不超过": "<=", "不高于": "<=", "不大于": "<=", "以下": "<=", "以内": "<=",
    "大于": ">", "超过": ">", "高于": ">",
    "小于": "<", "低于": "<",
    "等于": "=", "达到": "=", "=": "=",
}

# 单位归一：原文单位 → (规则口径单位, 基准换算系数[统一到主单位])
_UNIT_NORM = {
    "年": ("year", 1.0), "月": ("month", 1.0), "个月": ("month", 1.0),
    "台": ("count", 1.0), "个": ("count", 1.0), "人": ("count", 1.0),
    "名": ("count", 1.0), "家": ("count", 1.0), "项": ("count", 1.0),
    "套": ("count", 1.0), "座": ("count", 1.0), "份": ("count", 1.0),
    "%": ("percent", 1.0),
    "万元": ("money_wan", 1.0), "万": ("money_wan", 1.0),
    "元": ("money_yuan", 1.0), "人民币": ("money_yuan", 1.0),
    "小时": ("hour", 1.0), "分钟": ("minute", 1.0), "秒": ("second", 1.0),
    "天": ("day", 1.0), "工作日": ("day", 1.0), "日": ("day", 1.0),
}

# 属性规则：(attribute, 主体关键词, 语境关键词) —— 正则命中数字后按窗口判定
_ATTRIBUTE_RULES: list[tuple[str, tuple[str, ...], tuple[str, ...]]] = [
    ("experience_years", ("项目经理", "项目负责人", "技术负责人", "工程师", "从业人员", "项目经理经验", "经验"), ("经验", "从业", "工作年限")),
    ("device_count", ("设备接入", "接入设备", "接入管理", "接入"), ("设备", "接入")),
    ("concurrent_users", ("并发", "在线用户", "同时在线"), ()),
    ("availability", ("可用性", "可用率", "在线率"), ()),
    ("accuracy", ("准确率", "识别率", "人脸识别"), ()),
    ("response_time", ("响应时间", "操作响应", "响应"), ()),
    ("arrival_time", ("到场", "到达现场", "到达"), ()),
    ("repair_time", ("修复", "恢复"), ()),
    ("warranty_years", ("质保", "保修"), ()),
    ("onsite_staff", ("驻场", "驻点", "驻场人员", "驻场运维"), ()),
    ("employees", ("员工", "研发人员", "人员规模"), ()),
    ("project_count", ("业绩", "类似项目", "案例", "项目"), ()),
    ("contract_amount", ("合同额", "合同金额", "金额", "总投资", "投资额"), ()),
    ("duration_months", ("工期", "建设期", "实施周期"), ()),
    ("founded_years", ("成立",), ()),
    ("registered_capital", ("注册资本", "注册资金"), ()),
]

# 资质/证书存在性约束（正则 + subject 归一）
_CERT_PATTERNS = [
    (re.compile(r"ISO\s*9\s*0\s*0\s*1"), "ISO9001"),
    (re.compile(r"ISO\s*2\s*7\s*0\s*0\s*1"), "ISO27001"),
    (re.compile(r"ISO\s*1\s*4\s*0\s*0\s*1"), "ISO14001"),
    (re.compile(r"ISO\s*2\s*0\s*0\s*0\s*0"), "ISO20000"),
    (re.compile(r"CMMI\s*[0-9IVX]+(?:级)?"), "CMMI"),
    (re.compile(r"等级保护\s*[一二三]级|等保\s*[一二三]级"), "等保"),
    (re.compile(r"高新技术企业"), "高新技术企业"),
    (re.compile(r"PMP"), "PMP"),
    (re.compile(r"信息系统项目管理师"), "信息系统项目管理师"),
    (re.compile(r"建造师"), "建造师"),
    (re.compile(r"GB/T\s*\d{4,5}"), "GB/T标准"),
    (re.compile(r"软件著作权"), "软件著作权"),
]

# 数值模式：操作符? 数字(可选小数) 单位?
# % 只放 unit 组不放 num 组：否则 "99.95%" 的 % 被 num 吞掉、单位归一为 count，
# 百分号指标无法与能力卡 percent 值比较
_NUM_PATTERN = re.compile(
    r"(?P<op>不少于|不低于|不小于|不超过|不高于|不大于|大于|超过|高于|小于|低于|等于|达到|至少|以上|以下|以内|≥|≤|＞|＜|>|<|=)?"
    r"\s*(?P<num>\d[\d,，]*(?:\.\d+)?)\s*"
    r"(?P<unit>工作日|个月|万元|小时|分钟|人民币|年|月|台|个|人|名|家|项|套|座|份|%|万|元|秒|天|日)?")

_WINDOW = 22   # 数字前后语境窗口（字符）


def _parse_number(raw: str) -> Optional[float]:
    try:
        return float(raw.replace(",", "").replace("，", "").replace("%", ""))
    except ValueError:
        return None


def _normalize_unit(raw_unit: str) -> tuple[str, float]:
    """原文单位 → (规则口径单位, 换算到主单位的系数)。未知单位按 count。"""
    if not raw_unit:
        return "count", 1.0
    for key, (unit, factor) in _UNIT_NORM.items():
        if key == raw_unit or raw_unit.startswith(key):
            return unit, factor
    return "count", 1.0


def _match_attribute(window: str) -> Optional[str]:
    """数字语境窗口 → 属性键（最长主体关键词命中者优先）。"""
    best_attr, best_len = None, 0
    for attr, subjects, contexts in _ATTRIBUTE_RULES:
        for kw in subjects:
            if kw in window:
                if contexts and not any(c in window for c in contexts):
                    continue
                if len(kw) > best_len:
                    best_attr, best_len = attr, len(kw)
    return best_attr


def _match_subject(window: str, attribute: str) -> str:
    """属性 → 约束主体（取窗口中命中的主体关键词）。"""
    for attr, subjects, _ctx in _ATTRIBUTE_RULES:
        if attr != attribute:
            continue
        for kw in subjects:
            if kw in window:
                return kw
    return ""


class ConstraintExtractor:
    """需求结构化器（M3-03，确定性）。"""

    # ------------------------------------------------------------------
    def extract(self, title: str, text: str = "",
                quantitative: Optional[list] = None,
                req_type: RequirementTypeM3 = RequirementTypeM3.OTHER,
                ) -> list[Constraint]:
        """title/text(+M1 quantitative) → Constraint 列表。"""
        constraints: list[Constraint] = []
        seen: set[tuple] = set()

        def _add(c: Constraint) -> None:
            key = (c.attribute, c.operator, c.value, c.unit, c.subject)
            if key not in seen:
                seen.add(key)
                constraints.append(c)

        haystack = f"{title}。{text}"

        # 1. M1 quantitative 数组（LLM 提取已原样保留的量化项；
        #    兼容 Pydantic 模型 —— normalizer 传入的是 Requirement 的模型对象）
        quant_keys: set[tuple] = set()
        for q in quantitative or []:
            if not isinstance(q, dict):
                if hasattr(q, "model_dump"):
                    q = q.model_dump()
                else:
                    continue
            metric = str(q.get("metric") or "")
            op = _OP_MAP.get(str(q.get("op") or "").strip())
            value = _parse_number(str(q.get("value") or ""))
            if value is None or op is None:
                continue
            unit, factor = _normalize_unit(str(q.get("unit") or ""))
            attr = _match_attribute(metric)
            if attr is None:
                continue
            _add(Constraint(
                subject=metric, attribute=attr, metric=metric, operator=op,
                value=value * factor, unit=unit, raw_value=str(q.get("value") or ""),
                source_text=f"{metric}{q.get('op')}{q.get('value')}{q.get('unit')}",
            ))
            quant_keys.add((attr, value * factor, unit))

        # 2. 正则扫描（覆盖 quantitative 未提取到的表述）
        for m in _NUM_PATTERN.finditer(haystack):
            num_raw = m.group("num")
            value = _parse_number(num_raw)
            if value is None:
                continue
            start, end = m.start(), m.end()
            window = haystack[max(0, start - _WINDOW):min(len(haystack), end + _WINDOW)]
            attr = _match_attribute(window)
            if attr is None:
                continue
            op_raw = (m.group("op") or "=").strip()
            op = _OP_MAP.get(op_raw, ">=")
            unit_raw = m.group("unit") or ""
            unit, factor = _normalize_unit(unit_raw)
            value = value * factor
            # 缺操作符的扫描值（默认 "="）若与 quantitative 同属性同值同单位，
            # 视为同一指标的重复捕获——quantitative 由 M1 提取且带操作符，
            # 口径更可信，丢弃默认 "=" 防止出现"6>=5 且 6=5"的矛盾约束对
            if m.group("op") is None and (attr, value, unit) in quant_keys:
                continue
            _add(Constraint(
                subject=_match_subject(window, attr), attribute=attr, metric=attr,
                operator=op, value=value, unit=unit,
                raw_value=num_raw, source_text=window.strip(),
            ))

        # 3. 存在性约束（资质/证书）
        for pattern, subject in _CERT_PATTERNS:
            m = pattern.search(haystack)
            if m:
                _add(Constraint(
                    subject=subject, attribute="certification",
                    operator="exists", exists=True,
                    source_text=haystack[max(0, m.start() - 10):m.end() + 10],
                ))

        return constraints


__all__ = ["ConstraintExtractor", "_OP_MAP", "_UNIT_NORM", "_ATTRIBUTE_RULES",
           "_CERT_PATTERNS"]
