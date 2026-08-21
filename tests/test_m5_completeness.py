# -*- coding: utf-8 -*-
"""
tests/test_m5_completeness.py —— M5-07/08/09 完整性检查（批次 2）

覆盖：
- 基线：5 个 UNKNOWN → PENDING_CONFIRMATION(INFO)，无 REQUIREMENT_MISSING
- 删 canonical 行（中重要）→ REQUIREMENT_MISSING(ERROR)
- 删 canonical 行（高重要）→ REQUIREMENT_MISSING(CRITICAL)
- score_points：空表通过；插入未覆盖评分项 → SCORE_MISSING；覆盖 → 通过
- 章节 content_md 清空 → SECTION_MISSING(CRITICAL)
- 封面项目名替换 → PROJECT_MISMATCH(ERROR)
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.services.quality.checks import all_checks  # noqa: E402
from app.services.quality.context import build_check_context  # noqa: E402
from app.services.quality.models import (  # noqa: E402
    IssueType, QualityIssue, Severity)


def _issues(seed_m5) -> list[QualityIssue]:
    ctx = build_check_context(seed_m5["db"], seed_m5["tender_id"],
                              as_of="2026-08-18")
    return all_checks(ctx)


def _delete_canonical(seed_m5, rid: str) -> None:
    db, tid = seed_m5["db"], seed_m5["tender_id"]
    db.execute("DELETE FROM canonical_requirements WHERE id = ? AND tender_id = ?",
               (rid, tid))
    db.execute("DELETE FROM requirement_matches WHERE requirement_id = ? "
               "AND tender_id = ?", (rid, tid))
    db.execute("DELETE FROM requirement_section_maps WHERE requirement_id = ? "
               "AND tender_id = ?", (rid, tid))


def _by_type(issues, t: IssueType) -> list[QualityIssue]:
    return [i for i in issues if i.issue_type == t]


# ═══════════════════════════════════════════════════════════════════════
# 需求完整性
# ═══════════════════════════════════════════════════════════════════════
def test_baseline_unknown_pending_and_no_missing(seed_m5):
    """基线：5 个 UNKNOWN → 5 条 PENDING(INFO)，无 REQUIREMENT_MISSING。"""
    issues = _issues(seed_m5)
    pending = _by_type(issues, IssueType.PENDING_CONFIRMATION)
    unknown = [i for i in pending if "UNKNOWN" in i.message]
    assert len(unknown) == 5, [i.message for i in pending]
    assert all(i.severity == Severity.INFO for i in unknown)
    assert _by_type(issues, IssueType.REQUIREMENT_MISSING) == []


def test_delete_canonical_missing_error(seed_m5):
    """删 canonical 行 → 其承接的 M1 需求成孤儿 → REQUIREMENT_MISSING(ERROR)。

    REQ-C-0020 ← M1 [REQ-0022（中）] → 1 条 ERROR。
    """
    _delete_canonical(seed_m5, "REQ-C-0020")   # 系统操作响应时间不超过3秒
    issues = _issues(seed_m5)
    hits = _by_type(issues, IssueType.REQUIREMENT_MISSING)
    assert len(hits) == 1, [i.message for i in issues]
    assert hits[0].severity == Severity.ERROR
    assert hits[0].requirement_id == "REQ-0022"
    assert "操作响应" in hits[0].message


def test_delete_star_canonical_missing_critical(seed_m5):
    """删 合并 canonical 行 → 2 条 M1 孤儿，高重要那条 CRITICAL。

    REQ-C-0002 ← M1 [REQ-0001（高）, REQ-0002（中）] → CRITICAL + ERROR。
    """
    _delete_canonical(seed_m5, "REQ-C-0002")   # 设备接入不少于1000台（高）
    issues = _issues(seed_m5)
    hits = _by_type(issues, IssueType.REQUIREMENT_MISSING)
    assert len(hits) == 2, [i.message for i in issues]
    ids = {i.requirement_id for i in hits}
    assert ids == {"REQ-0001", "REQ-0002"}, ids
    crit = [i for i in hits if i.severity == Severity.CRITICAL]
    err = [i for i in hits if i.severity == Severity.ERROR]
    assert len(crit) == 1 and "设备接入不少于1000台" in crit[0].message
    assert len(err) == 1


# ═══════════════════════════════════════════════════════════════════════
# 评分项覆盖
# ═══════════════════════════════════════════════════════════════════════
def test_score_points_empty_passes(seed_m5):
    issues = _issues(seed_m5)
    assert _by_type(issues, IssueType.SCORE_MISSING) == []


def test_score_point_uncovered(seed_m5):
    db, tid = seed_m5["db"], seed_m5["tender_id"]
    db.insert("score_points", {
        "id": "SC-M5-TEST", "tender_id": tid, "category": "售后",
        "item": "应急预案演练频次", "max_score": 10.0,
        "criteria": "对应急预案演练频次进行评分", "rule_id": "",
        "weight": 0.0, "source_ref": "T-M3 评分细则",
        "created_at": "2026-08-18 00:00:00"})
    issues = _issues(seed_m5)
    hits = _by_type(issues, IssueType.SCORE_MISSING)
    assert len(hits) == 1, [i.message for i in issues]
    assert hits[0].severity == Severity.WARNING
    assert "应急预案演练" in hits[0].message


def test_score_point_covered_passes(seed_m5):
    db, tid = seed_m5["db"], seed_m5["tender_id"]
    db.insert("score_points", {
        "id": "SC-M5-OK", "tender_id": tid, "category": "售后",
        "item": "售后服务承诺", "max_score": 10.0,
        "criteria": "对售后服务承诺内容评分", "rule_id": "",
        "weight": 0.0, "source_ref": "T-M3 评分细则",
        "created_at": "2026-08-18 00:00:00"})
    issues = _issues(seed_m5)
    assert _by_type(issues, IssueType.SCORE_MISSING) == []


# ═══════════════════════════════════════════════════════════════════════
# 章节完整性
# ═══════════════════════════════════════════════════════════════════════
def test_section_content_cleared(seed_m5):
    """章节 content_md 清空 → SECTION_MISSING(CRITICAL)。"""
    db, tid = seed_m5["db"], seed_m5["tender_id"]
    db.execute("UPDATE generation_sections SET content_md = '' "
               "WHERE section_id = 'CH-06-1' AND tender_id = ?", (tid,))
    issues = _issues(seed_m5)
    hits = _by_type(issues, IssueType.SECTION_MISSING)
    assert len(hits) == 1, [i.message for i in issues]
    assert hits[0].severity == Severity.CRITICAL
    assert hits[0].section_id == "CH-06-1"


# ═══════════════════════════════════════════════════════════════════════
# 项目名一致性
# ═══════════════════════════════════════════════════════════════════════
def test_project_name_mismatch(seed_m5):
    """封面项目名替换 → PROJECT_MISMATCH(ERROR)。"""
    db, tid = seed_m5["db"], seed_m5["tender_id"]
    db.execute("UPDATE generation_sections SET content_md = "
               "REPLACE(content_md, 'M5质量检查测试项目', '虚假项目名称') "
               "WHERE section_id = 'CH-01' AND tender_id = ?", (tid,))
    issues = _issues(seed_m5)
    hits = _by_type(issues, IssueType.PROJECT_MISMATCH)
    assert len(hits) == 1, [i.message for i in issues]
    assert hits[0].severity == Severity.ERROR
    assert hits[0].section_id == "CH-01"
