"""Application configuration."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="SHADOWOPS_",
        extra="ignore",
    )

    app_name: str = "ShadowOps"
    environment: str = "development"
    http_host: str = "127.0.0.1"
    http_port: int = 8000
    database_url: str = "postgresql+psycopg://shadowops:shadowops@control-postgres:5432/shadowops"
    redis_url: str = "redis://redis:6379/0"
    postgres_major: int = 16
    log_level: str = "INFO"
    health_connect_timeout_seconds: int = 2
    health_read_timeout_seconds: float = 2.0
    health_pool_timeout_seconds: float = 2.0
    health_statement_timeout_ms: int = 2_000


@lru_cache
def get_settings() -> Settings:
    return Settings()
