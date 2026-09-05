"""Application configuration."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr
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
    outbox_batch_size: int = 50
    outbox_poll_interval_seconds: float = 0.5
    outbox_retry_base_seconds: float = 1.0
    outbox_retry_max_seconds: float = 30.0
    reconcile_batch_size: int = 50
    reconcile_interval_seconds: float = 2.0
    recovery_stale_after_seconds: float = 10.0
    recovery_max_attempts: int = 5
    sse_poll_interval_seconds: float = 0.25
    sse_keepalive_seconds: float = 10.0
    repo_root: Path = Path("/repositories")
    artifact_root: Path = Path("/var/lib/shadowops/artifacts")
    snapshot_max_files: int = 10_000
    snapshot_max_file_bytes: int = 5 * 1024 * 1024
    snapshot_max_total_bytes: int = 100 * 1024 * 1024
    snapshot_read_chunk_bytes: int = 1024 * 1024
    shadow_postgres_image: str = (
        "postgres:16@sha256:4b7183ac05f8ef417db21fd72d71047a4238340c261d3cc3ddb6d579ab5071ae"
    )
    runner_image: str = "shadowops-runner:0.1.0"
    sandbox_lease_seconds: int = 600
    sandbox_readiness_timeout_seconds: int = 30
    sandbox_execution_timeout_seconds: int = 210
    sandbox_sweep_interval_seconds: float = 30.0
    agent_mode: Literal["fake", "recorded", "live"] = "fake"
    llm_model: str | None = None
    llm_recorded_responses_json: str | None = None
    openai_api_key: SecretStr | None = None
    openai_base_url: str = "https://api.openai.com/v1"
    llm_timeout_seconds: float = 30.0
    llm_max_attempts: int = 2


@lru_cache
def get_settings() -> Settings:
    return Settings()
