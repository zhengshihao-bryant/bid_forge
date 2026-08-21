# -*- coding: utf-8 -*-
"""
app/api/routes_workbench.py —— 工作台聚合路由（M6）

端点一览：

    GET /api/workbench/projects           项目列表 + 全流程状态聚合
    GET /api/workbench/projects/{id}      单项目概览（+ 文档明细 + 待处理问题）

口径：聚合只读、不落库；所有计数走 SQL 聚合（本地 SQLite、项目量级小，
逐项目查询可接受）；六阶段状态由数据派生（pending/in_progress/done/warning/error），
供前端画阶段进度条。企业资料（KB）为全局阶段，随每次响应带出。
"""
from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException

from .. import config
from ..db import Database

router = APIRouter(prefix="/api/workbench", tags=["工作台"])

_STAGE_KEYS = ("docs", "extract", "kb", "match", "generate", "quality")
_STAGE_LABELS = ("招标文件", "需求解析", "企业资料", "需求匹配", "标书生成", "质量检查")


def _db() -> Database:
    return Database(config.DB_PATH)


def _get_tender_or_404(tender_id: str) -> dict:
    tender = _db().query_one("SELECT * FROM tenders WHERE id = ?", (tender_id,))
    if not tender:
        raise HTTPException(status_code=404, detail="招标项目不存在")
    return tender


def _kb_stats(db: Database) -> dict:
    """全局企业资料统计（能力卡是匹配/生成的公共输入）。"""
    mats = db.query_one("SELECT COUNT(*) AS n FROM kb_materials", ())
    caps = db.query_one("SELECT COUNT(*) AS n FROM capabilities", ())
    ready = db.query_one(
        "SELECT COUNT(*) AS n FROM kb_materials WHERE process_status = '已完成'", ())
    return {"materials": (mats or {}).get("n") or 0,
            "capabilities": (caps or {}).get("n") or 0,
            "ready_materials": (ready or {}).get("n") or 0}


def _final_exists(tender_id: str) -> bool:
    """终版产物存在性（finalize 三件套落 DATA_DIR/out/）。"""
    return (config.DATA_DIR / "out" / f"{tender_id}_final.md").exists()


def _match_distribution(db: Database, tender_id: str) -> dict:
    dist = {"FULL": 0, "PARTIAL": 0, "MISSING": 0, "UNKNOWN": 0}
    for r in db.query(
            "SELECT status, COUNT(*) AS n FROM requirement_matches "
            "WHERE tender_id = ? GROUP BY status", (tender_id,)):
        if r["status"] in dist:
            dist[r["status"]] = r["n"]
    return dist


def _stages(db: Database, tender_id: str, base: dict, kb: dict) -> list:
    """六阶段状态派生（key/label/status/summary；summary 供前端直接展示）。"""
    out = []
    for key, label in zip(_STAGE_KEYS, _STAGE_LABELS):
        if key == "docs":
            d = base["documents"]
            if not d["total"]:
                status, summary = "pending", "未上传招标文件"
            elif d["ok"] == d["total"]:
                status, summary = "done", f"{d['ok']}/{d['total']} 解析成功"
            else:
                status, summary = "warning", f"{d['ok']}/{d['total']} 解析成功"
            out.append({"key": key, "label": label, "status": status,
                        "summary": summary})
        elif key == "extract":
            es = base["extraction_status"]
            status = {"已完成": "done", "已提取": "done",  # 已提取：旧种子数据口径，兼容
                      "提取中": "in_progress",
                      "失败": "error"}.get(es, "pending")
            out.append({"key": key, "label": label, "status": status,
                        "summary": (f"{base['requirement_count']} 条需求"
                                    if es in ("已完成", "已提取") else es)})
        elif key == "kb":
            status = "done" if kb["capabilities"] else "pending"
            out.append({"key": key, "label": label, "status": status,
                        "summary": f"{kb['capabilities']} 张能力卡"})
        elif key == "match":
            m = base["matching"]
            status = {"已完成": "done", "匹配中": "in_progress",
                      "失败": "error"}.get(m["status"], "pending")
            dist = m["distribution"]
            out.append({"key": key, "label": label, "status": status,
                        "summary": (f"FULL {dist['FULL']} / PARTIAL {dist['PARTIAL']} / "
                                    f"MISSING {dist['MISSING']} / UNKNOWN {dist['UNKNOWN']}"
                                    if m["match_count"] else m["status"])})
        elif key == "generate":
            g = base["generation"]
            status = {"已完成": "done", "生成中": "in_progress",
                      "部分失败": "warning", "失败": "error"}.get(g["status"], "pending")
            out.append({"key": key, "label": label, "status": status,
                        "summary": (f"{g['done_sections']}/{g['total_sections']} 章节"
                                    if g["total_sections"] else g["status"])})
        else:  # quality
            q = base["quality"]
            if not q["report_id"]:
                status, summary = "pending", "未检查"
            elif q["pending_issues"]:
                status, summary = "warning", f"{q['score']} 分 · {q['pending_issues']} 待处理"
            else:
                status, summary = "done", f"{q['score']} 分"
            out.append({"key": key, "label": label, "status": status,
                        "summary": summary})
    return out


