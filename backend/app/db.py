# -*- coding: utf-8 -*-
"""
app/db.py —— SQLite 存储层

线程安全策略（技术校验结论）：
- **每操作独立连接**：connect() 每次新建连接，执行完即关 ——
  后台任务与请求线程各自持有自己的连接，杜绝共享连接跨线程并发损坏
- 勿用 check_same_thread=False（只是移除守卫，共享连接跨线程并发仍有实证损坏案例）
- PRAGMA journal_mode=WAL + busy_timeout=5000：读写并发不互锁

表清单（M2/M3 表现在预建，向前兼容）：
  M1：tenders / documents / requirements / score_points
  M2：kb_materials / kb_chunks（M2 新增）/ capabilities / matches（matches 实际 M3 使用）
  M3：outlines / drafts

requirements 行与 Requirement 模型的映射在 requirement_to_row / row_to_requirement，
保持 schema ↔ 持久化单一出处。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Optional

from . import config
from .schemas import Requirement, SourceAnchor, now_str
from .db_mappers import MappersMixin
from .db_schema import DDL


class Database(MappersMixin):
    """SQLite 封装：每次操作独立连接（见模块 docstring）。"""

    def __init__(self, path: Path | str | None = None):
        self._path = str(path or config.DB_PATH)

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    # ------------------------------------------------------------------
    # 基础操作
    # ------------------------------------------------------------------
    def execute(self, sql: str, params: tuple = ()) -> int:
        """执行写语句，返回 lastrowid。"""
        with self.connect() as conn:
            cur = conn.execute(sql, params)
            conn.commit()
            return cur.lastrowid or 0

    def executemany(self, sql: str, rows: list[tuple]) -> None:
        with self.connect() as conn:
            conn.executemany(sql, rows)
            conn.commit()

    def query(self, sql: str, params: tuple = ()) -> list[dict]:
        with self.connect() as conn:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]

    def query_one(self, sql: str, params: tuple = ()) -> Optional[dict]:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def insert(self, table: str, row: dict) -> None:
        cols = ", ".join(row.keys())
        marks = ", ".join("?" for _ in row)
        self.execute(f"INSERT INTO {table} ({cols}) VALUES ({marks})", tuple(row.values()))

    def update(self, table: str, id_col: str, row_id: str, values: dict) -> int:
        """按主键更新，返回受影响行数。"""
        if not values:
            return 0
        sets = ", ".join(f"{k} = ?" for k in values)
        with self.connect() as conn:
            cur = conn.execute(
                f"UPDATE {table} SET {sets} WHERE {id_col} = ?",
                (*values.values(), row_id),
            )
            conn.commit()
            return cur.rowcount

    # ------------------------------------------------------------------
    # 建表
    # ------------------------------------------------------------------
    def init_schema(self) -> None:
        with self.connect() as conn:
            conn.executescript(DDL)
            self._migrate(conn)
            conn.commit()

    @staticmethod
    def _migrate(conn: sqlite3.Connection) -> None:
        """幂等 ALTER（M7 起）：PRAGMA table_info 检查列存在再加列。

        项目无迁移框架（M1–M6 均只追加新表）；M7 给已有表加列用此
        检查法（比依赖异常驱动的判断可靠）。
        """
        def add_col(table: str, column: str, ddl: str) -> None:
            cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
            if column not in cols:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")

        add_col("tenders", "owner_id", "owner_id TEXT NOT NULL DEFAULT ''")
        add_col("capabilities", "version", "version INTEGER NOT NULL DEFAULT 1")
        add_col("capabilities", "updated_at", "updated_at TEXT NOT NULL DEFAULT ''")
        add_col("generation_jobs", "kb_version", "kb_version TEXT NOT NULL DEFAULT ''")



# ── 从 db_schema 重新导出（保持 from ..db import seed_rbac, get_db 兼容）──
from .db_schema import (  # noqa: E402,F401
    M7_PERMISSIONS,
    M7_ROLE_PERMISSIONS,
    get_db,
    seed_rbac,
)
