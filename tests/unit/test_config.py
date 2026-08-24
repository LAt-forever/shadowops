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


def test_environment_variables_override_defaults(monkeypatch) -> None:
    monkeypatch.setenv("SHADOWOPS_HTTP_PORT", "8123")
    settings_type = getattr(config, "Settings", None)
    assert settings_type is not None
    settings = settings_type(_env_file=None)

    assert settings.http_port == 8123
