# ============================================================================
# BidForge 一键演示脚本（Windows PowerShell）
# 用法: .\scripts\demo.ps1
# 前置: 已安装 Docker Desktop（推荐）或 Python 3.10+ / Node
# ============================================================================
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  BidForge 企业标书生成平台 · 一键演示" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan

# 1. Docker 方式（推荐）
if (Get-Command docker -ErrorAction SilentlyContinue) {
    Write-Host "[1/3] 检查 docker compose..." -ForegroundColor Yellow
    docker compose version | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "错误: 需要 docker compose v2" -ForegroundColor Red
        exit 1
    }

    Push-Location $Root
    try {
        Write-Host "[2/3] 构建并启动容器（首次构建需数分钟）..." -ForegroundColor Yellow
        docker compose up --build -d

        Write-Host "[3/3] 等待服务就绪..." -ForegroundColor Yellow
        $ready = $false
        for ($i = 1; $i -le 30; $i++) {
            try {
                Invoke-WebRequest -Uri "http://localhost:8001/docs" -UseBasicParsing -TimeoutSec 2 | Out-Null
                $ready = $true
                break
            } catch { Start-Sleep -Seconds 1 }
        }
        if (-not $ready) {
            Write-Host "错误: 后端 30 秒内未就绪，查看日志: docker compose logs backend" -ForegroundColor Red
            exit 1
        }

        Write-Host ""
        Write-Host "  ✅ 演示环境已启动" -ForegroundColor Green
        Write-Host "  前端工作台:   http://localhost:8080"
        Write-Host "  API 文档:     http://localhost:8001/docs"
        Write-Host "  停止:         docker compose down"
        Write-Host "  重置数据:     docker compose down -v && docker compose up --build -d"
        Write-Host ""
        Write-Host "演示步骤（10 分钟快速演示版）："
        Write-Host "  1. 前端项目页新建项目，上传 backend/data/samples/智慧园区项目/01_招标文件正文.docx"
        Write-Host "  2. 知识库页上传 backend/data/samples/企业资料包/ 下 8 份资料"
        Write-Host "  3. 匹配页运行匹配 → 四状态 + 证据链（REQ-C-0002 = FULL）"
        Write-Host "  4. 生成页生成 26 章节（SSE 实时进度）→ CH-05-2 技术方案数字可溯源"
        Write-Host "  5. 质检页把 2000 改成 5000 → 重查 → NUMBER_MISMATCH 被抓"
    } finally {
        Pop-Location
    }
} else {
    # 2. 本机方式
    Write-Host "[1/3] 检查 Python / Node..." -ForegroundColor Yellow
    if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
        Write-Host "错误: 未安装 Python"; exit 1
    }
    if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
        Write-Host "错误: 未安装 Node"; exit 1
    }
    Write-Host "[2/3] 安装后端依赖..." -ForegroundColor Yellow
    Push-Location $Root
    try {
        python -m pip install -r requirements.txt
        Write-Host "[3/3] 启动后端（前台）..." -ForegroundColor Yellow
        Write-Host "  另开终端: cd frontend; npm install; npm run dev   (前端 http://localhost:5173)"
        Push-Location backend
        $env:AUTH_ENABLED = "false"
        python -m uvicorn app.api.main:app --host 0.0.0.0 --port 8001
    } finally { Pop-Location }
}
