from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    """部署配置；模型标识从不属于配置。"""

    model_config = SettingsConfigDict(
        env_file=ROOT_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str | None = None
    redis_url: str | None = None
    jwt_secret_key: SecretStr | None = None
    jwt_access_token_minutes: int = 10080  # 7 days

    minio_endpoint: str | None = None
    minio_access_key: SecretStr | None = None
    minio_secret_key: SecretStr | None = None
    minio_bucket: str = "ai-bid-advisor"
    max_upload_bytes: int = 100 * 1024 * 1024

    # MinerU may be self-hosted or the official hosted API. It is intentionally
    # optional so project/file management remains available when parsing is
    # not deployed. The worker reports a retryable parser failure instead.
    mineru_base_url: str | None = None
    mineru_api_key: SecretStr | None = None

    llm_base_url: str | None = None
    llm_api_key: SecretStr | None = None
    # Existing local LLM deployments expose these names. They are deployment
    # Connection aliases only; model IDs remain server-side constants.
    chat_base_url: str | None = None
    chat_api_key: SecretStr | None = None
    reranker_base_url: str | None = None
    reranker_api_key: SecretStr | None = None
    embedding_base_url: str | None = None
    embedding_api_key: SecretStr | None = None

    # P2 connector endpoints are deployment-only values. Connector codes, request
    # actions and all authorization rules remain fixed in server code.
    erp_integration_base_url: str | None = None
    erp_integration_api_key: SecretStr | None = None
    crm_integration_base_url: str | None = None
    crm_integration_api_key: SecretStr | None = None
    public_resource_integration_base_url: str | None = None
    public_resource_integration_api_key: SecretStr | None = None

    @model_validator(mode="after")
    def use_chat_llm_configuration(self) -> "Settings":
        """Reuse the established local CHAT_* deployment credentials."""
        if not self.llm_base_url and self.chat_base_url:
            self.llm_base_url = self.chat_base_url
        if not self.llm_api_key and self.chat_api_key:
            self.llm_api_key = self.chat_api_key
        return self

    @property
    def ai_is_configured(self) -> bool:
        return all(
            (
                self.llm_base_url,
                self.llm_api_key,
                self.reranker_base_url,
                self.embedding_base_url,
            )
        )

    @property
    def llm_model_name(self) -> str:
        """Fixed server-owned model ID; never accept an environment override."""
        from app.core.constants import LLM_MODEL_ID

        return LLM_MODEL_ID


@lru_cache
def get_settings() -> Settings:
    return Settings()
