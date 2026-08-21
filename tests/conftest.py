# -*- coding: utf-8 -*-
"""
tests/conftest.py —— 测试夹具

- sys.path 挂载 backend（测试从仓库根跑：pytest tests/）
- sample_files：样例包缺失时用 --no-llm 补生成 pdf/xlsx/scan
  （docx 依赖 LLM/缓存生成耗时，缺失时由各测试 skip 而非自动生成，
   避免与后台 LLM 生成进程竞争输出文件）
- kb_sample_dir：M2 样例企业资料包（8 类全规则生成，缺失时离线补生成）
- tmp_env：把 DB/RAW/PARSED/KB_RAW/KB_PARSED 指到 pytest 临时目录，测试不污染真实数据
- kb_fake_env：M2 离线环境——禁 Milvus + FakeLLM(data_key=capabilities) + FakeEmbedding
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND = REPO_ROOT / "backend"
sys.path.insert(0, str(BACKEND))

from app import config  # noqa: E402
from app.db import Database  # noqa: E402

SAMPLE_DIR = config.SAMPLES_DIR / "智慧园区项目"
KB_SAMPLE_DIR = config.SAMPLES_DIR / "企业资料包"


@pytest.fixture(scope="session")
def sample_dir(tmp_path_factory) -> Path:
    """确保 pdf/xlsx/scan 三个规则生成文件存在（缺失时离线补生成）。"""
    missing = []
    for name in ("02_技术规格书.pdf", "03_设备清单.xlsx", "04_补充通知(扫描件).pdf"):
        if not (SAMPLE_DIR / name).exists():
            missing.append(name)
    if missing:
        # pdf / xlsx / scan 生成不走 LLM，几秒内完成，无缓存竞争
        env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
        for flag in ("pdf", "xlsx", "scan"):
            subprocess.run(
                [sys.executable, str(REPO_ROOT / "scripts" / "make_sample_tender.py"),
                 "--only", flag],
                cwd=str(REPO_ROOT), check=True, env=env, capture_output=True)
    return SAMPLE_DIR


@pytest.fixture(scope="session")
def docx_sample(sample_dir) -> Path:
    """docx 样例：未生成时跳过依赖它的测试（后台 LLM 生成可能仍在跑）。"""
    p = sample_dir / "01_招标文件正文.docx"
    if not p.exists():
        pytest.skip("01_招标文件正文.docx 未生成（运行 scripts/make_sample_tender.py 后重跑）")
    return p


# ═══════════════════════════════════════════════════════════════════════
# M2 企业知识库夹具
# ═══════════════════════════════════════════════════════════════════════
_KB_FILES = {
    "产品": "01_产品介绍.pdf",
    "项目案例": "02_项目案例.docx",
    "公司资质": "03_公司资质.docx",
    "人员资质": "04_人员资质.docx",
    "技术方案": "05_技术方案.pdf",
    "售后服务": "06_售后服务.docx",
    "公司介绍": "07_公司介绍.pdf",
    "历史标书": "08_历史标书.docx",
}


@pytest.fixture(scope="session")
def kb_sample_dir(tmp_path_factory) -> Path:
    """样例企业资料包：8 类全规则生成（--no-llm），缺失时离线补生成。"""
    missing = [f for f in _KB_FILES.values() if not (KB_SAMPLE_DIR / f).exists()]
    if missing:
        env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
        subprocess.run(
            [sys.executable, str(REPO_ROOT / "scripts" / "make_sample_kb.py"), "--no-llm"],
            cwd=str(REPO_ROOT), check=True, env=env, capture_output=True)
    return KB_SAMPLE_DIR


@pytest.fixture()
def tmp_env(monkeypatch, tmp_path):
    """隔离 DB / RAW / PARSED / KB_RAW / KB_PARSED 到临时目录。"""
    data = tmp_path / "data"
    for sub in ("raw", "parsed", "kb_raw", "kb_parsed"):
        (data / sub).mkdir(parents=True)
    monkeypatch.setattr(config, "DB_PATH", data / "bid.db")
    monkeypatch.setattr(config, "RAW_DIR", data / "raw")
    monkeypatch.setattr(config, "PARSED_DIR", data / "parsed")
    monkeypatch.setattr(config, "KB_RAW_DIR", data / "kb_raw")
    monkeypatch.setattr(config, "KB_PARSED_DIR", data / "kb_parsed")
    return data


@pytest.fixture()
def kb_fake_env(monkeypatch, tmp_env):
    """M2 离线处理环境：禁 Milvus + 脚本化能力卡 LLM + 确定性伪嵌入。

    注意嵌入工厂要在两处名字都 patch：capability_extractor 模块导入时已
    绑定 create_embedding，而 SearchService.__init__ 惰性 import embedding 模块
    的 create_embedding——只 patch 一处会漏掉另一条路径。
    """
    from app.services import capability_extractor, embedding
    from app.services.embedding import FakeEmbedding

    monkeypatch.setattr(config, "MILVUS_ENABLED", False)
    fake = FakeLLM(data_key="capabilities")
    monkeypatch.setattr(capability_extractor, "create_llm_client", lambda: fake)
    fake_emb = FakeEmbedding(dimension=config.EMBEDDING_DIM)
    monkeypatch.setattr(capability_extractor, "create_embedding", lambda: fake_emb)
    monkeypatch.setattr(embedding, "create_embedding", lambda: fake_emb)
    return fake


# ═══════════════════════════════════════════════════════════════════════
# FakeLLM（离线提取测试）
# ═══════════════════════════════════════════════════════════════════════
class FakeLLM:
    """脚本化 LLM：按窗口顺序弹出预设响应；耗尽后返回空列表。

    data_key 控制响应包装字段：需求提取用 "requirements"（默认），
    能力卡提取用 "capabilities"（FakeLLM(data_key="capabilities")）。
    """

    model = "fake"

    def __init__(self, responses=None, data_key="requirements"):
        self.responses = list(responses or [])
        self.data_key = data_key
        self.calls = 0

    def chat_json(self, system, user, temperature=None, max_tokens=None):
        self.calls += 1
        if self.responses:
            resp = self.responses.pop(0)
            if isinstance(resp, dict) and "data" in resp:
                return resp
            return {"data": {self.data_key: resp}, "finish_reason": "stop",
                    "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}}
        return {"data": {self.data_key: []}, "finish_reason": "stop",
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}}

    def chat(self, messages, temperature=None, max_tokens=None, response_format=None):
        return {"content": '{"requirements": []}', "model": "fake",
                "usage": {}, "finish_reason": "stop"}


@pytest.fixture()
def fake_llm():
    return FakeLLM()


# 基准需求样本：一条技术（带量化）、一条人员（★条款）
BASELINE_ITEMS = [
    {
        "type": "技术要求", "title": "设备接入不少于1000台",
        "original_text": "平台应支持不少于 1000 台（个）设备的接入管理。",
        "quantitative": [{"metric": "设备接入", "op": "不少于", "value": "1000", "unit": "台"}],
        "importance": "高", "is_star": False, "page": 12,
    },
    {
        "type": "人员要求", "title": "项目经理5年经验",
        "original_text": "★项目经理须具有 5 年以上智慧园区类项目管理经验，并具有 PMP 证书。",
        "quantitative": [{"metric": "经验", "op": "不少于", "value": "5", "unit": "年"}],
        "importance": "中", "is_star": False, "page": 45,
    },
]


# ═══════════════════════════════════════════════════════════════════════
# M3 需求-能力匹配夹具
# ═══════════════════════════════════════════════════════════════════════
_REQ_SEQ = {"n": 0}


def m3_req(tender_id: str = "T-M3", rid: str | None = None,
           type_: str = "技术要求", title: str = "", text: str = "",
           quantitative: list | None = None, importance: str = "中",
           is_star: bool = False, document: str = "01_招标文件.docx",
           page: int | None = 1, section_path: str = "第三章 技术要求",
           block_id: str = "", snippet: str = ""):
    """构造 M1 Requirement（M3 测试用）。不传 rid 时自动 REQ-XXXX 编号。"""
    from app.schemas import (QuantitativeItem, Requirement, RequirementType,
                             SourceAnchor)

    if rid is None:
        _REQ_SEQ["n"] += 1
        rid = f"REQ-{_REQ_SEQ['n']:04d}"
    return Requirement(
        id=rid, tender_id=tender_id,
        type=RequirementType(type_),
        title=title, original_text=text,
        quantitative=[QuantitativeItem(**q) for q in (quantitative or [])],
        importance=importance, is_star=is_star,
        source=SourceAnchor(
            document=document, doc_id=f"doc-{document}",
            page=page, section_path=section_path,
            block_id=block_id, snippet=snippet or text[:80]),
    )


def seed_m3_kb(db, emb, materials: list[dict] | None = None,
               capabilities: list[dict] | None = None,
               chunks: list[dict] | None = None) -> dict:
    """直接把企业资料/能力卡/知识块写入临时 DB（跳过上传与处理管线）。

    返回 {material_ids: {file_name: id}} 供测试断言溯源。
    chunks 的 embedding 用注入的 FakeEmbedding 计算（SQLite 降级检索依赖）。
    """
    import json

    from app.schemas import Capability, CapabilityCategory, KbChunk

    ids: dict[str, str] = {}
    for m in (materials or []):
        db.insert("kb_materials", {
            "id": m["id"], "category": m["category"],
            "file_name": m["file_name"], "stored_name": m["file_name"],
            "file_type": m.get("file_type", "pdf"),
            "total_pages": m.get("total_pages", 1),
            "char_count": m.get("char_count", 0),
            "ocr_pages": "[]", "raw_hash": "", "parser_version": "1.0.0",
            "parse_error": "", "parsed_file": f"{m['id']}.json",
            "process_status": "已完成", "process_progress": "",
            "chunk_count": m.get("chunk_count", 0),
            "capability_count": m.get("capability_count", 0),
            "index_status": "done",
            "created_at": m.get("created_at", "2026-01-01 00:00:00"),
        })
        ids[m["file_name"]] = m["id"]
    for c in (capabilities or []):
        cap = Capability(
            id=c["id"], category=CapabilityCategory(c["category"]),
            name=c["name"], attributes=c.get("attributes", {}),
            description=c.get("description", ""),
            source_doc=c.get("source_doc", ""),
            source_page=c.get("source_page"),
        )
        db.insert("capabilities", Database.capability_to_row(cap))
    for ch in (chunks or []):
        chunk = KbChunk(
            id=ch["id"], material_id=ch["material_id"],
            category=CapabilityCategory(ch["category"]),
            file_name=ch["file_name"], content=ch["content"],
            section_path=ch.get("section_path", ""),
            page_start=ch.get("page_start"), page_end=ch.get("page_end"),
            block_ids=ch.get("block_ids", []),
            seq=ch.get("seq", 1),
        )
        vec = emb.embed([ch["content"]])[0]
        db.insert("kb_chunks", Database.chunk_to_row(
            chunk, embedding=[round(x, 6) for x in vec]))
    return ids


@pytest.fixture()
def m3_env(monkeypatch, tmp_env):
    """M3 离线环境：禁 Milvus + 确定性伪嵌入（SearchService 惰性导入路径）。

    不 patch LLM：无 LLM_API_KEY 时 create_llm_client 自动 Mock，
    normalizer/judge 均按 mock 模型走确定性回退 —— 正是 M3 的离线口径。
    """
    from app.services import embedding
    from app.services.embedding import FakeEmbedding

    monkeypatch.setattr(config, "MILVUS_ENABLED", False)
    fake_emb = FakeEmbedding(dimension=config.EMBEDDING_DIM)
    monkeypatch.setattr(embedding, "create_embedding", lambda: fake_emb)
    return fake_emb


# ═══════════════════════════════════════════════════════════════════════
# M4 标书生成种子（批次 2 起共享）
# ═══════════════════════════════════════════════════════════════════════
@pytest.fixture()
def seed_m4(tmp_env, m3_env):
    """M4 全种子：M3 匹配结果 + 默认大纲实例化 + 需求→章节映射落库。

    = M3 基线（_tender_reqs + seed_m3_kb + Matcher().match）＋
      outline seed/materialize → generation_sections ＋
      RequirementSectionMapper.map_all → requirement_section_maps

    返回 {db, tender_id, sections(flat BidSection 列表), coverage, builder, mapper}。
    数据源单一（test_m3_matcher），M4 各批次测试共享。
    """
    import test_m3_matcher as tm

    from app.services.generation import OutlineBuilder, RequirementSectionMapper

    db = tm._setup(tmp_env, m3_env)
    tm._run(db)
    tender_id = tm.TENDER_ID
    builder = OutlineBuilder(db)
    outline_id = builder.seed_default()
    tree = builder.materialize(tender_id, builder.get(outline_id))
    sections = OutlineBuilder.flatten(tree)
    for sec in sections:
        db.insert("generation_sections",
                  Database.planning_to_row(sec, tender_id=tender_id))
    mapper = RequirementSectionMapper(db)
    coverage = mapper.map_all(tender_id)
    return {"db": db, "tender_id": tender_id, "sections": sections,
            "coverage": coverage, "builder": builder, "mapper": mapper}
