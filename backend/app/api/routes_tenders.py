# -*- coding: utf-8 -*-
"""
app/api/routes_tenders.py —— 招标项目路由（M1）

上传安全：
    - 扩展名白名单（pdf/docx/xlsx/png/jpg/jpeg/tif/tiff）
    - 单文件 50MB 上限
    - uuid 落盘（原名入库展示），路径穿越无风险

线程安全：路由全部同步 def（FastAPI 自动进线程池）；DB 每操作独立连接；
需求提取为 BackgroundTasks（状态落库，轮询 GET /{id} 的 extraction_status）。
"""

from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path

from fastapi import (APIRouter, BackgroundTasks, Depends, File, Form,
                     HTTPException, UploadFile)
from pydantic import BaseModel

from .. import config
from ..auth.audit import record_audit
from ..auth.deps import get_current_user, require_permission
from ..db import Database
from ..parsers import SUPPORTED_EXTENSIONS, parse_file
from ..schemas import RequirementType, now_str
from ..services.extraction import run_extraction_task
from ..services.task_tracker import create_task

router = APIRouter(prefix="/api/tenders", tags=["招标项目"])

MAX_FILE_SIZE = 50 * 1024 * 1024      # 50MB / 文件
_CHUNK = 1024 * 1024                  # 写入分块 1MB


class RequirementPatch(BaseModel):
    """人工修订请求体（全部可选；任何修订都置 human_confirmed）。"""
    title: str | None = None
    type: str | None = None
    importance: str | None = None
    status: str | None = None
    response: str | None = None


# ═══════════════════════════════════════════════════════════════════════
# 创建 / 列表 / 详情
# ═══════════════════════════════════════════════════════════════════════
@router.post("", status_code=201,
             dependencies=[Depends(require_permission("tender_doc", "upload"))])
def create_tender(
    files: list[UploadFile] = File(...),
    name: str = Form(""),
    user: dict = Depends(get_current_user),
) -> dict:
    """多文件上传 → 逐文件解析 → 入库。单个文件解析失败不阻塞整单（记录 parse_error）。

    M7：建单人自动成为项目 owner（project_members），普通员工须被添加为
    成员后才能查看该项目的终版（final:* 资源强制成员校验）。
    """
    if not files:
        raise HTTPException(status_code=400, detail="至少上传一个文件")

    tender_id = uuid.uuid4().hex[:12]
    tender_dir = Path(config.RAW_DIR) / tender_id
    parsed_dir = Path(config.PARSED_DIR) / tender_id
    tender_dir.mkdir(parents=True, exist_ok=True)
    parsed_dir.mkdir(parents=True, exist_ok=True)

    db = Database(config.DB_PATH)
    db.insert("tenders", {
        "id": tender_id,
        "name": name.strip() or Path(files[0].filename or "招标文件").stem,
        "created_at": now_str(),
        "extraction_status": "未提取",
        "owner_id": user["id"],
    })
    # M7-02：建单人 = owner（成员行与建单同事务语义：同步紧接写入）
    db.insert("project_members", {
        "project_id": tender_id, "user_id": user["id"], "role": "owner",
        "created_at": now_str(),
    })

    results = []
    for f in files:
        ext = Path(f.filename or "").suffix.lower()
        if ext not in SUPPORTED_EXTENSIONS:
            results.append({"file": f.filename, "ok": False,
                            "error": f"不支持的文件类型 {ext}（支持 {sorted(SUPPORTED_EXTENSIONS)}）"})
            continue

        stored_name = uuid.uuid4().hex + ext
        dest = tender_dir / stored_name
        # 落盘 + SHA-256 + 大小上限
        sha = hashlib.sha256()
        size = 0
        too_large = False
        with dest.open("wb") as out:
            while True:
                chunk = f.file.read(_CHUNK)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_FILE_SIZE:
                    too_large = True
                    break
                sha.update(chunk)
                out.write(chunk)
        if too_large:
            dest.unlink(missing_ok=True)
            results.append({"file": f.filename, "ok": False,
                            "error": f"超过单文件 {MAX_FILE_SIZE // (1024 * 1024)}MB 上限"})
            continue

        # 解析（失败记录 parse_error，不阻塞整单）
        try:
            parsed = parse_file(dest)
            parsed_file = f"{stored_name}.json"
            (parsed_dir / parsed_file).write_text(
                parsed.model_dump_json(indent=2), encoding="utf-8")
            parse_error = ""
        except Exception as e:
            parsed = None
            parsed_file = ""
            parse_error = str(e)[:500]

        doc_id = uuid.uuid4().hex[:12]
        db.insert("documents", {
            "id": doc_id, "tender_id": tender_id,
            "file_name": f.filename or stored_name,
            "stored_name": stored_name, "file_type": ext.lstrip("."),
            "total_pages": parsed.total_pages if parsed else 0,
            "char_count": parsed.char_count if parsed else 0,
            "ocr_pages": json.dumps(parsed.ocr_pages if parsed else []),
            "raw_hash": sha.hexdigest(),
            "parser_version": config.PARSER_VERSION,
            "parse_error": parse_error,
            "parsed_file": parsed_file,
            "created_at": now_str(),
        })
        results.append({
            "file": f.filename, "ok": not parse_error,
            "document_id": doc_id,
            "total_pages": parsed.total_pages if parsed else 0,
            "char_count": parsed.char_count if parsed else 0,
            "ocr_pages": parsed.ocr_pages if parsed else [],
            "sections": len(parsed.sections) if parsed else 0,
            "error": parse_error,
        })

    record_audit(db, user, "upload_tender", "project", tender_id,
                 detail=f"文件数={len(files)} 成功={sum(1 for r in results if r['ok'])}")
    return {"id": tender_id, "name": name.strip() or "招标项目",
            "results": results}


