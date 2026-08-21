# -*- coding: utf-8 -*-
"""
matching/judge/llm_judge.py —— LLM Judge + 启发式回退（M3-11）

LLM Judge 铁律（system prompt 固化）：
    1. 只能依据提供的证据判断，不得补充任何企业事实（不编造）
    2. 证据不足 / 证据与要求无关 → UNKNOWN（没有证据 ≠ 不满足）
    3. 证据明确显示不满足 → MISSING；满足 → FULL；部分覆盖 → PARTIAL
    4. 严格输出 JSON：{"status", "confidence", "reason", "evidence_ids"}
       evidence_ids 只能引用提供的 EVD 编号

无 LLM（Mock 客户端）时走 HeuristicJudge 确定性回退：规则结论优先、
无有效证据 → UNKNOWN、证据覆盖不足 → PARTIAL、冲突未决 → UNKNOWN。
"""
from __future__ import annotations

import logging
from typing import Optional

from pydantic import BaseModel

from ....config import M3_JUDGE_EVIDENCE
from ..models import Evidence, EvidenceValidation, MatchStatus

logger = logging.getLogger(__name__)

# 判定结果（LLM 与启发式统一输出形状）
_JUDGE_ALLOWED = {s.value for s in MatchStatus}


class JudgeVerdict(BaseModel):
    """判定结论：{status, confidence, reason, evidence_ids}。"""
    status: MatchStatus
    confidence: float = 0.0
    reason: str = ""
    evidence_ids: list[str] = []


# ---------------------------------------------------------------------------
# LLM Judge
# ---------------------------------------------------------------------------
_JUDGE_SYSTEM = (
    "你是企业投标资格审查助手。根据招标要求与企业证据判断匹配状态。"
    "铁律：\n"
    "1. 只能依据【证据池】中的证据判断，不得补充、推测任何证据中没有的企业事实；\n"
    "2. 证据不足或与要求无关时，status 必须为 UNKNOWN（没有证据 ≠ 不满足）；\n"
    "3. 证据明确显示企业不满足要求 → MISSING；证据充分满足 → FULL；"
    "仅部分覆盖 → PARTIAL；\n"
    "4. evidence_ids 只能引用证据池中给出的 EVD 编号，禁止编造编号；\n"
    "5. reason 用中文说明判断依据（引用证据编号与关键内容）；\n"
    "6. 只输出 JSON：{\"status\": \"FULL|PARTIAL|MISSING|UNKNOWN\", "
    "\"confidence\": 0-1, \"reason\": \"...\", \"evidence_ids\": [\"EVD-...\"]}"
)


def render_evidence(e: Evidence, idx: int) -> str:
    """证据 → Judge 输入行（编号 + 来源 + 内容 + 溯源）。"""
    src = {"capability_card": "能力卡", "chunk": "知识块", "document": "资料"}\
        .get(e.source_type.value, e.source_type.value)
    prov = " / ".join(x for x in (
        e.document_id, e.section_path, f"第{e.page}页" if e.page else "",
        e.block_id) if x)
    return (f"[{idx}] {e.evidence_id}（{src}｜验证:{e.validation.value}"
            f"｜置信:{e.confidence:.2f}）\n内容：{e.content[:600]}"
            + (f"\n出处：{prov}" if prov else ""))


