[CmdletBinding()]
param(
    [switch]$StopInfrastructure
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'lib\runtime.ps1')

$projectRoot = Get-ProjectRoot

foreach ($role in @('worker', 'general-worker', 'agent-worker', 'document-worker', 'api')) {
    Stop-ManagedProcess -ProjectRoot $projectRoot -Role $role
}

if ($StopInfrastructure) {
    $docker = Assert-CommandAvailable -Name 'docker'
    Invoke-CheckedCommand -FilePath $docker -ArgumentList @(
 'compose', '--profile', 'vector', 'stop', 'milvus', 'etcd'
    ) -WorkingDirectory $projectRoot
    Write-Output 'Stopped repository-managed Docker services; persistent Docker volumes were kept.'
}
