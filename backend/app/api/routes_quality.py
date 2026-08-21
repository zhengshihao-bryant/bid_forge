# -*- coding: utf-8 -*-
"""
app/api/routes_quality.py —— 标书质量检查路由（M5）

端点一览（prefix /api/quality）：

    POST  /api/quality/tenders/{id}/check?include_llm=   同步执行质量检查（落库 QualityReport）
    GET   /api/quality/tenders/{id}/reports              报告列表（最新在前）
    GET   /api/quality/reports/{report_id}               报告详情（含 issues）
    GET   /api/quality/tenders/{id}/issues?status=       问题列表（可过滤）
    PATCH /api/quality/issues/{issue_id}                 人工处理：{status, reviewer, note} → 审计
    POST  /api/quality/issues/{issue_id}/autofix         格式自动修复 → 重查该章节
    POST  /api/quality/tenders/{id}/finalize             终版闭环：{reviewer, force?}
    GET   /api/quality/tenders/{id}/final?format=        终版产物（json/docx/markdown）

口径：
- 确定性检查为主；include_llm=true 时附加 LLM 语义覆盖二次审查（仅判覆盖，
  数字/证书/证据存在性仍由确定性程序负责；无 Key 时 FakeLLM 空返回 → 不新增）。
- finalize 在 CRITICAL/ERROR 未清且非 force 时返回 409；产物落 DATA_DIR/out/。
"""
from __future__ import annotations

import json
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from .. import config
from ..db import Database
from ..schemas import now_str
from ..services.llm import create_llm_client
from ..services.quality.autofix import AutoFixer
from ..services.quality.checks.format_check import check_format
from ..services.quality.context import build_check_context
from ..services.quality.models import IssueStatus, IssueType, ReviewRecord
from ..services.quality.runner import (
    QualityFinalizeError, QualityRunner, default_output_dir)

router = APIRouter(prefix="/api/quality", tags=["标书质量检查"])

_ACTION_MAP = {
    IssueStatus.CONFIRMED.value: "确认",
    IssueStatus.IGNORED.value: "忽略",
    IssueStatus.FIXED.value: "修复",
}


def _db() -> Database:
    return Database(config.DB_PATH)


def _get_tender_or_404(tender_id: str) -> dict:
    tender = _db().query_one("SELECT * FROM tenders WHERE id = ?", (tender_id,))
    if not tender:
        raise HTTPException(status_code=404, detail="招标项目不存在")
    return tender


def _no_sections_sql() -> str:
    return ("SELECT COUNT(*) AS n FROM generation_sections "
            "WHERE tender_id = ? AND content_md != ''")


def _issue_json(row: dict) -> dict:
    return Database.row_to_issue(row).model_dump(mode="json")


# ═══════════════════════════════════════════════════════════════════════
# M5-16 检查执行
# ═══════════════════════════════════════════════════════════════════════
@router.post("/tenders/{tender_id}/check")
def run_quality_check(tender_id: str, include_llm: bool = False) -> dict:
    """同步执行全量质量检查并落库，返回报告 + 问题列表。"""
    _get_tender_or_404(tender_id)
    db = _db()
    if not db.query_one(_no_sections_sql(), (tender_id,))["n"]:
        raise HTTPException(
            status_code=409,
            detail="尚无已生成章节（请先 POST /api/generation/tenders/{id}/jobs 启动生成）")
    llm = create_llm_client() if include_llm else None
    result = QualityRunner(db).run(tender_id, include_llm=include_llm, llm=llm)
    return {
        "tender_id": tender_id,
        "report": result["report"].model_dump(mode="json"),
        "issues": [i.model_dump(mode="json") for i in result["issues"]],
    }


# ═══════════════════════════════════════════════════════════════════════
# M5-15 报告读取
# ═══════════════════════════════════════════════════════════════════════
@router.get("/tenders/{tender_id}/reports")
def list_quality_reports(tender_id: str) -> dict:
    """该招标项目的质量报告列表（最新在前）。"""
    _get_tender_or_404(tender_id)
    rows = _db().query(
        "SELECT * FROM quality_reports WHERE tender_id = ? "
        "ORDER BY created_at DESC, id DESC", (tender_id,))
    return {"tender_id": tender_id,
            "reports": [Database.row_to_report(r).model_dump(mode="json")
                        for r in rows]}


@router.get("/reports/{report_id}")
def get_quality_report(report_id: str) -> dict:
    """报告详情（含全部问题）。"""
    db = _db()
    row = db.query_one("SELECT * FROM quality_reports WHERE id = ?", (report_id,))
    if not row:
        raise HTTPException(status_code=404, detail="报告不存在")
    issues = db.query("SELECT * FROM quality_issues WHERE report_id = ? "
                      "ORDER BY id", (report_id,))
    return {"report": Database.row_to_report(row).model_dump(mode="json"),
            "issues": [_issue_json(r) for r in issues]}


