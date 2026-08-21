# -*- coding: utf-8 -*-
"""
tests/test_vector_store.py —— 向量存储与检索编排单元测试（离线）

数据落位铁律：SQLite = 事实源，Milvus = 可重建索引；
命中结果一律回 SQLite 取权威元数据拼 SourceAnchor。
真实 Milvus 回环由 @pytest.mark.milvus 集成测试覆盖（默认跳过）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app import config  # noqa: E402
from app.db import Database  # noqa: E402
from app.schemas import CapabilityCategory, KbChunk, now_str  # noqa: E402
from app.services.embedding import FakeEmbedding  # noqa: E402
from app.services.vector_store import (  # noqa: E402
    MilvusVectorStore,
    SearchService,
    SqliteVectorStore,
)


# ═══════════════════════════════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════════════════════════════
def _chunk(db: Database, cid: str, material_id: str, category: str,
           content: str, section_path: str = "", page: int | None = 1,
           block_ids: list[str] | None = None) -> None:
    c = KbChunk(id=cid, material_id=material_id, category=CapabilityCategory(category),
                file_name=f"{material_id}.docx", content=content,
                section_path=section_path, page_start=page, page_end=page,
                block_ids=block_ids or ["B0001"], seq=1, created_at=now_str())
    db.insert("kb_chunks", Database.chunk_to_row(c))


def _embed_rows(db: Database, emb: FakeEmbedding, cid: str, content: str) -> None:
    vec = emb.embed([content])[0]
    SqliteVectorStore(db).upsert(
        [{"chunk_id": cid, "embedding": vec, "material_id": "", "category": "",
          "file_name": "", "section_path": "", "page_start": 1, "page_end": 1,
          "block_ids": [], "content": content}])


# ═══════════════════════════════════════════════════════════════════════
# SQLite 暴力余弦
# ═══════════════════════════════════════════════════════════════════════
def test_sqlite_upsert_and_search_ranking(tmp_env):
    db = Database(config.DB_PATH)
    db.init_schema()
    emb = FakeEmbedding(dimension=config.EMBEDDING_DIM)
    _chunk(db, "m1_C0001", "m1", "人员资质", "张伟 项目经理 6年经验 PMP",
           section_path="项目团队 > 项目经理详情", page=3, block_ids=["B0001", "B0002"])
    _chunk(db, "m1_C0002", "m1", "人员资质", "王芳 实施工程师 5年经验", page=4)
    _chunk(db, "m2_C0001", "m2", "产品", "智慧园区综合管理平台 设备接入2000台", page=1)
    _embed_rows(db, emb, "m1_C0001", "张伟 项目经理 6年经验 PMP")
    _embed_rows(db, emb, "m1_C0002", "王芳 实施工程师 5年经验")
    _embed_rows(db, emb, "m2_C0001", "智慧园区综合管理平台 设备接入2000台")

    store = SqliteVectorStore(db)
    qv = emb.embed_queries(["张伟项目经理多少年经验"])[0]
    hits = store.search(qv, top_k=10)
    assert len(hits) == 3
    assert hits[0]["chunk_id"] == "m1_C0001"      # 共享字串最多 → 余弦最高
    assert hits[0]["score"] > hits[1]["score"]

    # 类别过滤
    hits = store.search(qv, top_k=10, category="人员资质")
    assert {h["chunk_id"] for h in hits} == {"m1_C0001", "m1_C0002"}
    # material_id 过滤
    hits = store.search(qv, top_k=10, material_id="m2")
    assert [h["chunk_id"] for h in hits] == ["m2_C0001"]


def test_sqlite_search_excludes_unembedded_rows(tmp_env):
    db = Database(config.DB_PATH)
    db.init_schema()
    _chunk(db, "m1_C0001", "m1", "产品", "已嵌入内容")
    _chunk(db, "m1_C0002", "m1", "产品", "未嵌入内容（embedding 为空）")
    emb = FakeEmbedding(dimension=config.EMBEDDING_DIM)
    _embed_rows(db, emb, "m1_C0001", "已嵌入内容")

    hits = SqliteVectorStore(db).search(emb.embed_queries(["已嵌入内容"])[0], top_k=10)
    assert [h["chunk_id"] for h in hits] == ["m1_C0001"]   # embedding='[]' 的行被排除


# ═══════════════════════════════════════════════════════════════════════
# SearchService 编排（降级链 + 四元溯源拼装）
# ═══════════════════════════════════════════════════════════════════════
class _BoomMilvus:
    """search 必抛异常的 Milvus 替身（降级测试）。"""

    name = "milvus"

    def ensure(self):
        pass

    def upsert(self, rows):
        return {"insert_count": 0}

    def delete_material(self, material_id):
        return {"delete_count": 0}

    def search(self, *args, **kwargs):
        raise RuntimeError("milvus down")

    def info(self):
        return {"reachable": False, "error": "down"}


class _FakeMilvus:
    """返回假命中的 Milvus 替身（引擎标识测试）。"""

    name = "milvus"

    def search(self, *args, **kwargs):
        return [{
            "chunk_id": "m1_C0001", "score": 0.98, "material_id": "m1",
            "category": "人员资质", "file_name": "stale.pdf",   # 故意给旧值
            "section_path": "旧路径", "page_start": 99, "page_end": 99,
            "block_ids": [], "content": "旧内容",
        }]

    def info(self):
        return {"reachable": True, "version": "2.3.3"}


def test_search_service_falls_back_to_sqlite(tmp_env):
    db = Database(config.DB_PATH)
    db.init_schema()
    emb = FakeEmbedding(dimension=config.EMBEDDING_DIM)
    _chunk(db, "m1_C0001", "m1", "人员资质", "张伟 项目经理 6年经验 PMP",
           section_path="项目团队 > 项目经理详情", page=3, block_ids=["B0001"])
    _embed_rows(db, emb, "m1_C0001", "张伟 项目经理 6年经验 PMP")

    svc = SearchService(milvus_store=_BoomMilvus(), db=db, embedding=emb)
    result = svc.search("张伟项目经理多少年经验", top_k=5)
    assert result.engine == "sqlite"               # 降级透明
    assert len(result.hits) == 1
    hit = result.hits[0]
    assert hit.chunk_id == "m1_C0001"
    # 四元溯源：SQLite 权威元数据
    assert hit.anchor.document == "m1.docx"
    assert hit.anchor.doc_id == "m1"
    assert hit.anchor.page == 3
    assert hit.anchor.section_path == "项目团队 > 项目经理详情"
    assert hit.anchor.block_id == "B0001"
    assert "张伟" in hit.anchor.snippet


def test_search_service_uses_milvus_and_resolves_authority(tmp_env):
    """Milvus 命中只作索引指引，元数据一律回 SQLite 取权威值。"""
    db = Database(config.DB_PATH)
    db.init_schema()
    emb = FakeEmbedding(dimension=config.EMBEDDING_DIM)
    _chunk(db, "m1_C0001", "m1", "人员资质", "张伟 项目经理 6年经验 PMP",
           section_path="项目团队 > 项目经理详情", page=3, block_ids=["B0001"])
    _embed_rows(db, emb, "m1_C0001", "张伟 项目经理 6年经验 PMP")

    svc = SearchService(milvus_store=_FakeMilvus(), db=db, embedding=emb)
    result = svc.search("张伟", top_k=5)
    assert result.engine == "milvus"
    hit = result.hits[0]
    assert hit.file_name == "m1.docx"              # 不是 Milvus 里的 stale.pdf
    assert hit.anchor.page == 3                    # 不是 99
    assert hit.anchor.section_path == "项目团队 > 项目经理详情"


def test_search_service_milvus_hit_missing_in_sqlite_is_skipped(tmp_env):
    """删除竞态：Milvus 有、SQLite 已删的命中直接跳过。"""
    db = Database(config.DB_PATH)
    db.init_schema()
    emb = FakeEmbedding(dimension=config.EMBEDDING_DIM)
    svc = SearchService(milvus_store=_FakeMilvus(), db=db, embedding=emb)
    result = svc.search("张伟", top_k=5)
    assert result.engine == "milvus"
    assert result.hits == []


# ═══════════════════════════════════════════════════════════════════════
# Milvus 客户端防御
# ═══════════════════════════════════════════════════════════════════════
def test_milvus_upsert_dim_mismatch_raises_before_connection():
    """维度不符在建立任何连接前早抛（防把错误维度写进集合）。"""
    store = MilvusVectorStore(uri="http://127.0.0.1:1")  # 连不上也不该走到连接
    with pytest.raises(ValueError):
        store.upsert([{"chunk_id": "c1", "embedding": [0.1, 0.2, 0.3]}])


# ═══════════════════════════════════════════════════════════════════════
# 真实 Milvus 回环（需运行中的 Milvus，默认跳过）
# ═══════════════════════════════════════════════════════════════════════
@pytest.mark.milvus
def test_milvus_roundtrip(tmp_env):
    """真实 Milvus：建临时集合 → upsert → 检索 → 按资料删除。

    前置：docker start milvus-etcd milvus-minio milvus-standalone，
    并用 scripts/probe_milvus.py 确认兼容。临时集合名 _kb_test_{pid}，测后即删，
    绝不触碰生产集合 bid_chunks。
    """
    import os

    store = MilvusVectorStore(collection=f"_kb_test_{os.getpid()}")
    try:
        store.ensure()
        emb = FakeEmbedding(dimension=config.EMBEDDING_DIM)
        rows = [{
            "chunk_id": f"t_{i}_C0001", "material_id": f"t_{i}", "category": "产品",
            "file_name": f"t_{i}.pdf", "section_path": "1.3 技术指标",
            "page_start": 1, "page_end": 1, "block_ids": ["B0001"],
            "content": f"智慧园区平台设备接入 {1000 + i} 台",
            "embedding": emb.embed([f"智慧园区平台设备接入 {1000 + i} 台"])[0],
        } for i in range(2)]
        res = store.upsert(rows)
        assert res["insert_count"] == 2

        hits = store.search(emb.embed_queries(["设备接入1001台"])[0], top_k=2)
        assert hits, "Milvus 检索返回空（检查 COSINE 方向/集合状态）"
        # COSINE 越大越相似：1001 应排第一
        assert hits[0]["chunk_id"] == "t_1_C0001"
        assert hits[0]["category"] == "产品"

        del_res = store.delete_material("t_1")
        assert del_res["delete_count"] >= 1
        hits = store.search(emb.embed_queries(["设备接入1000台"])[0], top_k=5)
        assert all(h["material_id"] != "t_1" for h in hits)
    finally:
        try:
            from pymilvus import MilvusClient
            MilvusClient(uri=store.uri).drop_collection(store.collection)
        except Exception:
            pass
