# -*- coding: utf-8 -*-
"""
tests/test_m5_integration.py —— M5 集成测试（批次 4：LLM 语义覆盖二次审查）

离线确定性用例（FakeLLM 脚本化，默认跑）：
- 空返回 → 不新增 SEMANTIC_COVERAGE（离线兜底口径）
- 注入 not covered → 恰好 1 条 SEMANTIC_COVERAGE（WARNING）+ 需求溯源
- 全链路：seed_m5 → 基线无 CRITICAL/ERROR → 变异 → 检查抓取（镜像验收脚本）

真实 LLM 用例（@pytest.mark.llm，有 Key 才跑）：
- include_llm=true 全链路：报告/问题结构完整、可落库
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.api import routes_quality  # noqa: E402

from conftest import FakeLLM  # noqa: E402


# ═══════════════════════════════════════════════════════════════════════
# 离线：语义覆盖 judge 的确定性行为
# ═══════════════════════════════════════════════════════════════════════
def test_llm_judge_offline_empty_no_issue(m5_api, monkeypatch):
    """FakeLLM 空返回 → 不新增任何 SEMANTIC_COVERAGE（确定性兜底）。"""
    data, client = m5_api
    tid = data["tender_id"]
    fake = FakeLLM()
    monkeypatch.setattr(routes_quality, "create_llm_client", lambda: fake)

    r = client.post(f"/api/quality/tenders/{tid}/check?include_llm=true")
    assert r.status_code == 200
    body = r.json()
    assert body["report"]["id"] == "QR-0001"
    assert not [i for i in body["issues"]
                if i["issue_type"] == "SEMANTIC_COVERAGE"]
    assert body["report"]["score"] == 99.1   # 空判定不扣分
    assert fake.calls > 0, "include_llm=true 应当真的走 LLM 调用"


def test_llm_judge_offline_not_covered(m5_api, monkeypatch):
    """注入 not covered → 恰好 1 条 SEMANTIC_COVERAGE（WARNING，带需求溯源）。"""
    data, client = m5_api
    tid = data["tender_id"]
    fake = FakeLLM(responses=[
        {"data": {"covered": False, "reason": "相关章节未实质响应"}}])
    monkeypatch.setattr(routes_quality, "create_llm_client", lambda: fake)

    r = client.post(f"/api/quality/tenders/{tid}/check?include_llm=true")
    assert r.status_code == 200
    body = r.json()
    llm_issues = [i for i in body["issues"]
                  if i["issue_type"] == "SEMANTIC_COVERAGE"]
    assert len(llm_issues) == 1
    assert llm_issues[0]["severity"] == "WARNING"
    assert llm_issues[0]["requirement_id"]      # 溯源到规范需求
    assert llm_issues[0]["section_id"]          # 溯源到相关章节
    assert fake.calls >= 1
    assert body["report"]["issue_counts"].get("SEMANTIC_COVERAGE") == 1


# ═══════════════════════════════════════════════════════════════════════
# 全链路（离线）：seed_m5 → 基线 → 变异 → 检查抓取（镜像验收脚本 9 组变异）
# ═══════════════════════════════════════════════════════════════════════
def test_full_chain_offline(m5_api):
    """基线无 CRITICAL/ERROR → 注入跨章节冲突 → 检查抓 CONFLICT + NUMBER_MISMATCH。"""
    data, client = m5_api
    db, tid = data["db"], data["tender_id"]

    r = client.post(f"/api/quality/tenders/{tid}/check")
    base = r.json()
    assert base["report"]["counts"]["critical"] == 0
    assert base["report"]["counts"]["error"] == 0
    assert base["report"]["counts"]["pending"] == 9
    assert base["report"]["score"] == 99.1

    # 变异 8：CH-05-2 改 5000 + CH-06-1 追加 2000 → 跨章节冲突
    db.execute("UPDATE generation_sections SET content_md = "
               "REPLACE(content_md, 'max_devices=2000', 'max_devices=5000') "
               "WHERE section_id = 'CH-05-2' AND tender_id = ?", (tid,))
    db.execute("UPDATE generation_sections SET content_md = "
               "REPLACE(content_md, 'scale=单个合同额500万元。', "
               "'scale=单个合同额500万元。\n设备接入能力为2000台。') "
               "WHERE section_id = 'CH-06-1' AND tender_id = ?", (tid,))

    r2 = client.post(f"/api/quality/tenders/{tid}/check")
    assert r2.status_code == 200
    issues = r2.json()["issues"]
    types = {i["issue_type"] for i in issues}
    assert "CONFLICT" in types, [i["issue_type"] for i in issues]
    assert "NUMBER_MISMATCH" in types, [i["issue_type"] for i in issues]
    conflict = next(i for i in issues if i["issue_type"] == "CONFLICT")
    assert conflict["severity"] == "ERROR"
    assert {r["section"] for r in conflict["source_refs"]} >= {"CH-05-2",
                                                              "CH-06-1"}


# ═══════════════════════════════════════════════════════════════════════
# 真实 LLM：include_llm=true 全链路（有 Key 才跑）
# ═══════════════════════════════════════════════════════════════════════
@pytest.mark.llm
def test_llm_judge_real_endpoint(m5_api):
    """真实 Key 下 include_llm=true：报告可落库、问题结构完整。"""
    data, client = m5_api
    tid = data["tender_id"]
    r = client.post(f"/api/quality/tenders/{tid}/check?include_llm=true")
    assert r.status_code == 200
    body = r.json()
    assert body["report"]["id"] == "QR-0001"
    assert isinstance(body["issues"], list)
    # 报告已落库，可回读
    d = client.get(f"/api/quality/reports/QR-0001")
    assert d.status_code == 200 and len(d.json()["issues"]) >= 9
