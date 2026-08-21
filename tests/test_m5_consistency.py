# -*- coding: utf-8 -*-
"""
tests/test_m5_consistency.py —— M5-10/11/12 一致性检查（批次 2）

覆盖：
- 基线：无 CONFLICT（2000 vs 1500-2500 相交；质保 2 年 vs 3 年均匹配
  注册表 → KB 一致豁免）
- 跨章节冲突：CH-05-2 改 5000 + CH-06-1 追加 2000 → CONFLICT(ERROR)
- 引用有效性：注入 EVD-9999 → INVALID_REFERENCE(CRITICAL)
- 待确认收集：基线 PENDING_CONFIRMATION 合计 9（5 UNKNOWN + 4 标记）
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


def _issues(seed_m5):
    ctx = build_check_context(seed_m5["db"], seed_m5["tender_id"], as_of=AS_OF)
    return all_checks(ctx)


def _mutate(seed_m5, sid, old, new):
    db, tid = seed_m5["db"], seed_m5["tender_id"]
    db.execute("UPDATE generation_sections SET content_md = REPLACE(content_md, ?, ?) "
               "WHERE section_id = ? AND tender_id = ?", (old, new, sid, tid))


def _by_type(issues, t: IssueType) -> list:
    return [i for i in issues if i.issue_type == t]


# ═══════════════════════════════════════════════════════════════════════
# 跨章节冲突（M5-10）
# ═══════════════════════════════════════════════════════════════════════
def test_baseline_no_conflict(seed_m5):
    """基线无冲突：2000 vs 1500-2500 相交；质保 2/3 年均匹配注册表。"""
    issues = _issues(seed_m5)
    assert _by_type(issues, IssueType.CONFLICT) == []


def test_conflict_cross_section(seed_m5):
    """CH-05-2 改 5000 + CH-06-1 追加 2000 → CONFLICT(ERROR)。

    5000 不匹配注册表 → 与其它章节的 2000/1500-2500 不相交 → 冲突。
    """
    _mutate(seed_m5, "CH-05-2", "max_devices=2000", "max_devices=5000")
    _mutate(seed_m5, "CH-06-1", "scale=单个合同额500万元。",
            "scale=单个合同额500万元。\n设备接入能力为2000台。")
    issues = _issues(seed_m5)
    hits = _by_type(issues, IssueType.CONFLICT)
    assert len(hits) == 1, [i.message for i in issues]
    assert hits[0].severity == Severity.ERROR
    assert "设备接入" in hits[0].message
    assert hits[0].section_id == "CH-05-2"
    refs = {r["section"] for r in hits[0].source_refs}
    assert refs >= {"CH-05-2", "CH-06-1"}, refs


# ═══════════════════════════════════════════════════════════════════════
# 引用有效性（M5-11）
# ═══════════════════════════════════════════════════════════════════════
def test_invalid_reference(seed_m5):
    """事实区注入 EVD-9999 → INVALID_REFERENCE(CRITICAL)。"""
    _mutate(seed_m5, "CH-04-1", "**本章证据依据：**",
            "另参考证据EVD-9999。\n**本章证据依据：**")
    issues = _issues(seed_m5)
    hits = [i for i in issues if i.issue_type == IssueType.INVALID_REFERENCE
            and "EVD-9999" in i.message]
    assert len(hits) == 1, [i.message for i in issues]
    assert hits[0].severity == Severity.CRITICAL
    assert hits[0].section_id == "CH-04-1"


def test_echo_zone_reference_not_flagged(seed_m5):
    """回显区（CH-08）内的 EVD 引用不扫（回显原文非自述事实）。"""
    _mutate(seed_m5, "CH-08", "（本章节以表格形式展示，数据待补充）",
            "（本章节以表格形式展示，数据待补充）证据EVD-9999")
    issues = _issues(seed_m5)
    hits = [i for i in issues if i.issue_type == IssueType.INVALID_REFERENCE]
    assert hits == [], [i.message for i in issues]


# ═══════════════════════════════════════════════════════════════════════
# 待确认收集（M5-12）
# ═══════════════════════════════════════════════════════════════════════
def test_baseline_pending_total_nine(seed_m5):
    """基线 PENDING 合计 9：5 个 UNKNOWN 需求 + CH-06-3 的 4 个【待确认】。"""
    issues = _issues(seed_m5)
    pending = _by_type(issues, IssueType.PENDING_CONFIRMATION)
    assert len(pending) == 9, [i.message for i in pending]
    ch63 = [i for i in pending if i.section_id == "CH-06-3"]
    assert len(ch63) == 4
    assert all(i.severity == Severity.INFO for i in ch63)