class LLMJudge:
    """LLM 判定器：证据池 → 严格 JSON 判定。"""

    def __init__(self, client=None, max_evidence: int | None = None):
        self.client = client
        self.max_evidence = max_evidence or M3_JUDGE_EVIDENCE

    def _client(self):
        if self.client is None:
            from ....services.llm import create_llm_client  # 延迟：测试注入 mock
            self.client = create_llm_client()
        return self.client

    @property
    def llm_enabled(self) -> bool:
        model = getattr(self._client(), "model", "mock")
        return model not in ("mock", "fake")

    # ------------------------------------------------------------------
    def judge(self, requirement, evidences: list[Evidence],
              ranked: bool = True) -> Optional[JudgeVerdict]:
        """证据池（已排序）→ JudgeVerdict；失败返回 None（调用方回退启发式）。"""
        pool = (evidences if ranked else sorted(
            evidences, key=lambda e: -e.confidence))[:self.max_evidence]
        if not pool:
            return JudgeVerdict(status=MatchStatus.UNKNOWN, confidence=0.1,
                                reason="证据池为空，无法判断（没有证据 ≠ 不满足）")
        user = (
            f"招标要求：{requirement.title}\n{requirement.text}\n\n"
            f"证据池：\n" + "\n".join(render_evidence(e, i)
                                     for i, e in enumerate(pool, 1))
        )
        try:
            resp = self._client().chat_json(system=_JUDGE_SYSTEM, user=user,
                                            temperature=0.0)
        except Exception as e:  # noqa: BLE001 —— 网络/协议异常回退启发式
            logger.warning("LLM Judge 调用失败: %s", str(e)[:150])
            return None
        if not resp or "data" not in resp:
            return None
        data = resp["data"]
        return self._parse(data, pool)

    # ------------------------------------------------------------------
    @staticmethod
    def _parse(data: dict, pool: list[Evidence]) -> Optional[JudgeVerdict]:
        """严格校验 LLM 输出：状态枚举 + 置信度 + 证据编号白名单。"""
        status = str(data.get("status", "")).upper()
        if status not in _JUDGE_ALLOWED:
            logger.warning("LLM Judge 状态非法: %r", data.get("status"))
            return None
        try:
            confidence = float(data.get("confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0
        confidence = min(1.0, max(0.0, confidence))
        # 证据编号白名单：引用池外编号一律剔除（防编造）
        allowed = {e.evidence_id for e in pool}
        ids = [str(x) for x in data.get("evidence_ids", []) if str(x) in allowed]
        reason = str(data.get("reason", ""))[:2000]
        return JudgeVerdict(status=MatchStatus(status),
                            confidence=confidence,
                            reason=reason, evidence_ids=ids)


# ---------------------------------------------------------------------------
# 启发式回退（确定性，离线测试基准）
# ---------------------------------------------------------------------------
class HeuristicJudge:
    """确定性判定器：规则结论优先 → 证据状态 → 冲突降级。"""

    def judge(self, requirement, evidences: list[Evidence],
              rule_result=None, conflicts: Optional[list] = None,
              ) -> JudgeVerdict:
        conflicts = conflicts or []
        pool = sorted(evidences, key=lambda e: -e.confidence)
        valid = [e for e in pool
                 if e.validation != EvidenceValidation.INVALID]

        # 冲突未决 → UNKNOWN（M3-13 铁律：不编造）
        if any(c.resolution == "unresolved" for c in conflicts):
            return JudgeVerdict(
                status=MatchStatus.UNKNOWN, confidence=0.2,
                reason=f"{len(conflicts)} 处证据冲突无法仲裁，判定 UNKNOWN（不编造）")

        # 规则结论优先（数值/存在性约束的结构化判定）
        if rule_result is not None and rule_result.status is not None:
            status = rule_result.status
            if status == MatchStatus.FULL:
                if not valid:
                    return JudgeVerdict(
                        status=MatchStatus.UNKNOWN, confidence=0.3,
                        reason="规则判定满足，但无有效证据支撑 → UNKNOWN")
                return JudgeVerdict(
                    status=MatchStatus.FULL,
                    confidence=min(0.98, 0.7 + 0.28 * max(
                        (e.confidence for e in valid), default=0)),
                    reason=f"规则引擎：{rule_result.note or ''}"
                           f"（证据：{'、'.join(e.evidence_id for e in valid[:3])}）",
                    evidence_ids=[e.evidence_id for e in valid])
            if status == MatchStatus.MISSING:
                if not valid:
                    return JudgeVerdict(
                        status=MatchStatus.UNKNOWN, confidence=0.3,
                        reason="规则判定不满足，但无有效证据佐证 → UNKNOWN")
                return JudgeVerdict(
                    status=MatchStatus.MISSING,
                    confidence=min(0.98, 0.7 + 0.28 * max(
                        (e.confidence for e in valid), default=0)),
                    reason=f"规则引擎：{rule_result.note or ''}"
                           f"（证据：{'、'.join(e.evidence_id for e in valid[:3])}）",
                    evidence_ids=[e.evidence_id for e in valid])
            # PARTIAL / UNKNOWN → 交给证据状态
            if status == MatchStatus.PARTIAL:
                return JudgeVerdict(
                    status=MatchStatus.PARTIAL,
                    confidence=0.5 + 0.2 * max(
                        (e.confidence for e in valid), default=0),
                    reason=f"规则引擎：{rule_result.note or '部分约束不满足'}",
                    evidence_ids=[e.evidence_id for e in valid[:3]])

        # 无规则结论：纯证据状态判定（RAG/CARD 路径）
        if not valid:
            return JudgeVerdict(
                status=MatchStatus.UNKNOWN, confidence=0.1,
                reason="无有效证据（没有证据 ≠ 不满足）→ UNKNOWN")
        top = valid[0]
        if (top.validation == EvidenceValidation.VALID
                and top.confidence >= 0.8
                and top.retrieval_score >= 0.25):
            return JudgeVerdict(
                status=MatchStatus.FULL, confidence=top.confidence,
                reason=f"高置信有效证据 {top.evidence_id} 覆盖要求要点",
                evidence_ids=[e.evidence_id for e in valid[:3]])
        return JudgeVerdict(
            status=MatchStatus.PARTIAL, confidence=0.4 + 0.3 * top.confidence,
            reason=f"证据 {top.evidence_id} 仅部分覆盖要求（最高置信 {top.confidence:.2f}）",
            evidence_ids=[e.evidence_id for e in valid[:3]])


__all__ = ["LLMJudge", "HeuristicJudge", "JudgeVerdict", "render_evidence"]
