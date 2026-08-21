# -*- coding: utf-8 -*-
"""
generation/generator.py —— M4-06 章节生成器 + M4-05 事实约束校验

    SectionGenerator.generate_section(section) → SectionDraft
        上下文构建（M4-03/04）→ 策略分派（M4-08）→ 覆盖标记 → 证据引用收集
        → _validate_fact_constraints 事实校验（M4-05，双保险）→ Markdown 渲染

事实校验器（M4-05 落点，LLM 输出之上再兜底）：
1. 证据编号真实性：段落引用的 EVD 必须 ∈ 上下文证据白名单，否则剔除 + warning；
2. 无证据不得声称具备：MISSING/UNKNOWN 需求章节，FACT 段无证据却含
   「完全满足/具备/拥有」→ 降级 INFERENCE + warning；
3. FACT 数字溯源：FACT 段数字（_NUM_RE）不在事实语料（证据 + 能力卡 +
   需求原文）→ 原位标【待确认】+ warning；
4. 无任何事实语料却标 FACT → 降级 INFERENCE + warning。

边界（守住）：一致性命中/事实核验/完整性/质量留给 M5；这里只保证
"生成内容不凭空捏造企业事实"。
"""
from __future__ import annotations

import logging
import re
from typing import Optional

from ...db import Database
from ...schemas import DraftStatus
from .context import GenerationContextBuilder
from .models import (CoverageItem, EvidenceRef, FactClass, Paragraph, SectionDraft,
                     SectionType)
from .strategies import strategy_for

logger = logging.getLogger(__name__)

_NUM_RE = re.compile(r"\d+(?:\.\d+)?")
# 无证据不得声称具备（避开「不满足」等合法表述）
_CLAIM_RE = re.compile(r"(我司|我公司|本公司|我方)(已)?(完全满足|能够满足|可满足|具备|拥有)")


