# -*- coding: utf-8 -*-
"""
quality/registry.py —— M5-02/10 事实注册表构建

事实注册表 = 一次检查中"知识库认为为真"的规范化事实集合。它是所有锚定
数字/人员/资质/项目检查的对照基准（M5-10 跨章节冲突的同口径取值源）。

事实源分级（只降级不跳跃）：
  1. capabilities.attributes（结构化字段，主源）——按 category 分派：
     人员资质→person、公司资质→certificate、项目案例→project、
     产品/技术方案/售后服务→metric、公司介绍→company；
  2. evidences（本 tender，排除 category=="历史标书"）——仅当某指标
     无卡覆盖时按关键词兜底（M5-02 溯源仍以卡/证据为准）。

口径边界（防误报）：
- 数值归一镜像 rule_engine._VALUE_RE/_UNIT_KEYS/_UNIT_FACTORS（同库同口径）。
- 同指标多卡并为一组（2000 与 1500-2500 同组，锚定命中任一即过）。
- 历史标书永不进入注册表（与 M4 事实约束同口径：只作行文风格参考）。
- 项目名/人名只做精确归一匹配，不做模糊归并（M5 边界，注释留档）。
"""
from __future__ import annotations

import json
from typing import Optional

from ...db import Database
from ...services.matching.rules.rule_engine import (
    _UNIT_FACTORS, _UNIT_KEYS, _VALUE_RE,)
from .models import FactRegistry, FactRegistryEntry


# 属性键 → (指标名, 锚点关键词, 隐含单位)；镜像 rule_engine._ATTR_ALIASES/_METRIC_WORDS
_ATTR_METRIC: dict[str, tuple[str, tuple[str, ...], str]] = {
    # 产品/技术
    "max_devices": ("设备接入", ("设备接入", "接入设备", "max_devices", "设备"), "count"),
    "device_count": ("设备接入", ("设备接入", "接入设备", "max_devices", "设备"), "count"),
    "device_capacity": ("设备接入", ("设备接入", "接入设备", "max_devices", "设备"), "count"),
    "concurrent_users": ("并发用户", ("并发", "concurrent_users", "用户"), "count"),
    "concurrency": ("并发用户", ("并发", "concurrent_users", "用户"), "count"),
    "availability": ("系统可用性", ("可用性", "availability", "uptime", "系统"), "percent"),
    "uptime": ("系统可用性", ("可用性", "availability", "uptime", "系统"), "percent"),
    "accuracy": ("识别准确率", ("准确率", "识别率", "accuracy"), "percent"),
    # 售后
    "warranty": ("质保期", ("质保", "保修", "warranty"), "year"),
    "warranty_years": ("质保期", ("质保", "保修", "warranty"), "year"),
    "onsite_staff": ("驻场人员", ("驻场", "onsite"), "count"),
    "operation_response": ("操作响应时间", ("响应时间", "响应", "operation"), "second"),
    # 人员
    "experience_years": ("项目经理经验", ("经验", "年限", "experience"), "year"),
    "experience": ("项目经理经验", ("经验", "年限", "experience"), "year"),
    # 商务/案例
    "project_count": ("项目业绩数量", ("业绩", "项目", "project_count"), "count"),
    "scale": ("单个合同额", ("合同额", "金额", "scale"), "money_wan"),
    "contract_amount": ("单个合同额", ("合同额", "金额", "scale"), "money_wan"),
    # 实施
    "duration_months": ("项目工期", ("工期", "duration"), "month"),
    "duration": ("项目工期", ("工期", "duration"), "month"),
    # 公司介绍
    "registered_capital": ("注册资本", ("注册资本", "注册资金", "registered_capital"), "money_wan"),
    "founded_years": ("成立年限", ("成立", "founded"), "year"),
    "employees": ("员工人数", ("员工", "employees", "staff"), "count"),
}

