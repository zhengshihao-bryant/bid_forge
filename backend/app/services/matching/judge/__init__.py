# -*- coding: utf-8 -*-
"""
matching/judge —— M3-11 LLM Judge + 启发式回退

- llm_judge：LLMJudge（严格 JSON {status, confidence, reason, evidence_ids}）
           + HeuristicJudge（确定性回退：规则结论优先 / 冲突未决 → UNKNOWN）
"""
from .llm_judge import LLMJudge, HeuristicJudge, JudgeVerdict, render_evidence

__all__ = ["LLMJudge", "HeuristicJudge", "JudgeVerdict", "render_evidence"]
