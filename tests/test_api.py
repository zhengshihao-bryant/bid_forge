# -*- coding: utf-8 -*-
"""
tests/test_api.py —— FastAPI 集成测试（离线）

- TestClient + tmp_env（DB/RAW/PARSED 隔离）
- LLM 通过 monkeypatch 换成 FakeLLM（提取流程离线跑通）
- 上传用样例包真实文件（pdf 正文 + xlsx 清单），解析为真实解析器产物

注意：BackgroundTasks 在 TestClient 响应返回后同步执行，
所以 POST /extract 返回后即可断言最终状态。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from fastapi.testclient import TestClient  # noqa: E402

from app.api.main import app  # noqa: E402
from app.services import extraction as extraction_module  # noqa: E402

from conftest import BASELINE_ITEMS, FakeLLM  # noqa: E402


@pytest.fixture()
def client(tmp_env):
    """每个测试独立 DB；提取层 LLM 全部走 FakeLLM。"""
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def fake_extraction(monkeypatch):
    fake = FakeLLM()
    monkeypatch.setattr(extraction_module, "create_llm_client", lambda: fake)
    return fake


def _upload(client, sample_dir, *names, name="智慧园区测试项目"):
    files = [("files", (n, open(sample_dir / n, "rb"),
                        "application/octet-stream")) for n in names]
    try:
        return client.post("/api/tenders", files=files, data={"name": name})
    finally:
        for _, (_, fh, _) in files:
            fh.close()


# ═══════════════════════════════════════════════════════════════════════
# 服务与健康
# ═══════════════════════════════════════════════════════════════════════
def test_service_info(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "企业标书生成平台" in r.json()["service"]


def test_health(client, tmp_env):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert str(tmp_env / "bid.db") in body["db"]


# ═══════════════════════════════════════════════════════════════════════
# 上传与解析
# ═══════════════════════════════════════════════════════════════════════
def test_create_tender_with_pdf_and_xlsx(client, sample_dir):
    r = _upload(client, sample_dir, "02_技术规格书.pdf", "03_设备清单.xlsx")
    assert r.status_code == 201
    body = r.json()
    assert body["id"]
    assert len(body["results"]) == 2
    assert all(res["ok"] for res in body["results"]), body["results"]
    pdf_res = next(res for res in body["results"] if res["file"].endswith(".pdf"))
    xlsx_res = next(res for res in body["results"] if res["file"].endswith(".xlsx"))
    assert pdf_res["total_pages"] >= 10
    assert xlsx_res["total_pages"] == 3
    assert pdf_res["ocr_pages"] == []

    # 详情：文档列表 + 章节树
    detail = client.get(f"/api/tenders/{body['id']}").json()
    assert detail["name"] == "智慧园区测试项目"
    assert len(detail["documents"]) == 2
    pdf_doc = next(d for d in detail["documents"] if d["file_type"] == "pdf")
    assert len(pdf_doc["sections"]) >= 9

    # 列表
    assert any(t["id"] == body["id"] for t in client.get("/api/tenders").json())


def test_create_tender_with_scan_pdf_detects_ocr(client, sample_dir):
    r = _upload(client, sample_dir, "04_补充通知(扫描件).pdf")
    assert r.status_code == 201
    res = r.json()["results"][0]
    assert res["ok"]
    assert res["ocr_pages"] == [1, 2]


def test_upload_rejects_bad_extension(client, tmp_path):
    bad = tmp_path / "malware.exe"
    bad.write_bytes(b"MZ....")
    r = _upload(client, tmp_path, "malware.exe")
    assert r.status_code == 201   # 整单仍创建，该文件单独失败
    res = r.json()["results"][0]
    assert res["ok"] is False
    assert "不支持的文件类型" in res["error"]


def test_tender_not_found(client):
    assert client.get("/api/tenders/nonexistent").status_code == 404
    assert client.get("/api/tenders/nonexistent/requirements").status_code == 404
    assert client.get("/api/tenders/nonexistent/score-points").status_code == 404
    assert client.post("/api/tenders/nonexistent/extract").status_code == 404


# ═══════════════════════════════════════════════════════════════════════
# 需求提取全流程（FakeLLM）
# ═══════════════════════════════════════════════════════════════════════
def test_extract_flow_end_to_end(client, sample_dir, fake_extraction):
    r = _upload(client, sample_dir, "02_技术规格书.pdf")
    tender_id = r.json()["id"]

    # FakeLLM 对每个窗口返回基线两条（技术 + ★人员）
    fake_extraction.responses = [list(BASELINE_ITEMS)]

    r = client.post(f"/api/tenders/{tender_id}/extract")
    assert r.status_code == 202
    assert r.json()["extraction_status"] == "提取中"

    # BackgroundTasks 已同步执行完
    detail = client.get(f"/api/tenders/{tender_id}").json()
    assert detail["extraction_status"] == "已完成", detail["extraction_progress"]
    assert detail["requirement_count"] == 2

    # 需求列表 + 过滤
    reqs = client.get(f"/api/tenders/{tender_id}/requirements").json()
    assert len(reqs) == 2
    tech = next(x for x in reqs if "设备接入" in x["title"])
    assert tech["type"] == "技术要求"
    assert tech["quantitative"][0]["value"] == "1000"
    assert tech["source"]["doc_id"]
    assert tech["source"]["section_path"]

    stars = client.get(
        f"/api/tenders/{tender_id}/requirements", params={"is_star": "true"}).json()
    assert len(stars) == 1
    assert "项目经理" in stars[0]["title"]
    assert stars[0]["importance"] == "高"

    highs = client.get(
        f"/api/tenders/{tender_id}/requirements", params={"importance": "高"}).json()
    # 设备接入（LLM 标"高"）+ 项目经理（★补扫升"高"）
    assert len(highs) == 2
    assert {r["title"] for r in highs} == {"设备接入不少于1000台", "项目经理5年经验"}

    # 人工修订：置 human_confirmed
    rid = tech["id"]
    patched = client.patch(
        f"/api/tenders/{tender_id}/requirements/{rid}",
        json={"title": "设备接入不少于1000台（修订）", "status": "已确认"})
    assert patched.status_code == 200
    assert patched.json()["human_confirmed"] is True
    assert patched.json()["status"] == "已确认"

    # 非法修订值被拒绝
    assert client.patch(
        f"/api/tenders/{tender_id}/requirements/{rid}",
        json={"type": "外星要求"}).status_code == 422
    assert client.patch(
        f"/api/tenders/{tender_id}/requirements/{rid}",
        json={"importance": "超重要"}).status_code == 422


def test_extract_409_when_running(client, sample_dir, fake_extraction):
    """状态为提取中时再次触发 → 409。"""
    r = _upload(client, sample_dir, "03_设备清单.xlsx")
    tender_id = r.json()["id"]
    # 手动把状态置为提取中，模拟并发窗口
    from app.db import Database
    from app import config as cfg
    Database(cfg.DB_PATH).update("tenders", "id", tender_id,
                                 {"extraction_status": "提取中"})
    assert client.post(f"/api/tenders/{tender_id}/extract").status_code == 409


def test_extract_400_when_no_valid_docs(client):
    r = _upload(client, Path(__file__).parent, "conftest.py")  # .py 会被拒绝 → 无有效文档
    tender_id = r.json()["id"]
    assert all(not res["ok"] for res in r.json()["results"])
    resp = client.post(f"/api/tenders/{tender_id}/extract")
    assert resp.status_code == 400


# ═══════════════════════════════════════════════════════════════════════
# 评分点
# ═══════════════════════════════════════════════════════════════════════
def test_score_points_from_docx(client, docx_sample, fake_extraction):
    """docx 样例第十一章评分表 → 规则解析出 13 个评分点（技术 9 + 商务 4）。"""
    r = _upload(client, docx_sample.parent, docx_sample.name)
    assert r.status_code == 201
    tender_id = r.json()["id"]
    assert all(res["ok"] for res in r.json()["results"]), r.json()["results"]

    fake_extraction.responses = []
    resp = client.post(f"/api/tenders/{tender_id}/extract")
    assert resp.status_code == 202

    points = client.get(f"/api/tenders/{tender_id}/score-points").json()
    assert len(points) == 13, f"评分点数 {len(points)} != 13"
    tech = [p for p in points if p["category"] == "技术"]
    biz = [p for p in points if p["category"] == "商务"]
    assert len(tech) == 9
    assert len(biz) == 4
    assert sum(p["weight"] for p in points) == 70.0
    assert all(p["rule_id"].startswith("RULE-") for p in points)
    assert all("#" in p["source_ref"] for p in points)
