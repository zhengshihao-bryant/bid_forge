# -*- coding: utf-8 -*-
"""
matching/validate/conflict_detector.py —— 证据冲突检测（M3-13）

同指标多条证据数值不一致 → Conflict；仲裁顺序（用户 M3-13 口径）：
    ① 来源权威：正式企业资料 > 项目案例 > 能力卡 > 历史标书 > 普通文本
    ② 文档新旧：资料入库时间近似（kb_materials.created_at）
    ③ 均无法判断 → unresolved —— matcher 将结果降级 UNKNOWN，**不编造**

数值口径：同一指标的数值经单位换算后相差 >1%（相对差）视为冲突；
无法单位换算的声明不参与冲突比较（不可比 ≠ 冲突）。
"""
from __future__ import annotations

import re
from typing import Optional

from ....db import Database
from ..models import CanonicalRequirement, Conflict, Evidence, EvidenceValidation
from ..retrieve.evidence_ranker import source_tier
from ..rules.rule_engine import _UNIT_KEYS, _VALUE_RE, _convert

# 冲突阈值：同指标数值相对差 > 1%
_CONFLICT_REL_DIFF = 0.01

# 来源档位 → 权威序（小 = 更权威；与 evidence_ranker 口径一致）
_TIER_ORDER = {"formal": 0, "case": 1, "card": 2, "historical": 3, "plain": 4}

# 数值上下文窗口（数字两侧各取多少字符找指标关键词）
_WINDOW = 24

# 单指标最多产出的冲突条数（防止噪声爆炸）
_MAX_CONFLICTS_PER_METRIC = 3


def _metric_keywords(constraint) -> list[str]:
    """指标关键词：subject / metric / attribute（长度 ≥2 才参与窗口匹配）。"""
    words = []
    for k in (constraint.subject, constraint.metric, constraint.attribute):
        w = (k or "").strip()
        if len(w) >= 2 and w not in words:
            words.append(w)
    return words


def _claims_in(content: str, constraint) -> list[dict]:
    """内容中与指标相关的数值声明（数字 + 单位 + 关键词窗口）。"""
    if not content:
        return []
    keywords = _metric_keywords(constraint)
    if not keywords:
        return []
    claims = []
    for m in _VALUE_RE.finditer(content):
        a = m.group("a")
        if not a:
            continue
        lo = max(0, m.start() - _WINDOW)
        hi = min(len(content), m.end() + _WINDOW)
        window = content[lo:hi]
        if not any(k in window for k in keywords):
            continue
        unit_key = _UNIT_KEYS.get(m.group("unit") or "", "count")
        claims.append({"value": float(a), "unit": unit_key})
    return claims


def _normalize(value: float, unit: str, to_unit: str) -> Optional[float]:
    """换算到约束单位；无法换算 → None（不可比）。"""
    if not to_unit:
        return value
    if unit == to_unit:
        return value
    return _convert(value, unit, to_unit)


