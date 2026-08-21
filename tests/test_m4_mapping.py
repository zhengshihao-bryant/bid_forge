# -*- coding: utf-8 -*-
"""
tests/test_m4_mapping.py —— M4-02 需求→章节映射（批次 2）

确定性规则（非 LLM）：canonical.sources[].type（M1 中文串）× 章节 requirement_types
做集合交集。一对多。覆盖统计 total == mapped + unmapped。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.services.generation import RequirementSectionMapper  # noqa: E402


def _section_titles_by_req(db, tender_id) -> dict[str, list[str]]:
    """需求标题 → 映射到的章节标题列表（映射表 + 章节表 join）。"""
    rows = db.query(
        "SELECT c.title AS req_title, s.title AS sec_title "
        "FROM requirement_section_maps m "
        "JOIN canonical_requirements c ON c.id = m.requirement_id "
        "JOIN generation_sections s ON s.section_id = m.section_id "
        "WHERE m.tender_id = ? ORDER BY m.section_id", (tender_id,))
    out: dict[str, list[str]] = {}
    for r in rows:
        out.setdefault(r["req_title"], []).append(r["sec_title"])
    return out


# ═══════════════════════════════════════════════════════════════════════
# 映射规则
# ═══════════════════════════════════════════════════════════════════════
def test_mapping_type_to_sections_one_to_many(seed_m4):
    """技术要求/功能要求 → 技术部分多章；资质→CH-04-2；人员→CH-06-2。"""
    data = seed_m4
    by_req = _section_titles_by_req(data["db"], data["tender_id"])
    # 技术要求（设备接入）→ 技术部分多个章节（一对多）
    assert "总体技术方案" in by_req.get("设备接入不少于1000台", [])
    assert "技术指标响应表" in by_req.get("设备接入不少于1000台", [])
    assert len(by_req["设备接入不少于1000台"]) > 1
    # 资质 → 企业资质与证书
    assert "企业资质与证书" in by_req.get("投标人须具有ISO9001质量管理体系认证", [])
    # 人员 → 组织机构与人员配备
    assert "组织机构与人员配备" in by_req.get("项目经理经验不少于5年", [])
    # 售后 → 售后服务承诺
    assert "售后服务承诺" in by_req.get("质保期不少于2年", [])


def test_mapping_scoring_excluded(seed_m4):
    """评分细则不参与映射（与匹配一致）。"""
    data = seed_m4
    scoring = data["db"].query_one(
        "SELECT * FROM canonical_requirements WHERE tender_id = ? AND is_scoring = 1",
        (data["tender_id"],))
    assert scoring is not None
    n = data["db"].query_one(
        "SELECT COUNT(*) AS n FROM requirement_section_maps WHERE requirement_id = ?",
        (scoring["id"],))["n"]
    assert n == 0


def test_mapping_coverage_stats_total_33(seed_m4):
    """覆盖统计：total==33（非评分规范需求），mapped==33（全部类型被声明）。"""
    stats = seed_m4["coverage"]
    assert stats.total == 33
    assert stats.mapped == 33
    assert stats.unmapped == 0
    assert stats.unmapped_reqs == []
    # 每章节计数：映射表行数 ≥ 覆盖需求数
    assert sum(stats.by_section.values()) >= stats.mapped
    # 技术部分核心章节有需求
    assert stats.by_section.get("CH-05-2", 0) >= 1
    assert stats.by_section.get("CH-06-2", 0) >= 1


def test_mapping_idempotent_rerun(seed_m4):
    """重跑 map_all：先清表重建，不产生重复映射行。"""
    data = seed_m4
    n1 = data["db"].query_one(
        "SELECT COUNT(*) AS n FROM requirement_section_maps WHERE tender_id = ?",
        (data["tender_id"],))["n"]
    assert n1 > 0
    stats2 = data["mapper"].map_all(data["tender_id"])
    n2 = data["db"].query_one(
        "SELECT COUNT(*) AS n FROM requirement_section_maps WHERE tender_id = ?",
        (data["tender_id"],))["n"]
    assert n2 == n1
    assert stats2.mapped == 33


def test_coverage_recompute_no_write(seed_m4):
    """coverage() 从映射表重算，不改库。"""
    data = seed_m4
    before = data["db"].query_one(
        "SELECT COUNT(*) AS n FROM requirement_section_maps WHERE tender_id = ?",
        (data["tender_id"],))["n"]
    stats = data["mapper"].coverage(data["tender_id"])
    after = data["db"].query_one(
        "SELECT COUNT(*) AS n FROM requirement_section_maps WHERE tender_id = ?",
        (data["tender_id"],))["n"]
    assert before == after
    assert stats.mapped == 33 and stats.unmapped == 0


def test_unmapped_reason_format(seed_m4):
    """未映射原因：说明是哪个类型未被声明。"""
    from app.services.matching.models import (CanonicalRequirement,
                                              RequirementSourceRef,
                                              RequirementTypeM3)
    c = CanonicalRequirement(
        id="REQ-C-XXX", tender_id=seed_m4["tender_id"],
        req_type=RequirementTypeM3.TECHNICAL, title="神秘类型需求",
        sources=[RequirementSourceRef(id="REQ-9999", type="评分标准",
                                      title="评分细则", original_text="x")])
    reason = seed_m4["mapper"]._unmapped_reason(c)
    assert "评分标准" in reason and "未被大纲任何章节声明" in reason
