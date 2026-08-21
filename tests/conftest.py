# -*- coding: utf-8 -*-
"""
tests/conftest.py —— 测试夹具

- sys.path 挂载 backend（测试从仓库根跑：pytest tests/）
- sample_files：样例包缺失时用 --no-llm 补生成 pdf/xlsx/scan
  （docx 依赖 LLM/缓存生成耗时，缺失时由各测试 skip 而非自动生成，
   避免与后台 LLM 生成进程竞争输出文件）
- tmp_env：把 DB/RAW/PARSED 指到 pytest 临时目录，测试不污染真实数据
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND = REPO_ROOT / "backend"
sys.path.insert(0, str(BACKEND))

from app import config  # noqa: E402

SAMPLE_DIR = config.SAMPLES_DIR / "智慧园区项目"


@pytest.fixture(scope="session")
def sample_dir(tmp_path_factory) -> Path:
    """确保 pdf/xlsx/scan 三个规则生成文件存在（缺失时离线补生成）。"""
    missing = []
    for name in ("02_技术规格书.pdf", "03_设备清单.xlsx", "04_补充通知(扫描件).pdf"):
        if not (SAMPLE_DIR / name).exists():
            missing.append(name)
    if missing:
        # pdf / xlsx / scan 生成不走 LLM，几秒内完成，无缓存竞争
        env = {**__import__("os").environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
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


@pytest.fixture()
def tmp_env(monkeypatch, tmp_path):
    """隔离 DB / RAW / PARSED 到临时目录。"""
    data = tmp_path / "data"
    (data / "raw").mkdir(parents=True)
    (data / "parsed").mkdir(parents=True)
    monkeypatch.setattr(config, "DB_PATH", data / "bid.db")
    monkeypatch.setattr(config, "RAW_DIR", data / "raw")
    monkeypatch.setattr(config, "PARSED_DIR", data / "parsed")
    return data


# ═══════════════════════════════════════════════════════════════════════
# FakeLLM（离线提取测试）
# ═══════════════════════════════════════════════════════════════════════
class FakeLLM:
    """脚本化 LLM：按窗口顺序弹出预设响应；耗尽后返回空列表。"""

    model = "fake"

    def __init__(self, responses=None):
        self.responses = list(responses or [])
        self.calls = 0

    def chat_json(self, system, user, temperature=None, max_tokens=None):
        self.calls += 1
        if self.responses:
            resp = self.responses.pop(0)
            if isinstance(resp, dict) and "data" in resp:
                return resp
            return {"data": {"requirements": resp}, "finish_reason": "stop",
                    "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}}
        return {"data": {"requirements": []}, "finish_reason": "stop",
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
