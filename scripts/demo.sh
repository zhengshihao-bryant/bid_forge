#!/usr/bin/env bash
# ============================================================================
# BidForge 一键演示脚本（Linux / macOS）
# 用法: ./scripts/demo.sh [--docker|--local]
#   --docker  容器方式（推荐）：docker compose up --build，无需本机 Python/Node
#   --local   本机方式：需已安装 Python 3.10+ 依赖与 Node（见 README 快速开始）
# 默认: --docker
# ============================================================================
set -euo pipefail
cd "$(dirname "$0")/.."

MODE="${1:---docker}"

echo "============================================================"
echo "  BidForge 企业标书生成平台 · 一键演示"
echo "============================================================"

if [ "$MODE" = "--docker" ]; then
    echo "[1/3] 检查 Docker..."
    command -v docker >/dev/null 2>&1 || { echo "错误: 未安装 Docker"; exit 1; }
    command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1 \
        || { echo "错误: 需要 docker compose v2"; exit 1; }

    echo "[2/3] 构建并启动容器（首次构建需数分钟）..."
    docker compose up --build -d

    echo "[3/3] 等待服务就绪..."
    for i in $(seq 1 30); do
        if curl -fsS http://localhost:8001/docs >/dev/null 2>&1; then
            echo "✔ 后端就绪"
            break
        fi
        [ "$i" = "30" ] && { echo "错误: 后端 30 秒内未就绪"; docker compose logs backend; exit 1; }
        sleep 1
    done

    echo ""
    echo "============================================================"
    echo "  ✅ 演示环境已启动"
    echo "  前端工作台:   http://localhost:8080"
    echo "  API 文档:     http://localhost:8001/docs"
    echo "  停止:         docker compose down"
    echo "  重置数据:     docker compose down -v && docker compose up --build -d"
    echo "============================================================"
    echo ""
    echo "演示步骤（面试 10 分钟版）："
    echo "  1. 打开前端 → 项目页新建项目，上传 backend/data/samples/智慧园区项目/01_招标文件正文.docx"
    echo "  2. 知识库页上传 backend/data/samples/企业资料包/ 下 8 份资料"
    echo "  3. 匹配页运行匹配 → 查看四状态 + 证据链（REQ-C-0002 = FULL）"
    echo "  4. 生成页生成 26 章节（SSE 实时进度）→ 查看 CH-05-2 技术方案数字可溯源"
    echo "  5. 质检页把 2000 改成 5000 → 重查 → NUMBER_MISMATCH 被抓"
    echo "  6. （演示完）docker compose down"
else
    echo "[1/3] 检查 Python / Node..."
    command -v python >/dev/null 2>&1 || { echo "错误: 未安装 Python"; exit 1; }
    command -v npm >/dev/null 2>&1 || { echo "错误: 未安装 Node"; exit 1; }

    echo "[2/3] 安装依赖（后端）..."
    pip install -r requirements.txt

    echo "[3/3] 启动后端（前台）..."
    echo "  另开终端: cd frontend && npm install && npm run dev  (前端 http://localhost:5173)"
    cd backend && AUTH_ENABLED=false python -m uvicorn app.api.main:app --host 0.0.0.0 --port 8001
fi
