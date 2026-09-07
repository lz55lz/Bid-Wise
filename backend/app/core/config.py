"""部署环境配置；模型标识不允许通过环境变量覆盖。"""

from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """仅接收部署所需配置，缺少 AI 地址或密钥时 AI 能力不可执行。"""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"
    # 数据库与 Redis 是应用底座：前者保存业务事实，后者只承担队列、锁与短期状态。
    database_url: str
    redis_url: str

    # 认证密钥只在后端部署环境中存在，禁止由请求、前端或数据库记录覆盖。
    jwt_secret_key: SecretStr | None = None
    jwt_access_token_minutes: int = 480

    # 文件对象保存在 MinIO；对象键不能被当作授权依据，访问前必须回查 PostgreSQL。
    minio_endpoint: str | None = None
    minio_access_key: SecretStr | None = None
    minio_secret_key: SecretStr | None = None
    minio_bucket: str = "bid-wise"
    max_upload_bytes: int = 100 * 1024 * 1024

    mineru_base_url: str | None = None
    mineru_api_key: SecretStr | None = None

    # 以下全部是部署连接信息；模型名称固定在 constants.py，绝不接受环境变量覆盖。
    llm_base_url: str | None = None
    llm_api_key: SecretStr | None = None
    # 单个长任务内部的 LLM 批次并发；Worker 本身还有 max_jobs，总并发需两层共同控制。
    llm_batch_concurrency: int = Field(default=3, ge=1, le=8)
    embedding_base_url: str | None = None
    embedding_api_key: SecretStr | None = None
    reranker_base_url: str | None = None
    reranker_api_key: SecretStr | None = None

    @property
    def llm_is_configured(self) -> bool:
        """LLM 专用任务只依赖 MiniMax 连接，不应被向量或重排配置无端阻塞。"""
        return bool(self.llm_base_url and self.llm_api_key)

    @property
    def embedding_is_configured(self) -> bool:
        """本地/内网 bge-m3 可无鉴权运行，地址存在即表示可调用。"""
        return bool(self.embedding_base_url)

    @property
    def reranker_is_configured(self) -> bool:
        """rankv2 与 embedding 一样支持无 API Key 的内网部署。"""
        return bool(self.reranker_base_url)

    @property
    def ai_is_configured(self) -> bool:
        return all(
            (
                self.llm_base_url,
                self.llm_api_key,
                self.embedding_base_url,
                self.reranker_base_url,
            )
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
