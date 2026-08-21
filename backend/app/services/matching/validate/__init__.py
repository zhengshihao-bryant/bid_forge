# -*- coding: utf-8 -*-
"""
matching/validate —— M3 证据验证 + 冲突检测（M3-05/13）

- evidence_validator：证据原文回验（VALID / INVALID / UNCHECKED）
- conflict_detector：同指标多证据数值冲突仲裁（authority / time / unresolved）
"""
from .conflict_detector import ConflictDetector
from .evidence_validator import EvidenceValidator, _card_text

__all__ = ["EvidenceValidator", "ConflictDetector", "_card_text"]
