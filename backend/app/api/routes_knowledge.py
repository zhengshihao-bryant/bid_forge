# -*- coding: utf-8 -*-
"""
app/api/routes_knowledge.py —— 企业知识库路由（M2）

端点一览：

    POST   /materials                多文件上传 + category → 解析入库（201；单文件失败不阻塞）
    GET    /materials                列表（category/status 过滤）
    GET    /materials/{id}           详情 + 章节树
    GET    /materials/{id}/chunks    内容块分页（不含 embedding）
    POST   /materials/{id}/process   后台任务：切块 + 嵌入 + 能力卡提取（202；状态轮询）
    DELETE /materials/{id}           级联删除（chunks/卡片/Milvus/落盘文件）
    GET    /materials/{id}/capabilities  资料的能力卡
    GET    /capabilities             全局能力卡（category/source_doc 过滤）
    PATCH  /capabilities/{cap_id}    人工修订（attributes 整体替换）
    GET    /search                   语义检索（engine 标识降级路径 + 四元溯源）

上传安全沿用 M1：扩展名白名单 + 单文件 50MB + uuid 落盘；
处理任务为 BackgroundTasks（状态落库，前端轮询 process_status）。
"""

from __future__ import annotations

import hashlib
import json
import shutil
import uuid
from pathlib import Path

from fastapi import (APIRouter, BackgroundTasks, Depends, File, Form,
                     HTTPException, Query, UploadFile)

from .. import config
from ..auth.audit import record_audit
from ..auth.deps import get_current_user, require_permission
from ..db import Database
from ..parsers import SUPPORTED_EXTENSIONS, parse_file
from ..schemas import CapabilityCategory, CapabilityPatch, now_str
from ..services.capability_extractor import run_kb_task
from ..services.kb_versions import record_version
from ..services.task_tracker import create_task
from ..services.vector_store import create_search_service, get_milvus_store

router = APIRouter(prefix="/api/knowledge", tags=["知识库"])

MAX_FILE_SIZE = 50 * 1024 * 1024      # 50MB / 文件（沿用 M1）
_CHUNK = 1024 * 1024                  # 写入分块 1MB
_STATUSES = ("未处理", "处理中", "已完成", "失败")


def _check_category(value: str) -> str:
    try:
        return CapabilityCategory(value).value
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail=f"category 必须是: {[c.value for c in CapabilityCategory]}")


def _material_dict(row: dict) -> dict:
    return {
        "id": row["id"], "category": row["category"], "file_name": row["file_name"],
        "file_type": row["file_type"], "total_pages": row["total_pages"],
        "char_count": row["char_count"], "ocr_pages": json.loads(row["ocr_pages"] or "[]"),
        "raw_hash": row["raw_hash"], "parser_version": row["parser_version"],
        "parse_error": row["parse_error"],
        "process_status": row["process_status"], "process_progress": row["process_progress"],
        "chunk_count": row["chunk_count"], "capability_count": row["capability_count"],
        "index_status": row["index_status"], "created_at": row["created_at"],
    }


def _get_material_or_404(material_id: str) -> dict:
    db = Database(config.DB_PATH)
    mat = db.query_one("SELECT * FROM kb_materials WHERE id = ?", (material_id,))
    if not mat:
        raise HTTPException(status_code=404, detail="资料不存在")
    return mat


# ═══════════════════════════════════════════════════════════════════════
# 上传 / 列表 / 详情 / 内容块
# ═══════════════════════════════════════════════════════════════════════
@router.post("/materials", status_code=201,
             dependencies=[Depends(require_permission("knowledge", "upload"))])
