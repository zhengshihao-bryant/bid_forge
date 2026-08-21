# -*- coding: utf-8 -*-
"""
matching/rules/rule_engine.py —— 结构化规则匹配（M3-06）

第一条匹配路径：结构化约束 × 能力卡 → FULL/PARTIAL/MISSING/UNKNOWN。

    要求：经验 >= 5 年；企业：经验 = 6 年 → FULL
    要求：设备 >= 2000 台；企业：设备 = 1250 台 → MISSING（有明确相反证据）
    要求：设备 >= 2000 台；企业无该属性卡片 → UNKNOWN（没有证据 ≠ 不满足）

能力卡取值三类来源（按序探测）：
  1. attributes 直接字段（experience_years="6"、warranty="3年"）
  2. attributes.quantitative 数组（{metric:"设备接入", op:"不少于", value:"2000", unit:"台"}）
  3. 存在性（certification）：卡片名称/描述/cert 字段包含约束主体

单位跨换算表（year↔month、hour↔minute↔second、元↔万元…）；无法换算 → UNKNOWN。
区间值（"1000-2000"）→ 含中值判 PARTIAL、覆盖判 FULL、不达判 MISSING。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from ..models import Constraint, MatchStatus
from ..similarity import normalize_text

# 属性键别名：约束 attribute → 能力卡字段名候选（M2 7 类模板字段）
_ATTR_ALIASES: dict[str, tuple[str, ...]] = {
    "experience_years": ("experience_years", "experience", "years"),
    "device_count": ("device_count", "max_devices", "device_capacity"),
    "concurrent_users": ("concurrent_users", "concurrency", "max_concurrent"),
    "availability": ("availability", "uptime"),
    "accuracy": ("accuracy", "recognition_accuracy", "face_accuracy"),
    # 注意：M2 售后卡的 response_time 字段存的是到场时间（"2小时到场"），
    # 系统操作响应时间约束只能读 operation_response，否则 3 秒响应会被
    # 2 小时到场错误判为 MISSING
    "response_time": ("operation_response",),
    "arrival_time": ("arrival_time", "response_time"),      # M2 售后卡 response_time="2小时到场"
    "repair_time": ("repair_time", "fix_time"),
    "warranty_years": ("warranty_years", "warranty", "warranty_period"),
    "onsite_staff": ("onsite_staff", "onsite_people"),
    "employees": ("employees", "employee_count", "staff"),
    "project_count": ("project_count", "projects_count", "case_count"),
    "contract_amount": ("contract_amount", "scale"),        # 案例卡 scale="合同额1250万元"
    "duration_months": ("duration_months", "duration", "schedule"),
    "founded_years": ("founded_years",),
    "registered_capital": ("registered_capital",),
}

# quantitative[].metric → attribute 的判定词（与 constraint_extractor 同口径）
_METRIC_WORDS: dict[str, tuple[str, ...]] = {
    "device_count": ("设备接入", "接入设备", "设备"),
    "concurrent_users": ("并发",),
    "availability": ("可用性",),
    "accuracy": ("准确率", "识别率"),
    "response_time": ("响应时间", "响应"),
    "arrival_time": ("到场",),
    "repair_time": ("修复",),
    "warranty_years": ("质保", "保修"),
    "onsite_staff": ("驻场",),
    "employees": ("员工",),
    "project_count": ("业绩", "项目"),
    "contract_amount": ("合同额", "金额"),
    "duration_months": ("工期",),
    "experience_years": ("经验", "年限"),
}

# 单位跨换算：(from, to) → 系数（同单位=1；未知组合无法比较）
_UNIT_FACTORS: dict[tuple[str, str], float] = {
    ("month", "year"): 1 / 12, ("year", "month"): 12,
    ("minute", "hour"): 1 / 60, ("hour", "minute"): 60,
    ("second", "minute"): 1 / 60, ("minute", "second"): 60,
    ("second", "hour"): 1 / 3600, ("hour", "second"): 3600,
    ("day", "hour"): 24, ("hour", "day"): 1 / 24,
    ("money_yuan", "money_wan"): 1 / 10000, ("money_wan", "money_yuan"): 10000,
}

# 能力卡字符串取值模式："6"、"6年"、"2小时到场"、"合同额1250万元"、"1000-2000"、"99.95%"
# % 必须落在 unit 组（否则 "99.95%" 的 a 组吞掉百分号、单位归一失效）
_VALUE_RE = re.compile(r"(?P<a>\d+(?:\.\d+)?)\s*(?:[-~～至]\s*(?P<b>\d+(?:\.\d+)?))?\s*(?P<unit>工作日|个月|万元|小时|分钟|人民币|年|月|台|个|人|名|家|项|套|座|份|%|万|元|秒|天|日)?")

_UNIT_KEYS = {
    "年": "year", "月": "month", "个月": "month", "台": "count", "个": "count",
    "人": "count", "名": "count", "家": "count", "项": "count", "套": "count",
    "座": "count", "份": "count", "%": "percent", "万元": "money_wan", "万": "money_wan",
    "元": "money_yuan", "人民币": "money_yuan", "小时": "hour", "分钟": "minute",
    "秒": "second", "天": "day", "工作日": "day", "日": "day",
}

# 能力卡 quantitative.op → 规则符号
_CARD_OP = {"不少于": ">=", "不低于": ">=", "不小于": ">=", "不超过": "<=",
            "不高于": "<=", "不大于": "<=", "大于": ">", "超过": ">",
            "小于": "<", "低于": "<", "≤": "<=", "≥": ">=", ">": ">", "<": "<"}


@dataclass
class RuleOutcome:
    """单约束 × 单卡 的规则判定。"""
    status: MatchStatus
    card_id: str = ""
    matched_value: str = ""          # 卡内命中的原文值（展示用）
    note: str = ""


@dataclass
class RuleResult:
    """约束 × 多卡 / 需求级聚合结果。"""
    status: MatchStatus = MatchStatus.UNKNOWN
    outcomes: list[RuleOutcome] = field(default_factory=list)
    best_card_id: str = ""
    matched_value: str = ""
    note: str = ""

    def summary(self) -> str:
        parts = [o.note for o in self.outcomes if o.note]
        return "; ".join(parts[:3]) or self.note


def _unit_of(raw: str) -> Optional[str]:
    for key, unit in _UNIT_KEYS.items():
        if raw.startswith(key):
            return unit
    return None


def _convert(value: float, unit_from: str, unit_to: str) -> Optional[float]:
    if unit_from == unit_to:
        return value
    factor = _UNIT_FACTORS.get((unit_from, unit_to))
    return None if factor is None else value * factor


def _compare(actual: float, op: str, required: float) -> MatchStatus:
    """数值比较：满足约束 → FULL；明确违背 → MISSING。"""
    table = {
        ">=": lambda a, b: a >= b,
        ">": lambda a, b: a > b,
        "<=": lambda a, b: a <= b,
        "<": lambda a, b: a < b,
        "=": lambda a, b: abs(a - b) < 1e-9,
    }
    fn = table.get(op)
    if fn is None:
        return MatchStatus.UNKNOWN
    return MatchStatus.FULL if fn(actual, required) else MatchStatus.MISSING


class RuleEngine:
    """规则引擎（M3-06）：Constraint × Capability 卡片属性 → 判定。"""

    # ------------------------------------------------------------------
    def evaluate_constraint(self, constraint: Constraint, card) -> Optional[RuleOutcome]:
        """单约束 × 单卡。卡内无该属性 → None（无证据，由聚合层给 UNKNOWN）。"""
        attrs = getattr(card, "attributes", None) or {}
        name = getattr(card, "name", "") or ""
        desc = getattr(card, "description", "") or ""

        # ── 存在性约束（资质证书）──
        if constraint.exists or constraint.attribute == "certification":
            haystack = normalize_text(
                f"{name} {desc} {attrs.get('cert_name', '')} "
                f"{' '.join(map(str, attrs.get('certs', [])))} "
                f"{' '.join(map(str, attrs.get('certifications', [])))}")
            subject = normalize_text(constraint.subject)
            if subject and subject in haystack:
                return RuleOutcome(MatchStatus.FULL, card.id,
                                   matched_value=constraint.subject,
                                   note=f"{constraint.subject} 已在卡片中命中")
            return None

        # ── 数值约束：1. 直接字段 / 2. quantitative 数组 ──
        raw_value: Optional[str] = None
        for key in _ATTR_ALIASES.get(constraint.attribute, (constraint.attribute,)):
            if key in attrs and attrs[key] not in (None, ""):
                raw_value = str(attrs[key])
                break
        if raw_value is None:
            for q in attrs.get("quantitative", []) or []:
                if not isinstance(q, dict):
                    continue
                metric = str(q.get("metric") or "")
                if any(w in metric for w in _METRIC_WORDS.get(constraint.attribute, ())):
                    raw_value = (f"{q.get('op') or ''} {q.get('value') or ''} "
                                 f"{q.get('unit') or ''}").strip()
                    break
        if raw_value is None:
            return None

        m = _VALUE_RE.search(raw_value)
        if m is None:
            return None
        try:
            lo = float(m.group("a").replace("%", ""))
            hi = float(m.group("b").replace("%", "")) if m.group("b") else None
        except ValueError:
            return None
        unit_raw = m.group("unit") or ""
        # 无显式单位时按约束期望单位解释：属性字段本身隐含单位
        # （experience_years="6" 即 6 年；availability="99.95" 即 99.95%），
        # 否则 "count" 无法向 year/percent 换算，恒 UNKNOWN
        unit = _unit_of(unit_raw) if unit_raw else constraint.unit
        lo = _convert(lo, unit, constraint.unit)
        if lo is None:
            return RuleOutcome(MatchStatus.UNKNOWN, card.id, matched_value=raw_value,
                               note=f"单位不可比（卡:{unit} 要求:{constraint.unit}）")
        if hi is not None:
            hi = _convert(hi, unit, constraint.unit)
            return self._compare_range(constraint, lo, hi, card, raw_value)

        # 卡片 op 参与比较（如卡声明"不少于2000台"而要求">=1500"）
        card_op = _CARD_OP.get(raw_value[:4].strip(), "")
        if card_op:
            if card_op in (">=", ">") and constraint.operator in (">=", ">"):
                ok = lo >= constraint.value if card_op == ">=" else lo > constraint.value
                # 卡承诺的下限已覆盖要求：按卡下限与要求的数值比较
                return RuleOutcome(
                    MatchStatus.FULL if ok else MatchStatus.MISSING, card.id,
                    matched_value=raw_value,
                    note=f"{raw_value} 覆盖要求 {constraint.raw_value}{constraint.unit}"
                         if ok else f"{raw_value} 低于要求 {constraint.raw_value}{constraint.unit}")
            if card_op in ("<=", "<") and constraint.operator in ("<=", "<"):
                ok = lo <= constraint.value if card_op == "<=" else lo < constraint.value
                return RuleOutcome(
                    MatchStatus.FULL if ok else MatchStatus.MISSING, card.id,
                    matched_value=raw_value,
                    note=f"{raw_value} 满足上限要求" if ok else
                         f"{raw_value} 超出要求 {constraint.raw_value}{constraint.unit}")

        # 点值比较
        status = _compare(lo, constraint.operator, constraint.value)
        return RuleOutcome(
            status, card.id, matched_value=raw_value,
            note=(f"满足：{raw_value} {constraint.operator} "
                  f"{constraint.raw_value}{constraint.unit}" if status == MatchStatus.FULL
                  else f"不满足：{raw_value} 未达 {constraint.raw_value}{constraint.unit}"))

    def _compare_range(self, constraint: Constraint, lo: float, hi: float,
                       card, raw_value: str) -> RuleOutcome:
        """区间值：全覆盖 FULL / 含要求值 PARTIAL / 上限仍不足 MISSING。"""
        req = constraint.value
        if constraint.operator in (">=", ">"):
            if lo >= req:
                return RuleOutcome(MatchStatus.FULL, card.id, matched_value=raw_value,
                                   note=f"区间下限 {lo} 已覆盖要求 {req}")
            if hi >= req:
                return RuleOutcome(MatchStatus.PARTIAL, card.id, matched_value=raw_value,
                                   note=f"区间 {lo}-{hi} 含要求值 {req}，需进一步确认")
            return RuleOutcome(MatchStatus.MISSING, card.id, matched_value=raw_value,
                               note=f"区间上限 {hi} 未达要求 {req}")
        if constraint.operator in ("<=", "<"):
            if hi <= req:
                return RuleOutcome(MatchStatus.FULL, card.id, matched_value=raw_value,
                                   note=f"区间上限 {hi} 满足要求 {req}")
            if lo <= req:
                return RuleOutcome(MatchStatus.PARTIAL, card.id, matched_value=raw_value,
                                   note=f"区间 {lo}-{hi} 含要求值 {req}，需进一步确认")
            return RuleOutcome(MatchStatus.MISSING, card.id, matched_value=raw_value,
                               note=f"区间下限 {lo} 超出要求 {req}")
        return RuleOutcome(MatchStatus.UNKNOWN, card.id, matched_value=raw_value,
                           note="区间比较暂不支持该操作符")

    # ------------------------------------------------------------------
    def evaluate_constraint_many(self, constraint: Constraint, cards: list) -> RuleResult:
        """约束 × 多卡聚合：FULL 优先；无 FULL 时取最接近的判定。"""
        result = RuleResult(status=MatchStatus.UNKNOWN)
        for card in cards:
            outcome = self.evaluate_constraint(constraint, card)
            if outcome is None:
                continue
            result.outcomes.append(outcome)
        if not result.outcomes:
            result.note = f"无能力卡提供属性 {constraint.attribute or constraint.subject}"
            return result
        priority = {MatchStatus.FULL: 0, MatchStatus.PARTIAL: 1,
                    MatchStatus.MISSING: 2, MatchStatus.UNKNOWN: 3}
        best = sorted(result.outcomes, key=lambda o: priority[o.status])[0]
        result.status = best.status
        result.best_card_id = best.card_id
        result.matched_value = best.matched_value
        result.note = best.note
        return result

    # ------------------------------------------------------------------
    def evaluate_requirement(self, constraints: list[Constraint], cards: list) -> RuleResult:
        """需求级聚合（多约束）：全部 FULL → FULL；任一 MISSING → MISSING；
        任一 UNKNOWN → UNKNOWN；其余（FULL+PARTIAL）→ PARTIAL。"""
        per: list[RuleResult] = [self.evaluate_constraint_many(c, cards) for c in constraints]
        if not per:
            return RuleResult(status=MatchStatus.UNKNOWN, note="无可评估约束")
        agg = RuleResult(outcomes=[o for r in per for o in r.outcomes],
                         best_card_id=per[0].best_card_id,
                         matched_value=per[0].matched_value)
        statuses = [r.status for r in per]
        if all(s == MatchStatus.FULL for s in statuses):
            agg.status = MatchStatus.FULL
        elif any(s == MatchStatus.MISSING for s in statuses):
            agg.status = MatchStatus.MISSING
        elif any(s == MatchStatus.UNKNOWN for s in statuses):
            agg.status = MatchStatus.UNKNOWN
        else:
            agg.status = MatchStatus.PARTIAL
        agg.note = "; ".join(r.summary() for r in per if r.summary())[:400]
        return agg


__all__ = ["RuleEngine", "RuleOutcome", "RuleResult", "_ATTR_ALIASES",
           "_UNIT_FACTORS"]
