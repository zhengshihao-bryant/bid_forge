# -*- coding: utf-8 -*-
"""
app/api/routes_generation.py —— 标书生成路由（M4）

端点一览（随批次补充）：

    POST  /api/generation/tenders/{id}/outline          seed 默认大纲 + 实例化章节树
    GET   /api/generation/tenders/{id}/outline          读章节树（含状态）
    GET   /api/generation/tenders/{id}/coverage         需求→章节覆盖统计（M4-02）
    POST  /api/generation/tenders/{id}/jobs             启动后台生成任务（202，可单章节重生成）
    GET   /api/generation/tenders/{id}/jobs             最新任务状态
    GET   /api/generation/tenders/{id}/jobs/{job_id}    指定任务状态
    GET   /api/generation/tenders/{id}/jobs/{job_id}/events   SSE 流式进度
    GET   /api/generation/tenders/{id}/sections/{section_id}  章节草稿明细
    PATCH /api/generation/tenders/{id}/sections/{section_id}  人工编辑（草稿→已编辑→已确认）
    POST  /api/generation/tenders/{id}/sections/{section_id}/regenerate  单章节重生成
    GET   /api/generation/tenders/{id}/response-table   三列需求响应表（json/markdown）
    GET   /api/generation/tenders/{id}/document         组装文档（markdown/docx）
    GET   /api/generation/tenders/{id}/logs             生成日志
"""
from __future__ import annotations

import json
import time

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import FileResponse, StreamingResponse

from .. import config
from ..auth.audit import record_audit
from ..auth.deps import get_current_user, require_permission
from ..db import Database
from ..schemas import now_str
from ..services.generation import OutlineBuilder, run_generation_task, \
    tree_from_flat
from ..services.task_tracker import create_task

router = APIRouter(prefix="/api/generation", tags=["标书生成"])


def _db() -> Database:
    return Database(config.DB_PATH)


def _get_tender_or_404(tender_id: str) -> dict:
    tender = _db().query_one("SELECT * FROM tenders WHERE id = ?", (tender_id,))
    if not tender:
        raise HTTPException(status_code=404, detail="招标项目不存在")
    return tender


def _section_tree(db: Database, tender_id: str) -> list:
    """generation_sections 平铺行 → 树（BidSection model_dump）。"""
    rows = db.query(
        "SELECT * FROM generation_sections WHERE tender_id = ? "
        "ORDER BY level, ord", (tender_id,))
    flat = [db.row_to_bid_section(r) for r in rows]
    tree = tree_from_flat(flat)
    return [s.model_dump(mode="json") for s in tree]


# ═══════════════════════════════════════════════════════════════════════
# M4-01 标书结构规划
# ═══════════════════════════════════════════════════════════════════════
@router.post("/tenders/{tender_id}/outline",
             dependencies=[Depends(require_permission("bid", "generate"))])
def create_outline(tender_id: str,
                   user: dict = Depends(get_current_user)) -> dict:
    """seed 默认大纲 → 实例化章节树 → 落库 generation_sections（重跑幂等）。"""
    _get_tender_or_404(tender_id)
    db = _db()
    builder = OutlineBuilder(db)
    outline_id = builder.seed_default()
    outline = builder.get(outline_id)
    tree = builder.materialize(tender_id, outline)

    # 同 tender 重规划先清旧章节（镜像 matcher 清 canonical_requirements）
    db.execute("DELETE FROM generation_sections WHERE tender_id = ?", (tender_id,))
    db.execute("DELETE FROM requirement_section_maps WHERE tender_id = ?",
               (tender_id,))
    for sec in OutlineBuilder.flatten(tree):
        db.insert("generation_sections", Database.planning_to_row(
            sec, generation_id="", tender_id=tender_id))

    # M4-02 需求→章节映射落库（确定性；coverage 端点读这张表）
    from ..services.generation import RequirementSectionMapper
    stats = RequirementSectionMapper(db).map_all(tender_id)

    record_audit(db, user, "generate_outline", "tender", tender_id,
                 detail=f"outline_id={outline_id} 章节数={len(OutlineBuilder.flatten(tree))}")
    return {"tender_id": tender_id, "outline_id": outline_id,
            "total_sections": len(OutlineBuilder.flatten(tree)),
            "mapped_requirements": stats.mapped,
            "unmapped_requirements": stats.unmapped,
            "sections": [s.model_dump(mode="json") for s in tree]}


