# -*- coding: utf-8 -*-
"""
generation/job.py —— M4-10 生成任务状态机

    GenerationJobRunner.run()        同步逐章节生成（可断点继续 / 单章节重生成）
    run_generation_task()            后台任务入口（镜像 run_matching_task）

状态机：
    generation_jobs.status: 未生成 → 生成中 → 已完成 | 部分失败 | 失败
    generation_sections.status: 待生成 → 生成中 → 已完成 | 失败

断点继续（M4-10）：重跑同一 job 只处理 status != 已完成 的章节（失败的章节
在下次运行时重试）。单章节重生成：传 section_id，只重跑该章节（version+1）。

进度写入 generation_logs（SSE/日志轮询的读源）。
"""
from __future__ import annotations

import logging
import uuid
from typing import Optional

from ... import config
from ...db import Database
from ...schemas import now_str
from ..kb_versions import latest_kb_label
from ..task_tracker import fail_task, start_task, succeed_task
from ..trace import AgentTracer
from .generator import SectionGenerator
from .models import GenerationJob, SectionStatus
from .outline import OutlineBuilder, tree_from_flat

logger = logging.getLogger(__name__)


def new_job_id() -> str:
    return uuid.uuid4().hex[:12]


class GenerationJobRunner:
    """M4-10 生成任务执行器（同步；后台任务线程调用）。"""

    def __init__(self, db: Optional[Database] = None, llm=None):
        self.db = db or Database(config.DB_PATH)
        self.llm = llm

    # ------------------------------------------------------------------
    # job 生命周期
    # ------------------------------------------------------------------
    def create_job(self, tender_id: str, outline_id: str = "",
                   section_id: str = "", job_id: str = "") -> GenerationJob:
        job = GenerationJob(
            id=job_id or new_job_id(), tender_id=tender_id,
            outline_id=outline_id, status="未生成",
            section_states={s.id: s.status.value
                            for s in self._ordered_sections(tender_id)},
            total_sections=len(self._ordered_sections(tender_id)),
            kb_version=latest_kb_label(self.db),   # M7-04：KB 版本快照（无则 v0）
        )
        if section_id:
            job.total_sections = 1          # 单章节重生成只计 1
        self.db.insert("generation_jobs", Database.job_to_row(job))
        return job

    def get_job(self, job_id: str) -> Optional[GenerationJob]:
        row = self.db.query_one("SELECT * FROM generation_jobs WHERE id = ?",
                                (job_id,))
        return Database.row_to_job(row) if row else None

    def latest_job(self, tender_id: str) -> Optional[GenerationJob]:
        # 排序用 rowid（SQLite 隐式自增插入序）：id 为 uuid 随机串，
        # created_at 同秒时 ORDER BY created_at DESC, id DESC 会随机选错任务
        # （CI 3.10 实测 test_jobs_endpoint_conflict_while_running 偶发 202≠409）。
        row = self.db.query_one(
            "SELECT * FROM generation_jobs WHERE tender_id = ? "
            "ORDER BY rowid DESC LIMIT 1", (tender_id,))
        return Database.row_to_job(row) if row else None

    # ------------------------------------------------------------------
    def run(self, job: GenerationJob, section_id: str = "") -> GenerationJob:
        """逐章节生成。section_id 给定 → 单章节重生成；否则断点继续全流程。"""
        self._set(job, "生成中")
        targets = self._target_sections(job, section_id)
        done = failed = 0
        for sec in targets:
            if not section_id and sec.status == SectionStatus.DONE:
                continue                    # 断点继续：跳过已完成章节
            self._mark_section(job, sec.id, SectionStatus.RUNNING)
            try:
                draft = SectionGenerator(self.db, llm=self.llm).generate_section(
                    sec, sec.tender_id, generation_id=job.id)
                self._persist_draft(sec.id, draft)
                self._mark_section(job, sec.id, SectionStatus.DONE)
                done += 1
                self._log(job.id, sec.id, "info",
                          f"章节完成：{sec.title}（{len(draft.paragraphs)} 段，"
                          f"{len(draft.warnings)} 条校验告警）")
            except Exception as e:  # noqa: BLE001 —— 单章节失败不阻断整单
                logger.exception("章节生成失败 %s", sec.id)
                self.db.update("generation_sections", "section_id", sec.id, {
                    "status": SectionStatus.FAILED.value,
                    "error": str(e)[:300], "updated_at": now_str()})
                self._mark_section(job, sec.id, SectionStatus.FAILED)
                failed += 1
                self._log(job.id, sec.id, "error",
                          f"章节失败：{sec.title}（{str(e)[:120]}）")
            self._set(job, "生成中",
                      progress=f"[{done + failed}/{len(targets)}] {sec.title}")
        self._finalize(job, done, failed)
        return job

    # ------------------------------------------------------------------
    def _target_sections(self, job: GenerationJob, section_id: str,
                         ) -> list:
        """本轮要处理的章节（前序）。单章节重生成 → 只该章节。"""
        ordered = self._ordered_sections(job.tender_id)
        if not section_id:
            return ordered
        sec = next((s for s in ordered if s.id == section_id), None)
        if sec is None:
            raise ValueError(f"章节不存在: {section_id}")
        return [sec]

    def _ordered_sections(self, tender_id: str) -> list:
        """generation_sections 平铺行 → 前序章节树（重建，不依赖 level 分组）。"""
        rows = self.db.query(
            "SELECT * FROM generation_sections WHERE tender_id = ?", (tender_id,))
        flat = [Database.row_to_bid_section(r) for r in rows]
        return OutlineBuilder.flatten(tree_from_flat(flat))

    def _persist_draft(self, section_id: str, draft) -> None:
        row = self.db.query_one(
            "SELECT version, paragraphs FROM generation_sections "
            "WHERE section_id = ?", (section_id,))
        had_draft = bool(row and (row.get("paragraphs")
                                  not in (None, "", "[]")))
        base = (row.get("version") or 1) if row else 1
        values = Database.draft_to_row(draft)
        values["version"] = base + (1 if had_draft else 0)   # 重生成 version+1
        values["status"] = SectionStatus.DONE.value
        values["attempt"] = self._attempt(section_id) + 1
        self.db.update("generation_sections", "section_id", section_id, values)

    def _attempt(self, section_id: str) -> int:
        row = self.db.query_one(
            "SELECT attempt FROM generation_sections WHERE section_id = ?",
            (section_id,))
        return (row.get("attempt") or 0) if row else 0

    # ------------------------------------------------------------------
    def _mark_section(self, job: GenerationJob, section_id: str,
                      status: SectionStatus) -> None:
        job.section_states[section_id] = status.value
        if status == SectionStatus.DONE:
            job.done_sections += 1
        elif status == SectionStatus.FAILED:
            job.failed_sections += 1

    def _set(self, job: GenerationJob, status: str, progress: str = "") -> None:
        job.status = status
        if progress:
            job.progress = progress
        job.updated_at = now_str()
        self.db.update("generation_jobs", "id", job.id,
                       Database.job_to_row(job))

    def _finalize(self, job: GenerationJob, done: int, failed: int) -> None:
        total = job.total_sections
        if failed == 0:
            status, progress = "已完成", f"全部 {done}/{total} 章节生成完成"
        elif done == 0:
            status, progress = "失败", f"全部 {total} 章节失败"
        else:
            status, progress = "部分失败", f"{done} 完成 / {failed} 失败 / 共 {total}"
        self._set(job, status, progress=progress)
        self._log(job.id, "", "info", f"任务{status}：{progress}")

    def _log(self, job_id: str, section_id: str, level: str, message: str) -> None:
        # generation_logs.id 为 INTEGER AUTOINCREMENT，不显式写主键
        self.db.insert("generation_logs", {
            "generation_id": job_id,
            "section_id": section_id, "level": level, "message": message,
            "created_at": now_str()})


