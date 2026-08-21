# -*- coding: utf-8 -*-
"""
tests/test_m6_workbench.py —— M6 工作台聚合路由 + 生成 SSE 端点测试

覆盖：
- GET /api/workbench/projects：空库空列表；聚合字段正确（文档统计/匹配
  分布/章节进度/质量快照/六阶段派生）
- GET /api/workbench/projects/{id}：文档明细 + 待处理问题前 N 条 + 404
- GET /api/generation/tenders/{id}/jobs/{job_id}/events：已完成 job 立即
  推历史日志 + done 事件关闭流；未知 job/tender 404
"""
from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app import config  # noqa: E402
from app.api.main import app  # noqa: E402
from app.db import Database  # noqa: E402


def _doc_row(doc_id: str, tid: str, file_name: str, parse_error: str,
             ocr_pages: str) -> dict:
    return {
        "id": doc_id, "tender_id": tid, "file_name": file_name,
        "stored_name": f"{doc_id}.pdf", "file_type": "pdf",
        "total_pages": 10 if not parse_error else 0,
        "char_count": 100 if not parse_error else 0,
        "ocr_pages": ocr_pages, "raw_hash": "", "parser_version": "1.0.0",
        "parse_error": parse_error,
        "parsed_file": f"{doc_id}.json" if not parse_error else "",
        "created_at": "2026-01-01 00:00:00",
    }


# ═══════════════════════════════════════════════════════════════════════
# GET /api/workbench/projects
# ═══════════════════════════════════════════════════════════════════════
def test_workbench_empty_list(tmp_env):
    """空库 → projects 空列表 + kb 统计归零。"""
    with TestClient(app) as c:
        r = c.get("/api/workbench/projects")
    assert r.status_code == 200
    body = r.json()
    assert body["projects"] == []
    assert body["kb"]["materials"] == 0
    assert body["kb"]["capabilities"] == 0


def test_workbench_projects_aggregate(m5_api):
    """聚合字段正确：文档统计/匹配分布/章节进度/质量快照/六阶段派生。"""
    data, client = m5_api
    db, tid = data["db"], data["tender_id"]

    # 补 documents（1 成功 1 失败 1 OCR）+ 质量报告行，验证聚合计数
    db.insert("documents", _doc_row("DOC-1", tid, "招标文件.pdf", "", "[]"))
    db.insert("documents", _doc_row("DOC-2", tid, "扫描件.pdf", "解析失败", "[1,2]"))
    db.insert("quality_reports", {
        "id": "QR-X", "tender_id": tid, "document_version": "1",
        "score": 88.5, "dimensions": "[]", "counts": "{}", "issue_counts": "{}",
        "summary": "test", "status": "草稿", "reviewer": "", "review_time": "",
        "created_at": "2026-01-01 00:00:00"})

    r = client.get("/api/workbench/projects")
    assert r.status_code == 200
    body = r.json()
    proj = next(p for p in body["projects"] if p["id"] == tid)
    # 基础
    assert proj["name"] == "M5质量检查测试项目"
    # 文档
    assert proj["documents"] == {"total": 2, "ok": 1, "ocr": 1}
    # 匹配分布（离线 seed 直接写 requirement_matches，无 matching_runs 行）
    dist = proj["matching"]["distribution"]
    assert sum(dist.values()) == 33
    assert proj["matching"]["status"] == "未匹配"
    # 生成（seed_m5 全章节生成完成）
    assert proj["generation"]["status"] == "已完成"
    assert proj["generation"]["done_sections"] == 26
    assert proj["generation"]["total_sections"] == 26
    # 质量快照
    assert proj["quality"]["report_id"] == "QR-X"
    assert proj["quality"]["score"] == 88.5
    assert proj["quality"]["pending_issues"] == 0
    # 交付（m5_api 的 DATA_DIR 为临时目录，无产物）
    assert proj["delivery"]["finalized"] is False
    # 六阶段派生
    assert len(proj["stages"]) == 6
    by_key = {s["key"]: s for s in proj["stages"]}
    assert by_key["docs"]["status"] == "warning"       # 1/2 解析成功
    assert by_key["extract"]["status"] == "pending"    # 未提取
    assert by_key["kb"]["status"] == "done"            # 能力卡 > 0
    assert by_key["match"]["status"] == "pending"      # 无 matching_runs 行
    assert by_key["generate"]["status"] == "done"
    assert by_key["quality"]["status"] == "done"       # 有报告且无待处理
    # kb 全局统计（seed_m3_kb 种子能力卡）
    assert body["kb"]["capabilities"] > 0


