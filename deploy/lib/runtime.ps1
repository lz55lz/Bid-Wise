Set-StrictMode -Version Latest

function Get-ProjectRoot {
    return (Get-Item -LiteralPath (Join-Path $PSScriptRoot '..\..')).FullName
}

function Get-RuntimeDirectory {
    param([Parameter(Mandatory)][string]$ProjectRoot)

    $directory = Join-Path $ProjectRoot 'deploy\.runtime'
    New-Item -ItemType Directory -Force -Path $directory | Out-Null
    return $directory
}

function Get-LogsDirectory {
    param([Parameter(Mandatory)][string]$ProjectRoot)

    $directory = Join-Path $ProjectRoot 'deploy\logs'
    New-Item -ItemType Directory -Force -Path $directory | Out-Null
    return $directory
}

function Assert-CommandAvailable {
    param([Parameter(Mandatory)][string]$Name)

    $command = Get-Command $Name -ErrorAction SilentlyContinue
    if ($null -eq $command) {
        throw "Required command '$Name' was not found in PATH."
    }
    return $command.Source
}

function Invoke-CheckedCommand {
    param(
        [Parameter(Mandatory)][string]$FilePath,
        [Parameter(Mandatory)][string[]]$ArgumentList,
        [Parameter(Mandatory)][string]$WorkingDirectory
    )

    Push-Location -LiteralPath $WorkingDirectory
    try {
        & $FilePath @ArgumentList
        if ($LASTEXITCODE -ne 0) {
            throw "Command failed with exit code ${LASTEXITCODE}: $FilePath $($ArgumentList -join ' ')"
        }
    }
    finally {
        Pop-Location
    }
}

function Get-EnvironmentFileValues {
    param([Parameter(Mandatory)][string]$ProjectRoot)

    $environmentFile = Join-Path $ProjectRoot '.env'
    if (!(Test-Path -LiteralPath $environmentFile)) {
        throw "Missing $environmentFile. Copy .env.example to .env and fill deployment values."
    }

    $values = @{}
    foreach ($line in Get-Content -LiteralPath $environmentFile -Encoding utf8) {
        $trimmed = $line.Trim()
        if (!$trimmed -or $trimmed.StartsWith('#')) {
            continue
        }
        if ($trimmed -match '^([A-Za-z_][A-Za-z0-9_]*)=(.*)$') {
            $value = $Matches[2].Trim()
            if ($value.Length -ge 2 -and (
                ($value.StartsWith('"') -and $value.EndsWith('"')) -or
                ($value.StartsWith("'") -and $value.EndsWith("'"))
            )) {
                $value = $value.Substring(1, $value.Length - 2)
            }
            $values[$Matches[1]] = $value
        }
    }
    return $values
}

function Assert-EnvironmentValues {
    param(
        [Parameter(Mandatory)][hashtable]$Values,
        [Parameter(Mandatory)][string[]]$Names
    )

    $missing = @($Names | Where-Object {
            !$Values.ContainsKey($_) -or [string]::IsNullOrWhiteSpace([string]$Values[$_])
        })
    if ($missing.Count -gt 0) {
        throw "Required deployment values are empty: $($missing -join ', '). Values were not printed."
    }
}

function Get-ProcessRecordPath {
    param(
        [Parameter(Mandatory)][string]$ProjectRoot,
        [Parameter(Mandatory)][ValidateSet('api', 'worker', 'document-worker', 'agent-worker', 'general-worker')][string]$Role
    )

    return (Join-Path (Get-RuntimeDirectory -ProjectRoot $ProjectRoot) "$Role.json")
}

function Get-ManagedProcessRecord {
    param(
        [Parameter(Mandatory)][string]$ProjectRoot,
        [Parameter(Mandatory)][ValidateSet('api', 'worker', 'document-worker', 'agent-worker', 'general-worker')][string]$Role
    )

    $path = Get-ProcessRecordPath -ProjectRoot $ProjectRoot -Role $Role
    if (!(Test-Path -LiteralPath $path)) {
        return $null
    }
    try {
        return (Get-Content -LiteralPath $path -Raw -Encoding utf8 | ConvertFrom-Json)
    }
    catch {
        throw "Managed process record is invalid: $path"
    }
}