# ---------------------------------------------------------------------------
# 后台任务入口（镜像 run_matching_task；FastAPI BackgroundTasks 调用）
# ---------------------------------------------------------------------------
def run_generation_task(tender_id: str, job_id: str, section_id: str = "",
                        outline_id: str = "", task_id: str = "",
                        user_id: str = "") -> dict:
    """后台生成任务：加载/创建 job → 逐章节生成 → 更新终态。

    M7-05：task_id 非空时同步任务中心状态（start/succeed/fail；
    generation_jobs 本身是章节级进度事实源，任务行按 job 字段收口）。
    M7-06：Agent 链路（trace/span 两级；user_id 由启动端点传入）——
    监控旁路写库失败不打断生成本体。

    返回 {tender_id, job_id, status, ...}（后台线程同步执行，调用方轮询 DB）。
    """
    db = Database(config.DB_PATH)
    tracer = AgentTracer(db)
    trace_id = tracer.start("generate", target_id=tender_id, user_id=user_id)
    runner = GenerationJobRunner(db)
    job = runner.get_job(job_id) or runner.create_job(
        tender_id, outline_id, section_id, job_id=job_id)
    if task_id:
        start_task(db, task_id)
    try:
        with tracer.span(trace_id, "generate", "逐章节生成"):
            runner.run(job, section_id=section_id)
        if task_id:
            succeed_task(db, task_id, done=job.done_sections,
                         total=job.total_sections, progress=job.progress)
        tracer.finish(trace_id, "success")
        return {"tender_id": tender_id, "job_id": job_id,
                "status": job.status, "progress": job.progress}
    except Exception as e:  # noqa: BLE001 —— 状态机兜底
        logger.exception("生成任务失败 job=%s", job_id)
        runner._set(job, "失败", progress=f"生成失败: {str(e)[:200]}")
        runner._log(job_id, "", "error", f"任务失败：{str(e)[:200]}")
        if task_id:
            fail_task(db, task_id, error=str(e))
        tracer.finish(trace_id, "failed", error=str(e))
        return {"tender_id": tender_id, "job_id": job_id, "status": "失败",
                "error": str(e)}


__all__ = ["GenerationJobRunner", "run_generation_task", "new_job_id"]
