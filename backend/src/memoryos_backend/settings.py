from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="MEMORYOS_",
        env_file=".env",
        extra="ignore",
    )

    database_url: str = "postgresql+psycopg:///memoryos_dev"
    data_dir: Path = Path.home() / "Documents" / "MemoryOS" / "postgres"
    api_token: str | None = None
    bind_host: str = "127.0.0.1"
    port: int = 8766
    log_level: str = "INFO"
    outbox_poll_seconds: float = 5.0
    embedding_model: str | None = None
    embedding_model_version: str = "configured-local"
    ollama_url: str = "http://127.0.0.1:11434"
    embedding_dimensions: int = 384


@lru_cache
def get_settings() -> Settings:
    return Settings()
