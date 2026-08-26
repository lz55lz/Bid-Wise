[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [ValidateNotNullOrEmpty()]
    [string]$BackupRoot
)

$ErrorActionPreference = 'Stop'

foreach ($name in 'DATABASE_URL', 'MINIO_ENDPOINT', 'MINIO_ACCESS_KEY', 'MINIO_SECRET_KEY') {
    if ([string]::IsNullOrWhiteSpace([Environment]::GetEnvironmentVariable($name))) {
        throw "$name must be provided through the deployment environment."
    }
}

$backupRootPath = [IO.Path]::GetFullPath($BackupRoot)
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$backupPath = Join-Path $backupRootPath $stamp
New-Item -ItemType Directory -Force -Path $backupPath | Out-Null

$databaseDump = Join-Path $backupPath 'postgres.dump'
$pgDump = Get-Command pg_dump -ErrorAction SilentlyContinue
if ($null -ne $pgDump) {
    & $pgDump.Source --dbname=$env:DATABASE_URL --format=custom --file=$databaseDump --no-owner --no-privileges
} else {
    $databaseUri = [Uri]$env:DATABASE_URL
    $databaseHost = if ($databaseUri.Host -in @('localhost', '127.0.0.1', '::1')) { 'host.docker.internal' } else { $databaseUri.Host }
    $databasePort = if ($databaseUri.Port -gt 0) { $databaseUri.Port } else { 5432 }
    $userInfo = $databaseUri.UserInfo.Split(':', 2)
    if ($userInfo.Count -ne 2) {
        throw 'DATABASE_URL must include a username and password for the Docker pg_dump fallback.'
    }
    $databaseUser = [Uri]::UnescapeDataString($userInfo[0])
    $databasePassword = [Uri]::UnescapeDataString($userInfo[1])
    $databaseName = $databaseUri.AbsolutePath.Trim('/')
    docker run --rm `
        -e "PGPASSWORD=$databasePassword" `
        -v "${backupPath}:/backup" `
        postgres:18-alpine `
        pg_dump --host=$databaseHost --port=$databasePort --username=$databaseUser --format=custom --file=/backup/postgres.dump --no-owner --no-privileges $databaseName
}
if ($LASTEXITCODE -ne 0) {
    throw 'PostgreSQL backup failed.'
}

$minioUri = [Uri]$env:MINIO_ENDPOINT
$encodedAccessKey = [Uri]::EscapeDataString($env:MINIO_ACCESS_KEY)
$encodedSecretKey = [Uri]::EscapeDataString($env:MINIO_SECRET_KEY)
$mcHost = "{0}://{1}:{2}@{3}" -f $minioUri.Scheme, $encodedAccessKey, $encodedSecretKey, $minioUri.Authority
$minioTarget = Join-Path $backupPath 'minio'
New-Item -ItemType Directory -Force -Path $minioTarget | Out-Null
$minioBucket = if ([string]::IsNullOrWhiteSpace($env:MINIO_BUCKET)) { 'ai-bid-advisor' } else { $env:MINIO_BUCKET }

docker run --rm `
    -e "MC_HOST_source=$mcHost" `
    -v "${minioTarget}:/backup" `
    minio/mc `
    mirror --overwrite "source/$minioBucket" /backup
if ($LASTEXITCODE -ne 0) {
    throw 'MinIO mirror backup failed.'
}

$manifest = [ordered]@{
    created_at = (Get-Date).ToUniversalTime().ToString('o')
    postgres_dump = (Get-FileHash -LiteralPath $databaseDump -Algorithm SHA256).Hash
    minio_object_count = @(Get-ChildItem -LiteralPath $minioTarget -Recurse -File).Count
}
$manifest | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $backupPath 'manifest.json') -Encoding utf8
Write-Output "Backup completed: $backupPath"