@router.get("",
            dependencies=[Depends(require_permission("project", "view"))])
def list_tenders() -> list[dict]:
    db = Database(config.DB_PATH)
    rows = db.query("SELECT * FROM tenders ORDER BY created_at DESC")
    return [_tender_dict(r) for r in rows]


@router.get("/{tender_id}",
            dependencies=[Depends(require_permission("project", "view"))])
def get_tender(tender_id: str,
               user: dict = Depends(get_current_user)) -> dict:
    db = Database(config.DB_PATH)
    tender = db.query_one("SELECT * FROM tenders WHERE id = ?", (tender_id,))
    if not tender:
        raise HTTPException(status_code=404, detail="招标项目不存在")
    record_audit(db, user, "view_tender_doc", "tender", tender_id,
                 detail=tender["name"])
    docs = db.query("SELECT * FROM documents WHERE tender_id = ? ORDER BY created_at", (tender_id,))
    detail = _tender_dict(tender)
    detail["documents"] = []
    for d in docs:
        meta = {
            "id": d["id"], "file_name": d["file_name"], "file_type": d["file_type"],
            "total_pages": d["total_pages"], "char_count": d["char_count"],
            "ocr_pages": json.loads(d["ocr_pages"] or "[]"),
            "raw_hash": d["raw_hash"], "parser_version": d["parser_version"],
            "parse_error": d["parse_error"], "created_at": d["created_at"],
            "sections": [],
        }
        # 章节树（从解析产物读取，只回树不回落块）
        if d["parsed_file"]:
            pfile = Path(config.PARSED_DIR) / tender_id / d["parsed_file"]
            if pfile.exists():
                try:
                    data = json.loads(pfile.read_text(encoding="utf-8"))
                    meta["sections"] = data.get("sections", [])
                except Exception:
                    pass
        detail["documents"].append(meta)
    return detail


def _tender_dict(row: dict) -> dict:
    return {
        "id": row["id"], "name": row["name"], "created_at": row["created_at"],
        "extraction_status": row["extraction_status"],
        "extraction_progress": row["extraction_progress"],
        "requirement_count": row["requirement_count"],
        "score_point_count": row["score_point_count"],
    }


# ═══════════════════════════════════════════════════════════════════════
# 需求提取 / 需求列表 / 人工修订 / 评分点
# ═══════════════════════════════════════════════════════════════════════
@router.post("/{tender_id}/extract", status_code=202,
             dependencies=[Depends(require_permission("tender_doc", "upload"))])
