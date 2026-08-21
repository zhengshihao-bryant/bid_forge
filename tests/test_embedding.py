# -*- coding: utf-8 -*-
"""
tests/test_embedding.py —— 嵌入服务单元测试（离线）

FakeEmbedding（sha256 双字 bigram hash）是全部离线检索测试的确定性载体：
共享字串越多余弦越高。真实 BGE 语义召回由 @pytest.mark.milvus 集成测试覆盖。
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app import config  # noqa: E402
from app.services import embedding as embedding_module  # noqa: E402
from app.services.embedding import FakeEmbedding, create_embedding, reset_embedding  # noqa: E402


def _cos(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def test_fake_embedding_deterministic_and_normalized():
    emb = FakeEmbedding(dimension=64)
    v1 = emb.embed(["张伟项目经理6年经验"])[0]
    v2 = emb.embed(["张伟项目经理6年经验"])[0]
    assert v1 == v2  # 确定性（sha256，跨进程稳定，非内置 hash）
    assert abs(math.sqrt(sum(x * x for x in v1)) - 1.0) < 1e-6  # 归一化
    v3 = emb.embed(["完全不同的内容"])[0]
    assert v1 != v3


def test_fake_embedding_bigram_overlap_ranking():
    """共享字串越多余弦越高 —— 离线检索排序可断言的前提。"""
    emb = FakeEmbedding(dimension=64)
    base = emb.embed(["设备接入1000台"])[0]
    similar = emb.embed(["设备接入1000台并发500"])[0]
    unrelated = emb.embed(["篮球比赛精彩"])[0]
    assert _cos(base, similar) > _cos(base, unrelated)
    # 完全同文本 → 余弦为 1
    assert abs(_cos(base, base) - 1.0) < 1e-6


def test_fake_embedding_batch_and_queries():
    emb = FakeEmbedding(dimension=64)
    texts = ["第一段", "第二段", "第三段"]
    vecs = emb.embed(texts)
    assert len(vecs) == 3 and all(len(v) == 64 for v in vecs)
    # query 侧与索引侧等价（BGE 才有指令前缀差异）
    assert emb.embed_queries(["第一段"])[0] == emb.embed(["第一段"])[0]


def test_create_embedding_factory_switch(monkeypatch):
    """EMBEDDING_BACKEND=fake → 工厂返回 FakeEmbedding（dimension 跟随配置）。"""
    monkeypatch.setattr(config, "EMBEDDING_BACKEND", "fake")
    reset_embedding()
    emb = create_embedding()
    assert isinstance(emb, FakeEmbedding)
    assert emb.dimension == config.EMBEDDING_DIM
    reset_embedding()


def test_create_embedding_bge_backend_constructs_bge(monkeypatch):
    """bge 后端 → 工厂构造 BgeEmbedding 并缓存单例（替换类名验证，不真正加载模型 ~21s）。"""
    calls = []

    class _FakeBge:
        def __init__(self, model_name=None, dimension=None):
            calls.append((model_name, dimension))
            self.dimension = dimension

        def embed(self, texts, batch_size=32):
            return [[0.0]] * len(texts)

        def embed_queries(self, queries, batch_size=32):
            return [[0.0]] * len(queries)

    monkeypatch.setattr(embedding_module, "BgeEmbedding", _FakeBge)
    monkeypatch.setattr(config, "EMBEDDING_BACKEND", "bge")
    reset_embedding()
    emb = create_embedding()
    assert isinstance(emb, _FakeBge)
    # 模型名/维度默认值由 BgeEmbedding 内部取 config（工厂零参构造）
    assert calls == [(None, None)]
    # 懒加载单例：第二次调用不重复构造
    assert create_embedding() is emb
    assert len(calls) == 1
    reset_embedding()
