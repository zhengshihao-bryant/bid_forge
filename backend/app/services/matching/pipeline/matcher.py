# -*- coding: utf-8 -*-
"""
matching/pipeline/matcher.py —— 混合匹配管线（M3-09/14）+ 后台任务入口

单条规范需求的匹配链路（混合分派 M3-09）：

    ① RULE  规则引擎：结构化约束（数值/存在性）× 类别过滤能力卡 → 初判
    ② CARD  能力卡检索：全部需求走（结构化事实优先路径，M3-07）
    ③ RAG   语义检索：Top-K → Rerank（M3-08）
    ④ 证据池：②③ 合并去重 → 原文回验（M3-05）→ 排序（M3-10）→ 冲突仲裁（M3-13）
    ⑤ 判定：规则结论（FULL/MISSING/PARTIAL）→ 启发式定案（RULE）
             其余 → LLM Judge（M3-11）；无 LLM / 冲突未决 → HeuristicJudge

口径铁律（M3-12/13）：
    - 四种状态 FULL / PARTIAL / MISSING / UNKNOWN 恒保留
    - 没有证据 ≠ 不满足（无证据 → UNKNOWN；MISSING 仅当资料明确显示不满足）
    - 冲突无法仲裁 → UNKNOWN，绝不编造企业事实

M3 边界：只回答"招标方要求什么？我们有没有？证据是什么？"，不写标书（M4）。
"""
from __future__ import annotations

import logging
import threading
from typing import Callable, Optional

from .... import config
from ....db import Database
from ...task_tracker import (fail_task, start_task, succeed_task,
                             update_progress)
from ..classify import RequirementClassifier
from ..extract import ConstraintExtractor
from ..judge import HeuristicJudge, LLMJudge
from ..models import (CanonicalRequirement, Evidence, EvidenceSourceType,
                      MatchMethod, MatchReport, MatchResult, MatchStatus)
from ..normalize import RequirementNormalizer
from ..retrieve import (CapabilityRetriever, EvidenceRanker, SemanticRetriever,
                        _card_text)
from ..rules import RuleEngine
from ..similarity import key_overlap
from ..validate import ConflictDetector, EvidenceValidator

logger = logging.getLogger(__name__)

# 证据编号 / 匹配编号（tender 内全局自增，重跑时先清表）
_EVD_LOCK = threading.Lock()


