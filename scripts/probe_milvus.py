# -*- coding: utf-8 -*-
"""
scripts/probe_milvus.py —— M2 第一步：Milvus 连通性 spike

验证 pymilvus 3.0.0 客户端（MilvusClient 新 API）× 本机 Milvus standalone 2.3.3 服务端
在 insert / search / 索引层的兼容性，并锁定 COSINE 分数方向（越大越相似）。

背景（设计阶段实测）：
- 连接层已通：get_server_version() → v2.3.3
- 只操作自己的临时集合 bid_chunks_probe，用完即 drop，绝不碰生产集合

用法：
    python scripts/probe_milvus.py

退出码：0 = 全部通过；1 = 失败（不兼容时应回退 pin pymilvus==2.4.x 重跑，
或按方案回退 B：MILVUS_ENABLED=false 走纯 SQLite 暴力余弦）
"""

from __future__ import annotations

import sys

# ── 控制台编码兜底（Windows GBK 控制台打印中文会崩）──
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# ── 配置 ──
URI = "http://localhost:19530"
PROBE_COLLECTION = "bid_chunks_probe"
VECTOR_DIM = 1024

# ── 分数方向验证用向量（已归一化；COSINE 越大越相似）──
QUERY = [1.0] + [0.0] * (VECTOR_DIM - 1)     # 与 A 同向 → 期望 1.0
VEC_A = [1.0] + [0.0] * (VECTOR_DIM - 1)     # 同向：score ≈ 1.0
VEC_B = [0.0, 1.0] + [0.0] * (VECTOR_DIM - 2)  # 正交：score ≈ 0.0
VEC_C = [-1.0] + [0.0] * (VECTOR_DIM - 1)    # 反向：score ≈ -1.0


def main() -> int:
    from pymilvus import MilvusClient, DataType

    client = MilvusClient(uri=URI)
    print(f"[1] 连接 {URI} → 服务端版本 {client.get_server_version()}")

    collections = client.list_collections()
    print(f"[2] 既有集合（只读，不碰）：{collections}")

    # 清理本脚本上次残留（仅自己的探针集合）
    if client.has_collection(PROBE_COLLECTION):
        client.drop_collection(PROBE_COLLECTION)
        print(f"[3] 清理旧探针集合 {PROBE_COLLECTION}")

    # ── 建集合（与生产 bid_chunks 同构：chunk_id VARCHAR 主键 + 1024 维）──
    schema = client.create_schema(auto_id=False, enable_dynamic_field=False)
    schema.add_field("chunk_id", DataType.VARCHAR, max_length=128, is_primary=True)
    schema.add_field("material_id", DataType.VARCHAR, max_length=64)
    schema.add_field("category", DataType.VARCHAR, max_length=32)
    schema.add_field("file_name", DataType.VARCHAR, max_length=255)
    schema.add_field("section_path", DataType.VARCHAR, max_length=1024)
    schema.add_field("page_start", DataType.INT64)
    schema.add_field("page_end", DataType.INT64)
    schema.add_field("block_ids", DataType.VARCHAR, max_length=512)
    schema.add_field("content", DataType.VARCHAR, max_length=8192)
    schema.add_field("embedding", DataType.FLOAT_VECTOR, dim=VECTOR_DIM)

    index_params = client.prepare_index_params()
    index_params.add_index(
        field_name="embedding",
        index_type="HNSW",
        metric_type="COSINE",
        params={"M": 16, "efConstruction": 200},
    )
    client.create_collection(PROBE_COLLECTION, schema=schema, index_params=index_params)
    print(f"[4] 建集合 {PROBE_COLLECTION}（HNSW + COSINE）OK")

    # ── 插入 3 条 ──
    rows = [
        {"chunk_id": "probe_A", "material_id": "probe_mat", "category": "产品",
         "file_name": "probe.docx", "section_path": "第一章", "page_start": 1,
         "page_end": 1, "block_ids": "[]", "content": "同向样本", "embedding": VEC_A},
        {"chunk_id": "probe_B", "material_id": "probe_mat", "category": "产品",
         "file_name": "probe.docx", "section_path": "第一章", "page_start": 2,
         "page_end": 2, "block_ids": "[]", "content": "正交样本", "embedding": VEC_B},
        {"chunk_id": "probe_C", "material_id": "probe_mat", "category": "产品",
         "file_name": "probe.docx", "section_path": "第一章", "page_start": 3,
         "page_end": 3, "block_ids": "[]", "content": "反向样本", "embedding": VEC_C},
    ]
    ids = client.insert(PROBE_COLLECTION, data=rows)
    print(f"[5] 插入 3 条 OK（ids={ids}）")

    # ── 检索：验证命中排序 + COSINE 分数方向 ──
    results = client.search(
        collection_name=PROBE_COLLECTION,
        data=[QUERY],
        limit=3,
        search_params={"metric_type": "COSINE", "params": {"ef": 64}},
        output_fields=["chunk_id", "content"],
    )[0]
    order = [(r["entity"]["chunk_id"], round(r["distance"], 4)) for r in results]
    print(f"[6] 检索结果（期望 A≈1.0 > B≈0.0 > C≈-1.0）：{order}")

    ok = True
    if not results:
        print("✗ 检索无结果")
        ok = False
    elif results[0]["entity"]["chunk_id"] != "probe_A":
        print("✗ 命中排序错误（top1 应为 probe_A）")
        ok = False
    elif results[0]["distance"] < 0.9:
        print("✗ COSINE 分数方向异常（同向 score 应≈1.0）")
        ok = False

    # ── 按 material 删除验证（生产 delete_material 依赖）──
    del_cnt = client.delete(PROBE_COLLECTION, filter='material_id == "probe_mat"')
    print(f"[7] 按 material_id 过滤删除 → {del_cnt}")

    client.drop_collection(PROBE_COLLECTION)
    print(f"[8] 探针集合已 drop，spike 完成")

    if ok:
        print("\n✔ spike 通过：pymilvus 3.0.0 × Milvus 2.3.3 全链路兼容，COSINE 越大越相似")
        return 0
    print("\n✗ spike 失败：见上方 ✗ 行；回退方案 A pin pymilvus==2.4.x，或方案 B MILVUS_ENABLED=false")
    return 1


if __name__ == "__main__":
    sys.exit(main())
