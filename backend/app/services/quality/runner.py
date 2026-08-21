# -*- coding: utf-8 -*-
"""
quality/runner.py —— M5-16/19 QualityRunner 编排 + finalize 闭环

    run(tender_id, include_llm=False, llm=None)
        装载上下文 → 全量检查 → 5 维评分 → 报告落库（quality_reports +
        quality_issues 两表），返回 {report, issues}。

    finalize(tender_id, reviewer, force=False)
        人工审核闭环：CRITICAL/ERROR 未清（status=待处理）且非 force → 拒绝；
        通过则组装终版（final.docx + final.md + quality-report.json）落盘
        DATA_DIR/out/，报告置"已批准"，写入 review_records 审计快照。

口径：
- 报告 id：QR-{seq:04d}（按 tender 顺序递增）；问题 id：{report_id}-{i:04d}。
- document_version 取最新生成任务 id（无任务时为 "v1"）。
- score 为内部质量指标（见 scoring.py 口径声明）。
"""
from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path
from typing import Optional

from ... import config
from ...db import Database
from ...schemas import now_str
from ..generation.assembler import BidDocumentAssembler
from ..generation.job import GenerationJobRunner
from .checks import all_checks
from .context import build_check_context
from .models import (
    IssueStatus, QualityIssue, QualityReport, ReviewRecord, Severity)
from .report import render_json
from .scoring import score_report

logger = logging.getLogger(__name__)

_LATEST_JOB_SQL = ("SELECT id FROM generation_jobs WHERE tender_id = ? "
                   "ORDER BY created_at DESC, id DESC LIMIT 1")


def default_output_dir() -> Path:
    """终版产物目录：config.DATA_DIR/out（调用时计算，兼容测试改 DATA_DIR）。"""
    return config.DATA_DIR / "out"


class QualityFinalizeError(ValueError):
    """finalize 被拒绝（存在未处理的 CRITICAL/ERROR）。API 层映射为 409。"""


