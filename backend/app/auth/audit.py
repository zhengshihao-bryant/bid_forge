# -*- coding: utf-8 -*-
"""
app/auth/audit.py —— 操作审计（audit_logs）

埋点原则（M7-03）：对象级查看全埋（招标文件/知识库/证据/报告/终版），
列表/搜索/轮询/SSE 不埋（高频无单一对象语义，避免噪声）；
失败路径不记（失败语义由任务状态/异常承载）。
"""

from __future__ import annotations

from typing import Optional

from ..db import Database
from ..schemas import now_str


def record_audit(db: Database, user: Optional[dict], action: str,
                 resource_type: str = "", resource_id: str = "",
                 detail: str = "", ip: str = "") -> None:
    """写一条审计日志（username 冗余快照，用户改名/删除仍可审计）。"""
    db.insert("audit_logs", {
        "user_id": (user or {}).get("id", ""),
        "username": (user or {}).get("username", "") or "system",
        "action": action,
        "resource_type": resource_type,
        "resource_id": resource_id,
        "detail": detail,
        "ip": ip,
        "created_at": now_str(),
    })
