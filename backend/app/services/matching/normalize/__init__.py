# -*- coding: utf-8 -*-
"""matching.normalize —— M3-01 需求标准化（去重 / 聚类 / 归一）。"""
from .cluster import RequirementClusterer, SCORING_TYPE
from .deduplicator import Deduplicator, exact_key, pick_representative
from .normalizer import RequirementNormalizer, members_of

__all__ = ["RequirementClusterer", "SCORING_TYPE", "Deduplicator",
           "exact_key", "pick_representative", "RequirementNormalizer",
           "members_of"]