# ═══════════════════════════════════════════════════════════════════════
# M5-16 问题处理（人工确认 / 自动修复）
# ═══════════════════════════════════════════════════════════════════════
@router.get("/tenders/{tender_id}/issues")
def list_quality_issues(tender_id: str, status: str = "") -> dict:
    """问题列表（?status=待处理|已确认|已忽略|已修复 过滤）。"""
    _get_tender_or_404(tender_id)
    db = _db()
    if status:
        rows = db.query("SELECT * FROM quality_issues WHERE tender_id = ? "
                        "AND status = ? ORDER BY id", (tender_id, status))
    else:
        rows = db.query("SELECT * FROM quality_issues WHERE tender_id = ? "
                        "ORDER BY id", (tender_id,))
    return {"tender_id": tender_id, "status_filter": status,
            "issues": [_issue_json(r) for r in rows]}


@router.patch("/issues/{issue_id}")
def update_issue_status(issue_id: str, body: dict) -> dict:
    """人工处理问题：{status: 已确认|已忽略|已修复, reviewer?, note?} → 审计留痕。"""
    db = _db()
    row = db.query_one("SELECT * FROM quality_issues WHERE id = ?", (issue_id,))
    if not row:
        raise HTTPException(status_code=404, detail="问题不存在")
    status = body.get("status")
    allowed = {s.value for s in IssueStatus if s != IssueStatus.PENDING}
    if status not in allowed:
        raise HTTPException(
            status_code=422,
            detail=f"status 非法（可选：{'/'.join(sorted(allowed))}）")
    db.update("quality_issues", "id", issue_id, {"status": status})
    db.insert("review_records", Database.review_to_row(ReviewRecord(
        issue_id=issue_id, action=_ACTION_MAP.get(status, status),
        reviewer=body.get("reviewer", ""), note=body.get("note", ""),
        created_at=now_str())))
    return {"issue_id": issue_id, "status": status,
            "action": _ACTION_MAP.get(status, status)}


@router.post("/issues/{issue_id}/autofix")
def autofix_issue(issue_id: str) -> dict:
    """格式问题自动修复：只改格式 → 标记已修复 → 重查该章节剩余格式问题。"""
    db = _db()
    row = db.query_one("SELECT * FROM quality_issues WHERE id = ?", (issue_id,))
    if not row:
        raise HTTPException(status_code=404, detail="问题不存在")
    issue = Database.row_to_issue(row)
    if issue.issue_type != IssueType.FORMAT_ERROR or not issue.autofixable:
        raise HTTPException(
            status_code=422,
            detail="该问题不支持自动修复（仅格式类问题可修复）")
    section_id = issue.section_id
    sec = db.query_one("SELECT * FROM generation_sections "
                       "WHERE section_id = ? AND tender_id = ?",
                       (section_id, issue.tender_id))
    if not sec:
        raise HTTPException(status_code=404, detail="章节不存在")

    content = sec.get("content_md") or ""
    fixed = AutoFixer().apply(content, issue)
    changed = fixed != content
    if changed:
        db.update("generation_sections", "section_id", section_id,
                  {"content_md": fixed})
    db.update("quality_issues", "id", issue_id, {"status": "已修复"})
    db.insert("review_records", Database.review_to_row(ReviewRecord(
        issue_id=issue_id, action="自动修复", reviewer="",
        note="AutoFixer 格式修复", created_at=now_str())))

    ctx = build_check_context(db, issue.tender_id, as_of=now_str()[:10])
    remaining = [i for i in check_format(ctx) if i.section_id == section_id]
    return {"issue_id": issue_id, "section_id": section_id, "fixed": changed,
            "content_preview": fixed[:200],
            "remaining_format_issues":
                [i.model_dump(mode="json") for i in remaining]}


# ═══════════════════════════════════════════════════════════════════════
# M5-19 终版闭环
# ═══════════════════════════════════════════════════════════════════════
@router.post("/tenders/{tender_id}/finalize")
def finalize_tender(tender_id: str, body: Optional[dict] = None) -> dict:
    """人工审核通过 → final.docx + final.md + quality-report.json + 审计。

    存在未处理的 CRITICAL/ERROR 且非 force → 409；未执行过检查 → 409。
    """
    _get_tender_or_404(tender_id)
    body = body or {}
    try:
        return QualityRunner(_db()).finalize(
            tender_id, reviewer=body.get("reviewer", ""),
            force=bool(body.get("force", False)))
    except QualityFinalizeError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.get("/tenders/{tender_id}/final")
def get_final_artifact(tender_id: str, format: str = "json"):
    """终版产物：?format=json|markdown 返回内容，docx 走 FileResponse。"""
    _get_tender_or_404(tender_id)
    out_dir = default_output_dir()

    if format == "docx":
        path = out_dir / f"{tender_id}_final.docx"
        if not path.exists():
            raise HTTPException(
                status_code=409,
                detail="尚无终版产物（请先 POST /api/quality/tenders/{id}/finalize）")
        return FileResponse(
            path, filename=f"{tender_id}_final.docx",
            media_type="application/vnd.openxmlformats-officedocument."
                       "wordprocessingml.document")

    path = out_dir / (f"{tender_id}_final.md" if format == "markdown"
                      else f"{tender_id}_quality-report.json")
    if not path.exists():
        raise HTTPException(
            status_code=409,
            detail="尚无终版产物（请先 POST /api/quality/tenders/{id}/finalize）")
    if format == "markdown":
        return {"tender_id": tender_id, "format": "markdown",
                "content": path.read_text(encoding="utf-8")}
    return json.loads(path.read_text(encoding="utf-8"))


__all__ = ["router"]
