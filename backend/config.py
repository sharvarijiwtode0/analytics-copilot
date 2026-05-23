from functools import lru_cache
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

_ENV_FILE = Path(__file__).parent.parent / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(_ENV_FILE), env_file_encoding="utf-8", extra="ignore")

    # App
    app_name: str = "Data Visualization Copilot"
    app_env: str = "development"
    debug: bool = True
    secret_key: str = "change-me-in-production-32chars-min"
    api_prefix: str = "/api/v1"

    # JWT Auth
    jwt_secret_key: str = ""  # If empty, uses secret_key
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 7

    # Database (PostgreSQL — metadata, history, users)
    database_url: str = "sqlite+aiosqlite:///./dvc.db"

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # ClickHouse (Analytics DB — optional)
    clickhouse_host: str = "localhost"
    clickhouse_port: int = 8123
    clickhouse_user: str = "default"
    clickhouse_password: str = ""
    clickhouse_database: str = "default"

    # LLM Providers (via OpenRouter or direct)
    groq_api_key: str = ""
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    gemini_api_key: str = ""
    mistral_api_key: str = ""
    openrouter_api_key: str = ""
    deepseek_api_key: str = ""
    cohere_api_key: str = ""

    # Zhipu AI (GLM) — Z.ai OpenAI-compatible endpoint
    zhipu_api_key: str = ""
    zhipu_base_url: str = "https://api.z.ai/api/coding/paas/v4"

    # Model routing
    llm_fast_model: str = "zhipu/glm-5-turbo"
    llm_smart_model: str = "zhipu/glm-5-turbo"
    llm_premium_model: str = "zhipu/glm-5-turbo"
    llm_fallback_model: str = "zhipu/glm-5-turbo"

    # Vector Memory (Qdrant)
    qdrant_url: str = "http://localhost:6333"
    qdrant_collection: str = "dvc_memory"
    qdrant_enabled: bool = True   # Enabled for v2 knowledge features

    # MinIO (Conversation History Storage)
    minio_endpoint: str = ""   # e.g., "localhost:9000" or "minio.example.com:9000"
    minio_access_key: str = ""
    minio_secret_key: str = ""
    minio_secure: bool = False  # Set True for HTTPS
    minio_bucket_name: str = "analytics-copilot-conversations"

    # Integrations
    slack_bot_token: str = ""
    slack_signing_secret: str = ""
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""

    # Web Search (for comparison questions)
    tavily_api_key: str = ""  # https://tavily.com/
    serper_api_key: str = ""  # https://serper.dev/

    # Query limits
    max_rows_returned: int = 10000
    query_timeout_seconds: int = 90
    max_conversation_history: int = 20


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
