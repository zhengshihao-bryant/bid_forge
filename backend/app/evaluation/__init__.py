# -*- coding: utf-8 -*-
"""
app/evaluation —— M7-07 评估体系

离线评估四件套：
- golden.py  基线集（检索 8 条 + 需求基线 15 条，代码常量，与样例数据配套）
- metrics.py 指标实现（全部复用 M3/M5 现成件，无新算法）
- runner.py  评估执行器（run_retrieval / run_generation / run_trends）
- api.py     GET /api/eval/* 端点（每个响应带 disclaimer 口径声明——铁律）

确定性：评估本身不调 LLM；检索评估走 SearchService（离线环境
MILVUS_ENABLED=false + FakeEmbedding 时完全确定）。
"""
