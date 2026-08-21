# -*- coding: utf-8 -*-
"""
tests/test_m5_registry.py —— M5-02/10 事实注册表构建（批次 1）

覆盖（T-M3 基线）：
- 人员：张伟 6年 + PMP → person/certificate，require_all 防串线
- 资质：ISO9001/ISO27001/CMMI3/等保三级 → certificate
- 指标：设备接入 2000 与区间 1500-2500 同组；可用性点值/区间同组
- 商务/售后/公司：合同额 500 万 / 质保 3年+2年 / 到场 2 小时 / 注册资本 5000 万
- 口径：历史标书（工期10个月）不进入注册表；单位跨换算（月→年）
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.services.quality.registry import FactRegistryBuilder  # noqa: E402


def _registry(seed_m5):
    return FactRegistryBuilder(seed_m5["db"]).build(seed_m5["tender_id"])


# ═══════════════════════════════════════════════════════════════════════
# 人员
# ═══════════════════════════════════════════════════════════════════════
def test_person_zhangwei_experience(seed_m5):
    reg = _registry(seed_m5)
    person = reg.metric("项目经理经验")
    assert len(person) == 1, person
    e = person[0]
    assert e.kind == "person" and e.name == "张伟"
    assert e.value == 6.0 and e.unit == "year"
    # 锚点须同时含姓名+经验关键词，防"质保3年 vs 张伟3年"串线
    assert e.anchor_keywords == ["张伟", "经验"] and e.require_all is True
    assert e.source_ref == "CAP-0003"


def test_person_pmp_cert(seed_m5):
    """人员证书入 certificate 注册表（PMP 属于张伟）。"""
    reg = _registry(seed_m5)
    pmp = [e for e in reg.certs() if e.name == "PMP"]
    assert len(pmp) == 1
    assert pmp[0].extra.get("person") == "张伟"
    assert pmp[0].source_ref == "CAP-0003"


# ═══════════════════════════════════════════════════════════════════════
# 资质
# ═══════════════════════════════════════════════════════════════════════
def test_company_certs(seed_m5):
    reg = _registry(seed_m5)
    names = {e.name for e in reg.certs() if e.metric == "公司证书"}
    assert {"ISO9001", "ISO27001", "CMMI3", "等保三级"} <= names
    iso = next(e for e in reg.certs() if e.name == "ISO9001")
    assert iso.source_ref == "CAP-0002"


# ═══════════════════════════════════════════════════════════════════════
# 指标（同指标多卡并组）
# ═══════════════════════════════════════════════════════════════════════
def test_device_capacity_group(seed_m5):
    """设备接入：CAP-0001 点值 2000 与 CAP-0007 区间 1500-2500 同组。"""
    reg = _registry(seed_m5)
    devs = reg.metric("设备接入")
    assert len(devs) == 2, devs
    by_ref = {e.source_ref: e for e in devs}
    e2000 = by_ref["CAP-0001"]
    assert e2000.value == 2000.0 and e2000.value_hi is None
    assert e2000.unit == "count"
    e1500 = by_ref["CAP-0007"]
    assert e1500.value == 1500.0 and e1500.value_hi == 2500.0
    # 关键词含英文属性键原名（正文策略兜底形式 max_devices=2000）
    assert "max_devices" in e2000.anchor_keywords


def test_availability_group(seed_m5):
    """可用性：点值 99.95% 与区间 99.9-99.99% 同组。"""
    reg = _registry(seed_m5)
    av = reg.metric("系统可用性")
    assert len(av) == 2, av
    by_ref = {e.source_ref: e for e in av}
    assert by_ref["CAP-0001"].value == 99.95
    r = by_ref["CAP-0009"]
    assert r.value == 99.9 and r.value_hi == 99.99 and r.unit == "percent"


def test_warranty_group_and_concurrency(seed_m5):
    reg = _registry(seed_m5)
    war = reg.metric("质保期")
    assert len(war) == 2, war
    assert {e.value for e in war} == {2.0, 3.0}
    conc = reg.metric("并发用户")
    assert len(conc) == 1 and conc[0].value == 1000.0


# ═══════════════════════════════════════════════════════════════════════
# 商务 / 售后 / 公司
# ═══════════════════════════════════════════════════════════════════════
def test_project_and_business(seed_m5):
    reg = _registry(seed_m5)
    amt = reg.metric("单个合同额")
    assert len(amt) == 1
    assert amt[0].kind == "project" and amt[0].value == 500.0
    assert amt[0].unit == "money_wan"
    cnt = reg.metric("项目业绩数量")
    assert cnt[0].value == 3.0 and cnt[0].unit == "count"


def test_arrival_and_warranty_dispatch(seed_m5):
    """M2 售后卡 response_time='2小时到场' → 故障到场时间（非操作响应）。"""
    reg = _registry(seed_m5)
    arr = reg.metric("故障到场时间")
    assert len(arr) == 1
    assert arr[0].value == 2.0 and arr[0].unit == "hour"
    # 操作响应时间无卡覆盖 → 无注册表条目（不误报）
    assert reg.metric("操作响应时间") == []


def test_company_metrics(seed_m5):
    reg = _registry(seed_m5)
    cap = reg.metric("注册资本")
    assert len(cap) == 1 and cap[0].value == 5000.0 and cap[0].unit == "money_wan"
    founded = reg.metric("成立年限")
    assert founded[0].value == 16.0 and founded[0].unit == "year"
    emp = reg.metric("员工人数")
    assert len(emp) == 1
    assert emp[0].value == 300.0 and emp[0].value_hi == 600.0


# ═══════════════════════════════════════════════════════════════════════
# 口径边界
# ═══════════════════════════════════════════════════════════════════════
def test_historical_bid_excluded(seed_m5):
    """历史标书证据（工期10个月）不进入注册表。"""
    reg = _registry(seed_m5)
    assert reg.metric("项目工期") == []
    # 旧版产品 1250 台是 chunk 证据且设备接入已被卡覆盖 → 不兜底
    devs = reg.metric("设备接入")
    assert all(e.value != 1250.0 for e in devs)


def test_registry_no_spurious_fallback(seed_m5):
    """卡已覆盖的指标不产生证据兜底重复条目。"""
    reg = _registry(seed_m5)
    # 员工人数仅来自 CAP-0010（300-600），证据"员工500人"不额外注册
    assert len(reg.metric("员工人数")) == 1
    # 注册资本仅来自 CAP-0006，证据"注册资本5000万元"不重复
    assert len(reg.metric("注册资本")) == 1
