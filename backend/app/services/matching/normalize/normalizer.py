# -*- coding: utf-8 -*-
"""
matching/normalize/normalizer.py —— RequirementNormalizer（M3-01 核心）

管线：

    RawRequirement(M1) ──Deduplicator──▶ 去重组
        ──RequirementClusterer──▶ 语义簇
        ──簇内归一──▶ CanonicalRequirement（REQ-C-XXXX）

簇内归一两种路径：
  - LLM 路径（默认，有 Key）：批量把簇成员扩写合并为一条规范化需求
    （title + text，JSON 提示词，重试 3 次，坏条丢弃）
  - 确定性回退（Mock/失败）：取簇内代表成员（★ > 高 > 中 > 低 > 文本长）的
    title/original_text 作为规范文本 —— 数字绝不改写

恒保留：
  - source_requirement_ids：簇内全部原始需求 ID（REQ-001/REQ-127/REQ-278）
  - sources：逐条四元出处（document/page/section_path/snippet）
  - parent_requirement_id：评分细则挂靠实体需求（区分"评分细则与真正需求"）
  - is_scoring：评分细则标记，不参与能力匹配
"""
from __future__ import annotations

import logging
from typing import Optional

from ..similarity import jaccard_similarity
from .cluster import SCORING_TYPE, RequirementClusterer
from .deduplicator import Deduplicator
from ..models import CanonicalRequirement, RequirementSourceRef, RequirementTypeM3

logger = logging.getLogger(__name__)

# 评分细则特征（评分标准类型之外，原文含评分措辞也视为细则）
_SCORING_HINTS = ("评分", "得分", "评审", "分值", "打分")

_NORMALIZE_SYSTEM = """你是一名资深投标顾问，负责把多条【相似的需求条目】扩写合并为一条规范化需求。

铁律（事实约束）：
1. 只能合并原文中明确写出的要求，绝不补充、推测或改写数字
2. 量化指标必须原样保留：数值、比较符（≥/≤/不少于/不高于）、单位
3. 标题 ≤30 字；正文 ≤120 字，一句完整表述
4. 若输入条目并非同一需求，输出 should_merge=false

输出 JSON 格式（务必只输出 JSON）：
{"title": "一句话概括(≤30字)", "text": "规范化需求陈述(≤120字)", "should_merge": true或false}"""


def _is_scoring_member(r) -> bool:
    """评分细则判定：M1 类型为评分标准，或原文含评分措辞。"""
    if getattr(r, "type", None) and r.type.value == SCORING_TYPE:
        return True
    haystack = f"{r.title} {r.original_text}"
    return any(kw in haystack for kw in _SCORING_HINTS)


def _rank_key(r) -> tuple:
    importance_order = {"高": 3, "中": 2, "低": 1}
    return (int(r.is_star), importance_order.get(r.importance, 0),
            len(r.original_text or ""))


def _to_source_ref(r) -> RequirementSourceRef:
    src = r.source
    return RequirementSourceRef(
        id=r.id,
        title=r.title,
        original_text=r.original_text,
        type=r.type.value if r.type else "",
        importance=r.importance,
        is_star=r.is_star,
        document=(src.document if src else "") or "",
        doc_id=(src.doc_id if src else "") or "",
        page=src.page if src else None,
        section_path=(src.section_path if src else "") or "",
        block_id=(src.block_id if src else "") or "",
        snippet=(src.snippet if src else "") or "",
    )


