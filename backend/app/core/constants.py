"""不可由环境、数据库或请求覆盖的模型标识。"""

LLM_MODEL_ID = "MiniMax-M3"
# 与原项目及当前本地 rank 服务实际加载的模型保持一致；调用方无覆盖入口。
RERANKER_MODEL_ID = "bge-reranker-v2-m3"
EMBEDDING_MODEL_ID = "bge-m3"
