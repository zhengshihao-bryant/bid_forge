# -*- coding: utf-8 -*-
"""
tests/test_kb_api.py —— 知识库 API 集成测试（离线）

TestClient + tmp_env + kb_sample_dir + kb_fake_env（真实样例文件，全离线）：
- 上传（8 类枚举校验）→ 列表/状态过滤 → 详情章节树 → chunks 分页（无 embedding 字段）
- process 端到端：BackgroundTasks 在 TestClient 返回后同步执行 → 直接断言终态
- 语义检索：Milvus 禁用 → engine=sqlite 降级透明 + 四元溯源出处正确
- 守卫：坏类别 422 / 解析失败 400 / 处理中 409 / 不存在 404
- 删除级联 + 能力卡全局列表/过滤 + PATCH 人工修订
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from fastapi.testclient import TestClient  # noqa: E402

from app import config  # noqa: E402
from app.api.main import app  # noqa: E402
from app.db import Database  # noqa: E402

from conftest import FakeLLM  # noqa: E402


@pytest.fixture()
def client(tmp_env):
    """每个测试独立 DB（tmp_env 隔离）；知识库处理全离线（kb_fake_env）。"""
    with TestClient(app) as c:
        yield c


def _upload(client, path: Path, category: str) -> "httpx.Response":  # noqa: F821
    files = [("files", (path.name, open(path, "rb"), "application/octet-stream"))]
    try:
        return client.post("/api/knowledge/materials",
                           files=files, data={"category": category})
    finally:
        for _, (_, fh, _) in files:
            fh.close()


def _upload_kb(client, kb_sample_dir, category: str, file_name: str):
    return _upload(client, kb_sample_dir / file_name, category)


_ZHANGWEI = {
    "category": "人员资质", "name": "张伟-项目经理",
    "description": "6 年智慧园区类项目管理经验，PMP",
    "attributes": {"person_name": "张伟", "role": "项目经理",
                   "experience_years": "6",
                   "certs": ["PMP", "信息系统项目管理师"],
                   "projects": ["智慧园区一期"]},
    "page": 2,
}


# ═══════════════════════════════════════════════════════════════════════
# 上传 / 列表 / 详情
# ═══════════════════════════════════════════════════════════════════════
def test_upload_and_list(client, kb_sample_dir, kb_fake_env):
    r = _upload_kb(client, kb_sample_dir, "人员资质", "04_人员资质.docx")
    assert r.status_code == 201
    res = r.json()["results"][0]
    assert res["ok"], res
    mid = res["material_id"]
    assert res["char_count"] > 0

    r2 = _upload_kb(client, kb_sample_dir, "产品", "01_产品介绍.pdf")
    assert r2.status_code == 201
    assert r2.json()["results"][0]["ok"]
    assert r2.json()["results"][0]["total_pages"] >= 2   # PDF 有页码

    # 列表 + 类别过滤
    mats = client.get("/api/knowledge/materials").json()
    assert len(mats) == 2
    prods = client.get("/api/knowledge/materials",
                       params={"category": "产品"}).json()
    assert len(prods) == 1 and prods[0]["category"] == "产品"
    # 非法 status → 422
    assert client.get("/api/knowledge/materials",
                      params={"status": "乱写"}).status_code == 422

    # 详情：章节树 + 初始状态
    detail = client.get(f"/api/knowledge/materials/{mid}").json()
    assert detail["file_name"] == "04_人员资质.docx"
    assert detail["process_status"] == "未处理"
    assert detail["sections"], "章节树应从解析产物加载"
    assert detail["sections"][0]["title"]


# ═══════════════════════════════════════════════════════════════════════
# 处理端到端 + 检索（TestClient 同步执行 BackgroundTasks）
# ═══════════════════════════════════════════════════════════════════════
def test_process_end_to_end(client, kb_sample_dir, kb_fake_env):
    fake = kb_fake_env
    fake.responses = [[dict(_ZHANGWEI)]]
    r = _upload_kb(client, kb_sample_dir, "人员资质", "04_人员资质.docx")
    mid = r.json()["results"][0]["material_id"]

    pr = client.post(f"/api/knowledge/materials/{mid}/process")
    assert pr.status_code == 202
    assert pr.json()["process_status"] == "处理中"

    # BackgroundTasks 已同步跑完 → 直接断言终态
    detail = client.get(f"/api/knowledge/materials/{mid}").json()
    assert detail["process_status"] == "已完成"
    assert detail["chunk_count"] > 0
    assert detail["capability_count"] == 1
    assert detail["index_status"] == "done"

    # 能力卡事实核对（张伟 6 年 / PMP，出处正确）
    caps = client.get(f"/api/knowledge/materials/{mid}/capabilities").json()
    assert len(caps) == 1
    cap = caps[0]
    assert cap["id"].startswith("CAP-")
    assert cap["name"] == "张伟-项目经理"
    assert cap["source_doc"] == "04_人员资质.docx"
    assert cap["attributes"]["experience_years"] == "6"
    assert "PMP" in cap["attributes"]["certs"]

    # chunks 分页：不含 embedding（向量只在内部使用）
    page = client.get(f"/api/knowledge/materials/{mid}/chunks",
                      params={"limit": 50}).json()
    assert page["total"] == detail["chunk_count"]
    assert all("embedding" not in c for c in page["chunks"])
    assert all(c["id"].startswith(mid) and c["seq"] >= 1
               for c in page["chunks"])
    joined = "".join(c["content"] for c in page["chunks"])
    assert "张伟" in joined and "6" in joined

    # 语义检索：Milvus 禁用 → engine=sqlite；四元溯源完整
    s = client.get("/api/knowledge/search",
                   params={"q": "张伟项目经理多少年经验"}).json()
    assert s["engine"] == "sqlite"
    assert s["hits"], "离线 FakeEmbedding 也应命中（共享字串 → 余弦）"
    top = s["hits"][0]
    assert top["file_name"] == "04_人员资质.docx"
    assert top["category"] == "人员资质"
    anchor = top["anchor"]
    assert "张伟" in anchor["snippet"]
    assert anchor["document"] == "04_人员资质.docx"
    assert anchor["section_path"]

    # 类别过滤检索
    s2 = client.get("/api/knowledge/search",
                    params={"q": "张伟", "category": "产品"}).json()
    assert s2["hits"] == []


def test_process_historical_bid_skips_capabilities(client, kb_sample_dir, kb_fake_env):
    """历史标书：处理完成但 0 张卡片（只切块嵌入），LLM 0 次调用。"""
    fake = kb_fake_env
    r = _upload_kb(client, kb_sample_dir, "历史标书", "08_历史标书.docx")
    mid = r.json()["results"][0]["material_id"]
    assert client.post(f"/api/knowledge/materials/{mid}/process").status_code == 202

    detail = client.get(f"/api/knowledge/materials/{mid}").json()
    assert detail["process_status"] == "已完成"
    assert detail["chunk_count"] > 0
    assert detail["capability_count"] == 0
    assert detail["index_status"] == "done"
    assert fake.calls == 0


# ═══════════════════════════════════════════════════════════════════════
# 守卫
# ═══════════════════════════════════════════════════════════════════════
def test_process_guards(client, kb_sample_dir, kb_fake_env, tmp_path):
    # 坏类别 → 422（上传即拒）
    r = _upload_kb(client, kb_sample_dir, "外星类别", "04_人员资质.docx")
    assert r.status_code == 422

    # 解析失败（受支持扩展名 + 坏内容）→ 入库带 parse_error，process → 400
    bad = tmp_path / "broken.docx"
    bad.write_bytes(b"not a zip file at all")
    r = _upload(client, bad, "产品")
    assert r.status_code == 201
    res = r.json()["results"][0]
    assert res["ok"] is False
    pr = client.post(f"/api/knowledge/materials/{res['material_id']}/process")
    assert pr.status_code == 400

    # 处理中 → 409（直接落库造状态：TestClient 同步跑完任务，串行请求造不出竞态）
    r = _upload_kb(client, kb_sample_dir, "人员资质", "04_人员资质.docx")
    mid = r.json()["results"][0]["material_id"]
    db = Database(config.DB_PATH)
    db.update("kb_materials", "id", mid, {"process_status": "处理中"})
    assert client.post(f"/api/knowledge/materials/{mid}/process").status_code == 409

    # 不存在 → 404
    assert client.post("/api/knowledge/materials/nope/process").status_code == 404
    assert client.get("/api/knowledge/materials/nope").status_code == 404
    assert client.delete("/api/knowledge/materials/nope").status_code == 404


# ═══════════════════════════════════════════════════════════════════════
# 删除级联 + 能力卡 PATCH
# ═══════════════════════════════════════════════════════════════════════
def test_delete_cascade_and_patch(client, kb_sample_dir, kb_fake_env):
    fake = kb_fake_env
    fake.responses = [[dict(_ZHANGWEI)]]
    r = _upload_kb(client, kb_sample_dir, "人员资质", "04_人员资质.docx")
    mid = r.json()["results"][0]["material_id"]
    assert client.post(f"/api/knowledge/materials/{mid}/process").status_code == 202

    caps = client.get(f"/api/knowledge/materials/{mid}/capabilities").json()
    cap_id = caps[0]["id"]

    # 全局列表 + 双过滤器
    assert len(client.get("/api/knowledge/capabilities").json()) == 1
    by_cat = client.get("/api/knowledge/capabilities",
                        params={"category": "人员资质"}).json()
    assert len(by_cat) == 1
    by_doc = client.get("/api/knowledge/capabilities",
                        params={"source_doc": "04_人员资质.docx"}).json()
    assert len(by_doc) == 1
    assert client.get("/api/knowledge/capabilities",
                      params={"category": "产品"}).json() == []

    # PATCH 人工修订（attributes 整体替换）
    p = client.patch(f"/api/knowledge/capabilities/{cap_id}",
                     json={"name": "张伟-高级项目经理",
                           "attributes": {"person_name": "张伟",
                                          "role": "高级项目经理",
                                          "experience_years": "6"}})
    assert p.status_code == 200
    assert p.json()["name"] == "张伟-高级项目经理"
    assert p.json()["attributes"]["role"] == "高级项目经理"

    # PATCH 守卫
    assert client.patch("/api/knowledge/capabilities/CAP-9999",
                        json={"name": "x"}).status_code == 404
    assert client.patch(f"/api/knowledge/capabilities/{cap_id}",
                        json={"name": "   "}).status_code == 422
    assert client.patch(f"/api/knowledge/capabilities/{cap_id}",
                        json={"category": "外星"}).status_code == 422

    # 删除级联：材料/卡片/chunks 全清，检索不再命中
    d = client.delete(f"/api/knowledge/materials/{mid}")
    assert d.status_code == 200
    assert d.json()["capabilities_deleted"] == 1
    assert client.get(f"/api/knowledge/materials/{mid}").status_code == 404
    assert client.get(f"/api/knowledge/materials/{mid}/chunks").status_code == 404
    assert client.get(f"/api/knowledge/materials/{mid}/capabilities").status_code == 404
    assert client.get("/api/knowledge/capabilities").json() == []
    s = client.get("/api/knowledge/search", params={"q": "张伟"}).json()
    assert s["hits"] == []
