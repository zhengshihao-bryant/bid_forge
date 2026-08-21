# -*- coding: utf-8 -*-
"""
app/services/vector_store.py —— 向量存储抽象 + Milvus / SQLite 双实现 + 检索编排

数据落位铁律（M2 方案核心决策）：
    SQLite（kb_chunks）= 事实源：全文 + 四元溯源 + 向量 JSON
    Milvus（bid_chunks）= 可重建向量索引：chunk_id 主键，挂掉可从 SQLite 全量重建
    命中结果一律回 SQLite 取权威元数据拼 SourceAnchor——Milvus 里存的只是索引副本

Milvus 集成要点（2026-08-14 spike 实测，scripts/probe_milvus.py 固化）：
- pymilvus 3.0.0 MilvusClient 新 API × 本机 Milvus standalone 2.3.3 全链路兼容
  （建集合/插入/检索/过滤删除全部通过）；COSINE 分数方向=越大越相似（已锁定）
- 用 MilvusClient 而非 ORM Collection/connections——ORM 路径有弃用警告风险
- 集合名 bid_chunks 独立命名：不同服务实例各自独立集合，绝不跨实例操作集合
- Milvus INT64 字段无 NULL 概念：page_start/page_end 为 None 时插 0（0 = 无页码）

降级策略（MILVUS_ENABLED=false 或 Milvus 挂掉）：
    SqliteVectorStore：numpy 暴力余弦 + SQLite 元数据过滤。
    M2 全库 ~200 块规模完全够用；engine 字段透明标识降级路径，验收不因 Milvus 挂而失败。
"""

from __future__ import annotations

import json
import logging
import time
from typing import Optional

from .. import config
from ..db import Database
from ..schemas import SearchHit, SearchResult, SourceAnchor

logger = logging.getLogger(__name__)

# 无页码的 INT64 哨兵值（Milvus 无 NULL；0 = 无页码）
_NO_PAGE = 0


# ═══════════════════════════════════════════════════════════════════════
# 抽象接口
# ═══════════════════════════════════════════════════════════════════════
class VectorStore:
    """向量存储统一契约：Milvus 与 SQLite 降级实现同接口。"""

    name: str = "vector"

    def ensure(self) -> None:
        """建集合/索引（幂等）。"""
        raise NotImplementedError

    def upsert(self, rows: list[dict]) -> dict:
        """插入/更新 chunk 向量行。rows 元素含 chunk_id/embedding 及元数据。"""
        raise NotImplementedError

    def delete_material(self, material_id: str) -> dict:
        """按 material_id 删除该资料的向量条目。"""
        raise NotImplementedError

    def search(self, query_vector: list[float], top_k: int = 10,
               category: Optional[str] = None,
               material_id: Optional[str] = None) -> list[dict]:
        """向量检索，返回 [{chunk_id, score, **元数据}] 按 score 降序。"""
        raise NotImplementedError

    def info(self) -> dict:
        """存储状态（/health 用），须轻量快速。"""
        raise NotImplementedError


# ═══════════════════════════════════════════════════════════════════════
# Milvus 实现（MilvusClient 新 API）
# ═══════════════════════════════════════════════════════════════════════
_MILVUS_FIELDS = {
    "chunk_id": dict(dtype="VARCHAR", max_length=128, is_primary=True),
    "material_id": dict(dtype="VARCHAR", max_length=64),
    "category": dict(dtype="VARCHAR", max_length=32),
    "file_name": dict(dtype="VARCHAR", max_length=255),
    "section_path": dict(dtype="VARCHAR", max_length=1024),
    "page_start": dict(dtype="INT64"),
    "page_end": dict(dtype="INT64"),
    "block_ids": dict(dtype="VARCHAR", max_length=512),
    "content": dict(dtype="VARCHAR", max_length=8192),
    "embedding": dict(dtype="FLOAT_VECTOR", dim=config.EMBEDDING_DIM),
}

_INDEX_PARAMS = {"index_type": "HNSW", "metric_type": "COSINE",
                 "params": {"M": 16, "efConstruction": 200}}
_SEARCH_PARAMS = {"metric_type": "COSINE", "params": {"ef": 64}}

_OUTPUT_FIELDS = ["chunk_id", "material_id", "category", "file_name",
                  "section_path", "page_start", "page_end", "block_ids", "content"]


def _truncate(text: str, max_chars: int) -> str:
    """安全截断到 max_chars 字符（中文安全，防超 VARCHAR max_length）。"""
    return text[:max_chars] if len(text) > max_chars else text


