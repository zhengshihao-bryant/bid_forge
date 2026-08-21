# -*- coding: utf-8 -*-
"""
matching/retrieve/semantic_retriever.py —— RAG 语义匹配检索（M3-08）

无法通过结构化规则直接判断的需求（如"具备大型智慧园区综合运营平台建设经验"）
走 RAG：Embedding → Milvus（挂自动降级 SQLite）→ Top-K → 规则 Rerank。

Rerank 不引入新模型（M3 不扩展技术栈）：余弦 ×0.5 + 关键词重叠 ×0.3 +
类别亲和 ×0.2 —— 确定性、可解释，离线测试可断言排序。
"""
from __future__ import annotations

import logging
from typing import Optional

from ....schemas import CapabilityCategory, SearchHit
from ..models import CanonicalRequirement, RequirementTypeM3
from ..similarity import key_overlap
from .capability_retriever import TYPE_CATEGORY_MAP

logger = logging.getLogger(__name__)

# Rerank 权重（余弦 / 关键词 / 类别亲和）
_W_COS, _W_KW, _W_CAT = 0.5, 0.3, 0.2


class Reranker:
    """规则 Reranker：对 SearchHit 重排（确定性）。"""

    def __init__(self, w_cos: float = _W_COS, w_kw: float = _W_KW,
                 w_cat: float = _W_CAT):
        self.w_cos, self.w_kw, self.w_cat = w_cos, w_kw, w_cat

    # ------------------------------------------------------------------
    def rerank(self, req: CanonicalRequirement,
               hits: list[SearchHit]) -> list[tuple[SearchHit, float]]:
        """hits → [(hit, final_score)] 按 final 降序。"""
        query = f"{req.title} {req.text}"
        allowed = {c.value for c in TYPE_CATEGORY_MAP.get(req.req_type, [])}
        out: list[tuple[SearchHit, float]] = []
        for hit in hits:
            cos = max(0.0, hit.score)                     # COSINE 可为负（SQLite 路径）
            kw = key_overlap(query, hit.content)
            cat = 1.0 if hit.category in allowed else 0.2
            final = self.w_cos * cos + self.w_kw * kw + self.w_cat * cat
            out.append((hit, round(final, 6)))
        out.sort(key=lambda x: -x[1])
        return out


class SemanticRetriever:
    """RAG 检索编排：M2 SearchService → Reranker。"""

    def __init__(self, search_service=None, reranker: Optional[Reranker] = None,
                 top_k: int = 8):
        self.search_service = search_service
        self.reranker = reranker or Reranker()
        self.top_k = top_k

    def _service(self):
        if self.search_service is None:
            from ...vector_store import create_search_service  # 延迟：离线测试注入 mock
            self.search_service = create_search_service()
        return self.search_service

    # ------------------------------------------------------------------
    def retrieve(self, req: CanonicalRequirement,
                 categories: Optional[list[CapabilityCategory]] = None,
                 ) -> list[tuple[SearchHit, float]]:
        """规范需求 → [(SearchHit, final_score)]。

        类别过滤逐类查询（Milvus filter 单值）：先按映射类别查，不足再放开。
        """
        query = f"{req.title}。{req.text}"
        service = self._service()
        raw_hits: list[SearchHit] = []
        seen: set[str] = set()
        if categories is None:
            categories = TYPE_CATEGORY_MAP.get(req.req_type, [])
        for cat in categories or []:
            try:
                result = service.search(query, top_k=self.top_k, category=cat.value)
            except Exception as e:  # noqa: BLE001 —— 单类检索失败不阻断
                logger.warning("RAG 类别检索失败 %s: %s", cat.value, str(e)[:150])
                continue
            for h in result.hits:
                if h.chunk_id not in seen:
                    seen.add(h.chunk_id)
                    raw_hits.append(h)
        if len(raw_hits) < self.top_k // 2:
            try:
                result = service.search(query, top_k=self.top_k)
                for h in result.hits:
                    if h.chunk_id not in seen:
                        seen.add(h.chunk_id)
                        raw_hits.append(h)
            except Exception as e:  # noqa: BLE001
                logger.warning("RAG 全量检索失败: %s", str(e)[:150])
        return self.reranker.rerank(req, raw_hits)


__all__ = ["SemanticRetriever", "Reranker"]