def create_material(
    files: list[UploadFile] = File(...),
    category: str = Form(...),
    user: dict = Depends(get_current_user),
) -> dict:
    """多文件上传（同一类别）→ 逐文件落盘 + 同步解析 + 入库。

    单个文件失败不阻塞整批（results 逐项记录）；解析失败资料入库但
    parse_error 非空，process 端点会拒绝处理。
    """
    cat = _check_category(category)
    if not files:
        raise HTTPException(status_code=400, detail="至少上传一个文件")

    db = Database(config.DB_PATH)
    results = []
    for f in files:
        ext = Path(f.filename or "").suffix.lower()
        if ext not in SUPPORTED_EXTENSIONS:
            results.append({"file": f.filename, "ok": False,
                            "error": f"不支持的文件类型 {ext}（支持 {sorted(SUPPORTED_EXTENSIONS)}）"})
            continue

        material_id = uuid.uuid4().hex[:12]
        raw_dir = Path(config.KB_RAW_DIR) / material_id
        parsed_dir = Path(config.KB_PARSED_DIR) / material_id
        raw_dir.mkdir(parents=True, exist_ok=True)
        parsed_dir.mkdir(parents=True, exist_ok=True)

        stored_name = uuid.uuid4().hex + ext
        dest = raw_dir / stored_name
        # 落盘 + SHA-256 + 大小上限（沿用 M1）
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

        # 同步解析（失败记录 parse_error，不阻塞整批）
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

        db.insert("kb_materials", {
            "id": material_id, "category": cat,
            "file_name": f.filename or stored_name,
            "stored_name": stored_name, "file_type": ext.lstrip("."),
            "total_pages": parsed.total_pages if parsed else 0,
            "char_count": parsed.char_count if parsed else 0,
            "ocr_pages": json.dumps(parsed.ocr_pages if parsed else []),
            "raw_hash": sha.hexdigest(),
            "parser_version": config.PARSER_VERSION,
            "parse_error": parse_error,
            "parsed_file": parsed_file,
            "process_status": "未处理",
            "process_progress": "",
            "chunk_count": 0, "capability_count": 0,
            "index_status": "none",
            "created_at": now_str(),
        })
        results.append({
            "file": f.filename, "ok": not parse_error,
            "material_id": material_id,
            "total_pages": parsed.total_pages if parsed else 0,
            "char_count": parsed.char_count if parsed else 0,
            "ocr_pages": parsed.ocr_pages if parsed else [],
            "sections": len(parsed.sections) if parsed else 0,
            "error": parse_error,
        })

    record_audit(db, user, "upload_knowledge", "kb_material", "",
                 detail=f"category={cat} 文件数={len(files)}")
    return {"category": cat, "results": results}


@router.get("/materials",
            dependencies=[Depends(require_permission("knowledge", "view"))])
def list_materials(
    category: str | None = None,
    status: str | None = None,
) -> list[dict]:
    db = Database(config.DB_PATH)
    sql = "SELECT * FROM kb_materials"
    conds: list[str] = []
    params: list = []
    if category:
        conds.append("category = ?")
        params.append(_check_category(category))
    if status:
        if status not in _STATUSES:
            raise HTTPException(status_code=422, detail=f"status 必须是: {list(_STATUSES)}")
        conds.append("process_status = ?")
        params.append(status)
    if conds:
        sql += " WHERE " + " AND ".join(conds)
    sql += " ORDER BY created_at DESC"
    return [_material_dict(r) for r in db.query(sql, tuple(params))]


@router.get("/materials/{material_id}",
            dependencies=[Depends(require_permission("knowledge", "view"))])
def get_material(material_id: str,
                 user: dict = Depends(get_current_user)) -> dict:
    mat = _get_material_or_404(material_id)
    record_audit(Database(config.DB_PATH), user, "view_knowledge",
                 "kb_material", material_id, detail=mat["file_name"])
    detail = _material_dict(mat)
    detail["sections"] = []
    if mat["parsed_file"]:
        pfile = Path(config.KB_PARSED_DIR) / material_id / mat["parsed_file"]
        if pfile.exists():
            try:
                data = json.loads(pfile.read_text(encoding="utf-8"))
                detail["sections"] = data.get("sections", [])
            except Exception:
                pass
    return detail


@router.get("/materials/{material_id}/chunks",
            dependencies=[Depends(require_permission("knowledge", "view"))])
def list_chunks(
    material_id: str,
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
) -> dict:
    """内容块分页（不含 embedding——向量只在降级检索/重建索引时内部使用）。"""
    _get_material_or_404(material_id)
    db = Database(config.DB_PATH)
    total = db.query_one(
        "SELECT COUNT(*) AS n FROM kb_chunks WHERE material_id = ?", (material_id,))["n"]
    rows = db.query(
        "SELECT * FROM kb_chunks WHERE material_id = ? ORDER BY seq LIMIT ? OFFSET ?",
        (material_id, limit, offset))
    return {
        "total": total, "offset": offset, "limit": limit,
        "chunks": [
            {
                "id": r["id"], "material_id": r["material_id"], "category": r["category"],
                "file_name": r["file_name"], "content": r["content"],
                "section_path": r["section_path"],
                "page_start": r["page_start"], "page_end": r["page_end"],
                "block_ids": json.loads(r["block_ids"] or "[]"),
                "seq": r["seq"], "created_at": r["created_at"],
            }
            for r in rows
        ],
    }


