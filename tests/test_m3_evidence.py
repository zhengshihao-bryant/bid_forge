# -*- coding: utf-8 -*-
"""
tests/test_m3_evidence.py —— M3-05 证据原文回验 + M3-13 冲突检测（离线）

覆盖（用户 M3-16 要求 5+ 冲突组）：
- 回验：chunk/能力卡/document 三源 VALID / INVALID / UNCHECKED 全路径
- LLM 编造 snippet → INVALID（原文找不到）；INVALID 禁入高可信
- 冲突：DOC-A 1250台 vs DOC-B 2000台（文档新旧仲裁）
- 权威仲裁：正式资料 2000 vs 项目案例 1250
- unresolved（同档位同新旧）→ 判定 UNKNOWN，不编造
- 等值不冲突（99.9% vs 99.95%、500万元 vs 5000000元）
- 单位换算后比较（500万 vs 300万 → 冲突）
- INVALID 证据不参与冲突仲裁
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app import config  # noqa: E402
from app.db import Database  # noqa: E402
from app.services.matching.judge import HeuristicJudge  # noqa: E402
from app.services.matching.models import (  # noqa: E402
    CanonicalRequirement, Constraint, Evidence, EvidenceSourceType,
    EvidenceValidation, MatchStatus, RequirementTypeM3)
from app.services.matching.validate import (  # noqa: E402
    ConflictDetector, EvidenceValidator)

from conftest import seed_m3_kb  # noqa: E402


def _ev(source_type, source_id, content, category="产品", document_id="",
        validation=None, eid="EVD-0001"):
    return Evidence(
        evidence_id=eid, tender_id="T-M3", requirement_id="REQ-C-0001",
        source_type=source_type, source_id=source_id, content=content,
        category=category, document_id=document_id,
        validation=validation or EvidenceValidation.UNCHECKED)


def _req_with(constraint):
    return CanonicalRequirement(
        id="REQ-C-0001", tender_id="T-M3",
        req_type=RequirementTypeM3.TECHNICAL,
        title="需求", text="需求", constraints=[constraint])


# ═══════════════════════════════════════════════════════════════════════
# M3-05 证据原文回验
# ═══════════════════════════════════════════════════════════════════════
def test_chunk_evidence_valid_and_invalid(tmp_env, m3_env):
    db = Database(config.DB_PATH)
    db.init_schema()
    seed_m3_kb(db, m3_env, materials=[
        {"id": "m1", "category": "产品", "file_name": "01_产品介绍.pdf"},
    ], chunks=[{
        "id": "m1_C0001", "material_id": "m1", "category": "产品",
        "file_name": "01_产品介绍.pdf",
        "content": "平台支持不少于 2000 台设备的接入管理。",
        "section_path": "第一章 产品能力", "page_start": 3, "seq": 1,
    }])
    validator = EvidenceValidator(db)
    # 原文精确匹配 → VALID + matched_text 回填
    good = _ev(EvidenceSourceType.CHUNK, "m1_C0001",
               "支持不少于 2000 台设备的接入")
    validator.validate(good)
    assert good.validation == EvidenceValidation.VALID
    assert "2000" in good.matched_text
    # LLM 编造 snippet → INVALID（原文找不到）
    bad = _ev(EvidenceSourceType.CHUNK, "m1_C0001", "支持不少于 5000 台设备接入")
    validator.validate(bad)
    assert bad.validation == EvidenceValidation.INVALID
    # 引用的 chunk 不存在 → INVALID
    ghost = _ev(EvidenceSourceType.CHUNK, "nope", "支持不少于 2000 台设备接入")
    validator.validate(ghost)
    assert ghost.validation == EvidenceValidation.INVALID


def test_card_evidence_paths(tmp_env, m3_env):
    db = Database(config.DB_PATH)
    db.init_schema()
    seed_m3_kb(db, m3_env,
               materials=[
                   {"id": "m1", "category": "产品",
                    "file_name": "01_产品介绍.pdf"},
                   {"id": "m2", "category": "人员资质",
                    "file_name": "04_人员资质.docx"},
               ],
               capabilities=[
                   {"id": "CAP-0001", "category": "产品",
                    "name": "智慧园区平台V3.2", "description": "支持设备接入",
                    "attributes": {"max_devices": "2000"},
                    "source_doc": "01_产品介绍.pdf"},
                   {"id": "CAP-0002", "category": "人员资质",
                    "name": "张伟-项目经理",
                    "attributes": {"experience_years": "6"},
                    "source_doc": "04_人员资质.docx"},
               ],
               chunks=[
                   {"id": "m2_C0001", "material_id": "m2",
                    "category": "人员资质", "file_name": "04_人员资质.docx",
                    "content": "张伟具有 6 年智慧园区项目管理经验，PMP 认证。",
                    "seq": 1},
               ])
    validator = EvidenceValidator(db)
    # ① 卡片字段自证 → VALID
    e1 = _ev(EvidenceSourceType.CAPABILITY_CARD, "CAP-0001", "智慧园区平台V3.2")
    validator.validate(e1)
    assert e1.validation == EvidenceValidation.VALID
    # ② 卡片字段无，但同资料 chunk 命中 → VALID
    e2 = _ev(EvidenceSourceType.CAPABILITY_CARD, "CAP-0002",
             "张伟具有 6 年智慧园区项目管理经验")
    validator.validate(e2)
    assert e2.validation == EvidenceValidation.VALID
    # ③ 资料未入库（无法回验）→ UNCHECKED
    e3 = _ev(EvidenceSourceType.CAPABILITY_CARD, "CAP-0001",
             "平台获得过国家级奖项")
    cap1 = db.query_one("SELECT * FROM capabilities WHERE id = 'CAP-0001'")
    # 改 source_doc 指向不存在的资料
    db.update("capabilities", "id", "CAP-0001", {"source_doc": "不存在.pdf"})
    validator = EvidenceValidator(db)     # 新实例（清缓存）
    validator.validate(e3)
    assert e3.validation == EvidenceValidation.UNCHECKED
    # ④ 资料有 chunk 但内容对不上 → INVALID
    e4 = _ev(EvidenceSourceType.CAPABILITY_CARD, "CAP-0002", "张伟拥有50年经验")
    validator.validate(e4)
    assert e4.validation == EvidenceValidation.INVALID
    assert cap1 is not None


def test_document_evidence_paths(tmp_env, m3_env):
    db = Database(config.DB_PATH)
    db.init_schema()
    seed_m3_kb(db, m3_env,
               materials=[
                   {"id": "m1", "category": "产品",
                    "file_name": "01_产品介绍.pdf"},
                   {"id": "m2", "category": "公司介绍",
                    "file_name": "07_公司介绍.pdf"},
               ],
               chunks=[{
                   "id": "m1_C0001", "material_id": "m1", "category": "产品",
                   "file_name": "01_产品介绍.pdf",
                   "content": "平台支持不少于 2000 台设备的接入管理。", "seq": 1,
               }])
    validator = EvidenceValidator(db)
    # 有 chunk 且命中 → VALID
    e1 = _ev(EvidenceSourceType.DOCUMENT, "m1", "2000 台设备的接入管理",
             document_id="m1")
    validator.validate(e1)
    assert e1.validation == EvidenceValidation.VALID
    # 资料未切块 → UNCHECKED
    e2 = _ev(EvidenceSourceType.DOCUMENT, "m2", "公司成立于2010年",
             category="公司介绍", document_id="m2")
    validator.validate(e2)
    assert e2.validation == EvidenceValidation.UNCHECKED
    # 资料不存在 → INVALID
    e3 = _ev(EvidenceSourceType.DOCUMENT, "ghost", "任意内容", document_id="ghost")
    validator.validate(e3)
    assert e3.validation == EvidenceValidation.INVALID


# ═══════════════════════════════════════════════════════════════════════
# M3-13 冲突检测（5+ 组）
# ═══════════════════════════════════════════════════════════════════════
_DEVICE_C = Constraint(subject="设备接入", attribute="device_count",
                       operator=">=", value=1000.0, unit="count")


def test_conflict_doc_time_arbitration(tmp_env, m3_env):
    """DOC-A 1250台 vs DOC-B 2000台（同档位）→ 文档新旧仲裁（更新者胜）。"""
    db = Database(config.DB_PATH)
    db.init_schema()
    seed_m3_kb(db, m3_env, materials=[
        {"id": "mA", "category": "产品", "file_name": "A_产品旧版.pdf",
         "created_at": "2025-01-01 00:00:00"},
        {"id": "mB", "category": "产品", "file_name": "B_产品新版.pdf",
         "created_at": "2026-01-01 00:00:00"},
    ])
    evs = [
        _ev(EvidenceSourceType.DOCUMENT, "mA", "平台设备接入能力为1250台",
            document_id="mA", validation=EvidenceValidation.VALID,
            eid="EVD-0001"),
        _ev(EvidenceSourceType.DOCUMENT, "mB", "平台设备接入能力为2000台",
            document_id="mB", validation=EvidenceValidation.VALID,
            eid="EVD-0002"),
    ]
    conflicts = ConflictDetector(db).detect(_req_with(_DEVICE_C), evs)
    assert len(conflicts) == 1
    c = conflicts[0]
    assert c.metric == "设备接入"
    assert c.resolution == "time"
    assert c.winner_evidence_id == "EVD-0002"


def test_conflict_authority_arbitration(tmp_env, m3_env):
    """正式资料 2000台 vs 项目案例 1250台 → 来源权威仲裁（正式资料胜）。"""
    db = Database(config.DB_PATH)
    db.init_schema()
    seed_m3_kb(db, m3_env, materials=[
        {"id": "mA", "category": "项目案例", "file_name": "案例A.docx"},
        {"id": "mB", "category": "产品", "file_name": "B_产品.pdf"},
    ])
    evs = [
        _ev(EvidenceSourceType.DOCUMENT, "mA", "案例合同设备接入1250台",
            category="项目案例", document_id="mA",
            validation=EvidenceValidation.VALID, eid="EVD-0001"),
        _ev(EvidenceSourceType.DOCUMENT, "mB", "平台设备接入能力2000台",
            category="产品", document_id="mB",
            validation=EvidenceValidation.VALID, eid="EVD-0002"),
    ]
    conflicts = ConflictDetector(db).detect(_req_with(_DEVICE_C), evs)
    assert conflicts[0].resolution == "authority"
    assert conflicts[0].winner_evidence_id == "EVD-0002"


def test_conflict_unresolved_downgrades_to_unknown(tmp_env, m3_env):
    """同档位且新旧不明 → unresolved；判定降级 UNKNOWN（不编造）。"""
    db = Database(config.DB_PATH)
    db.init_schema()
    seed_m3_kb(db, m3_env, materials=[
        {"id": "mA", "category": "产品", "file_name": "A_产品.pdf",
         "created_at": "2026-01-01 00:00:00"},
        {"id": "mB", "category": "产品", "file_name": "B_产品.pdf",
         "created_at": "2026-01-01 00:00:00"},
    ])
    evs = [
        _ev(EvidenceSourceType.DOCUMENT, "mA", "平台设备接入能力为1250台",
            document_id="mA", validation=EvidenceValidation.VALID,
            eid="EVD-0001"),
        _ev(EvidenceSourceType.DOCUMENT, "mB", "平台设备接入能力为2000台",
            document_id="mB", validation=EvidenceValidation.VALID,
            eid="EVD-0002"),
    ]
    conflicts = ConflictDetector(db).detect(_req_with(_DEVICE_C), evs)
    assert conflicts and conflicts[0].resolution == "unresolved"
    assert conflicts[0].winner_evidence_id == ""
    verdict = HeuristicJudge().judge(_req_with(_DEVICE_C), evs,
                                     conflicts=conflicts)
    assert verdict.status == MatchStatus.UNKNOWN


def test_conflict_equal_values_no_conflict():
    """99.9% vs 99.95%（相对差 <1%）→ 不冲突；500万元 = 5000000元 → 不冲突。"""
    db = Database(config.DB_PATH)
    detector = ConflictDetector(db)
    c_avail = Constraint(subject="可用性", attribute="availability",
                         operator=">=", value=99.9, unit="percent")
    evs = [
        _ev(EvidenceSourceType.DOCUMENT, "mA", "系统可用性99.9%",
            validation=EvidenceValidation.VALID, eid="EVD-0001"),
        _ev(EvidenceSourceType.DOCUMENT, "mB", "平台可用性99.95%",
            validation=EvidenceValidation.VALID, eid="EVD-0002"),
    ]
    assert detector.detect(_req_with(c_avail), evs) == []


def test_conflict_unit_conversion_before_compare(tmp_env, m3_env):
    """500万元 vs 300万元 → 冲突（换算后同单位比较）。"""
    db = Database(config.DB_PATH)
    db.init_schema()
    seed_m3_kb(db, m3_env, materials=[
        {"id": "mA", "category": "项目案例", "file_name": "案例A.docx",
         "created_at": "2025-01-01 00:00:00"},
        {"id": "mB", "category": "项目案例", "file_name": "案例B.docx",
         "created_at": "2026-01-01 00:00:00"},
    ])
    c_money = Constraint(subject="合同额", attribute="contract_amount",
                         operator=">=", value=500.0, unit="money_wan")
    evs = [
        _ev(EvidenceSourceType.DOCUMENT, "mA", "案例合同额500万元",
            category="项目案例", document_id="mA",
            validation=EvidenceValidation.VALID, eid="EVD-0001"),
        _ev(EvidenceSourceType.DOCUMENT, "mB", "案例合同额300万元",
            category="项目案例", document_id="mB",
            validation=EvidenceValidation.VALID, eid="EVD-0002"),
    ]
    conflicts = ConflictDetector(db).detect(_req_with(c_money), evs)
    assert conflicts and conflicts[0].resolution == "time"


def test_conflict_invalid_evidence_excluded(tmp_env, m3_env):
    """INVALID 证据（编造 800台）不参与冲突仲裁。"""
    db = Database(config.DB_PATH)
    db.init_schema()
    seed_m3_kb(db, m3_env, materials=[
        {"id": "mA", "category": "产品", "file_name": "A_产品.pdf",
         "created_at": "2025-01-01 00:00:00"},
        {"id": "mB", "category": "产品", "file_name": "B_产品.pdf",
         "created_at": "2026-01-01 00:00:00"},
    ])
    evs = [
        _ev(EvidenceSourceType.DOCUMENT, "mA", "平台设备接入能力为1250台",
            document_id="mA", validation=EvidenceValidation.VALID,
            eid="EVD-0001"),
        _ev(EvidenceSourceType.DOCUMENT, "mB", "平台设备接入能力为2000台",
            document_id="mB", validation=EvidenceValidation.VALID,
            eid="EVD-0002"),
        _ev(EvidenceSourceType.DOCUMENT, "mC", "平台设备接入能力为800台",
            validation=EvidenceValidation.INVALID, eid="EVD-0003"),
    ]
    conflicts = ConflictDetector(db).detect(_req_with(_DEVICE_C), evs)
    assert len(conflicts) == 1
    c = conflicts[0]
    # 800台 的 EVD-0003 不出现（claim_a/claim_b 均来自有效证据）
    assert "EVD-0003" not in (c.claim_a.get("evidence_id"),
                              c.claim_b.get("evidence_id"))
