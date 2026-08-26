# 备份与恢复操作

所有连接信息只从部署环境读取，脚本不会写出或打印密码、令牌、对象存储密钥。

每日备份（在受限运维主机或计划任务中执行）：

```powershell
$env:DATABASE_URL = '...'
$env:MINIO_ENDPOINT = '...'
$env:MINIO_ACCESS_KEY = '...'
$env:MINIO_SECRET_KEY = '...'
./backup.ps1 -BackupRoot 'D:\private-backups\ai-bid-advisor'
```

该脚本生成 PostgreSQL 自定义格式逻辑备份、私有 MinIO 对象镜像与不含密钥的 SHA-256 清单。恢复前先运行：

```powershell
./verify-recovery.ps1 -BackupPath 'D:\private-backups\ai-bid-advisor\YYYYMMDD-HHMMSS'
```

恢复时使用隔离环境，并按以下顺序执行：PostgreSQL、MinIO、由 `search_chunks` 重建 Milvus、清空 Redis 后重建队列状态、启动 API/Worker，最后检查 `/health/ready`，并抽样验证 Evidence 原件和报告 DOCX/PDF。
