[CmdletBinding()]
param(
    [switch]$Restart,
    [switch]$SkipInfrastructure,
    [switch]$SkipMigrations,
    [switch]$SkipWorker,
    [ValidateRange(1, 65535)][int]$ApiPort = 8000
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'lib\runtime.ps1')

function Start-LocalProcess {
    param(
        [Parameter(Mandatory)][ValidateSet('api', 'worker', 'document-worker', 'agent-worker', 'general-worker')][string]$Role,
        [Parameter(Mandatory)][string]$FilePath,
        [Parameter(Mandatory)][string[]]$ArgumentList,
        [Parameter(Mandatory)][string]$WorkingDirectory,
        [Parameter(Mandatory)][string]$IdentityRegex,
        [ValidateRange(0, 65535)][int]$Port = 0
    )

    if (Test-ManagedProcess -ProjectRoot $projectRoot -Role $Role) {
        $record = Get-ManagedProcessRecord -ProjectRoot $projectRoot -Role $Role
        Write-Output "$Role is already running (PID $($record.pid))."
        return
    }

    $logsDirectory = Get-LogsDirectory -ProjectRoot $projectRoot
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    $stdout = Join-Path $logsDirectory "$Role-$stamp.out.log"
    $stderr = Join-Path $logsDirectory "$Role-$stamp.err.log"
    $process = Start-Process -FilePath $FilePath -ArgumentList $ArgumentList -WorkingDirectory $WorkingDirectory `
        -RedirectStandardOutput $stdout -RedirectStandardError $stderr -WindowStyle Hidden -PassThru
    Write-ManagedProcessRecord -ProjectRoot $projectRoot -Role $Role -ProcessId $process.Id -IdentityRegex $IdentityRegex -Port $Port
    Write-Output "Started $Role (PID $($process.Id)). Logs: $logsDirectory"
}

function Test-ManagedProcessOnPort {
    param(
        [Parameter(Mandatory)][ValidateSet('api')][string]$Role,
        [Parameter(Mandatory)][ValidateRange(1, 65535)][int]$Port
    )

    if (!(Test-ManagedProcess -ProjectRoot $projectRoot -Role $Role)) {
        return $false
    }
    $record = Get-ManagedProcessRecord -ProjectRoot $projectRoot -Role $Role
    $recordedPort = if ($null -eq $record.PSObject.Properties['port']) { 0 } else { [int]$record.port }
    if ($recordedPort -ne $Port) {
        throw "Managed $Role is already running on port $recordedPort. Stop it before requesting port $Port."
    }
    return $true
}

$projectRoot = Get-ProjectRoot
$backendDirectory = Join-Path $projectRoot 'backend'
$environmentValues = Get-EnvironmentFileValues -ProjectRoot $projectRoot

$requiredValues = @(
    'DATABASE_URL', 'REDIS_URL', 'JWT_SECRET_KEY',
    'MINIO_ENDPOINT', 'MINIO_ACCESS_KEY', 'MINIO_SECRET_KEY',
 'MILVUS_URI',
    'CHAT_BASE_URL', 'CHAT_API_KEY',
    'RERANKER_BASE_URL', 'EMBEDDING_BASE_URL',
    'MINERU_BASE_URL', 'MINERU_API_KEY'
)
Assert-EnvironmentValues -Values $environmentValues -Names $requiredValues

$uv = Assert-CommandAvailable -Name 'uv'
$python = Join-Path $backendDirectory '.venv\Scripts\python.exe'
if (!(Test-Path -LiteralPath $python)) {
    throw 'Backend dependencies are missing. Run .\deploy\setup-local.ps1 before starting.'
}

if ($Restart) {
    & (Join-Path $PSScriptRoot 'stop-local.ps1')
}

if (!$SkipInfrastructure) {
    Assert-EnvironmentValues -Values $environmentValues -Names @('MILVUS_MINIO_ADDRESS')
    $docker = Assert-CommandAvailable -Name 'docker'
    Invoke-CheckedCommand -FilePath $docker -ArgumentList @(
 'compose', '--profile', 'vector', 'up', '-d', 'etcd', 'milvus'
    ) -WorkingDirectory $projectRoot
    Wait-ForTcpPort -HostName '127.0.0.1' -Port 19530 -TimeoutSeconds 120
}

if (!$SkipMigrations) {
    Invoke-CheckedCommand -FilePath $uv -ArgumentList @('run', 'alembic', 'upgrade', 'head') -WorkingDirectory $backendDirectory
}

if (!(Test-ManagedProcessOnPort -Role 'api' -Port $ApiPort)) {
    if (Test-LocalPortInUse -Port $ApiPort) {
        throw "API port $ApiPort is occupied by a process not managed by these scripts. Resolve it manually; no process was stopped."
    }
    Start-LocalProcess -Role 'api' -FilePath $python -ArgumentList @(
        '-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', [string]$ApiPort
    ) -WorkingDirectory $backendDirectory -IdentityRegex 'uvicorn.*app\.main:app' -Port $ApiPort
}
Wait-ForHttpStatus -Uri "http://127.0.0.1:$ApiPort/health" -ExpectedStatus 200 -TimeoutSeconds 60
Wait-ForHttpStatus -Uri "http://127.0.0.1:$ApiPort/health/ready" -ExpectedStatus 200 -TimeoutSeconds 60

$aiHealthUri = "http://127.0.0.1:$ApiPort/internal/health/ai"
$aiHealthStatus = Get-HttpStatusCode -Uri $aiHealthUri
if ($aiHealthStatus -eq 503) {
    Write-Warning 'AI readiness is degraded. API will start and Worker will start unless -SkipWorker is set, but AI tasks remain unavailable until the fixed AI services and vector dimension checks recover.'
}
elseif ($aiHealthStatus -ne 200) {
    throw "AI health endpoint returned HTTP $aiHealthStatus. Expected 200 or 503 from $aiHealthUri."
}

if (!$SkipWorker) {
    Start-LocalProcess -Role 'worker' -FilePath $python -ArgumentList @(
        'start_worker.py'
    ) -WorkingDirectory $backendDirectory -IdentityRegex 'python.*start_worker\.py'

    $deadline = (Get-Date).AddSeconds(60)
    $workerReady = $false
    do {
        if (Test-ManagedProcess -ProjectRoot $projectRoot -Role 'worker') {
            $record = Get-ManagedProcessRecord -ProjectRoot $projectRoot -Role 'worker'
            $proc = Get-Process -Id $record.pid -ErrorAction SilentlyContinue
            if ($null -ne $proc -and !$proc.HasExited) {
                $workerReady = $true
                break
            }
        }
        Start-Sleep -Seconds 2
    } while ((Get-Date) -lt $deadline)
    if (!$workerReady) {
        throw 'Managed ARQ Worker did not stay running. Check deploy/logs/worker-*.err.log.'
    }
}

Write-Output ''
Write-Output "API:      http://127.0.0.1:$ApiPort"
Write-Output 'Backend deployment started. API and runtime dependency health checks passed.'
