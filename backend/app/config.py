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

# M2 知识库目录（与招标文件目录隔离，uuid 空间不撞车）
KB_RAW_DIR = DATA_DIR / "kb_raw"        # 企业资料原文
KB_PARSED_DIR = DATA_DIR / "kb_parsed"  # 企业资料解析产物 JSON

for _d in (DATA_DIR, RAW_DIR, PARSED_DIR, SAMPLES_DIR, KB_RAW_DIR, KB_PARSED_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# ── env 配置 ──
load_dotenv(REPO_ROOT / ".env")

LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.deepseek.com")
LLM_MODEL = os.getenv("LLM_MODEL", "deepseek-chat")

# 需求提取：单窗口最大字符数（对齐 deepseek-chat 4096 输出上限）
EXTRACT_WINDOW_CHARS = int(os.getenv("EXTRACT_WINDOW_CHARS", "4000"))

BIDGEN_PORT = int(os.getenv("BIDGEN_PORT", "8001"))

# ── M2：企业知识库（BGE 嵌入 + Milvus，可降级）──
# Milvus：MILVUS_ENABLED=false 时检索/写入走 SQLite 暴力余弦降级路径
MILVUS_URI = os.getenv("MILVUS_URI", "http://localhost:19530")
MILVUS_ENABLED = os.getenv("MILVUS_ENABLED", "true").lower() in ("1", "true", "yes")
MILVUS_COLLECTION = "bid_chunks"       # 独立集合名（避免与同实例其他服务冲突）
# 嵌入：bge = 本地 BGE 模型；fake = 确定性伪嵌入（离线测试用）
# 踩坑：全局 HF_ENDPOINT=hf-mirror 网络不通，BGE 加载必须 local_files_only=True
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "BAAI/bge-large-zh")
EMBEDDING_DIM = int(os.getenv("EMBEDDING_DIM", "1024"))
EMBEDDING_BACKEND = os.getenv("EMBEDDING_BACKEND", "bge")
# 知识库切块：单块最大字符数
KB_CHUNK_CHARS = int(os.getenv("KB_CHUNK_CHARS", "600"))

# ── M3：需求-能力匹配 ──
# LLM 是否参与规范化合并与判定（false = 全离线确定性路径：规则+启发式；
# 无 LLM_API_KEY 时 create_llm_client 自动 Mock，效果等同 false）
M3_LLM_ENABLED = os.getenv("M3_LLM_ENABLED", "true").lower() in ("1", "true", "yes")
# 每条规范需求的语义检索候选数 / 送入判定的证据条数
M3_TOP_K = int(os.getenv("M3_TOP_K", "8"))
M3_JUDGE_EVIDENCE = int(os.getenv("M3_JUDGE_EVIDENCE", "5"))
# RAG 命中进入证据池的最低相关性分（Rerank 0-1；低于此分视为无关命中丢弃，
# 避免"无关 chunk 原文自证 VALID"污染证据池导致无证据需求被误判满足）
M3_RAG_MIN_SCORE = float(os.getenv("M3_RAG_MIN_SCORE", "0.3"))
# 关键词重叠下限（bigram key_overlap 0-1）：类别亲和分可能把"同类别但内容
# 无关"的 chunk 抬过总分下限（如涉密资质需求命中 ISO 证书 chunk），
# 关键词重叠必须达标才算"内容相关"
M3_RAG_MIN_KW = float(os.getenv("M3_RAG_MIN_KW", "0.2"))

# 解析器版本（写入 documents.parser_version，M5 重解析可追溯）
PARSER_VERSION = "1.0.0"

# ── M7：企业级能力（认证 / RBAC / 审计）──
# JWT 签名密钥（HS256；生产必须覆盖，默认值仅本机开发）
JWT_SECRET = os.getenv("JWT_SECRET", "bidforge-dev-secret-change-me")
JWT_EXPIRE_HOURS = int(os.getenv("JWT_EXPIRE_HOURS", "12"))
# 初始管理员（users 表为空时自动种子；生产请覆盖密码）
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123")
# 鉴权开关：false 时无 token 请求以系统用户放行（旧版验收脚本 / 本机调试用；
# 默认 true 全面鉴权）
AUTH_ENABLED = os.getenv("AUTH_ENABLED", "true").lower() in ("1", "true", "yes")
