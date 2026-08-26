# HA 部署与演练手册

`stack.yml` 是生产 HA 覆盖配置，目标是 Docker Swarm：API 三副本、Worker 两副本、前端和网关各两副本。它不部署 PostgreSQL、Redis、MinIO、Milvus、MinerU 或 ClamAV；这些必须由部署方提供高可用端点。当前 Windows Docker Desktop 本地 Compose 环境是单节点验收环境，不应宣称具备 HA。

## 部署前置条件

1. 至少三个可调度 Swarm 节点，镜像仓库提供带不可变 tag 或 digest 的 API 与前端镜像。
2. PostgreSQL 已启用复制/故障转移；Redis 使用高可用队列端点；MinIO、Milvus、MinerU 和 ClamAV 具备服务级健康检查、容量和恢复方案。
3. 创建 TLS Swarm Secret `ai_bid_advisor_tls_crt` 与 `ai_bid_advisor_tls_key`。数据库、对象存储、JWT、AI 和连接器密钥只在受控部署环境注入，绝不写入此文件、Stack 或命令历史。
4. 外部连接器实现 `POST /operations/lookup` 和/或 `POST /operations/export`；必须使用 `X-Integration-Run-ID` 做幂等去重。系统只会由用户显式发起调用，且不会自动重试有副作用的操作。

## 发布流程

1. 在受控终端设置 Stack 所要求的非密钥变量和密钥注入机制，先校验配置：

```powershell
docker compose -f deploy/ha/stack.yml config --quiet
```

2. 对目标数据库单独执行 Alembic 迁移，完成后检查 `alembic current` 与发布版本一致；迁移失败时不要发布新镜像。

3. 部署或滚动更新：

```powershell
docker stack deploy --with-registry-auth -c deploy/ha/stack.yml ai-bid-advisor
docker service ls
docker service ps ai-bid-advisor_api
docker service ps ai-bid-advisor_worker
```

4. 逐个确认新副本健康，再通过网关访问 `/health/ready`。健康检查会验证 PostgreSQL、Redis、MinIO、Milvus、MinerU 和 ClamAV；AI 端点未配置时 `ai_available: false` 是预期状态，不阻断非 AI 功能。

5. 如滚动发布监测期失败，Stack 会自动回滚。运维人员仍须检查服务事件、Worker 日志和审计日志，并记录故障处置结论。

## 队列与连接器故障处置

- 普通解析、索引和分析任务由 Celery 的至少一次投递语义处理，Worker 内以数据库任务状态拒绝重复执行。
- 外部连接器运行在发送前后均记录状态，API 不保存请求体，只保存输入摘要；Worker 会在调用前复核启用状态、部署配置、项目和摘要哈希。
- Worker 在调用前停止或连接器未配置时，运行会失败且不发出请求。调用后的进程故障必须依据外部系统的 `X-Integration-Run-ID` 对账；不得盲目自动重放。确认未执行后，由有权用户显式新建一次运行。
- Redis 不可用时停止发布变更，先恢复队列，再核对未完成 Task/IntegrationRun；Redis 不是业务事实源，业务记录、Evidence 与审计以 PostgreSQL 为准。

## 季度演练

1. 在隔离环境从最近一次受验证备份恢复 PostgreSQL 和 MinIO；按 `deploy/operations/README.md` 验证备份清单，再从 `search_chunks` 重建 Milvus。
2. 以单个 API、Worker 和网关副本失效为场景，验证剩余副本仍能登录、读取项目、下载授权文件和消费普通任务。
3. 以 PostgreSQL、Redis、MinIO、Milvus、MinerU、ClamAV 分别不可用为场景，验证 `/health/ready` 明确降级，且不会把失败任务伪装为成功。
4. 对一个启用的测试连接器进行幂等对账演练，确认同一 `X-Integration-Run-ID` 不产生重复外部副作用。
5. 将演练日期、负责人、依赖状态、恢复时间、失败项和整改计划写入受控运维记录。
