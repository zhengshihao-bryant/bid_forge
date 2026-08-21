# -*- coding: utf-8 -*-
"""matching.classify —— M3-02 需求类型分类。"""
from .requirement_classifier import (RequirementClassifier, _KEYWORD_RULES,
                                     _TYPE_AFFINITY)

__all__ = ["RequirementClassifier", "_KEYWORD_RULES", "_TYPE_AFFINITY"]
