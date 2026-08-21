# -*- coding: utf-8 -*-
"""app.services —— 服务层（LLM 客户端 / 需求提取 / M2 起知识库与生成）。"""

from .llm import EnhancedLLMClient, MockLLMClient, create_llm_client  # noqa: F401
from .extraction import RequirementExtractor, parse_score_tables  # noqa: F401

__all__ = [
    "EnhancedLLMClient", "MockLLMClient", "create_llm_client",
    "RequirementExtractor", "parse_score_tables",
]
