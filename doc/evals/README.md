# 文档抽取质量评测

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
