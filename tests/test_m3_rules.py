# -*- coding: utf-8 -*-
"""
tests/test_m3_rules.py —— M3-06 规则引擎（离线，确定性）

覆盖（用户 M3-16 要求 5+ 数值比较）：
- 经验 ≥5年 × 6年 → FULL；× 4年 → MISSING（有明确相反证据）
- 设备 ≥1000台 × 2000台 → FULL；× 1250台 → FULL；× 800台 → MISSING
- 质保 ≥2年 × 3年 FULL；× 1年 MISSING
- 单位跨换算：60个月 ↔ 5年；30分钟 vs 2小时；元 ↔ 万元
- % 指标：可用性 ≥99.9% × 99.95% → FULL
- 区间值：2000-3000 覆盖 / 1000-2000 部分 / 1000-1500 不足
- 存在性约束：ISO9001 命中 → FULL；无该资质 → UNKNOWN（不是 MISSING）
- 核心口径：无卡片/无属性 → UNKNOWN ≠ MISSING
- 需求级聚合：多约束 all FULL → FULL；任一 MISSING → MISSING；
  任一 UNKNOWN → UNKNOWN；FULL+PARTIAL → PARTIAL
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.services.matching.models import Constraint, MatchStatus  # noqa: E402
from app.services.matching.rules import RuleEngine  # noqa: E402


def _card(cap_id="CAP-0001", name="卡", attrs=None, desc=""):
    class _C:
        def __init__(self):
            self.id, self.name = cap_id, name
            self.attributes = attrs or {}
            self.description = desc
    return _C()


def _constraint(attribute, operator, value, unit, subject=""):
    return Constraint(subject=subject, attribute=attribute, metric=attribute,
                      operator=operator, value=float(value), unit=unit,
                      raw_value=str(value))


def _status(engine, constraint, card):
    return engine.evaluate_constraint(constraint, card)


# ═══════════════════════════════════════════════════════════════════════
# 数值比较（5+ 组）
# ═══════════════════════════════════════════════════════════════════════
def test_experience_5y_vs_6y_full():
    """要求 ≥5 年，企业 6 年 → FULL。"""
    e = RuleEngine()
    out = _status(e, _constraint("experience_years", ">=", 5, "year"),
                  _card(attrs={"experience_years": "6"}))
    assert out.status == MatchStatus.FULL and out.matched_value == "6"


def test_experience_5y_vs_4y_missing():
    """要求 ≥5 年，企业 4 年 → MISSING（资料明确显示不满足）。"""
    out = _status(RuleEngine(), _constraint("experience_years", ">=", 5, "year"),
                  _card(attrs={"experience_years": "4"}))
    assert out.status == MatchStatus.MISSING


def test_device_1000_vs_2000_full():
    out = _status(RuleEngine(), _constraint("device_count", ">=", 1000, "count"),
                  _card(attrs={"max_devices": "2000"}))   # 别名 max_devices
    assert out.status == MatchStatus.FULL


def test_device_1000_vs_1250_full_and_800_missing():
    e = RuleEngine()
    c = _constraint("device_count", ">=", 1000, "count")
    assert _status(e, c, _card(attrs={"max_devices": "1250"})).status == MatchStatus.FULL
    assert _status(e, c, _card(attrs={"max_devices": "800"})).status == MatchStatus.MISSING


def test_warranty_2y_vs_3y_full_1y_missing():
    e = RuleEngine()
    c = _constraint("warranty_years", ">=", 2, "year")
    assert _status(e, c, _card(attrs={"warranty": "3年"})).status == MatchStatus.FULL
    assert _status(e, c, _card(attrs={"warranty": "1年"})).status == MatchStatus.MISSING


# ═══════════════════════════════════════════════════════════════════════
# 单位跨换算
# ═══════════════════════════════════════════════════════════════════════
def test_unit_conversion_months_to_years():
    """60 个月 = 5 年 → FULL；36 个月 = 3 年 < 5 → MISSING。"""
    e = RuleEngine()
    c = _constraint("experience_years", ">=", 5, "year")
    assert _status(e, c, _card(attrs={"experience_years": "60个月"})).status == MatchStatus.FULL
    assert _status(e, c, _card(attrs={"experience_years": "36个月"})).status == MatchStatus.MISSING


def test_unit_conversion_minutes_to_hours():
    """到场 ≤2 小时：30 分钟 → FULL；150 分钟 → MISSING。"""
    e = RuleEngine()
    c = _constraint("arrival_time", "<=", 2, "hour")
    assert _status(e, c, _card(attrs={"response_time": "30分钟"})).status == MatchStatus.FULL
    assert _status(e, c, _card(attrs={"response_time": "150分钟"})).status == MatchStatus.MISSING


def test_unit_conversion_yuan_to_wan():
    """要求 ≥500 万元；卡 6000000 元 = 600 万 → FULL；300 万 → MISSING。"""
    e = RuleEngine()
    c = _constraint("contract_amount", ">=", 500, "money_wan")
    assert _status(e, c, _card(attrs={"scale": "合同额6000000元"})).status == MatchStatus.FULL
    assert _status(e, c, _card(attrs={"scale": "合同额300万元"})).status == MatchStatus.MISSING


def test_percent_availability():
    """99.95% vs ≥99.9% → FULL（% 必须落在单位组，否则数值解析出错）。"""
    out = _status(RuleEngine(), _constraint("availability", ">=", 99.9, "percent"),
                  _card(attrs={"availability": "99.95%"}))
    assert out.status == MatchStatus.FULL


# ═══════════════════════════════════════════════════════════════════════
# 区间值
# ═══════════════════════════════════════════════════════════════════════
def test_range_values():
    e = RuleEngine()
    c = _constraint("device_count", ">=", 2000, "count")
    assert _status(e, c, _card(attrs={"max_devices": "2000-3000"})).status == MatchStatus.FULL
    assert _status(e, c, _card(attrs={"max_devices": "1000-2000"})).status == MatchStatus.PARTIAL
    assert _status(e, c, _card(attrs={"max_devices": "1000-1500"})).status == MatchStatus.MISSING


# ═══════════════════════════════════════════════════════════════════════
# 存在性约束 + 核心口径（没有证据 ≠ 不满足）
# ═══════════════════════════════════════════════════════════════════════
def test_certification_exists_full():
    c = Constraint(subject="ISO9001", attribute="certification",
                   operator="exists", exists=True)
    out = _status(RuleEngine(), c,
                  _card(name="质量管理体系认证证书",
                        attrs={"certs": ["ISO9001", "ISO27001"],
                               "cert_name": "ISO9001"}))
    assert out.status == MatchStatus.FULL


def test_certification_absent_is_unknown_not_missing():
    """卡内无 ISO9001 → UNKNOWN（没有证据 ≠ 不满足）。"""
    c = Constraint(subject="ISO9001", attribute="certification",
                   operator="exists", exists=True)
    r = RuleEngine().evaluate_constraint_many(
        c, [_card(attrs={"certs": ["ISO27001"]})])
    assert r.status == MatchStatus.UNKNOWN
    assert r.status != MatchStatus.MISSING


def test_no_card_no_attribute_is_unknown():
    """无卡片/无属性 → UNKNOWN，绝不 MISSING。"""
    e = RuleEngine()
    c = _constraint("device_count", ">=", 2000, "count")
    assert e.evaluate_constraint(c, _card(attrs={})) is None
    r = e.evaluate_constraint_many(c, [_card(attrs={"other": "x"})])
    assert r.status == MatchStatus.UNKNOWN


# ═══════════════════════════════════════════════════════════════════════
# 需求级聚合
# ═══════════════════════════════════════════════════════════════════════
def test_requirement_aggregation():
    e = RuleEngine()
    cards = [_card(cap_id="CAP-0001", name="产品卡",
                   attrs={"max_devices": "2000"}),
             _card(cap_id="CAP-0002", name="质保卡",
                   attrs={"warranty": "1年"})]
    # 全满足 → FULL
    r = e.evaluate_requirement(
        [_constraint("device_count", ">=", 1000, "count"),
         _constraint("warranty_years", ">=", 1, "year")], cards)
    assert r.status == MatchStatus.FULL
    # 任一 MISSING → MISSING（质保 1 年 < 2 年）
    r = e.evaluate_requirement(
        [_constraint("device_count", ">=", 1000, "count"),
         _constraint("warranty_years", ">=", 2, "year")], cards)
    assert r.status == MatchStatus.MISSING
    # 任一 UNKNOWN → UNKNOWN（无该属性卡片）
    r = e.evaluate_requirement(
        [_constraint("device_count", ">=", 1000, "count"),
         _constraint("onsite_staff", ">=", 2, "count")], cards)
    assert r.status == MatchStatus.UNKNOWN
    # FULL + PARTIAL → PARTIAL（区间卡 1-3 年含要求值 2 → PARTIAL）
    cards2 = cards + [_card(cap_id="CAP-0003", name="质保区间卡",
                            attrs={"warranty": "1-3年"})]
    r = e.evaluate_requirement(
        [_constraint("device_count", ">=", 1000, "count"),
         _constraint("warranty_years", ">=", 2, "year")], cards2)
    assert r.status == MatchStatus.PARTIAL
