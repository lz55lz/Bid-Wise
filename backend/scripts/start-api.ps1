# AI 投标参谋长 - API 启动脚本
# 用法: .\start-api.ps1

$ErrorActionPreference = "Stop"
$BackendDir = Split-Path -Parent $PSScriptRoot

Write-Host "启动 API Server..." -ForegroundColor Cyan
Write-Host ""

Push-Location $BackendDir

try {
    $env:PYTHONPATH = $BackendDir

    # 直接运行，让 Ctrl+C 停止
    & uv run python -m app.main

} finally {
    Pop-Location
}