# 特殊分派：M2 售后卡把到场时间存进 response_time 字段（"2小时到场"）
_ARRIVAL_METRIC = ("故障到场时间", ("到场", "arrival", "响应时间"), "hour")


def _metric_meta(attr_key: str, raw_value: str) -> tuple[str, tuple[str, ...], str]:
    """属性键 → 指标元数据；response_time 按取值内容分派到场/操作响应。"""
    if attr_key == "response_time":
        if "到场" in str(raw_value):
            return _ARRIVAL_METRIC
        return _ATTR_METRIC["operation_response"]
    return _ATTR_METRIC.get(attr_key)


def _parse_value(raw: str) -> Optional[tuple[float, Optional[float], str]]:
    """字符串取值 → (lo, hi, unit)。用 rule_engine._VALUE_RE 同口径解析。

    "6"→(6,None,"")、"6年"→(6,None,"year")、"1500-2500"→(1500,2500,"")、
    "99.95%"→(99.95,None,"percent")、"5000万元"→(5000,None,"money_wan")。
    """
    m = _VALUE_RE.search(str(raw).strip())
    if not m:
        return None
    lo = float(m.group("a"))
    hi = float(m.group("b")) if m.group("b") else None
    unit_raw = m.group("unit") or ""
    unit = _UNIT_KEYS.get(unit_raw, "")
    return lo, hi, unit


def _normalize(lo: float, hi: Optional[float], from_unit: str,
               to_unit: str) -> Optional[tuple[float, Optional[float]]]:
    """跨单位换算（年↔月、元↔万元…）。不可换算 → None（该指标放弃锚定）。"""
    if from_unit == to_unit:
        return lo, hi
    f = _UNIT_FACTORS.get((from_unit, to_unit))
    if f is None:
        return None
    return lo * f, (hi * f if hi is not None else None)


def _to_implied(lo: float, hi: Optional[float], from_unit: str,
                to_unit: str) -> Optional[tuple[float, Optional[float]]]:
    """归一化到指标隐含单位：裸数字（"6"/"2000"/"300-600"）直接按隐含单位计；
    带单位走 _UNIT_FACTORS 换算。不可换算 → None。"""
    if not from_unit:
        return lo, hi
    return _normalize(lo, hi, from_unit, to_unit)


def _cap_attrs(row: dict) -> dict:
    """capabilities 行 attributes → dict（容错非 dict JSON）。"""
    try:
        attrs = json.loads(row.get("attributes") or "{}")
    except (TypeError, ValueError):
        attrs = {}
    return attrs if isinstance(attrs, dict) else {}


