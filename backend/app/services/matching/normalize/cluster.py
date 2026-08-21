# -*- coding: utf-8 -*-
"""
matching/normalize/cluster.py —— 相似需求聚类（M3-01 第二层）

把去重后的需求组聚成【语义簇】，每个簇归一为一条 CanonicalRequirement。
例如（跨类型归并）：

    REQ-001  需要具有园区平台建设经验      （技术要求）
    REQ-127  投标人应具备智慧园区项目经验  （资质要求）
    REQ-278  具有大型园区数字化平台建设能力（功能要求）
    ─────────────────────────────────────────────
    REQ-C-001  具备智慧园区数字化平台建设经验   source=[REQ-001, REQ-127, REQ-278]

阈值口径：
  - 同 M1 type：Jaccard ≥ same_type_threshold（默认 0.45，同章节近义改写）
  - 跨 M1 type：Jaccard ≥ cross_type_threshold（默认 0.60，更保守，防止误并）
  - 评分标准簇（M1 type=评分标准）只与评分标准簇合并，不与实体需求簇合并
    —— "评分细则与真正需求区分"由 normalizer 挂靠 parent 完成
"""
from __future__ import annotations

from ..similarity import jaccard_similarity

# M1 评分标准类型（评分细则不进入实体需求簇）
SCORING_TYPE = "评分标准"


def _scoring_group(rs: list) -> bool:
    return all(getattr(r, "type", None) and r.type.value == SCORING_TYPE for r in rs)


class RequirementClusterer:
    """相似需求聚类器（贪心 single-link，确定性）。"""

    def __init__(self, same_type_threshold: float = 0.45,
                 cross_type_threshold: float = 0.60):
        self.same_type_threshold = same_type_threshold
        self.cross_type_threshold = cross_type_threshold

    # ------------------------------------------------------------------
    def cluster(self, groups: list[list]) -> list[list]:
        """groups（去重后的需求组）→ 语义簇（每个簇是需求列表）。

        判定以各组的【代表】为锚（组内已去重，组间相似即簇间相似）。
        """
        clusters: list[list] = []
        # 锚点按重要度/★排序（与 deduplicator 口径一致，由调用方保证代表在前）
        for group in sorted(groups, key=_cluster_rank, reverse=True):
            anchor = group[0]
            placed = False
            for c in clusters:
                rep = c[0]          # 簇首需求即锚（组内已去重，代表在前）
                if _scoring_group([anchor]) != _scoring_group([rep]):
                    continue            # 评分细则不与实体需求同簇
                threshold = (self.same_type_threshold
                             if anchor.type.value == rep.type.value
                             else self.cross_type_threshold)
                if jaccard_similarity(anchor.title, rep.title) >= threshold:
                    c.extend(group)
                    placed = True
                    break
            if not placed:
                clusters.append(list(group))
        return clusters


def _cluster_rank(group: list) -> tuple:
    r = group[0]
    importance_order = {"高": 3, "中": 2, "低": 1}
    return (int(r.is_star), importance_order.get(r.importance, 0),
            len(r.original_text or ""))
