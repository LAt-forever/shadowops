import shadowops.config as config


def test_settings_use_safe_local_defaults() -> None:
    settings_type = getattr(config, "Settings", None)
    assert settings_type is not None
    settings = settings_type(_env_file=None)

    assert settings.app_name == "ShadowOps"
    assert settings.environment == "development"
    assert settings.http_host == "127.0.0.1"
    assert settings.http_port == 8000
    assert settings.postgres_major == 16
    assert settings.health_connect_timeout_seconds == 2
    assert settings.health_read_timeout_seconds == 2.0
    assert settings.health_pool_timeout_seconds == 2.0
    assert settings.health_statement_timeout_ms == 2_000
    assert settings.outbox_batch_size == 50
    assert settings.outbox_poll_interval_seconds == 0.5
    assert settings.outbox_retry_base_seconds == 1.0
    assert settings.outbox_retry_max_seconds == 30.0
    assert settings.reconcile_batch_size == 50
    assert settings.reconcile_interval_seconds == 2.0
    assert settings.recovery_stale_after_seconds == 10.0
    assert settings.recovery_max_attempts == 5


def test_environment_variables_override_defaults(monkeypatch) -> None:
    monkeypatch.setenv("SHADOWOPS_HTTP_PORT", "8123")
    settings_type = getattr(config, "Settings", None)
    assert settings_type is not None
    settings = settings_type(_env_file=None)

    assert settings.http_port == 8123
