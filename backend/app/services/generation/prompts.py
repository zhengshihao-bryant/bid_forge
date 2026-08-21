# -*- coding: utf-8 -*-
"""
generation/prompts.py —— M4-05 生成提示词（事实约束铁律）

方案型章节的 LLM 提示词把三件事写死：
1. 事实三分类（FACT / WRITING_STYLE / INFERENCE）—— 每句必须且只能标一类；
2. 匹配状态响应策略（FULL 正面+证据 / PARTIAL 差距+改进 / MISSING 如实+不声称 /
   UNKNOWN 待补充【待确认】）；
3. 硬约束：只引用【证据白名单】编号；未出现在证据/能力卡的数字原位标【待确认】；
   资质编号与人员姓名只取证据原文。

输出 JSON：{"paragraphs": [{"type", "text", "level", "fact_class", "evidence_ids"}]}
校验器（generator._validate_fact_constraints）在 LLM 输出之上再兜底一次。
"""
from __future__ import annotations

SOLUTION_SYSTEM = """你是资深标书撰写专家。请为指定章节撰写投标文件内容，严格遵守以下铁律。

## 事实三分类（每条语句必须且只能标注一类）
- FACT：企业事实断言。只能引用【证据白名单】中的 evidence_id；数字、资质编号、人员姓名只能来自证据白名单或企业能力卡，严禁编造。
- WRITING_STYLE：借鉴历史标书的写法/语气（措辞、行文结构），不是企业事实，不得包含具体企业数据承诺。
- INFERENCE：承诺/改进措施/过渡句（如"我司将组织专项团队"），不构成企业事实断言。

## 匹配状态响应策略（对每条需求按其状态撰写响应）
- FULL（满足）：正面陈述企业能力 + 引用证据编号；能力数值必须与证据一致。
- PARTIAL（部分满足）：先陈述已满足部分 + 引用证据，再如实说明差距，给出改进承诺（改进承诺标 INFERENCE）。
- MISSING（不满足）：如实写明当前不满足该指标，引用相反证据；严禁出现"我司已具备/完全满足/能够满足"等表述，不得编造满足数值。
- UNKNOWN（待确认）：写明"该项能力待补充确认"，涉及具体数值一律标注【待确认】；严禁编造。

## 硬约束
- 只能引用【证据白名单】中的 evidence_id，白名单之外的编号一律不用。
- 未出现在证据白名单/能力卡中的数字，必须在原位标注【待确认】。
- 资质编号（ISO9001、CMMI3、等保三级等）与人员姓名只能使用证据原文，禁止改写或自行编造。

## 输出 JSON
{"paragraphs": [{"type": "heading"|"paragraph"|"list_item"|"table", "text": "…", "level": 2, "fact_class": "FACT"|"WRITING_STYLE"|"INFERENCE", "evidence_ids": ["EVD-0001"]}]}
"""


def build_solution_user_prompt(ctx) -> str:
    """GenerationContext → 用户提示词（需求清单 + 证据白名单 + 能力卡 + 历史参考）。"""
    lines = [f"# 章节：{ctx.section.title}（{ctx.section.section_type.value}）"]
    if ctx.section.generation_prompt:
        lines.append(f"章节要求：{ctx.section.generation_prompt}")
    lines.append("")
    lines.append("## 需求清单（含匹配状态）")
    statuses = ctx.metadata.get("req_statuses", {})
    for r in ctx.requirements:
        st = statuses.get(r.id, "UNKNOWN")
        lines.append(f"- {r.id} [{r.req_type.value}] [{st}] {r.title}：{r.text}")
    lines.append("")
    lines.append("## 证据白名单（只能引用以下 evidence_id）")
    if not ctx.evidences:
        lines.append("（无企业事实证据 —— 涉及具体能力与数字一律标【待确认】，不得编造）")
    for e in ctx.evidences:
        lines.append(f"- {e.evidence_id}（{e.category}）：{e.content[:200]}")
    lines.append("")
    lines.append("## 企业能力卡（事实源，可引用其数值）")
    if not ctx.capability_cards:
        lines.append("（无能力卡）")
    for c in ctx.capability_cards:
        attrs = "；".join(f"{k}={v}" for k, v in c.attributes.items())
        lines.append(f"- {c.name}：{attrs}")
    lines.append("")
    if ctx.historical_examples:
        lines.append("## 历史标书参考（仅借鉴写法，不是企业事实）")
        for ex in ctx.historical_examples:
            lines.append(f"- {ex.source_document}：{ex.snippet[:120]}")
    return "\n".join(lines)


__all__ = ["SOLUTION_SYSTEM", "build_solution_user_prompt"]
