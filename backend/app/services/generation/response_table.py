# -*- coding: utf-8 -*-
"""
generation/response_table.py —— M4-07 需求响应表生成

三列：招标要求 | 企业响应 | 证据（覆盖全部规范需求，含未映射章节的）。
MISSING/UNKNOWN 不编造：响应列只如实陈述状态与差距（引用相反证据），
不声称具备，具体数值一律【待确认】。输出 JSON / Markdown 两态。
"""
from __future__ import annotations

import json
from typing import Optional

from ... import config
from ...db import Database
from ..matching.models import MatchStatus

_STATUS_LABELS = {s: s.label for s in MatchStatus}


class BidResponseTableBuilder:
    """M4-07 三列需求响应表。"""

    def __init__(self, db: Optional[Database] = None):
        self.db = db or Database(config.DB_PATH)

    # ------------------------------------------------------------------
    def build(self, tender_id: str) -> dict:
        """组装响应表数据：{tender_id, total, counts, rows}。

        rows[i] = {requirement_id, title, req_type, text, status, reason,
                   evidence_ids, evidences[], response}。
        """
        canonicals = [self.db.row_to_canonical(r) for r in self.db.query(
            "SELECT * FROM canonical_requirements WHERE tender_id = ? "
            "ORDER BY id", (tender_id,))]
        matches = {m.requirement_id: m for m in
                   (self.db.row_to_match(r) for r in self.db.query(
                       "SELECT * FROM requirement_matches WHERE tender_id = ?",
                       (tender_id,)))}
        ev_rows = self.db.query(
            "SELECT * FROM evidences WHERE tender_id = ? ORDER BY confidence DESC, id",
            (tender_id,))
        doc_names = {r["id"]: r["file_name"] for r in self.db.query(
            "SELECT id, file_name FROM kb_materials", ())}
        evs = {r["id"]: r for r in ev_rows}

        rows = []
        for c in canonicals:
            if c.is_scoring:
                continue                      # 评分细则不进响应表
            m = matches.get(c.id)
            status = m.status.value if m else MatchStatus.UNKNOWN.value
            reason = m.reason if m else "现有资料不足，无法判定，待确认"
            evidence_ids = list(m.evidence_ids) if m else []
            evidences = [self._ev_dict(evs[eid], doc_names)
                         for eid in evidence_ids if eid in evs]
            rows.append({
                "requirement_id": c.id, "title": c.title,
                "req_type": c.req_type.value, "text": c.text,
                "status": status, "reason": reason,
                "evidence_ids": evidence_ids, "evidences": evidences,
                "response": self._response(status, reason, evidences),
            })
        counts = {s.value: 0 for s in MatchStatus}
        for r in rows:
            counts[r["status"]] += 1
        return {"tender_id": tender_id, "total": len(rows),
                "counts": counts, "rows": rows}

    @staticmethod
    def _ev_dict(row: dict, doc_names: dict) -> dict:
        return {
            "evidence_id": row["id"],
            "document": doc_names.get(row["document_id"]) or row["document_id"] or "",
            "category": row.get("category") or "",
            "content": (row.get("content") or "")[:300],
            "page": row.get("page"),
            "section_path": row.get("section_path") or "",
            "confidence": row.get("confidence") or 0.0,
        }

    @staticmethod
    def _response(status: str, reason: str, evidences: list) -> str:
        """企业响应列 —— 状态如实陈述，MISSING/UNKNOWN 不编造。"""
        if status == MatchStatus.FULL.value:
            return f"满足：{reason}"
        if status == MatchStatus.PARTIAL.value:
            return f"部分满足：{reason}；已满足部分见对应章节，差距见改进承诺"
        if status == MatchStatus.MISSING.value:
            return f"不满足：{reason}（如实说明，不声称具备，见对应章节）"
        return f"待确认：{reason}【待确认】"

    # ------------------------------------------------------------------
    def to_json(self, tender_id: str, pretty: bool = False) -> str:
        data = self.build(tender_id)
        payload = {"tender_id": data["tender_id"], "total": data["total"],
                   "counts": data["counts"], "rows": data["rows"]}
        return (json.dumps(payload, ensure_ascii=False, indent=2) if pretty
                else json.dumps(payload, ensure_ascii=False))

    def to_markdown(self, tender_id: str) -> str:
        """三列 Markdown 表格：招标要求 | 企业响应 | 证据。"""
        data = self.build(tender_id)
        counts = data["counts"]
        lines = [
            f"# 需求响应表（tender: {tender_id}）",
            "",
            f"共 **{data['total']}** 条规范需求："
            + "，".join(f"{_STATUS_LABELS[MatchStatus(k)]} {v}"
                        for k, v in counts.items() if v),
            "",
            "| 招标要求 | 企业响应 | 证据 |",
            "|---------|---------|------|",
        ]
        for i, r in enumerate(data["rows"], 1):
            req = f"**{i}. {r['title']}**（{r['req_type']}）\n\n{r['text']}"
            ev = self._ev_md(r["evidences"], r["evidence_ids"])
            lines.append(f"| {req.replace('|', '\\|')} | "
                         f"{r['response'].replace('|', '\\|')} | {ev} |")
        lines += ["", "> 状态口径：FULL=满足（证据支撑）；PARTIAL=部分满足；"
                      "MISSING=资料明确显示不满足；UNKNOWN=现有资料不足，待确认。",
                  "> MISSING/UNKNOWN 不编造：响应列如实陈述，具体数值以【待确认】标注。"]
        return "\n".join(lines)

    @staticmethod
    def _ev_md(evidences: list, evidence_ids: list) -> str:
        if not evidences:
            return "—（无证据）" if evidence_ids else "—"
        first = evidences[0]
        loc = ""
        if first["document"]:
            loc += f"{first['document']}"
        if first["page"]:
            loc += f" 第{first['page']}页"
        return f"{first['evidence_id']} {first['content'][:60]}{'…' if len(first['content']) > 60 else ''}（{loc or '企业资料'}）"


__all__ = ["BidResponseTableBuilder"]
