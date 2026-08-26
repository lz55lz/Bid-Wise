[CmdletBinding()]
param(
    [ValidateRange(1, 65535)][int]$ApiPort = 8000
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'lib\runtime.ps1')

$projectRoot = Get-ProjectRoot
foreach ($role in @('api', 'worker')) {
    if (Test-ManagedProcess -ProjectRoot $projectRoot -Role $role) {
        $record = Get-ManagedProcessRecord -ProjectRoot $projectRoot -Role $role
        Write-Output "$role : running (PID $($record.pid))"
    }
    else {
        Write-Output "$role : stopped or not started by these scripts"
    }
}

Write-Output "API /health: $(Get-HttpStatusCode -Uri "http://127.0.0.1:$ApiPort/health")"
Write-Output "API /health/ready: $(Get-HttpStatusCode -Uri "http://127.0.0.1:$ApiPort/health/ready")"
Write-Output "API /internal/health/ai: $(Get-HttpStatusCode -Uri "http://127.0.0.1:$ApiPort/internal/health/ai")"

$docker = Get-Command docker -ErrorAction SilentlyContinue
if ($null -ne $docker) {
    Push-Location -LiteralPath $projectRoot
    try {
        & $docker.Source compose --profile vector --profile security ps
    }
    finally {
        Pop-Location
    }
}