class FactRegistryBuilder:
    """构建一次检查的 FactRegistry（M5-02/10）。确定性、无 LLM。"""

    def __init__(self, db: Database):
        self.db = db

    def build(self, tender_id: str) -> FactRegistry:
        registry = FactRegistry()
        cards = self._load_capabilities()
        covered_metrics: set[str] = set()

        # 1) capabilities 结构化主源
        for cap in cards:
            attrs = _cap_attrs(cap)
            covered_metrics.update(self._dispatch(registry, cap, attrs))

        # 2) evidences 兜底：仅覆盖卡未覆盖的指标（排除历史标书）
        covered = covered_metrics
        for ev in self._load_evidences(tender_id):
            if ev.get("category") == "历史标书":
                continue
            self._evidence_fallback(registry, ev, covered)
        return registry

    # ── 数据装载 ──────────────────────────────────────────────────────
    def _load_capabilities(self) -> list[dict]:
        return self.db.query("SELECT * FROM capabilities")

    def _load_evidences(self, tender_id: str) -> list[dict]:
        return self.db.query(
            "SELECT * FROM evidences WHERE tender_id = ?", (tender_id,))

    # ── 分派 ──────────────────────────────────────────────────────────
    def _dispatch(self, registry: FactRegistry, cap: dict,
                  attrs: dict) -> set[str]:
        """按 category 分派，返回本次覆盖的指标名集合。"""
        category = cap.get("category") or ""
        source_ref = cap.get("id") or ""
        covered: set[str] = set()

        if category == "人员资质":
            covered |= self._dispatch_person(registry, cap, attrs)
        elif category == "公司资质":
            covered |= self._dispatch_certificate(registry, cap, attrs)
        elif category == "项目案例":
            covered |= self._dispatch_project(registry, cap, attrs)
        elif category in ("产品", "技术方案", "售后服务"):
            covered |= self._dispatch_metric(registry, cap, attrs)
        elif category == "公司介绍":
            covered |= self._dispatch_company(registry, cap, attrs)
        # 历史标书等其它分类不产生事实
        return covered

    def _dispatch_person(self, registry: FactRegistry, cap: dict,
                         attrs: dict) -> set[str]:
        """人员卡 → person（姓名 + 经验年限）与证书条目（姓名并入 extra）。"""
        name = (cap.get("name") or "").split("-")[0].strip()
        if not name:
            return set()
        covered: set[str] = set()
        raw_exp = attrs.get("experience_years") or attrs.get("experience")
        if raw_exp is not None:
            parsed = _parse_value(str(raw_exp))
            if parsed:
                lo, hi, unit = parsed
                norm = _to_implied(lo, hi, unit, "year")
                if norm:
                    covered.add("项目经理经验")
                    registry.entries.append(FactRegistryEntry(
                        metric="项目经理经验", kind="person", name=name,
                        anchor_keywords=[name, "经验"], require_all=True,
                        value=norm[0], value_hi=norm[1], unit="year",
                        source_ref=cap.get("id") or "",
                        source_category=cap.get("category") or "",
                        extra={"role": attrs.get("role", "")}))
        for cert in (attrs.get("certs") or []):
            registry.entries.append(FactRegistryEntry(
                metric="人员证书", kind="certificate", name=str(cert),
                anchor_keywords=[str(cert)], require_all=False,
                source_ref=cap.get("id") or "",
                source_category=cap.get("category") or "",
                extra={"person": name}))
        return covered

    def _dispatch_certificate(self, registry: FactRegistry, cap: dict,
                              attrs: dict) -> set[str]:
        """公司资质卡 → certificate（certs[] 展开 + cert_no/valid_until 入 extra）。"""
        for cert in (attrs.get("certs") or []):
            cert = str(cert)
            registry.entries.append(FactRegistryEntry(
                metric="公司证书", kind="certificate", name=cert,
                anchor_keywords=[cert], require_all=False,
                source_ref=cap.get("id") or "",
                source_category=cap.get("category") or "",
                extra={
                    "cert_no": attrs.get("cert_no") or "",
                    "valid_until": attrs.get("valid_until") or "",
                }))
        return {"公司证书"}

    def _dispatch_project(self, registry: FactRegistry, cap: dict,
                          attrs: dict) -> set[str]:
        """项目案例卡 → project（合同额/业绩数量；项目名精确匹配）。"""
        covered: set[str] = set()
        project_name = attrs.get("project_name") or ""
        if project_name:
            registry.entries.append(FactRegistryEntry(
                metric="项目名称", kind="project", name=str(project_name),
                anchor_keywords=[str(project_name)], require_all=False,
                source_ref=cap.get("id") or "",
                source_category=cap.get("category") or ""))
            covered.add("项目名称")
        scale = attrs.get("scale") or attrs.get("contract_amount")
        if scale is not None:
            parsed = _parse_value(str(scale))
            if parsed:
                lo, hi, unit = parsed
                norm = _to_implied(lo, hi, unit, "money_wan")
                if norm:
                    covered.add("单个合同额")
                    registry.entries.append(FactRegistryEntry(
                        metric="单个合同额", kind="project",
                        name=project_name or cap.get("name") or "",
                        anchor_keywords=("合同额", "金额", "scale"),
                        require_all=False,
                        value=norm[0], value_hi=norm[1], unit="money_wan",
                        source_ref=cap.get("id") or "",
                        source_category=cap.get("category") or ""))
        pc = attrs.get("project_count")
        if pc is not None:
            parsed = _parse_value(str(pc))
            if parsed:
                lo, hi, unit = parsed
                norm = _to_implied(lo, hi, unit, "count")
                if norm:
                    covered.add("项目业绩数量")
                    registry.entries.append(FactRegistryEntry(
                        metric="项目业绩数量", kind="project",
                        name=project_name or cap.get("name") or "",
                        anchor_keywords=("业绩", "项目", "project_count"),
                        require_all=False,
                        value=norm[0], value_hi=norm[1], unit="count",
                        source_ref=cap.get("id") or "",
                        source_category=cap.get("category") or ""))
        return covered

    def _dispatch_metric(self, registry: FactRegistry, cap: dict,
                         attrs: dict) -> set[str]:
        """产品/技术/售后卡 → metric（attributes 键经 _ATTR_METRIC 归一）。"""
        covered: set[str] = set()
        for key, raw in attrs.items():
            meta = _metric_meta(key, str(raw))
            if meta is None:
                continue
            metric, keywords, implied_unit = meta
            parsed = _parse_value(str(raw))
            if not parsed:
                continue
            lo, hi, unit = parsed
            norm = _to_implied(lo, hi, unit, implied_unit)
            if norm is None:
                continue  # 单位不可换算 → 放弃该指标（防误报）
            covered.add(metric)
            registry.entries.append(FactRegistryEntry(
                metric=metric, kind="metric",
                name=cap.get("name") or "",
                anchor_keywords=list(keywords),
                require_all=False,
                value=norm[0], value_hi=norm[1], unit=implied_unit,
                source_ref=cap.get("id") or "",
                source_category=cap.get("category") or ""))
        return covered

    def _dispatch_company(self, registry: FactRegistry, cap: dict,
                          attrs: dict) -> set[str]:
        """公司介绍卡 → company（注册资本/成立年限/员工）。"""
        covered: set[str] = set()
        for key, raw in attrs.items():
            meta = _metric_meta(key, str(raw))
            if meta is None:
                continue
            metric, keywords, implied_unit = meta
            parsed = _parse_value(str(raw))
            if not parsed:
                continue
            lo, hi, unit = parsed
            norm = _to_implied(lo, hi, unit, implied_unit)
            if norm is None:
                continue
            covered.add(metric)
            registry.entries.append(FactRegistryEntry(
                metric=metric, kind="company",
                name=cap.get("name") or "",
                anchor_keywords=list(keywords), require_all=False,
                value=norm[0], value_hi=norm[1], unit=implied_unit,
                source_ref=cap.get("id") or "",
                source_category=cap.get("category") or ""))
        return covered

    # ── 证据兜底 ──────────────────────────────────────────────────────
    def _evidence_fallback(self, registry: FactRegistry, ev: dict,
                           covered_metrics: set[str]) -> None:
        """指标无卡覆盖时，按证据原文 + 指标关键词兜底注册一条事实。

        仅取每个未覆盖指标的首条命中（确定性）；命中即算覆盖后续证据。
        """
        content = ev.get("content") or ""
        if not content:
            return
        for key, (metric, keywords, implied_unit) in _ATTR_METRIC.items():
            if metric in covered_metrics:
                continue
            if not any(k in content for k in keywords):
                continue
            parsed = _parse_value(content)
            if not parsed:
                continue
            lo, hi, unit = parsed
            norm = _to_implied(lo, hi, unit, implied_unit)
            if norm is None:
                continue
            covered_metrics.add(metric)
            registry.entries.append(FactRegistryEntry(
                metric=metric, kind="metric",
                name=ev.get("id") or "",
                anchor_keywords=list(keywords), require_all=False,
                value=norm[0], value_hi=norm[1], unit=implied_unit,
                source_ref=ev.get("id") or "",
                source_category=ev.get("category") or ""))


__all__ = ["FactRegistryBuilder", "FactRegistry"]