function Test-ManagedProcess {
    param(
        [Parameter(Mandatory)][string]$ProjectRoot,
        [Parameter(Mandatory)][ValidateSet('api', 'worker', 'document-worker', 'agent-worker', 'general-worker')][string]$Role
    )

    $record = Get-ManagedProcessRecord -ProjectRoot $ProjectRoot -Role $Role
    if ($null -eq $record) {
        return $false
    }
    $process = Get-Process -Id ([int]$record.pid) -ErrorAction SilentlyContinue
    if ($null -eq $process) {
        Remove-Item -LiteralPath (Get-ProcessRecordPath -ProjectRoot $ProjectRoot -Role $Role) -Force
        return $false
    }

    $processInfo = Get-CimInstance Win32_Process -Filter "ProcessId=$($record.pid)" -ErrorAction SilentlyContinue
    if ($null -eq $processInfo -or [string]::IsNullOrWhiteSpace($processInfo.CommandLine) -or
        $processInfo.CommandLine -notmatch $record.identity_regex) {
        throw "Managed $Role PID $($record.pid) no longer matches its recorded command. Refusing to act on it."
    }
    return $true
}

function Write-ManagedProcessRecord {
    param(
        [Parameter(Mandatory)][string]$ProjectRoot,
        [Parameter(Mandatory)][ValidateSet('api', 'worker', 'document-worker', 'agent-worker', 'general-worker')][string]$Role,
        [Parameter(Mandatory)][int]$ProcessId,
        [Parameter(Mandatory)][string]$IdentityRegex,
        [ValidateRange(0, 65535)][int]$Port = 0
    )

    $record = [ordered]@{
        pid = $ProcessId
        role = $Role
        started_at = (Get-Date).ToUniversalTime().ToString('o')
        identity_regex = $IdentityRegex
        port = $Port
    }
    $record | ConvertTo-Json | Set-Content -LiteralPath (Get-ProcessRecordPath -ProjectRoot $ProjectRoot -Role $Role) -Encoding utf8
}

function Stop-ManagedProcess {
    param(
        [Parameter(Mandatory)][string]$ProjectRoot,
        [Parameter(Mandatory)][ValidateSet('api', 'worker', 'document-worker', 'agent-worker', 'general-worker')][string]$Role
    )

    $record = Get-ManagedProcessRecord -ProjectRoot $ProjectRoot -Role $Role
    if ($null -eq $record) {
        Write-Output "$Role is not managed by these scripts."
        return
    }
    if (!(Test-ManagedProcess -ProjectRoot $ProjectRoot -Role $Role)) {
        Write-Output "$Role is already stopped."
        return
    }

    $taskKill = Join-Path $env:SystemRoot 'System32\taskkill.exe'
    & $taskKill /PID ([string]$record.pid) /T /F | Out-Null
    if ($LASTEXITCODE -ne 0 -and (Get-Process -Id ([int]$record.pid) -ErrorAction SilentlyContinue)) {
        throw "Could not stop managed $Role process $($record.pid)."
    }
    Remove-Item -LiteralPath (Get-ProcessRecordPath -ProjectRoot $ProjectRoot -Role $Role) -Force
    Write-Output "Stopped $Role."
}

function Test-LocalPortInUse {
    param([Parameter(Mandatory)][ValidateRange(1, 65535)][int]$Port)

    return $null -ne (Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue | Select-Object -First 1)
}

function Wait-ForTcpPort {
    param(
        [Parameter(Mandatory)][string]$HostName,
        [Parameter(Mandatory)][ValidateRange(1, 65535)][int]$Port,
        [Parameter(Mandatory)][ValidateRange(1, 300)][int]$TimeoutSeconds
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        if (Test-NetConnection -ComputerName $HostName -Port $Port -InformationLevel Quiet -WarningAction SilentlyContinue) {
            return
        }
        Start-Sleep -Seconds 1
    } while ((Get-Date) -lt $deadline)
    throw "Timed out waiting for ${HostName}:$Port."
}

function Get-HttpStatusCode {
    param([Parameter(Mandatory)][string]$Uri)

    try {
        return [int](Invoke-WebRequest -Uri $Uri -UseBasicParsing -TimeoutSec 5).StatusCode
    }
    catch {
        $responseProperty = $_.Exception.PSObject.Properties['Response']
        if ($null -ne $responseProperty -and $null -ne $responseProperty.Value) {
            return [int]$responseProperty.Value.StatusCode
        }
        return $null
    }
}

function Wait-ForHttpStatus {
    param(
        [Parameter(Mandatory)][string]$Uri,
        [Parameter(Mandatory)][int]$ExpectedStatus,
        [Parameter(Mandatory)][ValidateRange(1, 300)][int]$TimeoutSeconds
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        if ((Get-HttpStatusCode -Uri $Uri) -eq $ExpectedStatus) {
            return
        }
        Start-Sleep -Seconds 1
    } while ((Get-Date) -lt $deadline)
    throw "Timed out waiting for HTTP $ExpectedStatus from $Uri."
}
