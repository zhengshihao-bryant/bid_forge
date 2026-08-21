# -*- coding: utf-8 -*-
"""
app/services/embedding.py —— 文本嵌入服务（M2 企业知识库）

移植企业法律知识助手的成熟方案（legal-ai-assistant/services/embedding/bge_embedding.py），
本机踩坑结论（2026-08-14 实测）：

1. **必须 local_files_only=True**：全局环境变量 HF_ENDPOINT=https://hf-mirror.com
   网络不通，sentence-transformers 5.x 加载模型前会先发 hub HEAD 请求，
   不带此参数会超时 50s+ 甚至失败；实测本地缓存离线加载约 21s 成功。
2. BGE 官方约定：query 侧加指令前缀「为这个句子生成表示以用于检索相关文章：」，
   passage（文档/切块）侧不加。索引侧用 embed()，检索侧用 embed_queries()。
3. normalize_embeddings=True：Milvus COSINE 只比方向，归一化后计算稳定。
4. 模型懒加载单例：import 本模块不加载模型，首次 create_embedding() 才构造
   SentenceTransformer（~21s）；缺 sentence-transformers 时给出安装提示。

FakeEmbedding：双字 bigram hash 伪嵌入——确定性、且共享字串越多余弦越高，
离线测试可断言检索排序（真实语义召回由 @pytest.mark.milvus 集成测试覆盖）。

工厂 create_embedding() 是测试 monkeypatch 目标（沿用 M1 惯例：替换工厂而非客户端）。
"""

from __future__ import annotations

import hashlib
import logging
from typing import List, Optional

from .. import config

logger = logging.getLogger(__name__)

# BGE 官方建议的 query 侧指令前缀（passage 侧不用加）
_QUERY_INSTRUCTION = "为这个句子生成表示以用于检索相关文章："


class BaseEmbedding:
    """嵌入器抽象：M2 检索链路统一走此接口，便于离线测试替换。"""

    dimension: int = 1024

    def embed(self, texts: List[str], batch_size: int = 32) -> List[List[float]]:
        """批量文本 → {dimension} 维归一化向量（索引/切块侧）。"""
        raise NotImplementedError

    def embed_queries(self, queries: List[str], batch_size: int = 32) -> List[List[float]]:
        """检索 query 侧（BGE 需加指令前缀；Fake 与 embed 等价）。"""
        raise NotImplementedError


class BgeEmbedding(BaseEmbedding):
    """BGE 中文向量模型（sentence-transformers 实现，本地权重加载）。"""

    def __init__(self, model_name: str = None, dimension: int = None):
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError:
            raise ImportError(
                "需要安装 sentence-transformers（含 torch，约 2GB）：pip install sentence-transformers")
        self.model_name = model_name or config.EMBEDDING_MODEL
        self.dimension = dimension or config.EMBEDDING_DIM
        # local_files_only：HF_ENDPOINT 镜像网络不通，必须离线加载（见模块 docstring）
        self.model = SentenceTransformer(self.model_name, local_files_only=True)

    def embed(self, texts: List[str], batch_size: int = 32) -> List[List[float]]:
        vecs = self.model.encode(
            list(texts),
            batch_size=batch_size,
            normalize_embeddings=True,   # 长度归一化为 1，COSINE 计算稳定
            show_progress_bar=False,
        )
        return vecs.tolist()

    def embed_queries(self, queries: List[str], batch_size: int = 32) -> List[List[float]]:
        return self.embed([_QUERY_INSTRUCTION + q for q in queries], batch_size=batch_size)


class FakeEmbedding(BaseEmbedding):
    """确定性伪嵌入：双字 bigram hash 累加 + 归一化。

    共享字串越多余弦越高（同向可检索），且跨进程确定性（sha256 而非
    Python 内置 hash——内置 hash 对 str 每次进程盐值不同，测试会飘）。
    用途：离线测试 + 无 BGE 环境的降级演示。
    """

    def __init__(self, dimension: int = 64):
        self.dimension = dimension

    def _vec(self, text: str) -> List[float]:
        v = [0.0] * self.dimension
        grams = [text[i:i + 2] for i in range(len(text) - 1)] or [text]
        for g in grams:
            h = int.from_bytes(hashlib.sha256(g.encode("utf-8")).digest()[:8], "big")
            v[h % self.dimension] += 1.0
        norm = (sum(x * x for x in v) ** 0.5) or 1.0
        return [x / norm for x in v]

    def embed(self, texts: List[str], batch_size: int = 32) -> List[List[float]]:
        return [self._vec(t) for t in texts]

    def embed_queries(self, queries: List[str], batch_size: int = 32) -> List[List[float]]:
        return self.embed(queries, batch_size=batch_size)


# ---------------------------------------------------------------------------
# 工厂（懒加载单例）
# ---------------------------------------------------------------------------
_embedding_singleton: Optional[BaseEmbedding] = None


def create_embedding() -> BaseEmbedding:
    """嵌入器工厂：EMBEDDING_BACKEND=bge|fake（默认 bge）。

    懒加载单例（BGE 构造 ~21s，只加载一次）；测试用 monkeypatch 整体替换本函数。
    """
    global _embedding_singleton
    if _embedding_singleton is None:
        backend = config.EMBEDDING_BACKEND
        if backend == "fake":
            logger.info("嵌入后端：fake（确定性伪嵌入，离线模式）")
            _embedding_singleton = FakeEmbedding(dimension=config.EMBEDDING_DIM)
        else:
            logger.info("嵌入后端：%s（本地 BGE，首次加载约 20s）", config.EMBEDDING_MODEL)
            _embedding_singleton = BgeEmbedding()
    return _embedding_singleton


def reset_embedding() -> None:
    """清空单例（测试/切换配置用）。"""
    global _embedding_singleton
    _embedding_singleton = None


__all__ = ["BaseEmbedding", "BgeEmbedding", "FakeEmbedding", "create_embedding", "reset_embedding"]
