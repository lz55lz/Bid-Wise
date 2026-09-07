# BidWise

> 面向企业私有部署的招投标智能分析与投标决策平台。

BidWise 将招标文件解析、人工需求复核、企业材料匹配、风险研判、决策建议、报告生成与项目问答连接为一条可追溯的投标准备流程。系统的核心不是“总结招标文件”，而是基于**已绑定企业**及其资质、业绩、人员、财务与信用材料，回答“这个企业是否适合投、还缺什么、风险在哪里”。

## 适用场景

- 企业收到招标文件后，快速整理资格、评分、商务及技术要求。
- 投标负责人核对企业已有材料与招标要求，识别决定性缺口。
- 团队在提交前统一查看风险、投标建议和企业适配度结论。
- 在同一项目内持续追问，获得带原文依据或企业材料依据的回答。

## 核心能力

- **文件解析与结构化**：支持招标文件上传、异步解析和章节化浏览；复杂 PDF 可接入 MinerU，DOCX 提供本地解析兜底。
- **需求复核**：自动提取资格、评分、商务和技术类要求，人工确认高价值或不确定事项后再进入后续分析。
- **企业匹配与风险研判**：以项目 ID 和企业 ID 关联数据，匹配企业材料并识别缺失项、风险等级与投标建议。
- **企业适配度分析**：围绕绑定企业输出适配结论、优势、决定性缺口、风险与推进条件，而非只罗列招标要求。
- **报告与项目问答**：报告汇总企业适配度、匹配、风险与决策；问答基于项目原文与企业材料返回答案和引用，支持连续追问与会话历史。
- **运行可观测性**：系统设置提供后端依赖健康检查，展示 PostgreSQL、Redis、MinIO、MinerU 与模型服务可用状态。

## 技术亮点

- 后端采用 FastAPI 模块化单体与独立 ARQ Worker，耗时解析、分析和报告任务不阻塞请求。
- PostgreSQL 作为业务事实源并通过 `pgvector` 保存向量；MinIO 保存文件对象，Redis 承担队列、锁和短期状态。
- 所有项目、文档、证据和报告均由服务端按身份、角色、成员资格和资源归属进行授权校验。
- 前端使用 Vue 3、TypeScript、Vite 与 Element Plus，覆盖项目管理、文档浏览、需求复核、报告和智能问答等完整演示路径。

## 业务流程

```text
创建项目并绑定企业
        ↓
上传招标文件 → 字段提取 → 人工直接审批 / 修改补充后审批
        ↓
匹配分析 → 风险研判 → 决策建议 → 企业适配度报告
        ↓
项目问答与多轮追问
```

1. 创建项目并绑定待评估的企业资料。
2. 上传招标文件，等待解析完成后查看结构化内容。
3. 在“需求复核”中直接审批，或修改、补充后再审批关键要求。
4. 启动分析，查看企业匹配、风险、决策建议和材料缺口。
5. 查看报告中的企业适配度结论；也可在项目问答中围绕当前项目继续追问。

## 仓库结构

```text
Bid-Wise/
├── backend/                 # FastAPI API、领域模块、ARQ Worker、迁移与测试
├── frontend/                # Vue 3 管理端
├── deploy/                  # 部署与运维脚本
├── doc/                     # 产品、架构、数据库与使用文档
├── .env.example             # 环境变量模板（不含真实值）
```

## 本地启动

### 1. 配置环境变量

复制根目录 `.env.example` 为 `.env`，按实际部署环境填写数据库、对象存储、模型与解析服务地址。`.env` 已被 Git 忽略，切勿提交密钥或生产数据。

### 2. 启动后端与 Worker

后端使用 Python 3.12 和 `uv`：

```powershell
cd backend
uv sync --all-groups
uv run alembic upgrade head
uv run uvicorn app.main:app --reload
```

另开一个终端启动异步任务 Worker：

```powershell
cd backend
uv run python start_worker.py
```

### 3. 启动前端

```powershell
cd frontend
npm install
npm run dev
```

默认开发地址通常为：前端 `http://localhost:3000`、后端 `http://localhost:8000`。具体环境依赖、启动与停服说明见[本机部署与运行手册](doc/deployment-local.md)。

## 质量检查

```powershell
cd backend
uv run pytest
uv run ruff check app tests

cd ..\frontend
npm run type-check
npm run build
```

## 数据与提交卫生

仓库不包含 `.env`、运行日志、真实上传文件、个人简历、测试截图、浏览器自动化产物、模型中间输出或演示交付物。`.gitignore` 已覆盖这些本地产物；提交前仍建议运行 `git status`，确认只包含源码、文档与必要配置。

## 文档基线

- [产品需求](doc/prd.md)
- [软件需求](doc/srs.md)
- [架构设计](doc/architecture-design.md)
- [数据库设计](doc/database-design.md)
- [详细设计](doc/detailed-design.md)
- [本机部署与运行手册](doc/deployment-local.md)
- [使用手册](doc/user-manual.md)

PostgreSQL、Redis、MinIO、MinerU 及模型服务由部署环境提供。向量数据使用 PostgreSQL 的 `pgvector` 扩展，不需要部署独立向量数据库。首次创建管理员可执行 `uv run python -m app.cli.bootstrap_admin`，该命令交互式读取密码，不会写入命令历史。

## 项目说明

- [面试讲解稿](doc/interview-guide.md)：项目背景、架构取舍、核心链路、典型问题与面试问答。