# ═══════════════════════════════════════════════════════════════════════
# 处理 / 删除
# ═══════════════════════════════════════════════════════════════════════
@router.post("/materials/{material_id}/process", status_code=202,
             dependencies=[Depends(require_permission("knowledge", "upload"))])
def process_material(material_id: str, background_tasks: BackgroundTasks,
                     user: dict = Depends(get_current_user)) -> dict:
    """启动后台处理：切块 → 嵌入 + 向量写入 → 能力卡提取（历史标书跳过卡片）。

    状态轮询 GET /materials/{id} 的 process_status / process_progress。
    M7-05：任务中心登记（kb_process；started_by=当前用户）。
    """
    mat = _get_material_or_404(material_id)
    if mat["process_status"] == "处理中":
        raise HTTPException(status_code=409, detail="该资料正在处理中")
    if mat["parse_error"] or not mat["parsed_file"]:
        raise HTTPException(
            status_code=400,
            detail=f"该资料解析失败，无法处理（{mat['parse_error'] or '无解析产物'}）——请删除后重新上传")
    db = Database(config.DB_PATH)
    db.update("kb_materials", "id", material_id, {"process_status": "处理中"})
    task = create_task(db, "kb_process", target_id=material_id,
                       started_by=user["id"])
    background_tasks.add_task(run_kb_task, material_id, task["id"])
    return {"material_id": material_id, "process_status": "处理中",
            "task_id": task["id"],
            "hint": "轮询 GET /api/knowledge/materials/{id} 查看 process_status / process_progress"}


@router.delete("/materials/{material_id}",
               dependencies=[Depends(require_permission("knowledge", "edit"))])
def delete_material(material_id: str,
                    user: dict = Depends(get_current_user)) -> dict:
    """级联删除：chunks + 能力卡（source_doc）+ Milvus（best-effort）+ 落盘文件。"""
    mat = _get_material_or_404(material_id)
    db = Database(config.DB_PATH)
    db.execute("DELETE FROM kb_chunks WHERE material_id = ?", (material_id,))
    # execute 返回 lastrowid（DELETE 恒 0），计数需先 COUNT
    caps = db.query_one("SELECT COUNT(*) AS n FROM capabilities WHERE source_doc = ?",
                        (mat["file_name"],))["n"]
    db.execute("DELETE FROM capabilities WHERE source_doc = ?", (mat["file_name"],))
    db.execute("DELETE FROM kb_materials WHERE id = ?", (material_id,))

    milvus = get_milvus_store()
    milvus_deleted = 0
    if milvus is not None:
        try:
            milvus_deleted = milvus.delete_material(material_id).get("delete_count", 0)
        except Exception:  # noqa: BLE001 —— Milvus 挂不阻塞删除（重跑时再清）
            pass
    for base in (config.KB_RAW_DIR, config.KB_PARSED_DIR):
        shutil.rmtree(Path(base) / material_id, ignore_errors=True)
    record_audit(db, user, "delete_knowledge", "kb_material", material_id,
                 detail=f"file={mat['file_name']} 能力卡删除={caps}")
    return {"material_id": material_id, "deleted": True,
            "capabilities_deleted": caps, "milvus_deleted": milvus_deleted}


# ═══════════════════════════════════════════════════════════════════════
# 能力卡 / 语义检索
# ═══════════════════════════════════════════════════════════════════════
@router.get("/versions",
            dependencies=[Depends(require_permission("knowledge", "view"))])
def list_kb_versions(
    capability_id: str = "",
    label: str = "",
    limit: int = Query(50, ge=1, le=200),
) -> dict:
    """知识库版本列表（M7-04）：?capability_id= 或 ?label= 过滤，最新在前。

    追溯链（标书 → 生成记录 → 知识库版本 → 原始文件/页码）：
    generation_jobs.kb_version 记生成时的最新 label，据此可逐跳定位到
    capabilities.source_doc/source_page（章节级 EVD→材料→页码链 M3/M4 已有）。
    """
    db = Database(config.DB_PATH)
    sql = "SELECT * FROM knowledge_versions"
    conds: list[str] = []
    params: list = []
    if capability_id:
        conds.append("capability_id = ?")
        params.append(capability_id)
    if label:
        conds.append("label = ?")
        params.append(label)
    if conds:
        sql += " WHERE " + " AND ".join(conds)
    sql += " ORDER BY created_at DESC, id DESC LIMIT ?"
    rows = db.query(sql, (*params, limit))
    return {"total": len(rows), "versions": [dict(r) for r in rows]}


