# -*- coding: utf-8 -*-
"""
quality/checks/llm_judge.py —— M5-13 语义覆盖二次审查（可选增强）

只判语义覆盖：对每个已映射章节的规范需求，把「需求文本 + 相关章节内容」
交给 LLM 判定 covered 布尔与理由。**只出 SEMANTIC_COVERAGE issue**；
数字/证书/证据存在性等硬事实仍由确定性程序（facts/completeness/
consistency）负责，LLM 不碰。

离线口径（防误报与防不可用）：
- 未启用（include_llm=False）→ 完全不调用；
- FakeLLM / 无 Key / 调用异常 / JSON 解析失败 → 空返回，不新增 issue；
- 需求无章节映射 → 无可判内容，跳过（不误报）。

每次调用一条需求（调用次数 = 已映射需求数；接口默认不带该参数，
仅验收脚本与集成测试显式 include_llm=true 时触发）。
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Optional

from ..models import CheckContext, IssueType, QualityIssue, Severity

_SYSTEM_PROMPT = (
    "你是标书质量审查员。对给定的一条招标需求与其对应标书章节内容，"
    "判定该需求是否被标书正文充分响应（语义覆盖）。"
    "只判断语义层面是否覆盖了需求的实质内容，不看数字大小是否正确。"
    "返回 JSON：{\"covered\": true/false, \"reason\": \"一句话理由\"}。"
    "若正文确实提及并实质性回应了该需求 → covered=true；"
    "正文未提及、或仅泛泛而过无实质回应 → covered=false。")

_SECTION_CAP = 2          # 单需求最多取 2 个章节参与判定
_CHAR_CAP = 500           # 单章节截断长度


def check_semantic_coverage(ctx: CheckContext, llm) -> list[QualityIssue]:
    """逐需求 LLM 语义覆盖判定。离线/失败/无映射 → 空。"""
    issues: list[QualityIssue] = []
    sections = {s.get("section_id"): s for s in ctx.sections}
    mapped: dict[str, list[str]] = defaultdict(list)
    for m in ctx.section_maps:
        if m.get("requirement_id"):
            mapped[m["requirement_id"]].append(m.get("section_id") or "")
    seen: set[str] = set()          # 同一需求多次调用去重（一个需求一条判定）

    for c in ctx.canonicals:
        if c.get("is_scoring"):
            continue
        rid = c.get("id") or ""
        if rid in seen:
            continue
        seen.add(rid)
        sids = [s for s in mapped.get(rid, []) if s in sections][:_SECTION_CAP]
        if not sids:
            continue
        body = "\n".join(
            f"[{sid}]\n{(sections[sid].get('content_md') or '')[:_CHAR_CAP]}"
            for sid in sids)
        data = _judge(llm, c, body)
        if data is None:
            continue                     # 离线/失败 → 确定性兜底，不新增
        if data.get("covered"):
            continue
        reason = str(data.get("reason") or "")[:_CHAR_CAP]
        issues.append(QualityIssue(
            section_id=sids[0], requirement_id=rid,
            issue_type=IssueType.SEMANTIC_COVERAGE, severity=Severity.WARNING,
            message=f"LLM 二次审查：需求「{c.get('title')}」相关章节语义覆盖不足"
                    + (f"（{reason}）" if reason else ""),
            source_refs=[{"requirement": rid, "sections": sids}],
            suggestion="在对应章节补充对该需求实质内容的响应"))
    return issues


def _judge(llm, req: dict, body: str) -> Optional[dict]:
    """单次调用并解析。返回 {"covered": bool, "reason": str}；失败 → None。"""
    title = req.get("title") or ""
    text = req.get("text") or ""
    user = (f"招标需求：{title}\n{text}\n\n"
            f"对应标书章节内容：\n{body}\n\n"
            f"请判定 covered 并给理由。")
    try:
        resp = llm.chat_json(_SYSTEM_PROMPT, user, temperature=0.0)
    except Exception:
        return None
    data = resp.get("data") if isinstance(resp, dict) else None
    if not isinstance(data, dict):
        return None
    covered = data.get("covered")
    if not isinstance(covered, bool):
        return None
    return {"covered": covered, "reason": str(data.get("reason") or "")}


__all__ = ["check_semantic_coverage"]
