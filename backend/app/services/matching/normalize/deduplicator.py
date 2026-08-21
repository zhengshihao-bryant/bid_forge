# -*- coding: utf-8 -*-
"""
matching/normalize/deduplicator.py —— 需求去重（M3-01 第一层）

两级去重，均保留原始需求 ID 映射：
  1. 精确去重：键 (M1 type, 去空白 title) —— M1 已做过窗口级去重，这里是跨文件兜底
  2. 相似去重：字符 bigram Jaccard ≥ threshold 视为重复（标题 30 字内的近义改写）

输出结构：list[list[Requirement]]，每组第一个为【代表】（importance 最高、
is_star 优先），其余为被吸收的重复项 —— 全部成员进 source_requirement_ids。
"""
from __future__ import annotations

from ..similarity import jaccard_similarity, normalize_text


def exact_key(title: str, rtype: str) -> tuple:
    return (rtype, normalize_text(title))


def _rank_key(r) -> tuple:
    """代表选取优先级：★ 条款 > 重要度高 > 文本长（信息量多）。"""
    importance_order = {"高": 3, "中": 2, "低": 1}
    return (int(r.is_star), importance_order.get(r.importance, 0),
            len(r.original_text or ""))


def pick_representative(rs: list) -> object:
    """从一组重复需求中选代表（排序第一）。"""
    return sorted(rs, key=_rank_key, reverse=True)[0]


class Deduplicator:
    """需求去重器：精确键 + 相似度两级。threshold 为相似去重阈值。"""

    def __init__(self, sim_threshold: float = 0.85):
        self.sim_threshold = sim_threshold

    # ------------------------------------------------------------------
    def dedupe(self, reqs: list) -> tuple[list[list], dict]:
        """reqs: list[Requirement] → (groups, stats)。

        groups 每组 list[Requirement]（代表在前）；stats 含
        exact_dupes / sim_dupes / groups 计数（报告与测试断言用）。
        """
        # 1. 精确键聚合（跨文件兜底）
        exact_groups: dict[tuple, list] = {}
        for r in reqs:
            exact_groups.setdefault(exact_key(r.title, r.type.value), []).append(r)

        # 2. 相似合并：锚点按重要度排序贪心吸收（代表先做锚点，避免低质量吞高质量）
        anchors = sorted(
            (pick_representative(g) for g in exact_groups.values()),
            key=_rank_key, reverse=True)
        groups: list[list] = []
        absorbed: set[int] = set()
        for i, anchor in enumerate(anchors):
            if i in absorbed:
                continue
            group = list(exact_groups[exact_key(anchor.title, anchor.type.value)])
            for j, other in enumerate(anchors):
                if j == i or j in absorbed:
                    continue
                if jaccard_similarity(anchor.title, other.title) >= self.sim_threshold:
                    group.extend(exact_groups[exact_key(other.title, other.type.value)])
                    absorbed.add(j)
            groups.append(sorted(group, key=_rank_key, reverse=True))

        stats = {
            "input": len(reqs),
            # 精确去重吸收的需求条数 / 相似去重吸收的组数（组内成员随并入映射）
            "exact_dupes": len(reqs) - len(exact_groups),
            "sim_dupes": len(exact_groups) - len(groups),
            "groups": len(groups),
        }
        return groups, stats
