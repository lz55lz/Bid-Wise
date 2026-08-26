[CmdletBinding()]
param(
    [ValidateRange(1, 65535)][int]$ApiPort = 8000
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'lib\runtime.ps1')

$projectRoot = Get-ProjectRoot
$python = Join-Path $projectRoot 'backend\.venv\Scripts\python.exe'
if (!(Test-Path -LiteralPath $python)) {
    throw 'Backend dependencies are missing. Run .\deploy\setup-local.ps1 before verification.'
}

foreach ($check in @(
        @{ Name = 'API health'; Uri = "http://127.0.0.1:$ApiPort/health" },
        @{ Name = 'Runtime readiness'; Uri = "http://127.0.0.1:$ApiPort/health/ready" },
        @{ Name = 'AI readiness'; Uri = "http://127.0.0.1:$ApiPort/internal/health/ai" }
    )) {
    $status = Get-HttpStatusCode -Uri $check.Uri
    if ($status -ne 200) {
        throw "$($check.Name) returned HTTP $status; expected 200."
    }
    Write-Output "$($check.Name): OK"
}

if (!(Test-ManagedProcess -ProjectRoot $projectRoot -Role 'worker')) {
    throw 'Managed ARQ Worker is not running. Run .\deploy\start-local.ps1 first.'
}
$record = Get-ManagedProcessRecord -ProjectRoot $projectRoot -Role 'worker'
$proc = Get-Process -Id $record.pid -ErrorAction SilentlyContinue
if ($null -eq $proc -or $proc.HasExited) {
    throw 'Managed ARQ Worker process is not alive. Check deploy/logs/worker-*.err.log.'
}
Write-Output 'ARQ Worker: OK'
Write-Output 'Deployment verification passed.'