class ConflictDetector:
    """冲突检测器（M3-13）。只对 VALID/UNCHECKED 证据做冲突仲裁。"""

    def __init__(self, db: Optional[Database] = None):
        self.db = db or Database()
        self._material_time_cache: dict[str, str] = {}

    # ------------------------------------------------------------------
    def detect(self, requirement: CanonicalRequirement,
               evidences: list[Evidence]) -> list[Conflict]:
        """需求级冲突检测：逐约束指标收集声明 → 比较 → 仲裁。"""
        pool = [e for e in evidences
                if e.validation != EvidenceValidation.INVALID]
        conflicts: list[Conflict] = []
        for c in requirement.constraints:
            if not c.attribute and not c.subject:
                continue
            conflicts.extend(self._detect_constraint(c, pool))
        return conflicts

    # ------------------------------------------------------------------
    def _detect_constraint(self, constraint, evidences: list[Evidence]
                           ) -> list[Conflict]:
        claims: list[dict] = []  # {evidence, value, unit, tier, doc_time}
        for e in evidences:
            found = _claims_in(e.content, constraint)
            if not found:
                continue
            norm = _normalize(found[0]["value"], found[0]["unit"],
                              constraint.unit or "")
            if norm is None:
                continue  # 单位不可比 → 不参与冲突（不可比 ≠ 冲突）
            claims.append({
                "evidence": e,
                "value": round(norm, 4),
                "unit": constraint.unit or found[0]["unit"],
                "tier": source_tier(e),
                "doc_time": self._document_time(e),
            })
        if len(claims) < 2:
            return []
        # 权威序 → 新旧序 排位
        claims.sort(key=lambda x: (_TIER_ORDER.get(x["tier"], 9),
                                   -_time_key(x["doc_time"])))
        top = claims[0]
        losers = [x for x in claims[1:]
                  if abs(x["value"] - top["value"])
                  / max(abs(top["value"]), 1e-9) > _CONFLICT_REL_DIFF]
        if not losers:
            return []
        metric = constraint.subject or constraint.attribute or constraint.metric
        # 仲裁：① 权威严格更优 ② 同档比文档新旧 ③ unresolved
        top_order = _TIER_ORDER.get(top["tier"], 9)
        if all(_TIER_ORDER.get(x["tier"], 9) > top_order for x in losers):
            resolution, winner = "authority", top["evidence"].evidence_id
        else:
            # 只与同档位声明比新旧（低档位声明不影响时间仲裁）
            same_tier = [x for x in losers
                         if _TIER_ORDER.get(x["tier"], 9) == top_order]
            if same_tier and all(_time_key(top["doc_time"])
                                 > _time_key(x["doc_time"]) for x in same_tier):
                resolution, winner = "time", top["evidence"].evidence_id
            else:
                resolution, winner = "unresolved", ""
        note = self._note(resolution, top, losers)
        return [
            Conflict(
                metric=metric,
                claim_a=self._claim_dict(x, constraint),
                claim_b=self._claim_dict(top, constraint),
                resolution=resolution,
                winner_evidence_id=winner,
                note=note,
            )
            for x in losers[:_MAX_CONFLICTS_PER_METRIC]
        ]

    # ------------------------------------------------------------------
    def _document_time(self, e: Evidence) -> str:
        """文档新旧代理：资料入库时间，回退证据创建时间（ISO 串可比）。"""
        doc_id = e.document_id or ""
        if doc_id and doc_id not in self._material_time_cache:
            rows = self.db.query(
                "SELECT created_at FROM kb_materials WHERE id = ?", (doc_id,))
            self._material_time_cache[doc_id] = rows[0]["created_at"] if rows else ""
        t = self._material_time_cache.get(doc_id) or e.created_at or ""
        return t

    @staticmethod
    def _claim_dict(claim: dict, constraint) -> dict:
        e: Evidence = claim["evidence"]
        return {
            "evidence_id": e.evidence_id,
            "value": claim["value"],
            "unit": claim["unit"] or constraint.unit or "",
            "source_type": e.source_type.value,
            "document": e.document_id,
            "authority": claim["tier"],
        }

    @staticmethod
    def _note(resolution: str, top: dict, losers: list[dict]) -> str:
        if resolution == "authority":
            return (f"来源权威仲裁：{top['tier']}（正式度更高）"
                    f" 胜出，采信 EVD {top['evidence'].evidence_id}")
        if resolution == "time":
            return (f"文档新旧仲裁：同档位下以入库时间较新"
                    f"（{top['doc_time']}）胜出")
        return "无法仲裁（同档位且新旧不明）→ 匹配结论降级 UNKNOWN，不编造"


def _time_key(t: str) -> int:
    """ISO 时间串 → 可比整数（非数字 → 0）。"""
    digits = re.sub(r"\D", "", t or "")
    return int(digits) if digits else 0


__all__ = ["ConflictDetector", "Conflict"]
