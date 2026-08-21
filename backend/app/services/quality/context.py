# -*- coding: utf-8 -*-
"""
quality/context.py —— CheckContext 一次性装载（M5-01 数据装配）

runner 与检查器共用的装载入口：把生成章节/规范需求/匹配结果/证据/能力卡/
评分点/招标项目 + FactRegistry 装进 CheckContext，检查器只读不改。

事实区 = 除 {CH-01, CH-02, CH-03, CH-04-5, CH-05-4, CH-08} 外全部章节：
- CH-01/02/03/04-5 为封面/目录/投标函/报价表等固定格式，无企业事实；
- CH-05-4 技术指标响应表与 CH-08 需求响应表是"需求回显区"——实时回显
  招标原文（含 MISSING 需求 5000 台等），不是标书自述事实，必须排除。
"""
from __future__ import annotations

from typing import Any, Optional

from ...db import Database
from .models import CheckContext
from .registry import FactRegistryBuilder

# 需求回显区 + 固定格式区：不参与事实/一致性检查（数字锚定、跨章节冲突）
FACT_ZONE_EXCLUDED = {"CH-01", "CH-02", "CH-03", "CH-04-5", "CH-05-4", "CH-08"}


def build_check_context(db: Database, tender_id: str,
                        as_of: str = "") -> CheckContext:
    """装载单次检查的全部输入（确定性、无 LLM）。"""
    sections = db.query("SELECT * FROM generation_sections "
                        "WHERE tender_id = ?", (tender_id,))
    canonicals = db.query(
        "SELECT * FROM canonical_requirements WHERE tender_id = ?",
        (tender_id,))
    requirements = db.query("SELECT * FROM requirements WHERE tender_id = ?",
                            (tender_id,))
    matches: dict[str, dict[str, Any]] = {}
    for row in db.query("SELECT * FROM requirement_matches WHERE tender_id = ?",
                        (tender_id,)):
        matches[row["requirement_id"]] = row
    section_maps = db.query(
        "SELECT * FROM requirement_section_maps WHERE tender_id = ?",
        (tender_id,))
    score_points = db.query("SELECT * FROM score_points WHERE tender_id = ?",
                            (tender_id,))
    evidences: dict[str, dict[str, Any]] = {}
    for row in db.query("SELECT * FROM evidences WHERE tender_id = ?",
                        (tender_id,)):
        evidences[row["id"]] = row
    capabilities = db.query("SELECT * FROM capabilities")
    tender = db.query_one("SELECT * FROM tenders WHERE id = ?", (tender_id,))
    fact_zone_ids = [s["section_id"] for s in sections
                     if s["section_id"] not in FACT_ZONE_EXCLUDED]
    registry = FactRegistryBuilder(db).build(tender_id)
    return CheckContext(
        db=db, tender_id=tender_id, as_of=as_of,
        sections=sections, canonicals=canonicals,
        requirements=requirements, matches=matches,
        section_maps=section_maps, score_points=score_points,
        evidences=evidences, capabilities=capabilities,
        tender=tender or {}, registry=registry,
        fact_zone_ids=fact_zone_ids)


__all__ = ["build_check_context", "FACT_ZONE_EXCLUDED"]
