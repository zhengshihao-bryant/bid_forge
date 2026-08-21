# -*- coding: utf-8 -*-
"""
app/services/matching/ —— M3 需求-能力匹配（招标需求 × 企业能力的判定链）

主线（对应 M3-01～M3-15）：

    RawRequirement(M1) ──normalize──▶ CanonicalRequirement ──classify──▶ RequirementType
         ──extract──▶ Constraint(结构化) ──┐
                                            ├─▶ RuleEngine   ─┐
                                            ├─▶ CapabilityRetriever ─┐
                                            └─▶ SemanticRetriever(RAG) ─┤
                                               Evidence 池 ──validate──▶ VALID/INVALID
                                               ──rank──▶ 置信度 ──conflict──▶ 冲突仲裁
                                               ──judge──▶ FULL/PARTIAL/MISSING/UNKNOWN
                                               ──report──▶ 需求响应表 + 证据链

口径铁律：
- 四种状态 FULL/PARTIAL/MISSING/UNKNOWN 恒保留；没有证据 ≠ 不满足（MISSING 只由
  明确相反证据判定）
- Evidence 必须回原文精确匹配（INVALID 禁入高可信）；LLM 只能依据证据判定，
  不得补充企业事实
- 冲突无法仲裁 → UNKNOWN，绝不编造
"""
