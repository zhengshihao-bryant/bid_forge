# -*- coding: utf-8 -*-
"""
matching/pipeline —— M3-09/14 混合匹配管线

matcher.py：Matcher（标准化 → 逐条匹配 → 判定 → 落库 → 报告）
          + run_matching_task（后台任务入口，matching_runs 状态机）
"""
from .matcher import Matcher, run_matching_task

__all__ = ["Matcher", "run_matching_task"]
