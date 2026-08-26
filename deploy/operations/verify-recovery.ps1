[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [ValidateNotNullOrEmpty()]
    [string]$BackupPath
)

$ErrorActionPreference = 'Stop'
$backupPathResolved = [IO.Path]::GetFullPath($BackupPath)
$dumpPath = Join-Path $backupPathResolved 'postgres.dump'
$manifestPath = Join-Path $backupPathResolved 'manifest.json'
$minioPath = Join-Path $backupPathResolved 'minio'

if (!(Test-Path -LiteralPath $dumpPath) -or !(Test-Path -LiteralPath $manifestPath) -or !(Test-Path -LiteralPath $minioPath)) {
    throw 'Backup is incomplete: postgres.dump, manifest.json, and minio/ are required.'
}

$manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
$actualHash = (Get-FileHash -LiteralPath $dumpPath -Algorithm SHA256).Hash
if ($actualHash -ne $manifest.postgres_dump) {
    throw 'PostgreSQL dump hash does not match the backup manifest.'
}
if (@(Get-ChildItem -LiteralPath $minioPath -Recurse -File).Count -lt [int]$manifest.minio_object_count) {
    throw 'MinIO backup has fewer objects than the backup manifest.'
}

docker run --rm -v "${backupPathResolved}:/backup" postgres:18-alpine pg_restore --list /backup/postgres.dump | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw 'PostgreSQL dump cannot be read by pg_restore.'
}

Write-Output 'Backup artifacts and manifest are internally consistent.'
Write-Output 'Restore order: PostgreSQL, MinIO, Milvus rebuild from search_chunks, Redis flush/rebuild, API and Worker readiness checks.'