@router.get("/tenders/{tender_id}/outline",
            dependencies=[Depends(require_permission("bid", "view"))])
def get_outline(tender_id: str) -> dict:
    """章节树（含状态）。未规划过 → 404 提示先 POST /outline。"""
    _get_tender_or_404(tender_id)
    db = _db()
    n = db.query_one("SELECT COUNT(*) AS n FROM generation_sections "
                     "WHERE tender_id = ?", (tender_id,))["n"]
    if not n:
        raise HTTPException(status_code=404,
                            detail="该招标项目尚未规划章节（请先 POST /api/generation/tenders/{id}/outline）")
    return {"tender_id": tender_id, "sections": _section_tree(db, tender_id)}


# ═══════════════════════════════════════════════════════════════════════
# M4-02 需求→章节覆盖统计
# ═══════════════════════════════════════════════════════════════════════
@router.get("/tenders/{tender_id}/coverage",
            dependencies=[Depends(require_permission("bid", "view"))])
def get_coverage(tender_id: str) -> dict:
    """需求→章节覆盖统计（total/mapped/unmapped/by_section/unmapped_reqs）。"""
    _get_tender_or_404(tender_id)
    from ..services.generation import RequirementSectionMapper

    db = _db()
    if not db.query_one("SELECT 1 FROM generation_sections WHERE tender_id = ?",
                        (tender_id,)):
        raise HTTPException(status_code=404,
                            detail="尚未规划章节（请先 POST /api/generation/tenders/{id}/outline）")
    stats = RequirementSectionMapper(db).coverage(tender_id)
    return {"tender_id": tender_id, **stats.model_dump(mode="json")}


# ═══════════════════════════════════════════════════════════════════════
# M4-06 章节草稿明细
# ═══════════════════════════════════════════════════════════════════════
@router.get("/tenders/{tender_id}/sections/{section_id}",
            dependencies=[Depends(require_permission("bid", "view"))])
def get_section(tender_id: str, section_id: str) -> dict:
    """章节草稿明细（paragraphs/coverage/evidence_refs/warnings/content_md）。"""
    _get_tender_or_404(tender_id)
    db = _db()
    row = db.query_one("SELECT * FROM generation_sections "
                       "WHERE tender_id = ? AND section_id = ?",
                       (tender_id, section_id))
    if not row:
        raise HTTPException(status_code=404, detail="章节不存在")
    if row.get("status") in ("待生成", "生成中"):
        raise HTTPException(status_code=409,
                            detail=f"章节尚未生成完成（status={row.get('status')}）")
    draft = db.row_to_section(row)
    return draft.model_dump(mode="json")


# ═══════════════════════════════════════════════════════════════════════
# M4-07 需求响应表
# ═══════════════════════════════════════════════════════════════════════
@router.get("/tenders/{tender_id}/response-table",
            dependencies=[Depends(require_permission("bid", "view"))])
def get_response_table(tender_id: str, format: str = "json") -> dict:
    """三列需求响应表：?format=json|markdown。"""
    _get_tender_or_404(tender_id)
    from ..services.generation import BidResponseTableBuilder

    builder = BidResponseTableBuilder(_db())
    if format == "markdown":
        return {"tender_id": tender_id, "format": "markdown",
                "content": builder.to_markdown(tender_id)}
    return {"tender_id": tender_id, "format": "json",
            **json.loads(builder.to_json(tender_id))}


@router.get("/tenders/{tender_id}/jobs/{job_id}/events")
def generation_events(tender_id: str, job_id: str,
                      user: dict = Depends(require_permission("bid", "view"))):
    """SSE 流式进度：tail generation_logs，job 终态时推 done 事件关闭流。

    无历史日志时先发一条 job 当前 progress 快照；前端断连可回退轮询
    GET /api/generation/tenders/{id}/jobs/{job_id}。

    M7：bid:view 依赖返回值在 handler 签名捕获——SSE 是长连接，权限检查
    必须在请求进入时执行（依赖随签名参数执行，user 供闭包审计使用）。
    """
    _get_tender_or_404(tender_id)
    db = _db()
    job_row = db.query_one("SELECT * FROM generation_jobs WHERE id = ?",
                           (job_id,))
    if not job_row or job_row["tender_id"] != tender_id:
        raise HTTPException(status_code=404, detail="生成任务不存在")

    _TERMINAL = ("已完成", "部分失败", "失败")

    def event_stream():
        last_id = 0
        while True:
            rows = db.query(
                "SELECT * FROM generation_logs WHERE generation_id = ? "
                "AND id > ? ORDER BY id", (job_id, last_id))
            for r in rows:
                last_id = r["id"]
                yield ("data: " + json.dumps(r, ensure_ascii=False) + "\n\n")
            if not last_id:
                # 尚无日志：先推一条 job 快照，避免客户端空转
                yield ("event: snapshot\ndata: "
                       + json.dumps({"status": job_row["status"],
                                     "progress": job_row["progress"]},
                                    ensure_ascii=False) + "\n\n")
            current = db.query_one(
                "SELECT status FROM generation_jobs WHERE id = ?", (job_id,))
            if current and current["status"] in _TERMINAL:
                yield ("event: done\ndata: "
                       + json.dumps({"status": current["status"]},
                                    ensure_ascii=False) + "\n\n")
                break
            time.sleep(1)

    return StreamingResponse(
        event_stream(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache",
                 "X-Accel-Buffering": "no"})


