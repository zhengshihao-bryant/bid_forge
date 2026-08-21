# -*- coding: utf-8 -*-
"""
app/services/trace.py —— M7-06 Agent 链路（agent_traces / agent_spans）

规格链路：用户请求 → 需求分析 → 知识检索 → 生成章节 → 质量检查。
由 4+1 类任务的 trace（task_type）+ 阶段 span（stage）还原；
user_id 经 runner 可选参数传入（启动端点传 current_user.id）。

所有写入 try/finally 保证失败时也落终态；trace 写库失败不打断业务
（start/span/finish 内部吞异常——监控是旁路，绝不影响任务本体）。
"""

from __future__ import annotations

import logging
import uuid
from contextlib import contextmanager

from ..db import Database
from ..schemas import now_str

logger = logging.getLogger("bidgen.trace")


class AgentTracer:
    """Agent 链路记录器（trace + span 两级）。"""

    def __init__(self, db: Database):
        self._db = db

    def start(self, task_type: str, target_id: str = "",
              user_id: str = "") -> str:
        """开一条 trace（running），返回 trace_id。失败返回空串不抛。"""
        trace_id = uuid.uuid4().hex[:12]
        try:
            self._db.insert("agent_traces", {
                "id": trace_id, "task_type": task_type,
                "target_id": target_id, "user_id": user_id,
                "status": "running", "started_at": now_str(),
                "finished_at": "", "error": "",
            })
            return trace_id
        except Exception:
            logger.exception("trace start 失败")
            return ""

    @contextmanager
    def span(self, trace_id: str, stage: str, detail: str = ""):
        """span 上下文：进入 running，正常退出 success，异常 failed 后重抛。"""
        span_id = 0
        if trace_id:
            try:
                span_id = self._db.execute(
                    "INSERT INTO agent_spans (trace_id, stage, status, detail,"
                    " started_at, finished_at) VALUES (?,?,?,?,?,?)",
                    (trace_id, stage, "running", detail, now_str(), ""))
            except Exception:
                logger.exception("span 开启失败")
        try:
            yield
        except Exception:
            self._close_span(span_id, "failed")
            raise
        self._close_span(span_id, "success")

    def _close_span(self, span_id: int, status: str) -> None:
        if not span_id:
            return
        try:
            self._db.execute(
                "UPDATE agent_spans SET status = ?, finished_at = ? WHERE id = ?",
                (status, now_str(), span_id))
        except Exception:
            logger.exception("span 关闭失败")

    def finish(self, trace_id: str, status: str, error: str = "") -> None:
        """trace 终态：success / failed。"""
        if not trace_id:
            return
        try:
            self._db.update("agent_traces", "id", trace_id,
                            {"status": status, "error": (error or "")[:500],
                             "finished_at": now_str()})
        except Exception:
            logger.exception("trace finish 失败")