class Matcher:
    """M3 匹配管线：招标需求 × 企业知识库 → 可追溯判定。"""

    def __init__(self, db: Optional[Database] = None,
                 normalizer: Optional[RequirementNormalizer] = None,
                 classifier=None, extractor=None,
                 rule_engine: Optional[RuleEngine] = None,
                 card_retriever: Optional[CapabilityRetriever] = None,
                 semantic: Optional[SemanticRetriever] = None,
                 validator: Optional[EvidenceValidator] = None,
                 conflict_detector: Optional[ConflictDetector] = None,
                 ranker: Optional[EvidenceRanker] = None,
                 llm_judge: Optional[LLMJudge] = None,
                 heuristic: Optional[HeuristicJudge] = None,
                 use_llm: Optional[bool] = None):
        self.db = db or Database(config.DB_PATH)
        self.normalizer = normalizer or RequirementNormalizer(use_llm=use_llm)
        self.classifier = classifier or RequirementClassifier()
        self.extractor = extractor or ConstraintExtractor()
        self.rule_engine = rule_engine or RuleEngine()
        self.card_retriever = card_retriever or CapabilityRetriever(self.db)
        self.semantic = semantic
        self.validator = validator or EvidenceValidator(self.db)
        self.conflict_detector = conflict_detector or ConflictDetector(self.db)
        self.ranker = ranker or EvidenceRanker()
        self.heuristic = heuristic or HeuristicJudge()
        self.llm_judge = llm_judge
        self._material_id_cache: dict[str, str] = {}

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------
    def match(self, tender_id: str,
              progress_cb: Optional[Callable[[int, int, str], None]] = None
              ) -> MatchReport:
        """tender 全量匹配：标准化 → 逐条匹配 → 落库 → 报告。"""
        reqs = [self.db.row_to_requirement(r)
                for r in self.db.query("SELECT * FROM requirements WHERE tender_id = ?",
                                       (tender_id,))]
        if not reqs:
            raise ValueError(f"tender {tender_id} 没有原始需求（请先跑 M1 提取）")

        # ① 标准化（去重/聚类/LLM 合并 + 分类 + 约束提取 内联）
        canonicals, stats = self.normalizer.normalize(
            tender_id, reqs, classifier=self.classifier, extractor=self.extractor)

        # ② 落库：canonicals 全量重建；evidences/matches 清空重跑
        self.db.execute("DELETE FROM canonical_requirements WHERE tender_id = ?",
                        (tender_id,))
        for c in canonicals:
            self.db.insert("canonical_requirements", Database.canonical_to_row(c))
        self.db.execute("DELETE FROM evidences WHERE tender_id = ?", (tender_id,))
        self.db.execute("DELETE FROM requirement_matches WHERE tender_id = ?",
                        (tender_id,))

        # ③ 逐条匹配（评分细则不参与匹配 —— M3-01 区分细则与真正需求）
        matchable = [c for c in canonicals if not c.is_scoring]
        matches: list[MatchResult] = []
        total = len(matchable)
        evd_no, mat_no = 1, 1
        for i, c in enumerate(matchable, 1):
            if progress_cb:
                progress_cb(i, total, f"匹配 {c.id} {c.title[:20]}")
            match, pool, evd_no = self._match_one(c, evd_no, mat_no)
            for e in pool:
                self.db.insert("evidences", Database.evidence_to_row(e))
            self.db.insert("requirement_matches", Database.match_to_row(match))
            matches.append(match)
            mat_no += 1

        # ④ 回写 M1 需求状态（已匹配：原条目已并入规范需求并给出判定）
        matched_ids = {rid for c in matchable for rid in c.source_requirement_ids}
        for rid in matched_ids:
            self.db.update("requirements", "id", rid,
                           {"status": "已匹配", "updated_at": _now()})

        counts = {s.value: 0 for s in MatchStatus}
        for m in matches:
            counts[m.status.value] += 1
        logger.info("匹配完成 tender=%s: %d 条规范需求 → %s（标准化 %d→%d）",
                    tender_id, total, counts, stats["input"], len(canonicals))
        return MatchReport(tender_id=tender_id, total=total,
                           counts=counts, matches=matches)

    # ------------------------------------------------------------------
    # 单条需求匹配（混合分派核心）
    # ------------------------------------------------------------------
    def _match_one(self, c: CanonicalRequirement, evd_start: int, mat_no: int
                   ) -> tuple[MatchResult, list[Evidence], int]:
        # ① RULE：结构化约束 × 类别过滤能力卡
        card_hits = self.card_retriever.retrieve(c, top_k=5)
        rule_result = None
        if c.constraints:
            rule_result = self.rule_engine.evaluate_requirement(
                c.constraints, [card for card, _ in card_hits])

        # ②③ 证据池：能力卡 + RAG 命中合并去重
        pool: list[Evidence] = []
        seen: set[tuple] = set()
        for card, score in card_hits:
            key = (EvidenceSourceType.CAPABILITY_CARD.value, card.id)
            if key in seen:
                continue
            seen.add(key)
            pool.append(Evidence(
                tender_id=c.tender_id, requirement_id=c.id,
                source_type=EvidenceSourceType.CAPABILITY_CARD,
                source_id=card.id, content=_card_text(card),
                category=card.category.value,
                document_id=self._material_id_by_name(card.source_doc),
                page=card.source_page, retrieval_score=round(score, 4)))
        if self.semantic is not None or self._semantic_available():
            try:
                query = f"{c.title} {c.text}"
                for hit, score in self._semantic().retrieve(c):
                    # 相关性双下限：总分 + 关键词重叠（chunk 原文自证恒 VALID，
                    # 必须用检索分过滤，否则"无证据"需求会被误判满足；
                    # 类别亲和分可能抬过总分下限，关键词重叠必须达标）
                    if score < config.M3_RAG_MIN_SCORE:
                        continue
                    if key_overlap(query, hit.content) < config.M3_RAG_MIN_KW:
                        continue
                    key = (EvidenceSourceType.CHUNK.value, hit.chunk_id)
                    if key in seen:
                        continue
                    seen.add(key)
                    anchor = hit.anchor
                    pool.append(Evidence(
                        tender_id=c.tender_id, requirement_id=c.id,
                        source_type=EvidenceSourceType.CHUNK,
                        source_id=hit.chunk_id, content=hit.content,
                        category=hit.category, document_id=hit.material_id,
                        section_path=hit.section_path or "",
                        page=hit.page,
                        block_id=(anchor.block_id if anchor else "") or "",
                        retrieval_score=round(score, 4)))
            except Exception as e:  # noqa: BLE001 —— RAG 失败不阻断匹配
                logger.warning("RAG 检索失败 %s: %s", c.id, str(e)[:150])

        # ④ 回验 → 排序 → 编号 → 冲突
        self.validator.validate_all(pool)
        pool = self.ranker.rank(pool)
        for e in pool:
            e.evidence_id = f"EVD-{evd_start:04d}"
            evd_start += 1
        conflicts = self.conflict_detector.detect(c, pool)

        # ⑤ 判定分派
        verdict, method = self._judge(c, pool, rule_result, conflicts)
        match = MatchResult(
            id=f"MAT-{mat_no:04d}", tender_id=c.tender_id,
            requirement_id=c.id, status=verdict.status,
            confidence=verdict.confidence, reason=verdict.reason,
            method=method, evidence_ids=verdict.evidence_ids,
            conflicts=conflicts)
        return match, pool, evd_start

    # ------------------------------------------------------------------
    def _judge(self, c: CanonicalRequirement, pool: list[Evidence],
               rule_result, conflicts: list):
        """判定分派：规则 → LLM Judge → 启发式回退；冲突未决强制 UNKNOWN。"""
        unresolved = [x for x in conflicts if x.resolution == "unresolved"]
        if unresolved:
            v = self.heuristic.judge(c, pool, None, conflicts)
            return v, MatchMethod.HEURISTIC
        # 规则给出明确结论（FULL/MISSING/PARTIAL）→ 启发式定案（含证据链校验）
        if rule_result is not None and rule_result.status != MatchStatus.UNKNOWN:
            v = self.heuristic.judge(c, pool, rule_result, conflicts)
            return v, MatchMethod.RULE
        # 其余 → LLM Judge（有 Key）；无 LLM → 启发式
        if self._llm_judge().llm_enabled:
            v = self._llm_judge().judge(c, pool)
            if v is not None:
                return v, MatchMethod.LLM_JUDGE
        v = self.heuristic.judge(c, pool, rule_result, conflicts)
        return v, MatchMethod.HEURISTIC

    # ------------------------------------------------------------------
    # 懒加载 + 缓存
    # ------------------------------------------------------------------
    def _semantic(self) -> SemanticRetriever:
        if self.semantic is None:
            self.semantic = SemanticRetriever()   # 内部延迟建 SearchService
        return self.semantic

    def _semantic_available(self) -> bool:
        """RAG 是否启用：注入 mock 或知识库有索引时走语义路径。"""
        if self.semantic is not None:
            return True
        return self.db.query_one("SELECT COUNT(*) AS n FROM kb_chunks")["n"] > 0

    def _llm_judge(self) -> LLMJudge:
        if self.llm_judge is None:
            self.llm_judge = LLMJudge()
        return self.llm_judge

    def _material_id_by_name(self, file_name: str) -> str:
        """能力卡 source_doc（文件名）→ kb_materials 主键（溯源链 DOC 环节）。"""
        if not file_name:
            return ""
        if file_name not in self._material_id_cache:
            row = self.db.query_one(
                "SELECT id FROM kb_materials WHERE file_name = ?", (file_name,))
            self._material_id_cache[file_name] = row["id"] if row else ""
        return self._material_id_cache[file_name]