# Milvus 连接失败冷却（秒）：连接异常后冷却期内直接快速失败，
# 由 SearchService 立即降级 SQLite —— 否则每一条需求每次检索都重付
# 一次 ~10s 的连接超时，一场匹配上百次检索全耗在无效重试上
_CONN_COOLDOWN = 60.0


class MilvusVectorStore(VectorStore):
    """Milvus 向量存储（MilvusClient 新 API，集合 bid_chunks）。"""

    name = "milvus"

    def __init__(self, uri: str = None, collection: str = None):
        self.uri = uri or config.MILVUS_URI
        self.collection = collection or config.MILVUS_COLLECTION
        self._client = None
        self._conn_error_at = 0.0
        self._conn_error_msg = ""

    def _get_client(self):
        if self._client is None:
            if time.time() - self._conn_error_at < _CONN_COOLDOWN:
                raise RuntimeError(
                    f"Milvus 连接冷却中（上次失败：{self._conn_error_msg[:80]}）")
            from pymilvus import MilvusClient  # 延迟导入：离线环境无 pymilvus 也能 import 本模块
            try:
                self._client = MilvusClient(uri=self.uri)
            except Exception as e:  # noqa: BLE001 —— 记录失败时间，冷却期内不再重试
                self._conn_error_at = time.time()
                self._conn_error_msg = str(e)
                raise
        return self._client

    def ensure(self) -> None:
        client = self._get_client()
        if client.has_collection(self.collection):
            return
        from pymilvus import DataType
        schema = client.create_schema(auto_id=False, enable_dynamic_field=False)
        for name, kw in _MILVUS_FIELDS.items():
            dtype = DataType.FLOAT_VECTOR if kw["dtype"] == "FLOAT_VECTOR" else getattr(DataType, kw["dtype"])
            extra = dict(kw)
            extra.pop("dtype")
            if dtype == DataType.FLOAT_VECTOR:
                extra["dim"] = kw["dim"]
            schema.add_field(name, dtype, **extra)
        index_params = client.prepare_index_params()
        index_params.add_index(field_name="embedding", **_INDEX_PARAMS)
        client.create_collection(self.collection, schema=schema, index_params=index_params)
        logger.info("Milvus 集合 %s 已创建（HNSW + COSINE, dim=%d）", self.collection, config.EMBEDDING_DIM)

    def upsert(self, rows: list[dict]) -> dict:
        """插入或覆盖 chunk 向量。

        踩坑（2026-08-14 实测）：Milvus 2.3.3 服务端对 upsert RPC 支持不完整——
        pymilvus 3.0 客户端 upsert() 返回成功计数但数据实际未落库（row_count 仍为 0，
        检索全空）。而 insert → search 链路 spike 已实测通过。故改为：
        insert 优先；主键冲突（chunk_id 已存在）时先按 PK 删除再 insert。
        """
        if not rows:
            return {"upsert_count": 0}
        for r in rows:
            if len(r["embedding"]) != config.EMBEDDING_DIM:
                raise ValueError(
                    f"嵌入维度不匹配：chunk {r['chunk_id']} 为 {len(r['embedding'])} 维，"
                    f"集合 schema 为 {config.EMBEDDING_DIM} 维（EMBEDDING_DIM）")
        client = self._get_client()
        self.ensure()
        data = []
        for r in rows:
            data.append({
                "chunk_id": r["chunk_id"],
                "material_id": r["material_id"],
                "category": r.get("category", ""),
                "file_name": r.get("file_name", ""),
                "section_path": _truncate(r.get("section_path", ""), 1024),
                "page_start": r.get("page_start") or _NO_PAGE,
                "page_end": r.get("page_end") or _NO_PAGE,
                "block_ids": _truncate(json.dumps(r.get("block_ids", []), ensure_ascii=False), 512),
                "content": _truncate(r.get("content", ""), 8192),
                "embedding": r["embedding"],
            })
        pks = [r["chunk_id"] for r in data]
        try:
            res = client.insert(collection_name=self.collection, data=data)
        except Exception:  # noqa: BLE001 —— 主键冲突等：先删后插
            ids_expr = ", ".join(f'"{pk}"' for pk in pks)
            client.delete(collection_name=self.collection, filter=f"chunk_id in [{ids_expr}]")
            res = client.insert(collection_name=self.collection, data=data)
        return {"insert_count": res.get("insert_count", 0), "total": len(data)}

    def delete_material(self, material_id: str) -> dict:
        client = self._get_client()
        if not client.has_collection(self.collection):
            return {"delete_count": 0}
        res = client.delete(collection_name=self.collection,
                            filter=f'material_id == "{material_id}"')
        return {"delete_count": res.get("delete_count", 0)}

    def search(self, query_vector: list[float], top_k: int = 10,
               category: Optional[str] = None,
               material_id: Optional[str] = None) -> list[dict]:
        client = self._get_client()
        if not client.has_collection(self.collection):
            return []
        expr = ""
        conds = []
        if category:
            conds.append(f'category == "{category}"')
        if material_id:
            conds.append(f'material_id == "{material_id}"')
        expr = " and ".join(conds)
        results = client.search(
            collection_name=self.collection,
            data=[query_vector],
            limit=top_k,
            search_params=_SEARCH_PARAMS,
            output_fields=_OUTPUT_FIELDS,
            filter=expr or None,
        )[0]
        out = []
        for r in results:
            ent = r.get("entity", {})
            hit = {"chunk_id": ent.get("chunk_id", ""), "score": round(r.get("distance", 0.0), 6)}
            hit.update({k: ent.get(k) for k in _OUTPUT_FIELDS if k != "chunk_id"})
            out.append(hit)
        return out

    def info(self) -> dict:
        try:
            client = self._get_client()
            version = client.get_server_version()
            return {"reachable": True, "version": version,
                    "collection": self.collection,
                    "has_collection": client.has_collection(self.collection)}
        except Exception as e:  # noqa: BLE001 —— /health 必须兜住任何连接异常
            return {"reachable": False, "error": str(e)[:200]}