def _project_summary(db: Database, tender: dict, kb: dict) -> dict:
    """单项目全流程聚合（列表与概览共用）。"""
    tid = tender["id"]
    docs = db.query_one(
        "SELECT COUNT(*) AS total, "
        "SUM(CASE WHEN parse_error = '' THEN 1 ELSE 0 END) AS ok, "
        "SUM(CASE WHEN ocr_pages != '[]' THEN 1 ELSE 0 END) AS ocr "
        "FROM documents WHERE tender_id = ?", (tid,)) or {}
    mrun = db.query_one("SELECT * FROM matching_runs WHERE tender_id = ?", (tid,)) or {}
    job = db.query_one(
        "SELECT * FROM generation_jobs WHERE tender_id = ? "
        "ORDER BY created_at DESC, id DESC LIMIT 1", (tid,)) or {}
    secs = db.query_one(
        "SELECT COUNT(*) AS total, "
        "SUM(CASE WHEN status = '已完成' THEN 1 ELSE 0 END) AS done "
        "FROM generation_sections WHERE tender_id = ?", (tid,)) or {}
    report = db.query_one(
        "SELECT * FROM quality_reports WHERE tender_id = ? "
        "ORDER BY created_at DESC, id DESC LIMIT 1", (tid,)) or {}
    pending = db.query_one(
        "SELECT COUNT(*) AS n FROM quality_issues "
        "WHERE tender_id = ? AND status = '待处理'", (tid,)) or {}
    base = {
        "id": tid, "name": tender["name"], "created_at": tender["created_at"],
        "extraction_status": tender["extraction_status"],
        "requirement_count": tender["requirement_count"],
        "documents": {"total": docs.get("total") or 0,
                      "ok": docs.get("ok") or 0,
                      "ocr": docs.get("ocr") or 0},
        "matching": {"status": mrun.get("status") or "未匹配",
                     "canonical_count": mrun.get("canonical_count") or 0,
                     "match_count": mrun.get("match_count") or 0,
                     "distribution": _match_distribution(db, tid)},
        "generation": {"status": job.get("status") or "未生成",
                       "job_id": job.get("id") or "",
                       "total_sections": secs.get("total") or 0,
                       "done_sections": secs.get("done") or 0},
        "quality": {"report_id": report.get("id") or "",
                    "score": report.get("score") or 0,
                    "status": report.get("status") or "",
                    "pending_issues": pending.get("n") or 0},
        "delivery": {"finalized": _final_exists(tid)},
    }
    base["stages"] = _stages(db, tid, base, kb)
    return base


# ═══════════════════════════════════════════════════════════════════════
# 项目列表 / 单项目概览
# ═══════════════════════════════════════════════════════════════════════
@router.get("/projects")
def list_projects() -> dict:
    """项目列表 + 全流程状态聚合（工作台首页卡片数据源）。"""
    db = _db()
    kb = _kb_stats(db)
    rows = db.query("SELECT * FROM tenders ORDER BY created_at DESC")
    return {"kb": kb,
            "projects": [_project_summary(db, r, kb) for r in rows]}


@router.get("/projects/{tender_id}")
def project_overview(tender_id: str) -> dict:
    """单项目概览：全流程聚合 + 文档明细 + 待处理问题前 5 条。"""
    tender = _get_tender_or_404(tender_id)
    db = _db()
    kb = _kb_stats(db)
    summary = _project_summary(db, tender, kb)
    docs = [{
        "id": d["id"], "file_name": d["file_name"], "file_type": d["file_type"],
        "total_pages": d["total_pages"], "char_count": d["char_count"],
        "ocr_pages": json.loads(d["ocr_pages"] or "[]"),
        "parse_error": d["parse_error"], "created_at": d["created_at"],
    } for d in db.query(
        "SELECT * FROM documents WHERE tender_id = ? ORDER BY created_at",
        (tender_id,))]
    issues = [{
        "id": i["id"], "severity": i["severity"], "issue_type": i["issue_type"],
        "message": i["message"], "section_id": i["section_id"],
        "requirement_id": i["requirement_id"],
        "status": i["status"], "created_at": i["created_at"],
    } for i in db.query(
        "SELECT * FROM quality_issues WHERE tender_id = ? AND status = '待处理' "
        "ORDER BY CASE severity "
        "WHEN 'CRITICAL' THEN 0 WHEN 'ERROR' THEN 1 "
        "WHEN 'WARNING' THEN 2 ELSE 3 END LIMIT 5", (tender_id,))]
    summary["kb"] = kb
    summary["documents_detail"] = docs
    summary["pending_issues"] = issues
    return summary


__all__ = ["router"]
