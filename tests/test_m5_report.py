# -*- coding: utf-8 -*-
"""
tests/test_m5_report.py —— M5-14/15/16/17/19 评分+报告+autofix+runner（批次 3）

覆盖：
- 评分公式：权重累加 / clamp 到 0 / 5 维均值 round(,1) / INFO 0.5 权重
- 报告 Markdown：五维表 + 按严重度分组 + 待确认清单
- autofix：只改格式（行尾空白/标题空格/连续空行/表格管道），不动数字/证书，
  逐条应用后收敛（幂等）
- runner.run：基线 9 条 PENDING、score=99.1、落库
- finalize：无 CRITICAL/ERROR → 通过；有未处理 → 409；清状态 → 通过；
  force → 通过；产物 final.docx 魔数 PK + final.md + quality-report.json；
  review_records 批准审计 + 报告置"已批准"
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.services.quality.autofix import AutoFixer  # noqa: E402
from app.services.quality.checks.format_check import check_format  # noqa: E402
from app.services.quality.context import build_check_context  # noqa: E402
from app.services.quality.models import (  # noqa: E402
    DimensionScore, IssueType, QualityIssue, QualityReport, Severity)
from app.services.quality.report import render_markdown  # noqa: E402
from app.services.quality.runner import (  # noqa: E402
    QualityFinalizeError, QualityRunner)
from app.services.quality.scoring import score_report  # noqa: E402


def _issue(t: IssueType, sev: Severity, msg: str = "m") -> QualityIssue:
    return QualityIssue(issue_type=t, severity=sev, message=msg)


# ═══════════════════════════════════════════════════════════════════════
# M5-14 评分公式
# ═══════════════════════════════════════════════════════════════════════
def test_scoring_formula():
    """CRITICAL(20) 扣完整性；ERROR(10) 扣一致性；均值 round(,1)。"""
    issues = [_issue(IssueType.REQUIREMENT_MISSING, Severity.CRITICAL),
              _issue(IssueType.CONFLICT, Severity.ERROR)]
    total, dims = score_report(issues)
    by = {d.name: d.score for d in dims}
    assert by["完整性"] == 80.0
    assert by["一致性"] == 90.0
    assert by["事实准确性"] == 100.0
    assert by["证据覆盖"] == 100.0
    assert by["格式完整性"] == 100.0
    assert total == 94.0                    # (80+100+100+90+100)/5


def test_scoring_info_weight_and_clamp():
    """INFO(0.5) 权重累加；扣分超过 100 时钳位到 0。"""
    pending = [_issue(IssueType.PENDING_CONFIRMATION, Severity.INFO)
               for _ in range(3)]
    _, dims = score_report(pending)
    assert {d.name: d.score for d in dims}["事实准确性"] == 98.5

    crits = [_issue(IssueType.REQUIREMENT_MISSING, Severity.CRITICAL)
             for _ in range(6)]             # 扣 120 > 100
    _, dims2 = score_report(crits)
    assert {d.name: d.score for d in dims2}["完整性"] == 0.0


# ═══════════════════════════════════════════════════════════════════════
# M5-15 报告渲染
# ═══════════════════════════════════════════════════════════════════════
def test_report_markdown_sections():
    report = QualityReport(
        id="QR-0001", tender_id="T-M3", score=94.0,
        dimensions=[
            DimensionScore(name="完整性", score=80.0, deductions=["[CRITICAL] x"]),
            DimensionScore(name="事实准确性", score=100.0, deductions=[]),
            DimensionScore(name="证据覆盖", score=100.0, deductions=[]),
            DimensionScore(name="一致性", score=90.0, deductions=["[ERROR] y"]),
            DimensionScore(name="格式完整性", score=100.0, deductions=[]),
        ],
        counts={"critical": 1, "error": 1, "warning": 0, "info": 0,
                "pending": 2})
    issues = [_issue(IssueType.REQUIREMENT_MISSING, Severity.CRITICAL),
              _issue(IssueType.CONFLICT, Severity.ERROR)]
    md = render_markdown(report, issues)
    assert "五维得分" in md
    assert "完整性" in md and "格式完整性" in md
    assert "待确认清单" in md
    assert "REQUIREMENT_MISSING" in md and "CRITICAL" in md
    assert "内部质量指标" in md             # 口径声明不写成"准确率"


# ═══════════════════════════════════════════════════════════════════════
# M5-17 格式自动修复
# ═══════════════════════════════════════════════════════════════════════
def test_autofix_format_only(seed_m5):
    """四种格式缺陷逐条修复并收敛；数字/证书内容原样保留。"""
    db, tid = seed_m5["db"], seed_m5["tender_id"]
    crafted = ("ISO9001 认证，注册资本5000万元。\n"
               "##标题行\n"
               "尾部有空格  \n"
               "\n\n"
               "| 甲 | 乙 |\n"
               "| --- | --- |\n"
               "| 1 |\n"
               "数字5000和证书ISO9001必须原样保留。")
    _set_content(db, tid, "CH-04-1", crafted)

    fixer = AutoFixer()
    fixed = crafted
    for _ in range(10):                      # 边界防护：不收敛即失败
        ctx = build_check_context(db, tid, as_of="2026-08-18")
        issues = [i for i in check_format(ctx) if i.section_id == "CH-04-1"]
        if not issues:
            break
        _set_content(db, tid, "CH-04-1", fixed)
        fixed = fixer.apply(fixed, issues[0])
    else:
        pytest.fail("autofix 未收敛")

    assert "5000" in fixed and "ISO9001" in fixed
    assert "##标题行" not in fixed and "## 标题行" in fixed
    assert "尾部有空格  " not in fixed       # 行尾空白已删
    assert "\n\n\n" not in fixed             # 连续空行已压缩
    assert "| 1 ||" in fixed or "| 1 | |" in fixed   # 表格行已补齐管道
    # 幂等：修复后重查格式零问题
    _set_content(db, tid, "CH-04-1", fixed)
    ctx2 = build_check_context(db, tid, as_of="2026-08-18")
    assert [i for i in check_format(ctx2) if i.section_id == "CH-04-1"] == []


def _set_content(db, tid: str, sid: str, content: str) -> None:
    db.execute("UPDATE generation_sections SET content_md = ? "
               "WHERE section_id = ? AND tender_id = ?", (content, sid, tid))


# ═══════════════════════════════════════════════════════════════════════
# M5-16/19 runner + finalize
# ═══════════════════════════════════════════════════════════════════════
def test_runner_baseline_and_finalize(seed_m5, tmp_path):
    """基线：9 条 PENDING、score=99.1、落库；finalize 通过 + 产物 + 审计。"""
    db, tid = seed_m5["db"], seed_m5["tender_id"]
    runner = QualityRunner(db)
    result = runner.run(tid)
    report, issues = result["report"], result["issues"]
    assert report.id == "QR-0001"
    assert report.score == 99.1
    assert report.counts["pending"] == 9
    assert report.counts["critical"] == 0 and report.counts["error"] == 0
    assert len(issues) == 9
    assert db.query_one("SELECT * FROM quality_reports WHERE id = 'QR-0001'")

    res = runner.finalize(tid, reviewer="验收员", output_dir=tmp_path)
    assert res["status"] == "已批准"
    docx = Path(res["artifacts"]["final_docx"])
    assert docx.exists() and docx.read_bytes()[:2] == b"PK"   # DOCX = zip 魔数
    assert Path(res["artifacts"]["final_md"]).exists()
    assert Path(res["artifacts"]["report_json"]).exists()

    audit = db.query("SELECT * FROM review_records WHERE action = '批准'")
    assert len(audit) == 1
    assert audit[0]["reviewer"] == "验收员"
    row = db.query_one("SELECT * FROM quality_reports WHERE id = 'QR-0001'")
    assert row["status"] == "已批准" and row["reviewer"] == "验收员"


def test_finalize_blocked_then_cleared(seed_m5, tmp_path):
    """有未处理 CRITICAL → 拒绝；全部改"已确认" → 通过。"""
    db, tid = seed_m5["db"], seed_m5["tender_id"]
    runner = QualityRunner(db)
    runner.run(tid)
    db.execute("UPDATE generation_sections SET content_md = '' "
               "WHERE section_id = 'CH-06-1' AND tender_id = ?", (tid,))
    result = runner.run(tid)                  # QR-0002 含 SECTION_MISSING(CRITICAL)
    assert result["report"].counts["critical"] == 1

    with pytest.raises(QualityFinalizeError):
        runner.finalize(tid, reviewer="验收员")

    # 人工确认全部 CRITICAL/ERROR → 未清问题归零 → finalize 通过
    db.execute("UPDATE quality_issues SET status = '已确认' "
               "WHERE report_id = ? AND severity IN ('CRITICAL', 'ERROR')",
               (result["report"].id,))
    res = runner.finalize(tid, reviewer="验收员", output_dir=tmp_path)
    assert res["status"] == "已批准"


def test_finalize_force(seed_m5, tmp_path):
    """未清 CRITICAL 时 force=true 直接通过。"""
    db, tid = seed_m5["db"], seed_m5["tender_id"]
    runner = QualityRunner(db)
    runner.run(tid)
    db.execute("UPDATE generation_sections SET content_md = '' "
               "WHERE section_id = 'CH-06-1' AND tender_id = ?", (tid,))
    runner.run(tid)
    res = runner.finalize(tid, reviewer="验收员", force=True, output_dir=tmp_path)
    assert res["status"] == "已批准"