# ═══════════════════════════════════════════════════════════════════════
# SQLite 降级实现（numpy 暴力余弦）
# ═══════════════════════════════════════════════════════════════════════
class SqliteVectorStore(VectorStore):
    """SQLite 暴力余弦检索：kb_chunks.embedding JSON 即向量源。

    M2 全库 ~200 块规模，暴力扫描毫秒级；也是 Milvus 挂掉时的降级路径与
    离线测试的确定性载体。只读 kb_chunks（行生命周期由 run_kb_task/路由管理）。
    """

    name = "sqlite"

    def __init__(self, db: Optional[Database] = None):
        self.db = db or Database()

    def ensure(self) -> None:
        self.db.init_schema()  # kb_chunks 表由 DDL 幂等建出

    def upsert(self, rows: list[dict]) -> dict:
        """回填 embedding 列（行本身已由 run_kb_task 插入）。"""
        count = 0
        for r in rows:
            n = self.db.update("kb_chunks", "id", r["chunk_id"],
                               {"embedding": json.dumps(r["embedding"], ensure_ascii=False)})
            count += n
        return {"upsert_count": count, "total": len(rows)}

    def delete_material(self, material_id: str) -> dict:
        """清空该资料的向量（行删除由路由层执行）。"""
        n = self.db.execute(
            "UPDATE kb_chunks SET embedding='[]' WHERE material_id = ?", (material_id,))
        return {"delete_count": n}

    def search(self, query_vector: list[float], top_k: int = 10,
               category: Optional[str] = None,
               material_id: Optional[str] = None) -> list[dict]:
        import numpy as np  # 延迟导入：本模块 import 不依赖 numpy

        sql = "SELECT id, material_id, category, file_name, section_path, page_start, " \
              "page_end, block_ids, content, embedding FROM kb_chunks WHERE embedding != '[]'"
        params: list = []
        if category:
            sql += " AND category = ?"
            params.append(category)
        if material_id:
            sql += " AND material_id = ?"
            params.append(material_id)
        rows = self.db.query(sql, tuple(params))
        if not rows:
            return []
        qv = np.asarray(query_vector, dtype=np.float64)
        qn = float(np.linalg.norm(qv)) or 1.0
        scores: list[tuple[float, dict]] = []
        for row in rows:
            vec = np.asarray(json.loads(row["embedding"]), dtype=np.float64)
            n = float(np.linalg.norm(vec))
            if n == 0.0:
                continue
            cos = float(np.dot(qv, vec) / (qn * n))
            scores.append((cos, row))
        scores.sort(key=lambda x: -x[0])
        out = []
        for score, row in scores[:top_k]:
            out.append({
                "chunk_id": row["id"], "score": round(score, 6),
                "material_id": row["material_id"], "category": row["category"],
                "file_name": row["file_name"], "section_path": row["section_path"],
                "page_start": row["page_start"], "page_end": row["page_end"],
                "block_ids": json.loads(row["block_ids"] or "[]"),
                "content": row["content"],
            })
        return out

    def info(self) -> dict:
        return {"reachable": True, "engine": "sqlite-bruteforce"}


