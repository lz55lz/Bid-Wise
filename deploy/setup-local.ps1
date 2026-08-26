[CmdletBinding()]
param(
    [switch]$SkipDependencyInstall
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'lib\runtime.ps1')

$projectRoot = Get-ProjectRoot
$uv = Assert-CommandAvailable -Name 'uv'

$environmentFile = Join-Path $projectRoot '.env'
if (!(Test-Path -LiteralPath $environmentFile)) {
    Copy-Item -LiteralPath (Join-Path $projectRoot '.env.example') -Destination $environmentFile
    Write-Warning 'Created .env from .env.example. Fill every required value before starting the application.'
}

if (!$SkipDependencyInstall) {
    Invoke-CheckedCommand -FilePath $uv -ArgumentList @('sync', '--all-groups') -WorkingDirectory (Join-Path $projectRoot 'backend')
}

Write-Output 'Backend setup completed. Configure .env, then run .\deploy\start-local.ps1.'