# ---------------------------------------------------------------------------
# 后台任务入口（镜像 run_extraction_task 状态机；matching_runs 表可轮询）
# ---------------------------------------------------------------------------
def run_matching_task(tender_id: str, task_id: str = "") -> dict:
    """后台任务入口：标准化 + 匹配 + 落库，matching_runs 记录状态。

    M7-05：task_id 非空时同步任务中心状态（start/progress/succeed/fail）。
    """
    db = Database(config.DB_PATH)
    tender = db.query_one("SELECT * FROM tenders WHERE id = ?", (tender_id,))
    if not tender:
        logger.error("匹配任务：招标项目不存在 tender_id=%s", tender_id)
        return {"tender_id": tender_id, "error": "招标项目不存在"}

    if task_id:
        start_task(db, task_id)
    _upsert_run(db, tender_id, "匹配中", "标准化需求")
    try:
        matcher = Matcher(db)

        def progress_cb(done: int, total: int, msg: str) -> None:
            _upsert_run(db, tender_id, "匹配中", f"[{done}/{total}] {msg}")
            if task_id:
                update_progress(db, task_id, done, total, msg)

        report = matcher.match(tender_id, progress_cb=progress_cb)
        canon_n = db.query_one(
            "SELECT COUNT(*) AS n FROM canonical_requirements WHERE tender_id = ?",
            (tender_id,))["n"]
        _upsert_run(db, tender_id, "已完成",
                    f"{report.total} 条需求 / {report.counts}",
                    canonical_count=canon_n, match_count=report.total)
        if task_id:
            succeed_task(db, task_id, done=report.total, total=report.total,
                         progress=f"{report.total} 条需求 / {report.counts}")
        return {"tender_id": tender_id, "status": "已完成",
                "total": report.total, "counts": report.counts}
    except Exception as e:  # noqa: BLE001 —— 状态机兜底，错误透出到 runs 表
        logger.exception("匹配任务失败 tender_id=%s", tender_id)
        _upsert_run(db, tender_id, "失败", f"匹配失败: {str(e)[:300]}")
        if task_id:
            fail_task(db, task_id, error=str(e))
        return {"tender_id": tender_id, "status": "失败", "error": str(e)}


def _upsert_run(db: Database, tender_id: str, status: str, progress: str,
                canonical_count: int = 0, match_count: int = 0) -> None:
    """matching_runs 表 upsert（tender_id 主键）。"""
    existing = db.query_one(
        "SELECT * FROM matching_runs WHERE tender_id = ?", (tender_id,))
    if existing:
        db.update("matching_runs", "tender_id", tender_id, {
            "status": status, "progress": progress,
            "canonical_count": canonical_count,
            "match_count": match_count, "updated_at": _now()})
    else:
        db.insert("matching_runs", {
            "tender_id": tender_id, "status": status, "progress": progress,
            "canonical_count": canonical_count, "match_count": match_count,
            "updated_at": _now()})


def _now() -> str:
    from ....schemas import now_str
    return now_str()


__all__ = ["Matcher", "run_matching_task"]
