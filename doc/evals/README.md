# 文档抽取质量评测

## 检索评测中心

前端“评测中心”提供的是检索回归评测，不生成回答。它只检查每个问题的前 5
条检索结果是否包含人工维护的期望原文，因此 `Recall@5` 表示“证据被找回”的
比例，不能替代回答正确性或人工合规审查。

系统提供只读的内置基准集，以及管理员可维护的自定义题集。自定义题集中的每一
题包含问题、检索范围（法律知识库或项目文件）和一条或多条期望 Evidence；编辑
题集会递增版本号。题集应由人工依据已发布的法律、招标文件原文维护，不能从本次
模型回答倒推生成。

黄金集是人工从 MinerU 原文节点回查后维护的业务事实，不得由当前模型候选或报告反向生成。每一份黄金集通过文件 SHA-256 锁定来源；上传了同名但内容不同的文件时，评测命令会拒绝执行。

运行方式：

```powershell
cd backend
.\.venv\Scripts\python.exe scripts\evaluate_requirement_quality.py <project_id> `
  --document-version-id <document_version_id> `
  --golden ..\doc\evals\zb12-golden.json `
  --assert-thresholds --format markdown
```

评测严格限定到 `document_version_id` 的 Evidence，避免同一项目内企业材料或旧版本文档污染分数。它输出：关键条款召回、必选项召回、证据页定位率、字段召回、人工复核样本准确率和未通过的阈值。

`term_groups` 表示每一组至少命中一个词，所有组都必须命中；适合表达“主题 + 限定条件”。旧的 `terms` 仍可使用，默认任一词命中以兼容已有黄金集。`thresholds` 中列出的指标会在 `--assert-thresholds` 下作为回归门禁。

`zb5` 当前只标注了 Requirement，因为该历史样本的项目字段含有旧实现产生的错误事实，不能用它们作为高质量字段黄金集。`zb12` 同时覆盖 Requirement 和经过人工确认的字段。