# ═══════════════════════════════════════════════════════════════════════
# M4-10 生成任务状态机
# ═══════════════════════════════════════════════════════════════════════
@router.post("/tenders/{tender_id}/jobs", status_code=202,
             dependencies=[Depends(require_permission("bid", "generate"))])
def start_generation(tender_id: str, background_tasks: BackgroundTasks,
                     section_id: str = "",
                     user: dict = Depends(get_current_user)) -> dict:
    """启动后台生成任务（?section_id= 传则单章节重生成，否则断点继续全流程）。

    状态轮询 GET /api/generation/tenders/{id}/jobs/{job_id}
    （status: 未生成/生成中/已完成/部分失败/失败）。
    """
    _get_tender_or_404(tender_id)
    from ..services.generation import GenerationJobRunner

    db = _db()
    n = db.query_one("SELECT COUNT(*) AS n FROM generation_sections "
                     "WHERE tender_id = ?", (tender_id,))
    if not n["n"]:
        raise HTTPException(status_code=404,
                            detail="尚未规划章节（请先 POST /api/generation/tenders/{id}/outline）")
    runner = GenerationJobRunner(db)
    latest = runner.latest_job(tender_id)
    if latest and latest.status == "生成中":
        raise HTTPException(status_code=409, detail="该招标项目正在生成中")
    job = runner.create_job(tender_id, section_id=section_id)
    task = create_task(db, "generate", target_id=tender_id, ref_id=job.id,
                       started_by=user["id"])
    background_tasks.add_task(run_generation_task, tender_id, job.id,
                              section_id, "", task["id"])
    record_audit(db, user, "generate_bid", "generation_job", job.id,
                 detail=f"tender_id={tender_id} section_id={section_id or '全量'}")
    return {"tender_id": tender_id, "job_id": job.id, "status": "生成中",
            "task_id": task["id"], "section_id": section_id or "",
            "total_sections": job.total_sections,
            "hint": "轮询 GET /api/generation/tenders/{id}/jobs/{job_id}"}


@router.get("/tenders/{tender_id}/jobs",
            dependencies=[Depends(require_permission("bid", "view"))])
def list_generation_jobs(tender_id: str) -> dict:
    """该招标项目的生成任务列表（最新在前）。"""
    _get_tender_or_404(tender_id)
    rows = _db().query(
        "SELECT * FROM generation_jobs WHERE tender_id = ? "
        "ORDER BY created_at DESC, id DESC LIMIT 20", (tender_id,))
    return {"tender_id": tender_id,
            "jobs": [Database.row_to_job(r).model_dump(mode="json")
                     for r in rows]}


@router.get("/tenders/{tender_id}/jobs/{job_id}",
            dependencies=[Depends(require_permission("bid", "view"))])
def get_generation_job(tender_id: str, job_id: str) -> dict:
    """指定生成任务状态 + 章节级进度（section_states）。"""
    _get_tender_or_404(tender_id)
    from ..services.generation import GenerationJobRunner

    job = GenerationJobRunner(_db()).get_job(job_id)
    if not job or job.tender_id != tender_id:
        raise HTTPException(status_code=404, detail="生成任务不存在")
    return job.model_dump(mode="json")


@router.post("/tenders/{tender_id}/sections/{section_id}/regenerate",
             status_code=202,
             dependencies=[Depends(require_permission("bid", "regenerate"))])
