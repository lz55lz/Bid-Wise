LLM_MODEL_ID = "MiniMax-M3"
RERANKER_MODEL_ID = "bge-reranker-v2-m3"
EMBEDDING_MODEL_ID = "bge-m3"

# 召回配置
RAG_RETRIEVAL_LIMIT = 24  # 向量检索返回数量（粗筛）
RAG_CONTEXT_LIMIT = 10  # 最终输入 LLM 的上下文数量
RAG_SIMILARITY_THRESHOLD = 0.6  # cosine distance 阈值（越低越相似，0.6 以下保留）
RERANK_CANDIDATE_LIMIT = 64  # 送入 reranker 的候选数量（tender 用 64，legal 用 20）
RERANK_TRUNCATE_CHARS = 300  # 每个文档截断字符数（防超 reranker token limit）
MODEL_OVERRIDE_FIELDS = frozenset({"model", "provider", "embedding_model", "reranker_model"})

# 人工复核（HITL）开关：依赖 checkpointer 的 interrupt/resume 语义，
# 未接入 AsyncPostgresSaver 前保持关闭，validate 路由直接走 fan_out
HITL_ENABLED = False

SYSTEM_ADMIN = "SYSTEM_ADMIN"

# IM/Web 问答流水线的系统执行账号（渠道级授权：渠道绑定项目即项目授权，
# 单企业私有部署）。按用户名在运行时解析，避免硬编码 UUID 在新环境失效。
SYSTEM_ACTOR_USERNAME = "admin"
PROJECT_OWNER = "PROJECT_OWNER"
BID_SPECIALIST = "BID_SPECIALIST"
LEGAL_COMPLIANCE = "LEGAL_COMPLIANCE"
MATERIAL_ADMIN = "MATERIAL_ADMIN"
READ_ONLY = "READ_ONLY"

SYSTEM_ROLE_SEEDS = (
    (SYSTEM_ADMIN, "系统管理员", "管理用户、角色和全局审计日志"),
    (PROJECT_OWNER, "项目负责人", "管理项目成员、项目决策与报告"),
    (BID_SPECIALIST, "投标专员", "维护投标项目资料并处理风险"),
    (LEGAL_COMPLIANCE, "法务/合规", "复核风险并维护规则"),
    (MATERIAL_ADMIN, "企业材料管理员", "维护企业材料与证明文件"),
    (READ_ONLY, "管理层/只读", "查看被授权项目的已发布信息"),
)
