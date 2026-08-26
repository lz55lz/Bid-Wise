# BidWise

面向单企业私有部署的招投标智能分析与投标决策平台。它将招标文件解析、需求复核、企业材料匹配、风险研判、报告生成与基于证据的智能问答串成一条可追溯的投标准备流程。

## 核心能力

- **文件解析与结构化**：支持招标文件上传、异步解析和章节化浏览；复杂 PDF 可接入 MinerU，DOCX 提供本地解析兜底。
- **需求复核**：自动提取资格、评分、商务和技术类要求，人工确认高价值或不确定事项后再进入后续分析。
- **企业匹配与风险研判**：以项目 ID 和企业 ID 关联数据，匹配企业材料并识别缺失项、风险等级与投标建议。
- **报告与知识问答**：生成可下载报告；问答基于项目原文证据返回答案和引用，支持 Markdown 渲染与会话历史。
- **运行可观测性**：系统设置提供后端依赖健康检查，展示 PostgreSQL、Redis、MinIO、Milvus、MinerU 与模型服务可用状态。

## 技术亮点

- 后端采用 FastAPI 模块化单体与独立 ARQ Worker，耗时解析、分析和报告任务不阻塞请求。
- PostgreSQL 作为业务事实源；MinIO 保存文件对象，Milvus 保存可重建向量，Redis 承担队列、锁和短期状态。
- 所有项目、文档、证据和报告均由服务端按身份、角色、成员资格和资源归属进行授权校验。
- 前端使用 Vue 3、TypeScript、Vite 与 Element Plus，覆盖项目管理、文档浏览、需求复核、报告和智能问答等完整演示路径。

## 快速演示流程

1. 创建项目并关联企业资料。
2. 上传招标文件，等待解析完成后查看结构化内容。
3. 在“需求复核”确认关键要求。
4. 执行匹配分析，查看风险、材料缺口与投标建议。
5. 生成报告，或在项目问答中围绕原文继续追问。

## 截图与敏感信息

仓库不包含 `.env`、运行日志、真实上传文件、个人简历、测试截图或演示交付物。请复制 `.env.example` 为 `.env` 后自行填写部署环境配置，切勿提交密钥和生产数据。

## 文档基线

- [产品需求](doc/prd.md)
- [软件需求](doc/srs.md)
- [架构设计](doc/architecture-design.md)
- [数据库设计](doc/database-design.md)
- [详细设计](doc/detailed-design.md)
- [本机部署与运行手册](doc/deployment-local.md)
- [使用手册](doc/user-manual.md)

## 已初始化的结构

- `backend/`：应用、领域服务、持久化、Worker、集成、规则、报告、迁移和测试目录。
- `frontend/`：Vue 页面、组件、路由、状态管理和 typed API client 目录。
- `.env.example`：仅包含文档允许的 AI 服务连接键；模型标识固定在服务端代码，不属于环境配置。

## 后端本地开发

后端使用 `uv` 管理 Python 3.12：

```powershell
cd backend
uv sync --all-groups
uv run pytest
uv run ruff check app tests
```

PostgreSQL、Redis、MinIO、MinerU 和模型服务由部署或本机环境提供，不由本仓库的 Compose 管理。`deploy/start-local.ps1` 仅启动本仓库管理的 etcd、Milvus、数据库迁移、API 和 ARQ Worker；Vue 前端请在 `frontend/` 中独立启动。运行后端前，在根目录 `.env` 中配置 `DATABASE_URL`、`REDIS_URL`、`JWT_SECRET_KEY`、`MINIO_ENDPOINT`、`MINIO_ACCESS_KEY` 与 `MINIO_SECRET_KEY`，以及 AI 服务连接键。

完成迁移后，使用 `uv run python -m app.scripts.bootstrap_admin` 交互式创建首位系统管理员。该命令不会将密码写入命令历史。

## 本机运行

当前验收基线中，后端 API 和 ARQ Worker 在 Windows 主机直接启动；Docker
只管理 etcd、Milvus 与 ClamAV。PostgreSQL、Redis、MinIO、MinerU 和模型服务
均使用已有的受控端点；Vue 前端由开发者独立启动。完整配置、启动、重启、验证与停止步骤见
[本机部署与运行手册](doc/deployment-local.md)。