class RequirementNormalizer:
    """需求标准化器：去重 → 聚类 → 簇归一 → REQ-C 编号 + 出处映射。"""

    def __init__(self, client=None, use_llm: Optional[bool] = None,
                 dedup_threshold: float = 0.85,
                 same_type_threshold: float = 0.45,
                 cross_type_threshold: float = 0.60,
                 batch_size: int = 12):
        self.client = client
        self.use_llm = use_llm
        self.deduplicator = Deduplicator(sim_threshold=dedup_threshold)
        self.cross_type_threshold = cross_type_threshold
        self.clusterer = RequirementClusterer(
            same_type_threshold=same_type_threshold,
            cross_type_threshold=cross_type_threshold)
        self.batch_size = batch_size

    # ------------------------------------------------------------------
    def normalize(self, tender_id: str, reqs: list,
                  classifier=None, extractor=None) -> tuple[list[CanonicalRequirement], dict]:
        """M1 Requirement 列表 → CanonicalRequirement 列表。

        classifier/extractor 可选注入（分类 + 约束提取内联完成，省一遍遍历；
        不注入则仅做归并，后续由 matcher 补做）。
        """
        stats = {"input": len(reqs), "llm_calls": 0, "llm_merged": 0,
                 "llm_rejected": 0, "scoring_linked": 0}
        groups, dedup_stats = self.deduplicator.dedupe(reqs)
        stats.update(dedup_stats)
        clusters = self.clusterer.cluster(groups)
        stats["clusters"] = len(clusters)

        # 簇归一（LLM 合并；无 LLM 时确定性回退）
        canonicals: list[CanonicalRequirement] = []
        merged_titles: dict[int, dict] = {}
        if self._llm_enabled():
            merged_titles = self._llm_merge(clusters, stats)

        scoring_ids: list[int] = []      # 评分细则簇下标
        for ci, cluster in enumerate(clusters):
            members = sorted(cluster, key=_rank_key, reverse=True)
            rep = members[0]
            is_scoring = all(_is_scoring_member(m) for m in members)
            merge = merged_titles.get(ci) or {}
            title = (merge.get("title") or "").strip() or rep.title
            text = (merge.get("text") or "").strip() or rep.original_text
            canonical = CanonicalRequirement(
                tender_id=tender_id,
                req_type=RequirementTypeM3.OTHER,   # classify 阶段回填
                title=title[:60],
                text=text[:300],
                source_requirement_ids=[m.id for m in members],
                importance=members[0].importance,
                is_star=any(m.is_star for m in members),
                is_scoring=is_scoring,
                constraints=[],
                sources=[_to_source_ref(m) for m in members],
                merge_method=(merge.get("method") or
                              ("similarity" if len(members) > 1 else "exact")),
            )
            canonicals.append(canonical)
            if is_scoring:
                scoring_ids.append(ci)

        # 编号（REQ-C-0001 起）
        for i, c in enumerate(canonicals):
            c.id = f"REQ-C-{i + 1:04d}"

        # 评分细则挂靠实体需求（parent_requirement_id：区分细则与真正需求）
        for si in scoring_ids:
            scoring = canonicals[si]
            best_idx, best_sim = None, 0.0
            for i, c in enumerate(canonicals):
                if i == si or c.is_scoring:
                    continue
                sim = jaccard_similarity(scoring.title, c.title)
                if sim > best_sim:
                    best_idx, best_sim = i, sim
            if best_idx is not None and best_sim >= self.cross_type_threshold:
                scoring.parent_requirement_id = canonicals[best_idx].id
                stats["scoring_linked"] += 1

        # 分类 + 约束提取（内联注入时）
        if classifier is not None:
            for c in canonicals:
                if c.is_scoring:
                    c.req_type = RequirementTypeM3.OTHER
                else:
                    c.req_type = classifier.classify(
                        c.title, c.text,
                        m1_types=[s.type for s in c.sources])
        if extractor is not None:
            for c in canonicals:
                if not c.is_scoring:
                    # 逐成员提取再并集：合并簇的约束来自各成员原文
                    # （规范标题/正文只代表 rep 或 LLM 扩写，不能丢失成员约束）
                    constraints: list = []
                    seen_keys: set[tuple] = set()
                    for m in members_of(c, reqs):
                        for con in extractor.extract(
                                m.title, m.original_text,
                                quantitative=m.quantitative or [],
                                req_type=c.req_type):
                            key = (con.attribute, con.operator, con.value,
                                   con.unit, con.subject)
                            if key not in seen_keys:
                                seen_keys.add(key)
                                constraints.append(con)
                    c.constraints = constraints

        logger.info("标准化完成 tender=%s: %d → %d 条规范需求（%d 簇 / LLM 调用 %d）",
                    tender_id, stats["input"], len(canonicals), stats["clusters"],
                    stats["llm_calls"])
        return canonicals, stats

    # ------------------------------------------------------------------
    def _llm_enabled(self) -> bool:
        if self.use_llm is not None:
            return self.use_llm
        if self.client is None:
            return False
        return getattr(self.client, "model", "") not in ("mock", "fake")

    def _llm_merge(self, clusters: list[list], stats: dict) -> dict[int, dict]:
        """簇 → LLM 批量合并。返回 {簇下标: {title, text, method}}；失败簇回退。"""
        out: dict[int, dict] = {}
        for start in range(0, len(clusters), self.batch_size):
            batch = clusters[start:start + self.batch_size]
            items = []
            for offset, cluster in enumerate(batch):
                members = sorted(cluster, key=_rank_key, reverse=True)
                items.append(
                    f"[{offset}] " + " | ".join(
                        f"{m.title}（{m.original_text[:80]}）" for m in members[:6]))
            user = ("以下每组是同一需求的相似条目（不同写法），请逐组合并为一条"
                    "规范化需求。若某组条目并非同一需求，should_merge=false。\n\n"
                    + "\n\n".join(items)
                    + "\n\n输出：{\"results\": [{\"index\": 组编号, \"title\": \"...\", "
                      "\"text\": \"...\", \"should_merge\": true}...]}")
            resp = self.client.chat_json(_NORMALIZE_SYSTEM, user, max_tokens=4096)
            stats["llm_calls"] += 1
            if resp is None:
                continue
            results = (resp.get("data") or {}).get("results")
            if not isinstance(results, list):
                continue
            for item in results:
                if not isinstance(item, dict):
                    continue
                idx = item.get("index")
                if not isinstance(idx, int) or not (0 <= idx < len(batch)):
                    continue
                title = str(item.get("title") or "").strip()
                text = str(item.get("text") or "").strip()
                if not title or not text or item.get("should_merge") is False:
                    stats["llm_rejected"] += 1
                    continue
                out[start + idx] = {"title": title, "text": text, "method": "llm"}
                stats["llm_merged"] += 1
        return out


def members_of(canonical: CanonicalRequirement, reqs: list) -> list:
    """规范需求 → 原始 Requirement 对象（约束提取/溯源用）。"""
    by_id = {r.id: r for r in reqs}
    return [by_id[i] for i in canonical.source_requirement_ids if i in by_id]
