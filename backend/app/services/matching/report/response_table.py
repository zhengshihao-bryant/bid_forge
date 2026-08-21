# -*- coding: utf-8 -*-
"""
matching/report/response_table.py —— 需求响应表生成（M3-15）+ 证据链（M3-14）

输出两种形态：
  - JSON：requirements（规范需求 × 匹配结果 × 证据明细）
  - Markdown：需求响应表（招标要求 × 匹配结果 × 企业能力 × 证据 × 出处 × 置信度）
             + 逐条证据链（REQ-C → MATCH → EVD → CAP/chunk → DOC → 章节 → 原文）

每一条 FULL/PARTIAL/MISSING/UNKNOWN 都可点击/追溯到"我为什么得出这个结论"。
"""
from __future__ import annotations

import json
from typing import Optional

from .... import config
from ....db import Database
from ..models import MatchStatus, TraceLink

# 状态 → 中文标签（响应表展示列）
_STATUS_LABELS = {s: s.label for s in MatchStatus}

# 来源类型 → 中文
_SOURCE_LABELS = {"capability_card": "能力卡", "chunk": "知识块", "document": "资料"}


class ResponseTableBuilder:
    """需求响应表构建器：读库三表 → 组装 → Markdown/JSON。"""

    def __init__(self, db: Optional[Database] = None):
        self.db = db or Database(config.DB_PATH)

    # ------------------------------------------------------------------
    def build(self, tender_id: str) -> dict:
        """组装响应表数据：{tender_id, total, counts, rows}。

        rows[i] = {requirement, match, evidences[]}（证据含出处与原文片段）。
        """
        canonicals = [self.db.row_to_canonical(r) for r in self.db.query(
            "SELECT * FROM canonical_requirements WHERE tender_id = ? "
            "ORDER BY id", (tender_id,))]
        matches = {m.requirement_id: m for m in
                   (self.db.row_to_match(r) for r in self.db.query(
                       "SELECT * FROM requirement_matches WHERE tender_id = ?",
                       (tender_id,)))}
        evs = [self.db.row_to_evidence(r) for r in self.db.query(
            "SELECT * FROM evidences WHERE tender_id = ? ORDER BY id",
            (tender_id,))]
        by_req: dict[str, list] = {}
        for e in evs:
            by_req.setdefault(e.requirement_id, []).append(e)
        # 资料 id → 文件名（证据链 DOC 环节的展示名）
        doc_names = {r["id"]: r["file_name"] for r in self.db.query(
            "SELECT id, file_name FROM kb_materials", ())}

        rows = []
        for c in canonicals:
            if c.is_scoring:
                continue  # 评分细则不进响应表（挂靠 parent，细则不匹配能力）
            m = matches.get(c.id)
            evs = sorted(by_req.get(c.id, []), key=lambda e: -e.confidence)
            rows.append({
                "requirement": c,
                "match": m,
                "evidences": [self._evidence_dict(e, doc_names) for e in evs],
            })
        counts = {s.value: 0 for s in MatchStatus}
        for row in rows:
            if row["match"]:
                counts[row["match"].status.value] += 1
            else:
                counts[MatchStatus.UNKNOWN.value] += 1
        return {"tender_id": tender_id, "total": len(rows),
                "counts": counts, "rows": rows}

    # ------------------------------------------------------------------
    def trace_chain(self, match, evidences: list, doc_names: dict) -> list[TraceLink]:
        """证据链（M3-14）：MATCH → EVD → CAP/chunk → DOC → 章节 → 原文。"""
        links = []
        for e in evidences:
            doc = doc_names.get(e.document_id) or e.document_id or ""
            links.append(TraceLink(
                requirement_id=e.requirement_id,
                match_id=getattr(match, "id", ""),
                evidence_id=e.evidence_id,
                source_type=_SOURCE_LABELS.get(e.source_type.value,
                                               e.source_type.value),
                source_id=e.source_id,
                document=doc,
                section_path=e.section_path or "",
                page=e.page,
                block_id=e.block_id or "",
                snippet=(e.matched_text or e.content or "")[:200],
            ))
        return links

    @staticmethod
    def _evidence_dict(e, doc_names: dict) -> dict:
        return {
            "evidence_id": e.evidence_id,
            "source_type": _SOURCE_LABELS.get(e.source_type.value,
                                              e.source_type.value),
            "source_id": e.source_id,
            "document": doc_names.get(e.document_id) or e.document_id or "",
            "category": e.category,
            "section_path": e.section_path or "",
            "page": e.page,
            "block_id": e.block_id or "",
            "content": e.content[:300],
            "matched_text": e.matched_text[:200],
            "validation": e.validation.value,
            "confidence": e.confidence,
        }

    # ------------------------------------------------------------------
    def to_json(self, tender_id: str, pretty: bool = False) -> str:
        """响应表 JSON（rows 内嵌 match/evidences 的 model_dump 序列化）。"""
        data = self.build(tender_id)
        payload = {"tender_id": data["tender_id"], "total": data["total"],
                   "counts": data["counts"],
                   "rows": [{
                       "requirement": r["requirement"].model_dump(mode="json"),
                       "match": (r["match"].model_dump(mode="json")
                                 if r["match"] else None),
                       "evidences": r["evidences"],
                   } for r in data["rows"]]}
        return (json.dumps(payload, ensure_ascii=False, indent=2) if pretty
                else json.dumps(payload, ensure_ascii=False))

    # ------------------------------------------------------------------
    def to_markdown(self, tender_id: str) -> str:
        """需求响应表 Markdown（招标要求 × 匹配结果 × 企业能力 × 证据）。"""
        data = self.build(tender_id)
        counts = data["counts"]
        lines = [
            f"# 需求响应表（tender: {tender_id}）",
            "",
            f"共 **{data['total']}** 条规范需求："
            + "，".join(f"{MatchStatus(k).label} {v}" for k, v in counts.items()),
            "",
            "| # | 规范需求 | 类型 | 匹配结果 | 置信度 | 企业能力/证据 | 出处 |",
            "|---|---------|------|---------|--------|--------------|------|",
        ]
        for i, row in enumerate(data["rows"], 1):
            c, m, evs = row["requirement"], row["match"], row["evidences"]
            status = _STATUS_LABELS[m.status] if m else "待确认"
            conf = f"{m.confidence:.2f}" if m else "-"
            ability = (evs[0]["content"][:40] if evs else
                       ("-" if not m else m.reason[:40]))
            prov = ""
            if evs:
                prov = (f"{evs[0]['document']} " if evs[0]["document"] else "") \
                       + (f"第{evs[0]['page']}页" if evs[0]["page"] else "")
            lines.append(
                f"| {i} | {c.title} | {c.req_type.value} | **{status}** | "
                f"{conf} | {ability} | {prov} |")
        lines += ["", "## 逐条证据链", ""]
        for row in data["rows"]:
            c, m, evs = row["requirement"], row["match"], row["evidences"]
            if not m:
                lines.append(f"- {c.id} {c.title}：未匹配（无判定）")
                continue
            if not evs:
                lines.append(f"- {c.id} → {m.id}（{m.status.label}）：无证据")
                continue
            for e in evs[:3]:   # rows 里的证据是 _evidence_dict 的 dict（键访问）
                chain = " → ".join(x for x in (
                    c.id, m.id, e["evidence_id"],
                    f"{e['source_type']} {e['source_id']}", e["document"] or "",
                    e["section_path"] or "", f"第{e['page']}页" if e["page"] else "")
                    if x)
                snippet = (e["matched_text"] or e["content"] or "")[:120].replace("\n", " ")
                lines.append(f"- {chain}：{snippet}")
        return "\n".join(lines)


__all__ = ["ResponseTableBuilder"]