# ═══════════════════════════════════════════════════════════════════════
# GET /api/workbench/projects/{id}
# ═══════════════════════════════════════════════════════════════════════
def test_workbench_project_overview(m5_api):
    """单项目概览：文档明细 + 待处理问题前 N 条 + 未知项目 404。"""
    data, client = m5_api
    db, tid = data["db"], data["tender_id"]
    db.insert("documents", _doc_row("DOC-1", tid, "招标文件.pdf", "", "[]"))
    db.insert("quality_reports", {
        "id": "QR-X", "tender_id": tid, "document_version": "1",
        "score": 88.5, "dimensions": "[]", "counts": "{}", "issue_counts": "{}",
        "summary": "test", "status": "草稿", "reviewer": "", "review_time": "",
        "created_at": "2026-01-01 00:00:00"})
    db.insert("quality_issues", {
        "id": "QI-1", "report_id": "QR-X", "tender_id": tid,
        "document_version": "1", "section_id": "CH-01-1",
        "requirement_id": "", "issue_type": "FACT_MISMATCH",
        "severity": "CRITICAL", "status": "待处理", "message": "设备数量不一致",
        "source_refs": "[]", "suggestion": "", "autofixable": 0,
        "created_at": "2026-01-01 00:00:00"})

    r = client.get(f"/api/workbench/projects/{tid}")
    assert r.status_code == 200
    body = r.json()
    assert len(body["documents_detail"]) == 1
    assert body["documents_detail"][0]["file_name"] == "招标文件.pdf"
    assert len(body["pending_issues"]) == 1
    assert body["pending_issues"][0]["severity"] == "CRITICAL"
    assert body["pending_issues"][0]["message"] == "设备数量不一致"
    assert body["quality"]["pending_issues"] == 1

    assert client.get("/api/workbench/projects/T-NOPE").status_code == 404


# ═══════════════════════════════════════════════════════════════════════
# GET /api/generation/tenders/{id}/jobs/{job_id}/events（SSE）
# ═══════════════════════════════════════════════════════════════════════
def test_generation_events_sse(m5_api):
    """已完成 job → 推历史日志 + done 事件关闭流；未知 job/tender 404。"""
    data, client = m5_api
    tid = data["tender_id"]
    job = data["job"]
    with client.stream(
            "GET",
            f"/api/generation/tenders/{tid}/jobs/{job.id}/events") as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        content = "".join(resp.iter_text())
    assert "data:" in content            # 历史日志行
    assert "event: done" in content      # 终态关闭
    assert "已完成" in content

    assert client.get(
        f"/api/generation/tenders/{tid}/jobs/NO-JOB/events").status_code == 404
    assert client.get(
        f"/api/generation/tenders/T-NOPE/jobs/{job.id}/events"
    ).status_code == 404


# ═══════════════════════════════════════════════════════════════════════
# 回归：POST /outline × documents 行（M6 验收暴露）
# ═══════════════════════════════════════════════════════════════════════
def test_outline_with_documents_rows(m5_api):
    """documents 有 parsed_file 行时 POST /outline 不 500，且 source_refs 命中。

    M6 验收暴露：load_tender_doc_sections 曾以 str.startswith(WindowsPath)
    抛 TypeError（M4 时 documents 无行未触发）；相对 parsed_file 须按
    PARSED_DIR/{tender_id}/ 解析（与 routes_tenders/extraction 入库口径一致）。
    """
    import json

    from app import config

    data, client = m5_api
    db, tid = data["db"], data["tender_id"]

    # 真实 ParsedDocument JSON：落 PARSED_DIR/{tender_id}/（相对路径分支）
    parsed_dir = Path(config.PARSED_DIR) / tid
    parsed_dir.mkdir(parents=True, exist_ok=True)
    doc_json = {"schema_version": "1.0.0", "file_name": "招标文件.pdf",
                "file_type": "pdf", "total_pages": 10, "char_count": 100,
                "ocr_pages": [],
                "sections": [{"id": "S0001", "title": "第四章 技术要求",
                              "level": 1, "order": 1}],
                "blocks": []}
    rel_file = parsed_dir / "DOC-X.json"
    rel_file.write_text(json.dumps(doc_json, ensure_ascii=False),
                        encoding="utf-8")

    db.insert("documents", _doc_row("DOC-X", tid, "招标文件.pdf", "", "[]"))
    # 绝对路径分支：parsed_file 存全路径（兼容历史数据）
    db.insert("documents", _doc_row("DOC-Y", tid, "扫描件.pdf", "", "[1,2]"))
    db.execute("UPDATE documents SET parsed_file = ? WHERE id = 'DOC-Y'",
               (str(rel_file),))

    r = client.post(f"/api/generation/tenders/{tid}/outline")
    assert r.status_code == 200
    body = r.json()
    assert body["total_sections"] == 26
    # 相对/绝对两条路径读到同一份 ParsedDocument → source_refs 命中
    assert any("第四章 技术要求" in ref
               for s in body["sections"] for ref in s.get("source_refs", []))
