[CmdletBinding()]
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'lib\runtime.ps1')

$projectRoot = Get-ProjectRoot

foreach ($role in @('worker', 'general-worker', 'agent-worker', 'document-worker', 'api')) {
    Stop-ManagedProcess -ProjectRoot $projectRoot -Role $role
}
