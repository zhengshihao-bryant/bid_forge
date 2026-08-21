# -*- coding: utf-8 -*-
"""
tests/test_m4_generation.py —— M4-05/06/07/08 章节生成器 + 事实约束 + 需求响应表（批次 3）

覆盖（对照 M4-11 必测项）：
- 事实型策略：公司概况（注册资本/成立年限/员工）、人员（张伟+PMP+6年，不串线）
- 表格型策略：资质表（ISO9001/CMMI3/等保三级 与证据一致）、技术指标响应表行
- 方案型 LLM：FACT/INFERENCE 分类 + 证据引用 + 数字溯源（2000 锚点通过、8888 标【待确认】）
- MISSING 无编造：5000台 无证据声称 → 降级 INFERENCE + warning
- 证据 ID 完整性：content 中 EVD- 引用全部真实存在
- LLM 失败 → 回退 FactTemplate（不产生空章节）
- 响应表：三列 + 33 行 + FULL17/PARTIAL6/MISSING5/UNKNOWN5 + MISSING/UNKNOWN 不编造
- API：/coverage、/sections（未生成 409）、/response-table（json+markdown）
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.services.generation import (BidResponseTableBuilder,  # noqa: E402
                                     SectionGenerator, render_markdown)
from app.services.generation.models import FactClass  # noqa: E402

from conftest import FakeLLM  # noqa: E402


def _section(data, sid):
    return next(s for s in data["sections"] if s.id == sid)


def _product_evd_id(db) -> str:
    """产品证据（2000 台锚点）—— 真实 EVD 编号。"""
    row = db.query_one(
        "SELECT id FROM evidences WHERE category='产品' AND content LIKE '%2000台%' "
        "ORDER BY id LIMIT 1")
    assert row, "预埋产品证据应存在"
    return row["id"]


@pytest.fixture()
def fake_gen_llm(monkeypatch):
    """方案型章节的脚本化 LLM（chat_json 返回 data.paragraphs）。"""
    from app.services.generation import strategies
    fake = FakeLLM(data_key="paragraphs")
    monkeypatch.setattr(strategies, "create_llm_client", lambda: fake)
    return fake


# ═══════════════════════════════════════════════════════════════════════
# M4-08 事实型策略
# ═══════════════════════════════════════════════════════════════════════
def test_fact_company_profile(seed_m4):
    """公司概况：能力卡数值回填，且全在事实语料（不标【待确认】）。"""
    data = seed_m4
    gen = SectionGenerator(db=data["db"])
    draft = gen.generate_section(_section(data, "CH-04-1"), data["tender_id"])
    md = draft.content_md
    assert "公司概况与综合实力" in md
    assert "注册资本5000万元" in md
    assert "成立已16年" in md
    assert "员工规模300-600人" in md
    assert "【待确认】" not in md, "能力卡数值都在事实语料，不应标待确认"
    assert draft.section_type.value == "事实型"
    # 证据依据页脚
    assert "本章证据依据" in md


def test_fact_personnel_not_cross_wired(seed_m4):
    """人员配备：张伟绑定 PMP/6年；且不串入公司概况等其他章节。"""
    data = seed_m4
    gen = SectionGenerator(db=data["db"])
    md_ch062 = gen.generate_section(_section(data, "CH-06-2"),
                                    data["tender_id"]).content_md
    assert "张伟" in md_ch062
    assert "PMP" in md_ch062
    assert "6年" in md_ch062
    fact_paras = [p for p in gen.generate_section(_section(data, "CH-06-2"),
                    data["tender_id"]).paragraphs if p.fact_class == FactClass.FACT]
    assert any("张伟" in p.text for p in fact_paras), "人员 FACT 段应绑定"
    # 不串线：公司概况（无人员卡）不得出现张伟
    md_ch041 = gen.generate_section(_section(data, "CH-04-1"),
                                    data["tender_id"]).content_md
    assert "张伟" not in md_ch041, "人员姓名不得串入公司概况"


def test_warranty_template_honest_for_missing(seed_m4):
    """MISSING（5年质保/30分钟）段无“我司具备/5年质保”；质保卡事实（3年/2小时/2人）保留 FACT。"""
    data = seed_m4
    gen = SectionGenerator(db=data["db"])
    draft = gen.generate_section(_section(data, "CH-07-2"), data["tender_id"])
    md = draft.content_md
    assert "质保期3年" in md and "2小时到场" in md and "驻场工程师2人" in md
    assert "质保期5年" not in md and "30分钟" not in md, "不得编造满足 MISSING 指标"
    # FACT 段保留（数值回溯到能力卡/证据 m4_C0001）
    fact_paras = [p for p in draft.paragraphs if p.fact_class == FactClass.FACT]
    assert any("质保期3年" in p.text for p in fact_paras)


# ═══════════════════════════════════════════════════════════════════════
# M4-08 表格型策略
# ═══════════════════════════════════════════════════════════════════════
def test_table_certs_consistent_with_evidence(seed_m4):
    """资质表：编号与证据原文一致（ISO9001/ISO27001/CMMI3/等保三级）。"""
    data = seed_m4
    gen = SectionGenerator(db=data["db"])
    draft = gen.generate_section(_section(data, "CH-04-2"), data["tender_id"])
    tables = [p.table for p in draft.paragraphs if p.type == "table"]
    assert tables, "应有资质表"
    flat = " ".join(c for rows in tables[0] for c in rows)
    for cert in ("ISO9001", "ISO27001", "CMMI3", "等保三级"):
        assert cert in flat, cert
    # 证据原文含相同编号
    evd_row = data["db"].query_one(
        "SELECT content FROM evidences WHERE category='公司资质' LIMIT 1")
    assert evd_row and "ISO9001" in evd_row["content"] and "等保三级" in evd_row["content"]


def test_table_metric_response_rows(seed_m4):
    """技术指标响应表：逐需求一行；MISSING 不满足；UNKNOWN 待确认。"""
    data = seed_m4
    gen = SectionGenerator(db=data["db"])
    draft = gen.generate_section(_section(data, "CH-05-4"), data["tender_id"])
    tables = [p.table for p in draft.paragraphs if p.type == "table"]
    assert tables
    rows = tables[0]
    assert "招标要求" in rows[0] and "企业响应" in rows[0]
    by_title = {r[0]: r for r in rows[1:]}
    assert len(by_title) >= 10, "技术部分需求应逐行展开"
    # MISSING 5000台 → 不满足
    r5000 = next(v for k, v in by_title.items() if "5000台" in k)
    assert r5000[3] == "MISSING" and "不满足" in r5000[2], r5000
    # UNKNOWN 信创 → 待确认
    r_xin = next(v for k, v in by_title.items() if "信创" in k)
    assert r_xin[3] == "UNKNOWN" and "待确认" in r_xin[2], r_xin
    # FULL 设备接入（1000台）→ 满足 + 证据摘要
    r1000 = next(v for k, v in by_title.items() if "1000台" in k)
    assert r1000[3] == "FULL" and "满足" in r1000[2]


def test_table_case_projects(seed_m4):
    """业绩表：从能力卡取数（3 个/500万元），不编造。"""
    data = seed_m4
    gen = SectionGenerator(db=data["db"])
    draft = gen.generate_section(_section(data, "CH-04-3"), data["tender_id"])
    tables = [p.table for p in draft.paragraphs if p.type == "table"]
    assert tables
    flat = " ".join(c for rows in tables[0] for c in rows)
    assert "3" in flat and "500" in flat


# ═══════════════════════════════════════════════════════════════════════
# M4-05/06 方案型 LLM + 事实约束校验
# ═══════════════════════════════════════════════════════════════════════
def test_solution_llm_classification_and_number_trace(seed_m4, fake_gen_llm):
    """FACT 段数字全在事实语料（2000 锚点通过）；8888 编造 → 原位标【待确认】。"""
    data = seed_m4
    evd = _product_evd_id(data["db"])
    fake_gen_llm.responses.append([
        {"type": "heading", "text": "总体技术方案", "level": 2,
         "fact_class": "INFERENCE", "evidence_ids": []},
        {"type": "paragraph",
         "text": "我司平台设备接入支持2000台，并发1000用户，系统可用性99.95%。",
         "fact_class": "FACT", "evidence_ids": [evd]},
        {"type": "paragraph", "text": "我司提供接入能力8888台。",
         "fact_class": "FACT", "evidence_ids": [evd]},
    ])
    gen = SectionGenerator(db=data["db"])
    draft = gen.generate_section(_section(data, "CH-05-2"), data["tender_id"])
    p_ok = next(p for p in draft.paragraphs if "2000台" in p.text)
    assert p_ok.fact_class == FactClass.FACT
    assert "【待确认】" not in p_ok.text, "2000 在事实语料，不应标待确认"
    p_bad = next(p for p in draft.paragraphs if "8888" in p.text)
    assert p_bad.fact_class == FactClass.FACT
    assert "8888【待确认】" in p_bad.text, "8888 不在事实语料，应原位标待确认"
    assert any("8888" in w for w in draft.warnings), draft.warnings
    # 覆盖标记 + 证据依据页脚（真实 EVD 出现在页脚）
    assert any(c.covered for c in draft.requirement_coverage)
    assert "本章证据依据" in draft.content_md
    assert evd in draft.content_md


def test_missing_no_fabrication_downgrade(seed_m4, fake_gen_llm):
    """MISSING（5000台）无证据声称具备 → 降级 INFERENCE + warning（M4-11 落点）。"""
    data = seed_m4
    fake_gen_llm.responses.append([
        {"type": "paragraph",
         "text": "我司已完全满足5000台设备接入要求。",
         "fact_class": "FACT", "evidence_ids": []},
    ])
    gen = SectionGenerator(db=data["db"])
    draft = gen.generate_section(_section(data, "CH-05-2"), data["tender_id"])
    p = next(p for p in draft.paragraphs if "5000台" in p.text)
    assert p.fact_class == FactClass.INFERENCE, "MISSING 无证据声称具备 → 降级"
    assert any("无证据" in w for w in draft.warnings), draft.warnings


def test_evidence_id_integrity(seed_m4, fake_gen_llm):
    """证据 ID 存在性：引用不存在的 EVD 被剔除 + warning；content 中 EVD- 全部真实。"""
    data = seed_m4
    evd = _product_evd_id(data["db"])
    fake_gen_llm.responses.append([
        {"type": "paragraph", "text": "我司平台设备接入支持2000台。",
         "fact_class": "FACT", "evidence_ids": [evd, "EVD-9999"]},
    ])
    gen = SectionGenerator(db=data["db"])
    draft = gen.generate_section(_section(data, "CH-05-2"), data["tender_id"])
    p = next(p for p in draft.paragraphs if "2000台" in p.text)
    assert p.evidence_ids == [evd], "EVD-9999 应被剔除"
    assert any("EVD-9999" in w for w in draft.warnings)
    # evidence_refs 全部 ∈ evidences 表
    all_ids = {r["id"] for r in data["db"].query("SELECT id FROM evidences")}
    assert draft.evidence_refs
    assert all(r.evidence_id in all_ids for r in draft.evidence_refs)
    # content_md 中出现的 EVD- 引用全部真实
    for m in re.findall(r"EVD-\d+", draft.content_md):
        assert m in all_ids, f"内容引用了不存在的证据 {m}"


def test_solution_llm_failure_fallback_to_fact_template(seed_m4, fake_gen_llm):
    """方案型 LLM 无有效输出 → 回退 FactTemplate（不产生空章节）。"""
    data = seed_m4
    # FakeLLM 无响应 → {"paragraphs": []} → 回退
    gen = SectionGenerator(db=data["db"])
    draft = gen.generate_section(_section(data, "CH-05-2"), data["tender_id"])
    assert draft.paragraphs, "不应产生空章节"
    assert any("max_devices=2000" in p.text for p in draft.paragraphs), \
        "回退产物应来自产品能力卡"


def test_render_markdown_structure(seed_m4):
    """render_markdown：标题 + 正文 + 证据依据页脚。"""
    data = seed_m4
    gen = SectionGenerator(db=data["db"])
    draft = gen.generate_section(_section(data, "CH-04-1"), data["tender_id"])
    assert draft.content_md.startswith("## 公司概况与综合实力")
    assert "本章证据依据" in draft.content_md
    assert render_markdown(draft) == draft.content_md


# ═══════════════════════════════════════════════════════════════════════
# M4-08 固定格式
# ═══════════════════════════════════════════════════════════════════════
def test_fixed_format_quote_no_price(seed_m4):
    """报价表：模板渲染，绝不编造价格。"""
    data = seed_m4
    gen = SectionGenerator(db=data["db"])
    draft = gen.generate_section(_section(data, "CH-04-5"), data["tender_id"])
    md = draft.content_md
    assert "禁止编造价格" in md or "禁止编造报价" in md
    assert "单价" in md
    assert "万元" not in md, "报价表不得出现金额"
    assert all(p.fact_class == FactClass.INFERENCE for p in draft.paragraphs)


# ═══════════════════════════════════════════════════════════════════════
# M4-07 需求响应表
# ═══════════════════════════════════════════════════════════════════════
def test_response_table_33_rows_honest(seed_m4):
    """响应表：33 行四状态分布正确；MISSING/UNKNOWN 不编造。"""
    data = seed_m4
    built = BidResponseTableBuilder(data["db"]).build(data["tender_id"])
    assert built["total"] == 33
    assert built["counts"] == {"FULL": 17, "PARTIAL": 6, "MISSING": 5,
                               "UNKNOWN": 5}, built["counts"]
    missing = [r for r in built["rows"] if r["status"] == "MISSING"]
    unknown = [r for r in built["rows"] if r["status"] == "UNKNOWN"]
    assert missing and unknown
    for r in missing:
        assert "不满足" in r["response"], r
        assert "我司具备" not in r["response"]
        assert "我司已完全满足" not in r["response"]
    for r in unknown:
        assert "待确认" in r["response"], r
        assert "【待确认】" in r["response"]
    # 证据引用全部真实
    all_ids = {e["id"] for e in data["db"].query("SELECT id FROM evidences")}
    for r in built["rows"]:
        for ev in r["evidences"]:
            assert ev["evidence_id"] in all_ids


def test_response_table_markdown_three_columns(seed_m4):
    """响应表 Markdown：三列 + 33 行 + 状态口径注。"""
    data = seed_m4
    md = BidResponseTableBuilder(data["db"]).to_markdown(data["tender_id"])
    assert "| 招标要求 | 企业响应 | 证据 |" in md
    assert md.count("| **") >= 33, "应每行一条需求（序号列）"
    assert "MISSING=资料明确显示不满足" in md
    assert "不满足" in md


# ═══════════════════════════════════════════════════════════════════════
# API 端点
# ═══════════════════════════════════════════════════════════════════════
@pytest.fixture()
def gen_client(seed_m4):
    """seed_m4 + FastAPI TestClient（同一 tmp_env DB；补 tender 行供路由查询）。"""
    from fastapi.testclient import TestClient
    from app.api.main import app

    data = seed_m4
    data["db"].insert("tenders", {"id": data["tender_id"], "name": "M4生成测试项目",
                                  "created_at": "2026-01-01 00:00:00"})
    with TestClient(app) as c:
        yield data, c


def test_coverage_endpoint(gen_client):
    data, c = gen_client
    r = c.get(f"/api/generation/tenders/{data['tender_id']}/coverage")
    assert r.status_code == 200
    j = r.json()
    assert j["total"] == 33 and j["mapped"] == 33
    assert j["by_section"].get("CH-05-2", 0) >= 1


def test_section_endpoint_409_before_generation(gen_client):
    """未生成章节 GET → 409（先触发生成任务）。"""
    data, c = gen_client
    r = c.get(f"/api/generation/tenders/{data['tender_id']}/sections/CH-05-2")
    assert r.status_code == 409


def test_section_endpoint_404(gen_client):
    data, c = gen_client
    r = c.get(f"/api/generation/tenders/{data['tender_id']}/sections/CH-99")
    assert r.status_code == 404


def test_response_table_endpoint(gen_client):
    data, c = gen_client
    r = c.get(f"/api/generation/tenders/{data['tender_id']}/response-table")
    assert r.status_code == 200
    j = r.json()
    assert j["total"] == 33 and j["counts"]["FULL"] == 17
    r = c.get(f"/api/generation/tenders/{data['tender_id']}/response-table"
              "?format=markdown")
    assert r.status_code == 200
    assert "招标要求" in r.json()["content"]
