# -*- coding: utf-8 -*-
"""
tests/test_m5_models.py —— M5-01 质量检查数据模型（批次 1）

覆盖：
- IssueType 12 类 / Severity 4 级 / 权重常量
- QualityIssue / QualityReport / FactRegistry / CheckContext 默认值与访问器
- quality_reports / quality_issues / review_records 三表 row 映射 roundtrip
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app import config  # noqa: E402
from app.db import Database  # noqa: E402
from app.services.quality.models import (  # noqa: E402
    CheckContext, FactRegistry, FactRegistryEntry, IssueStatus, IssueType,
    QualityIssue, QualityReport, ReviewRecord, Severity, SEVERITY_WEIGHT,
    DimensionScore)


# ═══════════════════════════════════════════════════════════════════════
# 枚举与权重
# ═══════════════════════════════════════════════════════════════════════
def test_issue_type_has_12_categories():
    """M5-01：12 类问题类型全部齐备。"""
    types = {t.value for t in IssueType}
    assert types == {
        "NUMBER_MISMATCH", "PERSON_MISMATCH", "CERTIFICATE_MISMATCH",
        "PROJECT_MISMATCH", "REQUIREMENT_MISSING", "SCORE_MISSING",
        "SECTION_MISSING", "CONFLICT", "INVALID_REFERENCE",
        "PENDING_CONFIRMATION", "SEMANTIC_COVERAGE", "FORMAT_ERROR"}


def test_severity_and_weights():
    """M5-01/14：4 级严重度 + 评分权重常量。"""
    assert {s.value for s in Severity} == {"INFO", "WARNING", "ERROR", "CRITICAL"}
    assert SEVERITY_WEIGHT[Severity.CRITICAL] == 20.0
    assert SEVERITY_WEIGHT[Severity.ERROR] == 10.0
    assert SEVERITY_WEIGHT[Severity.WARNING] == 3.0
    assert SEVERITY_WEIGHT[Severity.INFO] == 0.5
    assert {s.value for s in IssueStatus} == {"待处理", "已确认", "已忽略", "已修复"}


# ═══════════════════════════════════════════════════════════════════════
# 模型默认值
# ═══════════════════════════════════════════════════════════════════════
def test_issue_defaults():
    i = QualityIssue()
    assert i.status == IssueStatus.PENDING
    assert i.autofixable is False
    assert i.source_refs == []
    assert i.created_at  # now_str 非空
    # 报告默认值
    r = QualityReport()
    assert r.id == "QR-0001" and r.status == "草稿" and r.score == 0.0


def test_fact_registry_accessors():
    reg = FactRegistry(entries=[
        FactRegistryEntry(metric="项目经理经验", kind="person", name="张伟"),
        FactRegistryEntry(metric="公司证书", kind="certificate", name="ISO9001"),
        FactRegistryEntry(metric="单个合同额", kind="project", name="项目A"),
        FactRegistryEntry(metric="设备接入", kind="metric"),
        FactRegistryEntry(metric="注册资本", kind="company"),
    ])
    assert len(reg.persons()) == 1
    assert [e.name for e in reg.certs()] == ["ISO9001"]
    assert len(reg.projects()) == 1
    assert len(reg.of_kind("company")) == 1
    assert [e.metric for e in reg.metric("设备接入")] == ["设备接入"]


def test_check_context_fact_zone_filter():
    """fact_zone_ids 过滤：非事实区章节（回显区）不参与检查。"""
    ctx = CheckContext(
        sections=[
            {"section_id": "CH-05-2", "content_md": "x"},
            {"section_id": "CH-08", "content_md": "回显"},
            {"section_id": "CH-05-4", "content_md": "回显"},
        ],
        fact_zone_ids=["CH-05-2"],
    )
    assert [s["section_id"] for s in ctx.fact_zone_sections()] == ["CH-05-2"]


# ═══════════════════════════════════════════════════════════════════════
# M5 三表 row 映射 roundtrip
# ═══════════════════════════════════════════════════════════════════════
def test_report_row_roundtrip():
    r = QualityReport(id="QR-0001", tender_id="T-M3", document_version="v2",
                      score=98.2, status="已批准", reviewer="张工",
                      review_time="2026-08-18 10:00:00",
                      dimensions=[DimensionScore(name="完整性", score=96.0)])
    row = Database.report_to_row(r)
    r2 = Database.row_to_report(row)
    assert r2.id == "QR-0001" and r2.score == 98.2
    assert r2.status == "已批准" and r2.reviewer == "张工"
    assert r2.dimensions[0].name == "完整性" and r2.dimensions[0].score == 96.0


def test_issue_row_roundtrip():
    i = QualityIssue(
        id="QR-0001-0001", report_id="QR-0001", tender_id="T-M3",
        document_version="v2", section_id="CH-05-2",
        issue_type=IssueType.NUMBER_MISMATCH, severity=Severity.ERROR,
        message="2000→5000", source_refs=[{"cap": "CAP-0001"}],
        suggestion="改回2000", autofixable=True)
    row = Database.issue_to_row(i)
    i2 = Database.row_to_issue(row)
    assert i2.issue_type == IssueType.NUMBER_MISMATCH
    assert i2.severity == Severity.ERROR
    assert i2.autofixable is True
    assert i2.source_refs == [{"cap": "CAP-0001"}]
    assert i2.section_id == "CH-05-2"


def test_review_row_roundtrip(tmp_env):
    """review_records 行映射 + 真库插入读回（自增 id）。"""
    db = Database(config.DB_PATH)
    db.init_schema()
    rv = ReviewRecord(issue_id="QR-0001-0001", action="确认", reviewer="张工",
                      note="已核对企业证书")
    db.insert("review_records", Database.review_to_row(rv))
    row = db.query_one("SELECT * FROM review_records WHERE issue_id = ?",
                       ("QR-0001-0001",))
    rv2 = Database.row_to_review(row)
    assert rv2.id > 0 and rv2.action == "确认" and rv2.reviewer == "张工"
    assert rv2.note == "已核对企业证书"


def test_quality_tables_created(tmp_env):
    """M5 三表随 init_schema 建出。"""
    db = Database(config.DB_PATH)
    db.init_schema()
    for t in ("quality_reports", "quality_issues", "review_records"):
        assert db.query_one(
            "SELECT COUNT(*) AS n FROM sqlite_master WHERE type='table' "
            "AND name=?", (t,))["n"] == 1, f"缺少表 {t}"