def extract_requirements(tender_id: str, background_tasks: BackgroundTasks,
                         user: dict = Depends(get_current_user)) -> dict:
    """启动后台需求提取（200 页文档分钟级耗时，不阻塞请求）。状态轮询 GET /{id}。

    M7-05：任务中心登记（extract；started_by=当前用户）。
    """
    db = Database(config.DB_PATH)
    tender = db.query_one("SELECT * FROM tenders WHERE id = ?", (tender_id,))
    if not tender:
        raise HTTPException(status_code=404, detail="招标项目不存在")
    if tender["extraction_status"] == "提取中":
        raise HTTPException(status_code=409, detail="提取任务已在运行中")
    docs = db.query("SELECT COUNT(*) AS n FROM documents WHERE tender_id = ? AND parse_error = ''",
                    (tender_id,))
    if not docs or docs[0]["n"] == 0:
        raise HTTPException(status_code=400, detail="该招标项目没有解析成功的文档")
    db.update("tenders", "id", tender_id, {"extraction_status": "提取中"})
    task = create_task(db, "extract", target_id=tender_id,
                       started_by=user["id"])
    background_tasks.add_task(run_extraction_task, tender_id, task["id"],
                              user["id"])
    return {"tender_id": tender_id, "extraction_status": "提取中",
            "task_id": task["id"],
            "hint": "轮询 GET /api/tenders/{id} 查看 extraction_status / extraction_progress"}


@router.get("/{tender_id}/requirements",
            dependencies=[Depends(require_permission("project", "view"))])
def list_requirements(
    tender_id: str,
    type: str | None = None,
    importance: str | None = None,
    status: str | None = None,
    is_star: bool | None = None,
) -> list[dict]:
    db = Database(config.DB_PATH)
    if not db.query_one("SELECT id FROM tenders WHERE id = ?", (tender_id,)):
        raise HTTPException(status_code=404, detail="招标项目不存在")
    sql = "SELECT * FROM requirements WHERE tender_id = ?"
    params: list = [tender_id]
    if type:
        sql += " AND type = ?"
        params.append(type)
    if importance:
        sql += " AND importance = ?"
        params.append(importance)
    if status:
        sql += " AND status = ?"
        params.append(status)
    if is_star is not None:
        sql += " AND is_star = ?"
        params.append(int(is_star))
    sql += " ORDER BY id"
    return [Database.row_to_requirement(r).model_dump(mode="json")
            for r in db.query(sql, tuple(params))]


@router.patch("/{tender_id}/requirements/{req_id}",
              dependencies=[Depends(require_permission("project", "edit"))])
def patch_requirement(tender_id: str, req_id: str, patch: RequirementPatch,
                      user: dict = Depends(get_current_user)) -> dict:
    """人工修订（title/type/importance/status/response），任何修订都置 human_confirmed。"""
    db = Database(config.DB_PATH)
    row = db.query_one(
        "SELECT * FROM requirements WHERE id = ? AND tender_id = ?", (req_id, tender_id))
    if not row:
        raise HTTPException(status_code=404, detail="需求条目不存在")

    values: dict = {}
    if patch.title is not None:
        values["title"] = patch.title.strip()
    if patch.type is not None:
        try:
            values["type"] = RequirementType(patch.type).value
        except ValueError:
            raise HTTPException(
                status_code=422,
                detail=f"type 必须是: {[t.value for t in RequirementType]}")
    if patch.importance is not None:
        if patch.importance not in ("高", "中", "低"):
            raise HTTPException(status_code=422, detail="importance 必须是 高/中/低")
        values["importance"] = patch.importance
    if patch.status is not None:
        if patch.status not in ("待响应", "已匹配", "已确认", "不适用"):
            raise HTTPException(status_code=422,
                                detail="status 必须是 待响应/已匹配/已确认/不适用")
        values["status"] = patch.status
    if patch.response is not None:
        values["response"] = patch.response
    values["human_confirmed"] = 1
    values["updated_at"] = now_str()
    db.update("requirements", "id", req_id, values)
    record_audit(db, user, "edit_requirement", "requirement", req_id,
                 detail=f"tender_id={tender_id} 修改字段={','.join(sorted(values))}")
    return Database.row_to_requirement(
        db.query_one("SELECT * FROM requirements WHERE id = ?", (req_id,))).model_dump(mode="json")


@router.get("/{tender_id}/score-points",
            dependencies=[Depends(require_permission("project", "view"))])
def list_score_points(tender_id: str) -> list[dict]:
    db = Database(config.DB_PATH)
    if not db.query_one("SELECT id FROM tenders WHERE id = ?", (tender_id,)):
        raise HTTPException(status_code=404, detail="招标项目不存在")
    rows = db.query(
        "SELECT * FROM score_points WHERE tender_id = ? ORDER BY category, id", (tender_id,))
    return [
        {
            "id": r["id"], "tender_id": r["tender_id"], "category": r["category"],
            "item": r["item"], "max_score": r["max_score"], "criteria": r["criteria"],
            "rule_id": r["rule_id"], "weight": r["weight"], "source_ref": r["source_ref"],
            "created_at": r["created_at"],
        }
        for r in rows
    ]
