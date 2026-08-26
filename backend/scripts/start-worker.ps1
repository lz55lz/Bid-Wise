# AI 投标参谋长 - ARQ Worker 启动脚本
# 用法: .\start-worker.ps1

$ErrorActionPreference = 'Stop'
$backendDir = Split-Path -Parent $PSScriptRoot

Push-Location $backendDir
try {
    $env:PYTHONPATH = $backendDir
    # API 通过 ArqTaskPublisher 入队；WorkerSettings 注册全部消费函数。
    & .venv\Scripts\python.exe -m arq app.worker.WorkerSettings
} finally {
    Pop-Location
}
