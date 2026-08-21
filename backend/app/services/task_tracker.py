# -*- coding: utf-8 -*-
"""
app/services/task_tracker.py —— M7-05 任务中心（tasks 表统一任务状态机）

5 类任务：extract（需求提取）/ kb_process（知识库处理）/ match（需求匹配）/
generate（标书生成，ref_id=generation_jobs.id）/ quality_check（质量检查，
同步执行包一层）。状态机：pending → running → success|failed；pending →
cancelled（仅待执行可取消——BackgroundTasks 不可杀，进程级任务队列列入
ROADMAP 演进方向）。

只记录 M7 起的新任务，不 union 回填历史（matching_runs 单行无历史等，
文档声明）。全部函数 try/except 包裹调用方传入的 db（监控/任务写库失败
绝不打断业务主流程由各 run_* 兜底）。
"""

from __future__ import annotations

from ..db import Database
from ..schemas import now_str


def create_task(db: Database, task_type: str, target_id: str = "",
                ref_id: str = "", started_by: str = "", total: int = 0) -> dict:
    """INSERT pending 任务行，返回行 dict（含 id）。"""
    now = now_str()
    row = {
        "id": _new_id(db),
        "task_type": task_type,
        "target_id": target_id,
        "ref_id": ref_id,
        "status": "pending",
        "progress": "",
        "progress_pct": 0,
        "total": total,
        "done": 0,
        "error": "",
        "started_by": started_by,
        "created_at": now,
        "started_at": "",
        "updated_at": now,
    }
    db.insert("tasks", row)
    return row


def start_task(db: Database, task_id: str) -> None:
    """pending → running + started_at。"""
    db.update("tasks", "id", task_id,
              {"status": "running", "started_at": now_str(),
               "updated_at": now_str()})


def update_progress(db: Database, task_id: str, done: int, total: int,
                    progress: str = "") -> None:
    """done/total → progress_pct（0-100 钳制）。"""
    pct = int(done / total * 100) if total > 0 else 0
    db.update("tasks", "id", task_id, {
        "done": done, "total": total,
        "progress_pct": min(max(pct, 0), 100),
        "progress": progress or f"{done}/{total}",
        "updated_at": now_str()})


def succeed_task(db: Database, task_id: str, done: int = 0, total: int = 0,
                 progress: str = "") -> None:
    """→ success；未传 done/total 时按 100% 收口。"""
    if done <= 0 and total <= 0:
        done = total = 1
    db.update("tasks", "id", task_id, {
        "status": "success", "done": done, "total": total,
        "progress_pct": 100, "error": "",
        "progress": progress or f"{done}/{total}",
        "updated_at": now_str()})


def fail_task(db: Database, task_id: str, error: str,
              progress: str = "") -> None:
    """→ failed（error ≤500 截断）。"""
    db.update("tasks", "id", task_id, {
        "status": "failed", "error": str(error)[:500],
        "progress": progress, "updated_at": now_str()})


def cancel_task(db: Database, task_id: str) -> None:
    """pending → cancelled（running 不可取消——调用方先查状态，409 语义）。"""
    db.update("tasks", "id", task_id,
              {"status": "cancelled", "updated_at": now_str()})


def _new_id(db: Database) -> str:
    """TSK-{n:04d} 全局顺序号。"""
    row = db.query_one("SELECT COUNT(*) AS n FROM tasks", ())
    return f"TSK-{((row or {}).get('n') or 0) + 1:04d}"


__all__ = ["create_task", "start_task", "update_progress", "succeed_task",
           "fail_task", "cancel_task"]
