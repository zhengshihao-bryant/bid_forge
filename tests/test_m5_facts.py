# -*- coding: utf-8 -*-
"""
tests/test_m5_facts.py —— M5-02/03/04/05/06 事实检查（批次 2）

覆盖：
- 基线：无 CRITICAL/ERROR，无数字/人员/证书/项目 mismatch（M5 验收口径）
- Layer A：不可溯源数字 8888 报；语料内数字不报；结构豁免（年份/页码/
  表序号/EVD-id/7×24）；回显区（CH-08/CH-05-4）不误报
- Layer B：2000→5000 NUMBER_MISMATCH；张伟 6→3 PERSON_MISMATCH；
  ISO9001→9002 CERTIFICATE_MISMATCH；合同额 500→800 PROJECT_MISMATCH；
  员工 300-600 内 "500人" 通过；张伟 require_all 防"质保3年"串线
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.services.quality.checks import all_checks  # noqa: E402
from app.services.quality.context import build_check_context  # noqa: E402
from app.services.quality.models import IssueType, Severity  # noqa: E402

AS_OF = "2026-08-18"
_MISMATCH_TYPES = {IssueType.NUMBER_MISMATCH, IssueType.PERSON_MISMATCH,
                   IssueType.CERTIFICATE_MISMATCH, IssueType.PROJECT_MISMATCH}


def _issues(seed_m5):
    ctx = build_check_context(seed_m5["db"], seed_m5["tender_id"], as_of=AS_OF)
    return all_checks(ctx)


def _mutate(seed_m5, sid, old, new):
    db, tid = seed_m5["db"], seed_m5["tender_id"]
    db.execute("UPDATE generation_sections SET content_md = REPLACE(content_md, ?, ?) "
               "WHERE section_id = ? AND tender_id = ?", (old, new, sid, tid))


# ═══════════════════════════════════════════════════════════════════════
# 基线
# ═══════════════════════════════════════════════════════════════════════
def test_baseline_no_critical_or_error(seed_m5):
    """M5 验收口径：基线标书不产生 CRITICAL/ERROR（含 9 条 PENDING 允许）。"""
    issues = _issues(seed_m5)
    bad = [i for i in issues if i.severity in (Severity.CRITICAL, Severity.ERROR)]
    assert bad == [], [i.message for i in bad]


def test_baseline_no_mismatch_types(seed_m5):
    issues = _issues(seed_m5)
    mismatches = [i for i in issues if i.issue_type in _MISMATCH_TYPES]
    assert mismatches == [], [i.message for i in mismatches]


# ═══════════════════════════════════════════════════════════════════════
# Layer A —— 成员资格
# ═══════════════════════════════════════════════════════════════════════
def test_layer_a_unverifiable_number(seed_m5):
    """不可溯源数字 → NUMBER_MISMATCH(WARNING)。

    独立新行注入（无任何锚点关键词），确保 8888 不被 Layer B 认领。
    """
    _mutate(seed_m5, "CH-04-1", "**本章证据依据：**",
            "辅助参考值8888台。\n**本章证据依据：**")
    issues = _issues(seed_m5)
    nums = [i for i in issues
            if i.issue_type == IssueType.NUMBER_MISMATCH and "8888" in i.message]
    assert len(nums) == 1, [i.message for i in issues]
    assert nums[0].severity == Severity.WARNING
    assert nums[0].section_id == "CH-04-1"


def test_layer_a_known_number_passes(seed_m5):
    """语料内数字（注册资本5000万元）不报。"""
    issues = _issues(seed_m5)
    nums = [i for i in issues if i.issue_type == IssueType.NUMBER_MISMATCH]
    assert all("5000" not in i.message for i in nums)


def test_layer_a_structural_exemptions(seed_m5):
    """年份/页码/表序号/EVD-id/7×24 全部豁免，不报。"""
    _mutate(seed_m5, "CH-04-1", "公司专注智慧园区领域",
            "公司专注智慧园区领域，合同工期约2025年交付，详见第12页，"
            "7×24小时保障，依据证据EVD-0001。")
    issues = _issues(seed_m5)
    nums = [i for i in issues if i.issue_type == IssueType.NUMBER_MISMATCH]
    assert nums == [], [i.message for i in issues]


def test_echo_zone_excluded(seed_m5):
    """回显区 CH-05-4 注入不可溯源数字不报（非标书自述事实）。"""
    _mutate(seed_m5, "CH-05-4", "5000台", "12345台")
    issues = _issues(seed_m5)
    ch54 = [i for i in issues if i.section_id == "CH-05-4"
            and i.issue_type == IssueType.NUMBER_MISMATCH]
    assert ch54 == [], [i.message for i in ch54]


# ═══════════════════════════════════════════════════════════════════════
# Layer B —— 锚定比较
# ═══════════════════════════════════════════════════════════════════════
def test_layer_b_device_capacity(seed_m5):
    """max_devices 2000→5000 → NUMBER_MISMATCH（锚定 设备接入）。"""
    _mutate(seed_m5, "CH-05-2", "max_devices=2000", "max_devices=5000")
    issues = _issues(seed_m5)
    hits = [i for i in issues
            if i.issue_type == IssueType.NUMBER_MISMATCH
            and "设备接入" in i.message]
    assert len(hits) == 1, [i.message for i in issues]
    assert hits[0].section_id == "CH-05-2"


def test_layer_b_person_experience(seed_m5):
    """张伟 6年→3年 → PERSON_MISMATCH(ERROR)。"""
    _mutate(seed_m5, "CH-06-2", "张伟具有6年", "张伟具有3年")
    issues = _issues(seed_m5)
    hits = [i for i in issues if i.issue_type == IssueType.PERSON_MISMATCH]
    assert len(hits) == 1, [i.message for i in issues]
    assert hits[0].severity == Severity.ERROR
    assert "张伟" in hits[0].message or "项目经理经验" in hits[0].message
    assert hits[0].section_id == "CH-06-2"


def test_layer_b_person_require_all_no_crosstalk(seed_m5):
    """张伟(6年) 与 质保3年 不串线：无虚假 PERSON_MISMATCH。"""
    issues = _issues(seed_m5)
    assert not [i for i in issues if i.issue_type == IssueType.PERSON_MISMATCH]


def test_layer_b_certificate(seed_m5):
    """证书表 ISO9001→9002 → CERTIFICATE_MISMATCH（9002 不在注册表）。"""
    _mutate(seed_m5, "CH-04-2", "ISO9001", "ISO9002")
    issues = _issues(seed_m5)
    hits = [i for i in issues
            if i.issue_type == IssueType.CERTIFICATE_MISMATCH
            and "9002" in i.message]
    assert len(hits) == 1, [i.message for i in issues]
    assert hits[0].severity == Severity.ERROR
    assert hits[0].section_id == "CH-04-2"


def test_layer_b_project_amount(seed_m5):
    """合同额 500万元→800万元 → PROJECT_MISMATCH(ERROR)。"""
    _mutate(seed_m5, "CH-04-3", "单个合同额500万元", "单个合同额800万元")
    issues = _issues(seed_m5)
    hits = [i for i in issues if i.issue_type == IssueType.PROJECT_MISMATCH
            and "800" in i.message]
    assert len(hits) == 1, [i.message for i in issues]
    assert hits[0].severity == Severity.ERROR
    assert hits[0].section_id == "CH-04-3"


def test_layer_b_employee_range_pass(seed_m5):
    """员工 300-600 区间内声明"500人" → 通过（区间相交）。"""
    _mutate(seed_m5, "CH-04-1", "公司专注智慧园区领域",
            "公司专注智慧园区领域，员工团队约500人。")
    issues = _issues(seed_m5)
    hits = [i for i in issues
            if i.issue_type in _MISMATCH_TYPES and "500" in i.message]
    assert hits == [], [i.message for i in issues]


def test_layer_b_warranty_cross_section_no_false(seed_m5):
    """质保 2年(CH-05-2) vs 3年(CH-07-1) 均匹配注册表 → 无 NUMBER_MISMATCH。"""
    issues = _issues(seed_m5)
    war = [i for i in issues if "质保" in i.message
           and i.issue_type in _MISMATCH_TYPES]
    assert war == [], [i.message for i in war]
