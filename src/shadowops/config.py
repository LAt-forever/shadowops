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
    database_url: str = (
        "postgresql+psycopg://shadowops:shadowops@control-postgres:5432/shadowops"
    )
    redis_url: str = "redis://redis:6379/0"
    postgres_major: int = 16
    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    return Settings()
