from pydantic_settings import BaseSettings, SettingsConfigDict


class QdrantSettings(BaseSettings):
    host: str = "localhost"
    port: int = 6333
    collection_name: str = "synatyx_context"
    vector_size: int = 1536

    model_config = SettingsConfigDict(env_prefix="QDRANT_")


class RedisSettings(BaseSettings):
    url: str = "redis://localhost:6379"
    l1_max_messages: int = 20

    model_config = SettingsConfigDict(env_prefix="REDIS_")


class PostgresSettings(BaseSettings):
    host: str = "localhost"
    port: int = 5432
    db: str = "context_engine"
    user: str = "context_engine"
    password: str = "context_engine"

    @property
    def dsn(self) -> str:
        return f"postgresql+asyncpg://{self.user}:{self.password}@{self.host}:{self.port}/{self.db}"

    model_config = SettingsConfigDict(env_prefix="POSTGRES_")


class KafkaSettings(BaseSettings):
    bootstrap_servers: str = "localhost:9092"
    topic_memory_updates: str = "synatyx.memory.updates"
    topic_summarize: str = "synatyx.summarize"

    model_config = SettingsConfigDict(env_prefix="KAFKA_")


class EmbeddingSettings(BaseSettings):
    provider: str = "sentence-transformers"  # or "openai"
    model: str = "all-MiniLM-L6-v2"
    openai_api_key: str = ""
    openai_model: str = "text-embedding-ada-002"

    model_config = SettingsConfigDict(env_prefix="EMBEDDING_")


class Settings(BaseSettings):
    app_name: str = "Synatyx Context Engine"
    debug: bool = False
    log_level: str = "INFO"

    qdrant: QdrantSettings = QdrantSettings()
    redis: RedisSettings = RedisSettings()
    postgres: PostgresSettings = PostgresSettings()
    kafka: KafkaSettings = KafkaSettings()
    embedding: EmbeddingSettings = EmbeddingSettings()

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        extra="ignore",
    )


settings = Settings()