class QualityRunner:
    """M5-16 质量检查编排器（确定性；LLM 语义覆盖为可选增强）。"""

    def __init__(self, db: Optional[Database] = None):
        self.db = db or Database(config.DB_PATH)

    # ------------------------------------------------------------------
    # 运行检查
    # ------------------------------------------------------------------
    def run(self, tender_id: str, include_llm: bool = False,
            llm=None) -> dict:
        """同步执行检查并落库，返回 {"report": QualityReport, "issues": [...]}。"""
        ctx = build_check_context(self.db, tender_id, as_of=now_str()[:10])
        issues = all_checks(ctx, include_llm=include_llm, llm=llm)
        return self._persist(ctx, issues)

    def _persist(self, ctx, issues: list[QualityIssue]) -> dict:
        score, dimensions = score_report(issues)
        seq = self._next_seq(ctx.tender_id)
        report = QualityReport(
            id=f"QR-{seq:04d}", tender_id=ctx.tender_id,
            document_version=self._document_version(ctx.tender_id),
            score=score, dimensions=dimensions,
            counts=_counts(issues), issue_counts=_issue_counts(issues),
            summary=_summary(score, issues), status="草稿",
            created_at=now_str())
        self.db.insert("quality_reports", Database.report_to_row(report))
        for i, issue in enumerate(issues, 1):
            issue.id = f"{report.id}-{i:04d}"
            issue.report_id = report.id
            issue.tender_id = ctx.tender_id
            issue.document_version = report.document_version
            self.db.insert("quality_issues", Database.issue_to_row(issue))
        logger.info("质量检查完成 %s score=%.1f issues=%d",
                    report.id, score, len(issues))
        return {"report": report, "issues": issues}

    # ------------------------------------------------------------------
    # 读取
    # ------------------------------------------------------------------
    def load_latest_report(self, tender_id: str
                           ) -> Optional[tuple[QualityReport, list[QualityIssue]]]:
        row = self.db.query_one(
            "SELECT * FROM quality_reports WHERE tender_id = ? "
            "ORDER BY rowid DESC LIMIT 1", (tender_id,))
        if not row:
            return None
        report = Database.row_to_report(row)
        issues = [Database.row_to_issue(r) for r in self.db.query(
            "SELECT * FROM quality_issues WHERE report_id = ? ORDER BY id",
            (report.id,))]
        return report, issues

    # ------------------------------------------------------------------
    # 终版（M5-19 finalize）
    # ------------------------------------------------------------------
    def finalize(self, tender_id: str, reviewer: str = "",
                 force: bool = False,
                 output_dir: Optional[Path] = None) -> dict:
        """人工审核通过 → 产出终版产物 + 审计留痕。拒绝时抛 QualityFinalizeError。"""
        latest = self.load_latest_report(tender_id)
        if latest is None:
            raise QualityFinalizeError("尚未执行质量检查（请先 POST /check）")
        report, issues = latest
        open_bad = [i for i in issues
                    if i.status == IssueStatus.PENDING
                    and i.severity in (Severity.CRITICAL, Severity.ERROR)]
        if open_bad and not force:
            names = sorted({i.issue_type.value for i in open_bad})
            raise QualityFinalizeError(
                f"存在 {len(open_bad)} 条未处理问题（{', '.join(names)}），"
                f"请先在问题列表处理或传 force=true")

        out_dir = output_dir or default_output_dir()
        out_dir.mkdir(parents=True, exist_ok=True)
        assembled = BidDocumentAssembler(self.db).assemble(
            tender_id, output_dir=out_dir)

        final_md = out_dir / f"{tender_id}_final.md"
        final_docx = out_dir / f"{tender_id}_final.docx"
        report_json = out_dir / f"{tender_id}_quality-report.json"
        final_md.write_text(assembled["markdown"], encoding="utf-8")
        if assembled.get("docx_path") and Path(assembled["docx_path"]).exists():
            shutil.copy(assembled["docx_path"], final_docx)
        report_json.write_text(
            json.dumps(render_json(report, issues), ensure_ascii=False,
                       indent=2), encoding="utf-8")

        # 报告置"已批准"+ 审计快照
        report.status = "已批准"
        report.reviewer = reviewer
        report.review_time = now_str()
        self.db.update("quality_reports", "id", report.id,
                       Database.report_to_row(report))
        audit_note = json.dumps({
            "document_version": report.document_version,
            "score": report.score,
            "counts": report.counts,
            "issue_total": len(issues),
            "artifacts": {
                "final_md": str(final_md), "final_docx": str(final_docx),
                "report_json": str(report_json)},
        }, ensure_ascii=False)
        self.db.insert("review_records", Database.review_to_row(
            ReviewRecord(issue_id=f"FINALIZE:{report.id}", action="批准",
                         reviewer=reviewer, note=audit_note,
                         created_at=now_str())))
        logger.info("finalize 完成 %s reviewer=%s", report.id, reviewer or "—")
        return {
            "report_id": report.id,
            "tender_id": tender_id,
            "status": report.status,
            "score": report.score,
            "reviewer": reviewer,
            "review_time": report.review_time,
            "artifacts": {
                "final_md": str(final_md),
                "final_docx": str(final_docx) if final_docx.exists() else "",
                "report_json": str(report_json),
            },
        }

    # ------------------------------------------------------------------
    # 辅助
    # ------------------------------------------------------------------
    def _next_seq(self, tender_id: str) -> int:
        row = self.db.query_one(
            "SELECT COUNT(*) AS n FROM quality_reports WHERE tender_id = ?",
            (tender_id,))
        return int(row["n"]) + 1

    def _document_version(self, tender_id: str) -> str:
        row = self.db.query_one(_LATEST_JOB_SQL, (tender_id,))
        return row["id"] if row else "v1"


def _counts(issues: list[QualityIssue]) -> dict[str, int]:
    return {
        "critical": sum(1 for i in issues if i.severity == Severity.CRITICAL),
        "error": sum(1 for i in issues if i.severity == Severity.ERROR),
        "warning": sum(1 for i in issues if i.severity == Severity.WARNING),
        "info": sum(1 for i in issues if i.severity == Severity.INFO),
        "pending": sum(1 for i in issues if i.status == IssueStatus.PENDING),
    }


def _issue_counts(issues: list[QualityIssue]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for i in issues:
        counts[i.issue_type.value] = counts.get(i.issue_type.value, 0) + 1
    return counts


def _summary(score: float, issues: list[QualityIssue]) -> str:
    c = _counts(issues)
    return (f"质量检查完成：score={score}，"
            f"严重 {c['critical']} / 错误 {c['error']} / "
            f"警告 {c['warning']} / 提示 {c['info']} / 待确认 {c['pending']}")


__all__ = ["QualityRunner", "QualityFinalizeError", "default_output_dir"]