# ═══════════════════════════════════════════════════════════════════════
# 检索编排（降级链 + 四元溯源拼装）
# ═══════════════════════════════════════════════════════════════════════
def _hit_to_search_hit(db: Database, hit: dict) -> Optional[SearchHit]:
    """命中回 SQLite 取权威元数据（事实源铁律），拼完整四元溯源。"""
    row = db.query_one("SELECT * FROM kb_chunks WHERE id = ?", (hit["chunk_id"],))
    if row is None:
        return None  # Milvus 里有但 SQLite 已删（删除竞态），跳过
    # 页码同样回 SQLite 取权威值：Milvus 元数据可能过期（如重建前的旧值）
    page = row["page_start"] if row["page_start"] and row["page_start"] > 0 else None
    return SearchHit(
        chunk_id=hit["chunk_id"],
        material_id=row["material_id"],
        file_name=row["file_name"],
        category=row["category"],
        section_path=row["section_path"],
        page=page if page and page > 0 else None,
        score=hit["score"],
        content=row["content"],
        anchor=SourceAnchor(
            document=row["file_name"],
            doc_id=row["material_id"],
            page=page if page and page > 0 else None,
            section_path=row["section_path"],
            block_id=(json.loads(row["block_ids"] or "[]") or [row["id"]])[0],
            snippet=row["content"][:200],
        ),
    )


class SearchService:
    """语义检索编排：嵌入 → Milvus（挂则降级 SQLite）→ 拼四元溯源。"""

    def __init__(self, milvus_store: Optional[VectorStore] = None,
                 sqlite_store: Optional[VectorStore] = None,
                 embedding=None, db: Optional[Database] = None):
        from .embedding import create_embedding
        self.milvus = milvus_store
        self.sqlite = sqlite_store or SqliteVectorStore()
        self.embedding = embedding or create_embedding()
        self.db = db or Database()

    def search(self, query: str, top_k: int = 10,
               category: Optional[str] = None,
               material_id: Optional[str] = None) -> SearchResult:
        vec = self.embedding.embed_queries([query])[0]
        engine = "sqlite"
        hits: list[dict] = []
        if self.milvus is not None:
            try:
                hits = self.milvus.search(vec, top_k=top_k, category=category,
                                          material_id=material_id)
                engine = "milvus"
            except Exception as e:  # noqa: BLE001 —— 任何异常都降级，检索不能因 Milvus 挂而失败
                logger.warning("Milvus 检索失败，降级 SQLite 暴力余弦：%s", str(e)[:200])
        if engine != "milvus":
            hits = self.sqlite.search(vec, top_k=top_k, category=category,
                                      material_id=material_id)
        result = SearchResult(engine=engine)
        for hit in hits:
            sh = _hit_to_search_hit(self.db, hit)
            if sh is not None:
                result.hits.append(sh)
        return result

    def milvus_health(self) -> dict:
        """Milvus 健康状态（/health 用，短超时轻量）。"""
        if self.milvus is None:
            return {"status": "degraded", "reason": "disabled"}
        info = self.milvus.info()
        if info.get("reachable"):
            return {"status": "ok", "version": info.get("version", "")}
        return {"status": "degraded", "reason": info.get("error", "unreachable")}


# ---------------------------------------------------------------------------
# 工厂
# ---------------------------------------------------------------------------
_milvus_singleton: Optional[MilvusVectorStore] = None


def get_milvus_store() -> Optional[MilvusVectorStore]:
    """Milvus 存储单例；MILVUS_ENABLED=false 时返回 None（纯 SQLite 降级）。"""
    global _milvus_singleton
    if not config.MILVUS_ENABLED:
        return None
    if _milvus_singleton is None:
        _milvus_singleton = MilvusVectorStore()
    return _milvus_singleton


def reset_milvus_store() -> None:
    global _milvus_singleton
    _milvus_singleton = None


def create_search_service() -> SearchService:
    """检索服务工厂（测试 monkeypatch 目标）。"""
    return SearchService(milvus_store=get_milvus_store())


__all__ = ["VectorStore", "MilvusVectorStore", "SqliteVectorStore",
           "SearchService", "get_milvus_store", "create_search_service"]
