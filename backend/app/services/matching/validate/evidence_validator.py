# -*- coding: utf-8 -*-
"""
matching/validate/evidence_validator.py —— 证据原文回验（M3-05）

铁律：检索/LLM 产生的证据必须在原文中找到（去空白标点后的精确包含）。
    VALID     → 可进入高可信证据
    INVALID   → 禁入高可信证据、不得单独支撑 FULL
    UNCHECKED → 无法回验（资料未切块/未入库），降权不封禁

回验对象分源：
    chunk 证据      → chunk.content 原文
    能力卡证据      → 卡片字段（name/description/attributes）原文
                      + 同资料（source_doc 反查 material）的所有 chunk 原文
    document 证据   → 资料的全部 chunk 原文（材料未处理 → UNCHECKED）

命中时回填 matched_text（原文精确匹配片段，证据链展示用）。
"""
from __future__ import annotations

import logging
from typing import Optional

from ....db import Database
from ..models import Evidence, EvidenceSourceType, EvidenceValidation
from ..similarity import contains_normalized, find_longest_match

logger = logging.getLogger(__name__)


def _card_text(card) -> str:
    """能力卡 → 回验原文（名称 + 描述 + 属性键值）。"""
    parts = [getattr(card, "name", "") or "", getattr(card, "description", "") or ""]
    for k, v in (getattr(card, "attributes", None) or {}).items():
        if isinstance(v, (str, int, float)):
            parts.append(f"{k}{v}")
        elif isinstance(v, list):
            parts.append(" ".join(str(x) for x in v))
    return "\n".join(parts)


class EvidenceValidator:
    """证据回验器（M3-05）。带资料/能力卡缓存，批量回验不重复查库。"""

    def __init__(self, db: Optional[Database] = None):
        self.db = db or Database()
        self._chunk_cache: dict[str, Optional[dict]] = {}
        self._material_cache: dict[str, Optional[dict]] = {}
        self._cap_cache: dict[str, Optional[dict]] = {}

    # ------------------------------------------------------------------
    def validate(self, evidence: Evidence) -> Evidence:
        """回验单条证据：就地改 validation + matched_text 并返回。"""
        if evidence.source_type == EvidenceSourceType.CAPABILITY_CARD:
            self._validate_card(evidence)
        elif evidence.source_type == EvidenceSourceType.CHUNK:
            self._validate_chunk(evidence)
        else:  # DOCUMENT
            self._validate_document(evidence)
        return evidence

    def validate_all(self, evidences: list[Evidence]) -> list[Evidence]:
        """批量回验（共享缓存），返回同一列表。"""
        for e in evidences:
            self.validate(e)
        return evidences

    # ------------------------------------------------------------------
    def _chunk(self, chunk_id: str) -> Optional[dict]:
        if chunk_id not in self._chunk_cache:
            rows = self.db.query("SELECT * FROM kb_chunks WHERE id = ?", (chunk_id,))
            self._chunk_cache[chunk_id] = rows[0] if rows else None
        return self._chunk_cache[chunk_id]

    def _material(self, material_id: str) -> Optional[dict]:
        if material_id not in self._material_cache:
            rows = self.db.query(
                "SELECT * FROM kb_materials WHERE id = ?", (material_id,))
            self._material_cache[material_id] = rows[0] if rows else None
        return self._material_cache[material_id]

    def _material_by_name(self, file_name: str) -> Optional[dict]:
        if not file_name:
            return None
        key = f"name:{file_name}"
        if key not in self._material_cache:
            rows = self.db.query(
                "SELECT * FROM kb_materials WHERE file_name = ?", (file_name,))
            self._material_cache[key] = rows[0] if rows else None
        return self._material_cache[key]

    def _capability(self, cap_id: str) -> Optional[dict]:
        if cap_id not in self._cap_cache:
            rows = self.db.query("SELECT * FROM capabilities WHERE id = ?", (cap_id,))
            self._cap_cache[cap_id] = rows[0] if rows else None
        return self._cap_cache[cap_id]

    def _material_chunks(self, material_id: str) -> list[dict]:
        rows = self.db.query(
            "SELECT * FROM kb_chunks WHERE material_id = ? ORDER BY seq", (material_id,))
        return rows

    def _match_in(self, haystack: str, evidence: Evidence) -> bool:
        """精确包含回验；命中回填 matched_text。"""
        if contains_normalized(haystack, evidence.content):
            evidence.matched_text = find_longest_match(haystack, evidence.content)
            evidence.validation = EvidenceValidation.VALID
            return True
        return False

    # ------------------------------------------------------------------
    def _validate_chunk(self, e: Evidence) -> None:
        chunk = self._chunk(e.source_id)
        if chunk is None:
            # 引用的 chunk 不存在 → 证据来源不可信
            e.validation = EvidenceValidation.INVALID
            return
        if self._match_in(chunk["content"], e):
            return
        e.validation = EvidenceValidation.INVALID

    def _validate_card(self, e: Evidence) -> None:
        row = self._capability(e.source_id)
        if row is None:
            e.validation = EvidenceValidation.INVALID
            return
        card = self.db.row_to_capability(row)
        # ① 卡片字段原文（结构化事实自证）
        if self._match_in(_card_text(card), e):
            return
        # ② 同资料 chunk 原文（source_doc 反查 material）
        material = self._material_by_name(card.source_doc)
        if material is None:
            # 资料未入库：卡片字段已核对，仍无法对原文档回验
            e.validation = EvidenceValidation.UNCHECKED
            return
        chunks = self._material_chunks(material["id"])
        if not chunks:
            # 资料未切块 → 无法回验
            e.validation = EvidenceValidation.UNCHECKED
            return
        for ch in chunks:
            if self._match_in(ch["content"], e):
                return
        e.validation = EvidenceValidation.INVALID

    def _validate_document(self, e: Evidence) -> None:
        material = self._material(e.document_id or e.source_id)
        if material is None:
            e.validation = EvidenceValidation.INVALID
            return
        chunks = self._material_chunks(material["id"])
        if not chunks:
            e.validation = EvidenceValidation.UNCHECKED
            return
        for ch in chunks:
            if self._match_in(ch["content"], e):
                return
        e.validation = EvidenceValidation.INVALID


__all__ = ["EvidenceValidator", "_card_text"]
