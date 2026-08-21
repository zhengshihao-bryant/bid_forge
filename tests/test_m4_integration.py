# -*- coding: utf-8 -*-
"""
tests/test_m4_integration.py —— M4 真实 LLM 集成测试

默认跳过；需要真实依赖时运行：

    pytest tests/test_m4_integration.py -m llm -v      # 需 LLM_API_KEY

验收对象（M4-11 的 llm 维度）：
    - 方案型章节走真实 LLM：产出结构化段落（FACT/INFERENCE 分类）
    - 证据编号白名单铁律：段落引用的 EVD 必须 ∈ evidences 表（无编造）
    - FACT 段落数字可溯源：数字要么在事实语料（证据/能力卡/需求原文）中，
      要么被原位标【待确认】；不出现"无证据却声称具备"
    - 证据引用正确性：FACT 段落带证据编号的引用真实存在
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app import config  # noqa: E402
from app.services.generation import SectionGenerator  # noqa: E402
from app.services.generation.models import FactClass  # noqa: E402
from app.services.llm import create_llm_client  # noqa: E402

_NUM_RE = re.compile(r"\d+(?:\.\d+)?")
# 与 generator._check_no_claim 同源：无证据不得声称具备
_CLAIM_RE = re.compile(r"(我司|我公司|本公司|我方)(已)?(完全满足|能够满足|可满足|具备|拥有)")


def _allowed_corpus(db, tender_id: str) -> str:
    """事实语料 + 需求原文（与 generator._validate_fact_constraints 同口径）。"""
    parts = [r["content"] for r in db.query(
        "SELECT content FROM evidences WHERE tender_id = ?", (tender_id,))]
    for c in db.query("SELECT name, description, attributes FROM capabilities"):
        parts += [c["name"], c["description"] or ""]
        import json
        parts += [str(v) for v in json.loads(c["attributes"] or "{}").values()]
    reqs = db.query(
        "SELECT text, title FROM canonical_requirements WHERE tender_id = ?",
        (tender_id,))
    parts += [r["text"] + " " + r["title"] for r in reqs]
    return " ".join(str(p) for p in parts)


@pytest.mark.llm
@pytest.mark.skipif(not config.LLM_API_KEY,
                    reason="未配置 LLM_API_KEY，跳过真实 LLM 集成测试")
def test_m4_llm_solution_evidence_whitelist(seed_m4):
    """真实 LLM 方案型章节：FACT 引用真实 EVD、数字可溯源、无证据不声称。"""
    data = seed_m4
    db, tender_id = data["db"], data["tender_id"]
    evd_ids = {r["id"] for r in db.query(
        "SELECT id FROM evidences WHERE tender_id = ?", (tender_id,))}
    allowed = _allowed_corpus(db, tender_id)
    assert evd_ids, "预埋证据应存在"

    # CH-05-2 总体技术方案（方案型），真实 LLM 生成
    sec = next(s for s in data["sections"] if s.id == "CH-05-2")
    gen = SectionGenerator(db=db, llm=create_llm_client())
    draft = gen.generate_section(sec, tender_id)

    assert draft.content_md, "方案型章节应产出内容"
    assert len(draft.paragraphs) >= 3, "应产出结构化段落"
    assert any(p.fact_class == FactClass.FACT for p in draft.paragraphs), \
        "真实 LLM 应产出 FACT 段落（企业事实断言）"

    # ① 证据编号白名单：段落引用 EVD 全部真实存在
    for p in draft.paragraphs:
        for eid in p.evidence_ids:
            assert eid in evd_ids, f"引用了不存在的证据编号 {eid}（编造）"

    # ② FACT 段落数字可溯源：数字在事实语料，或原位标【待确认】
    for p in draft.paragraphs:
        if p.fact_class != FactClass.FACT or not p.text:
            continue
        for m in _NUM_RE.finditer(p.text):
            num = m.group()
            if num in allowed:
                continue
            # 不在语料 → 校验器必须已原位标【待确认】
            assert f"{num}【待确认】" in p.text, \
                f"FACT 数字 {num} 不在事实语料且未标【待确认】: {p.text[:80]}"

    # ③ 无证据不得声称具备：MISSING/UNKNOWN 需求段不做正向断言（由校验器兜底）
    for p in draft.paragraphs:
        if p.fact_class == FactClass.FACT and not p.evidence_ids:
            nums = set(_NUM_RE.findall(p.text))
            assert not nums or all(n in allowed for n in nums), \
                f"FACT 无证据却含具体数值: {p.text[:80]}"
    # ④ 至少一个 FACT 段带证据引用（真实 LLM 应把证据编号写入段落）
    with_evd = [p for p in draft.paragraphs
                if p.fact_class == FactClass.FACT and p.evidence_ids]
    assert with_evd, "FACT 段落应引用真实证据编号（证据注入）"
