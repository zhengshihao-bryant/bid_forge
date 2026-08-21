# -*- coding: utf-8 -*-
"""
tests/test_m4_outline.py —— M4-01 标书结构规划（批次 1）

覆盖：
- 默认大纲：四大块 + section_type 合法 + order 递增 + 12 类需求类型除评分标准全声明
- seed_default 幂等
- materialize → 落库 → tree_from_flat 往返一致
- source_refs 与 M1 招标章节标题重叠匹配
- POST/GET /outline 端点（TestClient + tmp_env 隔离 DB）
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from fastapi.testclient import TestClient  # noqa: E402

from app import config  # noqa: E402
from app.api.main import app  # noqa: E402
from app.db import Database  # noqa: E402
from app.schemas import RequirementType, Section  # noqa: E402
from app.services.generation import (OutlineBuilder, build_default_outline,  # noqa: E402
                                     tree_from_flat)
from app.services.generation.models import SectionType  # noqa: E402

TENDER_ID = "T-OUTLINE"

_SECTION_TYPE_VALUES = {t.value for t in SectionType}


@pytest.fixture()
def client(tmp_env):
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def outline_db(tmp_env) -> Database:
    db = Database(config.DB_PATH)
    db.init_schema()
    db.insert("tenders", {"id": TENDER_ID, "name": "大纲测试项目",
                          "created_at": "2026-01-01 00:00:00"})
    return db


# ═══════════════════════════════════════════════════════════════════════
# 默认大纲结构
# ═══════════════════════════════════════════════════════════════════════
def test_default_outline_has_four_blocks():
    """四大块（商务/技术/实施/售后）+ 前置/后置固定章节。"""
    o = build_default_outline()
    assert o.id == "outline-default"
    top = {c.id: c for c in o.chapters}
    assert {"CH-01", "CH-02", "CH-03", "CH-04", "CH-05", "CH-06",
            "CH-07", "CH-08"} <= set(top)
    # 四大块各带子章节
    for cid in ("CH-04", "CH-05", "CH-06", "CH-07"):
        assert top[cid].children, f"{cid} 无子章节"
    # section_type 全部合法
    for c in o.chapters:
        assert c.section_type in _SECTION_TYPE_VALUES, f"{c.id} 非法 section_type"
        for child in c.children:
            assert child.section_type in _SECTION_TYPE_VALUES


def test_default_outline_order_increasing():
    """顶层 order 递增；子章节 order 递增。"""
    o = build_default_outline()
    top_orders = [c.order for c in o.chapters]
    assert top_orders == sorted(top_orders)
    for c in o.chapters:
        child_orders = [x.order for x in c.children]
        assert child_orders == sorted(child_orders), c.id


def test_default_outline_covers_all_requirement_types_except_scoring():
    """12 类 M1 需求类型除「评分标准」外全部被大纲声明。"""
    declared = set()
    for c in build_default_outline().chapters:
        declared.update(t.value for t in c.requirement_types)
        for child in c.children:
            declared.update(t.value for t in child.requirement_types)
    all_types = {t.value for t in RequirementType}
    assert "评分标准" in all_types
    assert all_types - {"评分标准"} <= declared, (
        f"未声明类型: {all_types - {'评分标准'} - declared}")


# ═══════════════════════════════════════════════════════════════════════
# seed / materialize / tree 往返
# ═══════════════════════════════════════════════════════════════════════
def test_seed_default_idempotent(outline_db):
    builder = OutlineBuilder(outline_db)
    oid1 = builder.seed_default()
    oid2 = builder.seed_default()
    assert oid1 == oid2 == "outline-default"
    n = outline_db.query_one("SELECT COUNT(*) AS n FROM outlines")["n"]
    assert n == 1, "seed_default 应幂等"
    o = builder.get(oid1)
    assert o.name == "通用标书结构" and len(o.chapters) == 8


def test_materialize_and_tree_roundtrip(outline_db):
    """materialize → 落库 → 读回 → tree_from_flat 结构一致。"""
    builder = OutlineBuilder(outline_db)
    oid = builder.seed_default()
    tree = builder.materialize(TENDER_ID, builder.get(oid))
    flat = OutlineBuilder.flatten(tree)
    assert len(flat) == 26, f"章节总数应为 26（8 顶层 + 18 子章节），实际 {len(flat)}"
    # 落库
    for sec in flat:
        outline_db.insert("generation_sections",
                          Database.planning_to_row(sec, tender_id=TENDER_ID))
    rows = outline_db.query(
        "SELECT * FROM generation_sections WHERE tender_id = ? "
        "ORDER BY level, ord", (TENDER_ID,))
    read_flat = [outline_db.row_to_bid_section(r) for r in rows]
    rebuilt = tree_from_flat(read_flat)
    assert len(rebuilt) == 8
    assert [s.id for s in rebuilt][0] == "CH-01"
    ch04 = next(s for s in rebuilt if s.id == "CH-04")
    assert [c.id for c in ch04.children] == [
        "CH-04-1", "CH-04-2", "CH-04-3", "CH-04-4", "CH-04-5"]
    assert all(s.status.value == "待生成" for s in flat)


def test_materialize_source_refs_by_title_overlap(outline_db):
    """source_refs：大纲章节标题 × M1 章节标题 bigram 重叠 ≥0.25 才关联。"""
    doc_sections = [
        Section(id="S0001", title="第四章 技术规格", level=1, order=4,
                children=[
                    Section(id="S0002", title="4.2 技术指标要求", level=2, order=2),
                    Section(id="S0003", title="4.3 付款条件", level=2, order=3),
                ]),
        Section(id="S0004", title="公司简介", level=1, order=1),
    ]
    builder = OutlineBuilder(outline_db)
    tree = builder.materialize(TENDER_ID, build_default_outline(),
                               doc_sections=doc_sections)
    by_id = {s.id: s for s in OutlineBuilder.flatten(tree)}
    # 技术指标响应表 ↔ 4.2 技术指标要求（重叠 0.5 ≥0.25 → 关联，路径含父章节）
    refs = by_id["CH-05-4"].source_refs
    assert any("4.2 技术指标要求" in r for r in refs), refs
    # 公司概况与综合实力 ↔ 公司简介（重叠 1/8 <0.25 → 不关联）
    assert by_id["CH-04-1"].source_refs == []
    # 报价表 ↔ 付款条件（bigram 不相交 → 不关联）
    assert by_id["CH-04-5"].source_refs == []
    # 无 doc_sections 时为空（不报错）
    tree2 = builder.materialize(TENDER_ID, build_default_outline(), doc_sections=[])
    assert all(s.source_refs == [] for s in OutlineBuilder.flatten(tree2))


# ═══════════════════════════════════════════════════════════════════════
# API 端点
# ═══════════════════════════════════════════════════════════════════════
def test_create_outline_endpoint(client):
    # 直接造 tender（避免走上传管线）
    db = Database(config.DB_PATH)
    db.insert("tenders", {"id": TENDER_ID, "name": "大纲测试项目",
                          "created_at": "2026-01-01 00:00:00"})
    r = client.post(f"/api/generation/tenders/{TENDER_ID}/outline")
    assert r.status_code == 200
    data = r.json()
    assert data["outline_id"] == "outline-default"
    assert data["total_sections"] == 26
    ids = [s["id"] for s in data["sections"]]
    assert {"CH-04", "CH-05", "CH-06", "CH-07", "CH-08"} <= set(ids)
    ch04 = next(s for s in data["sections"] if s["id"] == "CH-04")
    assert [c["id"] for c in ch04["children"]] == [
        "CH-04-1", "CH-04-2", "CH-04-3", "CH-04-4", "CH-04-5"]


def test_create_outline_endpoint_404(client):
    r = client.post("/api/generation/tenders/nope/outline")
    assert r.status_code == 404


def test_get_outline_endpoint(client):
    db = Database(config.DB_PATH)
    db.insert("tenders", {"id": TENDER_ID, "name": "大纲测试项目",
                          "created_at": "2026-01-01 00:00:00"})
    # 未规划 → 404
    r = client.get(f"/api/generation/tenders/{TENDER_ID}/outline")
    assert r.status_code == 404
    # 规划后 → 树
    client.post(f"/api/generation/tenders/{TENDER_ID}/outline")
    r = client.get(f"/api/generation/tenders/{TENDER_ID}/outline")
    assert r.status_code == 200
    assert r.json()["sections"][0]["id"] == "CH-01"


def test_replan_idempotent(client):
    """同 tender 重跑规划：先清旧章节，不产生重复。"""
    db = Database(config.DB_PATH)
    db.insert("tenders", {"id": TENDER_ID, "name": "大纲测试项目",
                          "created_at": "2026-01-01 00:00:00"})
    client.post(f"/api/generation/tenders/{TENDER_ID}/outline")
    client.post(f"/api/generation/tenders/{TENDER_ID}/outline")
    n = db.query_one("SELECT COUNT(*) AS n FROM generation_sections "
                     "WHERE tender_id = ?", (TENDER_ID,))["n"]
    assert n == 26


def test_create_outline_triggers_mapping(seed_m4):
    """POST /outline 必须触发 M4-02 映射落库（coverage 不依赖手工 map_all）。"""
    from fastapi.testclient import TestClient
    from app.api.main import app

    data = seed_m4
    # 路由需要 tenders 行（seed_m4 走直连不建）
    data["db"].insert("tenders", {"id": data["tender_id"],
                                  "name": "M4映射回归测试项目",
                                  "created_at": "2026-01-01 00:00:00"})
    # 先清掉映射表（模拟全新 HTTP 流程，seed_m4 内部 map_all 视为不存在）
    data["db"].execute("DELETE FROM requirement_section_maps WHERE tender_id = ?",
                       (data["tender_id"],))
    data["db"].execute("DELETE FROM generation_sections WHERE tender_id = ?",
                       (data["tender_id"],))
    with TestClient(app) as c:
        r = c.post(f"/api/generation/tenders/{data['tender_id']}/outline")
        assert r.status_code == 200
        j = r.json()
        assert j["mapped_requirements"] == 33, j
        # coverage 端点读的是路由写入的映射表
        cov = c.get(f"/api/generation/tenders/{data['tender_id']}/coverage").json()
        assert cov["total"] == 33 and cov["mapped"] == 33
