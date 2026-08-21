#!/bin/sh
# ============================================================================
# BidForge 容器入口：确保样例数据可用后启动 uvicorn
#
# 场景：backend/data 整个目录被挂载为持久卷（演示数据/上传产物持久化），
#       首次启动时卷为空 → 从镜像内备份 /app/samples-backup 补齐样例包。
# ============================================================================
set -e

SAMPLES="/app/backend/data/samples"
BACKUP="/app/samples-backup"

if [ -d "$BACKUP" ] && [ -z "$(ls -A "$SAMPLES" 2>/dev/null)" ]; then
    echo "[entrypoint] 初始化样例数据：$BACKUP -> $SAMPLES"
    mkdir -p "$SAMPLES"
    cp -r "$BACKUP/." "$SAMPLES/"
fi

echo "[entrypoint] 启动 uvicorn: 0.0.0.0:${BIDGEN_PORT:-8001}"
exec python -m uvicorn app.api.main:app \
    --host 0.0.0.0 \
    --port "${BIDGEN_PORT:-8001}"