@router.get("/materials/{material_id}/capabilities",
            dependencies=[Depends(require_permission("knowledge", "view"))])
def list_material_capabilities(material_id: str) -> list[dict]:
    mat = _get_material_or_404(material_id)
    db = Database(config.DB_PATH)
    rows = db.query("SELECT * FROM capabilities WHERE source_doc = ? ORDER BY id",
                    (mat["file_name"],))
    return [Database.row_to_capability(r).model_dump(mode="json") for r in rows]


@router.get("/capabilities",
            dependencies=[Depends(require_permission("knowledge", "view"))])
def list_capabilities(
    category: str | None = None,
    source_doc: str | None = None,
) -> list[dict]:
    db = Database(config.DB_PATH)
    sql = "SELECT * FROM capabilities"
    conds: list[str] = []
    params: list = []
    if category:
        conds.append("category = ?")
        params.append(_check_category(category))
    if source_doc:
        conds.append("source_doc = ?")
        params.append(source_doc)
    if conds:
        sql += " WHERE " + " AND ".join(conds)
    sql += " ORDER BY id"
    return [Database.row_to_capability(r).model_dump(mode="json")
            for r in db.query(sql, tuple(params))]


@router.patch("/capabilities/{cap_id}",
              dependencies=[Depends(require_permission("knowledge", "edit"))])
def patch_capability(cap_id: str, patch: CapabilityPatch,
                     user: dict = Depends(get_current_user)) -> dict:
    """人工修订（全可选，M7-04 版本化）：attributes 整体替换；category 枚举校验 422。

    每次修订 version+1 并写 knowledge_versions（capability_edit，
    summary = before/after JSON），如 CAP-0001 v1→v2（张伟 5年→6年+PMP）。
    """
    db = Database(config.DB_PATH)
    row = db.query_one("SELECT * FROM capabilities WHERE id = ?", (cap_id,))
    if not row:
        raise HTTPException(status_code=404, detail="能力卡不存在")

    values: dict = {}
    if patch.category is not None:
        values["category"] = _check_category(patch.category)
    if patch.name is not None:
        name = patch.name.strip()
        if not name:
            raise HTTPException(status_code=422, detail="name 不能为空")
        values["name"] = name
    if patch.description is not None:
        values["description"] = patch.description
    if patch.attributes is not None:
        values["attributes"] = json.dumps(patch.attributes, ensure_ascii=False)
    if not values:
        return Database.row_to_capability(row).model_dump(mode="json")

    before = {k: row.get(k) for k in ("name", "category", "description",
                                      "attributes", "source_doc", "source_page")}
    values["version"] = int(row.get("version") or 1) + 1
    values["updated_at"] = now_str()
    db.update("capabilities", "id", cap_id, values)
    new_row = db.query_one("SELECT * FROM capabilities WHERE id = ?", (cap_id,))
    after = {k: new_row.get(k) for k in before}
    record_version(db, change_type="capability_edit",
                   changed_by=user.get("username") or user.get("id", ""),
                   capability_id=cap_id,
                   summary=json.dumps({"before": before, "after": after},
                                      ensure_ascii=False))
    record_audit(db, user, "edit_capability", "capability", cap_id,
                 detail=f"v{row.get('version') or 1}→v{values['version']}")
    return Database.row_to_capability(new_row).model_dump(mode="json")


@router.get("/search",
            dependencies=[Depends(require_permission("knowledge", "view"))])
def search_knowledge(
    q: str = Query(..., min_length=1),
    category: str | None = None,
    material_id: str | None = None,
    top_k: int = Query(10, ge=1, le=50),
) -> dict:
    """语义检索：Milvus 挂自动降级 SQLite 暴力余弦，engine 字段透明标识。"""
    if category:
        category = _check_category(category)
    try:
        result = create_search_service().search(
            q.strip(), top_k=top_k, category=category, material_id=material_id)
    except Exception as e:  # noqa: BLE001 —— 检索不可因单点异常 500
        raise HTTPException(status_code=500, detail=f"检索失败: {str(e)[:300]}")
    return result.model_dump(mode="json")