class SectionGenerator:
    """M4-06 章节生成器。"""

    def __init__(self, db: Optional[Database] = None, llm=None, retriever=None):
        self.db = db or Database(config.DB_PATH)
        self.llm = llm
        self.retriever = retriever

    # ------------------------------------------------------------------
    def generate_section(self, section, tender_id: str,
                         generation_id: str = "") -> SectionDraft:
        ctx = GenerationContextBuilder(self.db).build(section, retriever=self.retriever)
        self._enrich_ctx(ctx, tender_id)
        paragraphs = strategy_for(section, llm=self.llm).generate(ctx)
        draft = SectionDraft(
            section_id=section.id, tender_id=tender_id,
            generation_id=generation_id, title=section.title,
            section_type=section.section_type, paragraphs=paragraphs,
            requirement_coverage=self._coverage(ctx, section),
            status=DraftStatus.DRAFT,
        )
        draft.evidence_refs = self._evidence_refs(draft, ctx)
        self._validate_fact_constraints(draft, ctx)
        draft.content_md = render_markdown(draft)
        draft.generation_metadata = {
            "strategy": section.section_type.value,
            "evidence_count": len(ctx.evidences),
            "card_count": len(ctx.capability_cards),
            "requirement_count": len(ctx.requirements),
            "warning_count": len(draft.warnings),
        }
        return draft

    # ------------------------------------------------------------------
    def _enrich_ctx(self, ctx, tender_id: str):
        """上下文补充：需求匹配状态 + 招标项目名（策略/prompt 消费）。"""
        statuses: dict[str, str] = {}
        for r in ctx.requirements:
            m = self.db.query_one(
                "SELECT status FROM requirement_matches WHERE requirement_id = ?",
                (r.id,))
            statuses[r.id] = m["status"] if m else "UNKNOWN"
        ctx.metadata["req_statuses"] = statuses
        tender = self.db.query_one("SELECT name FROM tenders WHERE id = ?",
                                   (tender_id,))
        ctx.metadata["tender_name"] = (tender or {}).get("name", "")

    def _coverage(self, ctx, section) -> list[CoverageItem]:
        """需求 → 覆盖标记：固定格式章节不产出响应，其余标记已覆盖。"""
        statuses = ctx.metadata.get("req_statuses", {})
        fixed = section.section_type == SectionType.FIXED
        items = []
        for r in ctx.requirements:
            st = statuses.get(r.id, "UNKNOWN")
            items.append(CoverageItem(
                requirement_id=r.id, title=r.title, status=st,
                covered=False if fixed else True,
                note=("固定格式章节，模板输出，需人工填写" if fixed else "由策略生成")))
        return items

    def _evidence_refs(self, draft: SectionDraft, ctx) -> list[EvidenceRef]:
        """段落证据编号 → 溯源引用（含资料文件名，供 Markdown/DOCX 展示）。"""
        doc_names = {r["id"]: r["file_name"] for r in self.db.query(
            "SELECT id, file_name FROM kb_materials", ())}
        evs = {e.evidence_id: e for e in ctx.evidences}
        refs: list[EvidenceRef] = []
        seen: set[str] = set()
        for p in draft.paragraphs:
            for eid in p.evidence_ids:
                if eid in seen or eid not in evs:
                    continue
                seen.add(eid)
                e = evs[eid]
                refs.append(EvidenceRef(
                    evidence_id=eid, requirement_id=e.requirement_id,
                    quote=e.content[:200],
                    source_document=doc_names.get(e.document_id) or e.document_id or "",
                    page=e.page, section_path=e.section_path or "",
                    block_id=e.block_id or "", fact_class=FactClass.FACT))
        return refs

    # ------------------------------------------------------------------
    def _validate_fact_constraints(self, draft: SectionDraft, ctx):
        """M4-05 事实约束校验（确定性，LLM 输出之上兜底）。"""
        whitelist = {e.evidence_id for e in ctx.evidences}
        fact_corpus = self._fact_corpus(ctx)          # 证据 + 能力卡（企业事实源）
        allowed = fact_corpus + " " + " ".join(       # + 需求原文（招标口径，可提及）
            r.text + " " + r.title for r in ctx.requirements)
        statuses = ctx.metadata.get("req_statuses", {})
        has_blocked = any(s in ("MISSING", "UNKNOWN") for s in statuses.values())

        for p in draft.paragraphs:
            self._check_evidence_ids(p, draft, whitelist)
            self._check_no_claim(p, draft, has_blocked, fact_corpus)
            self._check_number_trace(p, draft, allowed)

    @staticmethod
    def _fact_corpus(ctx) -> str:
        parts = [e.content for e in ctx.evidences]
        for c in ctx.capability_cards:
            parts += [c.name, c.description]
            parts += [str(v) for v in c.attributes.values()]
        return " ".join(parts)

    @staticmethod
    def _check_evidence_ids(p: Paragraph, draft: SectionDraft, whitelist: set):
        bad = [i for i in p.evidence_ids if i not in whitelist]
        if bad:
            draft.warnings.append(f"段落引用不存在的证据编号：{','.join(bad)}")
            p.evidence_ids = [i for i in p.evidence_ids if i in whitelist]

    @staticmethod
    def _check_no_claim(p: Paragraph, draft: SectionDraft, has_blocked: bool,
                        fact_corpus: str):
        """MISSING/UNKNOWN 章节：FACT 段无证据却声称具备 → 降级 INFERENCE。

        无证据的 FACT 段仅当数值全部可回溯到能力卡/证据时才允许（能力卡回填）：
        - 含数值但数值不在事实语料 → 疑似编造具体能力 → 降级；
        - 不含数值但命中「完全满足/具备/拥有」措辞 → 降级。
        """
        if (p.fact_class != FactClass.FACT or p.evidence_ids
                or not has_blocked or not p.text):
            return
        nums = set(_NUM_RE.findall(p.text))
        if nums and not all(n in fact_corpus for n in nums):
            p.fact_class = FactClass.INFERENCE
            draft.warnings.append(
                f"MISSING/UNKNOWN 章节不得无证据声明具体能力数值：{p.text[:40]}…")
        elif _CLAIM_RE.search(p.text):
            p.fact_class = FactClass.INFERENCE
            draft.warnings.append(
                f"MISSING/UNKNOWN 章节不得无证据声称具备：{p.text[:40]}…")

    @staticmethod
    def _check_number_trace(p: Paragraph, draft: SectionDraft, allowed: str):
        """FACT 段数字必须在事实语料（证据 + 能力卡 + 需求原文）内，否则原位标【待确认】。"""
        if p.fact_class != FactClass.FACT or not p.text:
            return
        warned: set[str] = set()
        for num in set(_NUM_RE.findall(p.text)):
            if num in allowed or num in warned:
                continue
            warned.add(num)
            p.text = p.text.replace(num, f"{num}【待确认】")
            draft.warnings.append(
                f"数字「{num}」不在事实语料（证据/能力卡/需求原文），已原位标【待确认】")


# ---------------------------------------------------------------------------
def render_markdown(draft: SectionDraft) -> str:
    """结构化段落 → Markdown（M4-09 组装的单元）。"""
    lines = [f"## {draft.title}"]
    for p in draft.paragraphs:
        if p.type == "heading":
            lines.append(f"{'#' * max(1, p.level or 2)} {p.text}")
        elif p.type == "list_item":
            lines.append(f"- {p.text}")
        elif p.type == "table":
            lines.extend(_md_table(p.table))
        else:
            if p.text:
                lines.append(p.text)
    if draft.section_type != SectionType.FIXED:
        lines.append("")
        if draft.evidence_refs:
            refs = "、".join(
                f"{r.evidence_id}（{r.source_document or '企业资料'}）"
                for r in draft.evidence_refs[:10])
            lines.append(f"**本章证据依据：** {refs}")
        else:
            lines.append("**本章证据依据：** 无直接证据引用（基于企业能力卡/模板）")
    return "\n".join(lines)


def _md_table(rows: list[list]) -> list[str]:
    if not rows:
        return []
    ncols = max(len(r) for r in rows)
    norm = [([str(c) for c in r] + [""] * (ncols - len(r))) for r in rows]
    out = ["| " + " | ".join(norm[0]) + " |"]
    out.append("| " + " | ".join(["---"] * ncols) + " |")
    out += ["| " + " | ".join(r) + " |" for r in norm[1:]]
    return out


__all__ = ["SectionGenerator", "render_markdown", "_md_table"]
