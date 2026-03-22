from enum import Enum
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Always resolve .env relative to the project root regardless of cwd
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_ENV_FILE = _PROJECT_ROOT / ".env"


class RunMode(str, Enum):
    MCP = "mcp"           # stdio MCP server only (default)
    GRAPHQL = "graphql"   # FastAPI GraphQL + SSE + HTTP MCP endpoint
    BOTH = "both"         # FastAPI + stdio MCP concurrently


class QdrantSettings(BaseSettings):
    host: str = "localhost"
    port: int = 6333
    collection_name: str = "synatyx_context"
    vector_size: int = 1536

    model_config = SettingsConfigDict(env_prefix="QDRANT_", env_file=str(_ENV_FILE), env_file_encoding="utf-8", extra="ignore")


class RedisSettings(BaseSettings):
    url: str = "redis://localhost:6379"
    l1_max_messages: int = 20

    model_config = SettingsConfigDict(env_prefix="REDIS_", env_file=str(_ENV_FILE), env_file_encoding="utf-8", extra="ignore")


class PostgresSettings(BaseSettings):
    host: str = "localhost"
    port: int = 5432
    db: str = "context_engine"
    user: str = "context_engine"
    password: str = "context_engine"

    @property
    def dsn(self) -> str:
        return f"postgresql+asyncpg://{self.user}:{self.password}@{self.host}:{self.port}/{self.db}"

    model_config = SettingsConfigDict(env_prefix="POSTGRES_", env_file=str(_ENV_FILE), env_file_encoding="utf-8", extra="ignore")



class EmbeddingSettings(BaseSettings):
    provider: str = "openai"  # or "sentence-transformers"
    model: str = "all-MiniLM-L6-v2"
    openai_api_key: str = ""
    openai_model: str = "text-embedding-3-small"

    model_config = SettingsConfigDict(env_prefix="EMBEDDING_", env_file=str(_ENV_FILE), env_file_encoding="utf-8", extra="ignore")


class Settings(BaseSettings):
    app_name: str = "Synatyx Context Engine"
    debug: bool = False
    log_level: str = "INFO"
    run_mode: RunMode = RunMode.MCP

    # Use Field(default_factory=...) so each sub-settings class is instantiated
    # independently and resolves its own env vars with its own env_prefix.
    qdrant: QdrantSettings = Field(default_factory=QdrantSettings)
    redis: RedisSettings = Field(default_factory=RedisSettings)
    postgres: PostgresSettings = Field(default_factory=PostgresSettings)
    embedding: EmbeddingSettings = Field(default_factory=EmbeddingSettings)

    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()

