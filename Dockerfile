# ============================================================================
# BidForge 多阶段构建
#   Stage 1: 前端构建（Vue3 + Vite → dist）
#   Stage 2: 后端运行时（Python + FastAPI + 前端产物）
# ============================================================================

# ---------- Stage 1: 前端构建 ----------
FROM node:20-alpine AS frontend-builder
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ---------- Stage 2: 后端运行时 ----------
FROM python:3.11-slim AS backend

ENV PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1 \
    PYTHONIOENCODING=utf-8 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# 系统依赖：中文字体（docx 导出）与常用工具
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       fonts-noto-cjk \
       curl \
    && rm -rf /var/lib/apt/lists/*

# 后端依赖（轻量运行集：torch/pymilvus 延迟导入，fake 嵌入 + SQLite 降级模式无需）
COPY requirements-docker.txt requirements.txt ./
RUN pip install --no-cache-dir -r requirements-docker.txt

# 项目文件（backend 作为 /app/backend 挂载点）
COPY backend/ ./backend/
# 前端产物（由 nginx 服务）
COPY --from=frontend-builder /build/frontend/dist /app/frontend-dist
# 样例数据备份（data 卷首次初始化时由 entrypoint 补齐）
COPY backend/data/samples /app/samples-backup
# 入口脚本
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

WORKDIR /app/backend

# 默认环境（compose 中可覆盖；离线可演示：FakeEmbedding + SQLite 降级）
ENV MILVUS_ENABLED=false \
    EMBEDDING_BACKEND=fake \
    AUTH_ENABLED=false \
    BIDGEN_PORT=8001

EXPOSE 8001

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s \
    CMD curl -fsS http://localhost:8001/docs > /dev/null || exit 1

ENTRYPOINT ["docker-entrypoint.sh"]
