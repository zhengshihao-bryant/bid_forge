# -*- coding: utf-8 -*-
"""
app/config.py —— 路径与配置

- 仓库根 .env 由 python-dotenv 加载（真实 Key 只在本机，gitignored）
- 所有路径用 pathlib；Windows 中文路径安全
- 控制台编码运行时兜底：PYTHONUTF8 必须于解释器启动前设置才生效，
  这里对 stdout/stderr 做 reconfigure，避免 GBK 控制台打印中文崩溃
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

# ── 控制台编码兜底（Windows GBK 控制台打印中文会崩）──
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# ── 路径 ──
BIDGEN_DIR = Path(__file__).resolve().parent.parent          # backend/
REPO_ROOT = BIDGEN_DIR.parent                                # bid-generation-platform/
DATA_DIR = BIDGEN_DIR / "data"
RAW_DIR = DATA_DIR / "raw"          # 上传原文（uuid 落盘，gitignored）
PARSED_DIR = DATA_DIR / "parsed"    # 解析产物 JSON（gitignored）
SAMPLES_DIR = DATA_DIR / "samples"  # 样例数据（入库）
DB_PATH = DATA_DIR / "bid.db"       # SQLite（gitignored）

for _d in (DATA_DIR, RAW_DIR, PARSED_DIR, SAMPLES_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# ── env 配置 ──
load_dotenv(REPO_ROOT / ".env")

LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.deepseek.com")
LLM_MODEL = os.getenv("LLM_MODEL", "deepseek-chat")

# 需求提取：单窗口最大字符数（对齐 deepseek-chat 4096 输出上限）
EXTRACT_WINDOW_CHARS = int(os.getenv("EXTRACT_WINDOW_CHARS", "4000"))

BIDGEN_PORT = int(os.getenv("BIDGEN_PORT", "8001"))

# 解析器版本（写入 documents.parser_version，M5 重解析可追溯）
PARSER_VERSION = "1.0.0"