def regenerate_section(tender_id: str, section_id: str,
                       background_tasks: BackgroundTasks,
                       user: dict = Depends(get_current_user)) -> dict:
    """单章节重新生成（走 job，版本 version+1）。"""
    _get_tender_or_404(tender_id)
    db = _db()
    row = db.query_one(
        "SELECT 1 FROM generation_sections WHERE section_id = ? "
        "AND tender_id = ?", (section_id, tender_id))
    if not row:
        raise HTTPException(status_code=404, detail="章节不存在")
    from ..services.generation import GenerationJobRunner

    runner = GenerationJobRunner(db)
    latest = runner.latest_job(tender_id)
    if latest and latest.status == "生成中":
        raise HTTPException(status_code=409, detail="该招标项目正在生成中")
    job = runner.create_job(tender_id, section_id=section_id)
    task = create_task(db, "generate", target_id=tender_id, ref_id=job.id,
                       started_by=user["id"])
    background_tasks.add_task(run_generation_task, tender_id, job.id,
                              section_id, "", task["id"])
    record_audit(db, user, "regenerate_section", "generation_job", job.id,
                 detail=f"tender_id={tender_id} section_id={section_id}")
    return {"tender_id": tender_id, "job_id": job.id, "section_id": section_id,
            "task_id": task["id"], "status": "生成中"}


@router.patch("/tenders/{tender_id}/sections/{section_id}",
              dependencies=[Depends(require_permission("bid", "edit"))])
def edit_section(tender_id: str, section_id: str, body: dict,
                 user: dict = Depends(get_current_user)) -> dict:
    """人工编辑：改 content_md，draft_status 草稿→已编辑（人工确认由前端/M5 处理）。"""
    _get_tender_or_404(tender_id)
    db = _db()
    row = db.query_one("SELECT * FROM generation_sections WHERE section_id = ? "
                       "AND tender_id = ?", (section_id, tender_id))
    if not row:
        raise HTTPException(status_code=404, detail="章节不存在")
    if row.get("status") in ("待生成", "生成中"):
        raise HTTPException(status_code=409,
                            detail="章节尚未生成完成，无法编辑")
    content = body.get("content_md")
    if content is None:
        raise HTTPException(status_code=422, detail="缺少 content_md")
    db.update("generation_sections", "section_id", section_id, {
        "content_md": content, "draft_status": "已编辑",
        "updated_at": now_str()})
    record_audit(db, user, "edit_section", "section", section_id,
                 detail=f"tender_id={tender_id} 字数={len(content)}")
    return {"tender_id": tender_id, "section_id": section_id,
            "status": row.get("status"), "draft_status": "已编辑"}


# ═══════════════════════════════════════════════════════════════════════
# M4-09 文档组装
# ═══════════════════════════════════════════════════════════════════════
@router.get("/tenders/{tender_id}/document",
            dependencies=[Depends(require_permission("bid", "view"))])
def get_document(tender_id: str, format: str = "markdown"):
    """组装完整标书：?format=markdown|docx（docx 走 FileResponse）。"""
    _get_tender_or_404(tender_id)
    from ..services.generation import BidDocumentAssembler

    db = _db()
    n = db.query_one("SELECT COUNT(*) AS n FROM generation_sections "
                     "WHERE tender_id = ? AND content_md != ''", (tender_id,))
    if not n["n"]:
        raise HTTPException(status_code=409,
                            detail="尚无已生成章节（请先 POST /api/generation/tenders/{id}/jobs 启动生成）")
    result = BidDocumentAssembler(db).assemble(tender_id)
    if format == "docx":
        return FileResponse(
            result["docx_path"],
            filename=f"{tender_id}_投标文件.docx",
            media_type="application/vnd.openxmlformats-officedocument."
                       "wordprocessingml.document")
    return {"tender_id": tender_id, "markdown": result["markdown"],
            "total_sections": result["total_sections"],
            "done_sections": result["done_sections"],
            "docx_path": result["docx_path"]}


@router.get("/tenders/{tender_id}/logs",
            dependencies=[Depends(require_permission("bid", "view"))])
def get_generation_logs(tender_id: str, limit: int = 50) -> dict:
    """生成日志（按任务倒序，SSE tail 的读源）。"""
    _get_tender_or_404(tender_id)
    rows = _db().query(
        "SELECT l.* FROM generation_logs l "
        "JOIN generation_jobs j ON j.id = l.generation_id "
        "WHERE j.tender_id = ? ORDER BY l.created_at DESC, l.id DESC LIMIT ?",
        (tender_id, limit))
    return {"tender_id": tender_id, "logs": rows}


__all__ = ["router"]
