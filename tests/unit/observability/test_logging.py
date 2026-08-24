import json

import structlog

import shadowops.observability.logging as logging_config


def test_configured_logger_emits_json(capsys) -> None:
    configure_logging = getattr(logging_config, "configure_logging", None)
    assert configure_logging is not None
    configure_logging("INFO")

    structlog.get_logger().info("service_started", service="api")

    payload = json.loads(capsys.readouterr().out)
    assert payload["event"] == "service_started"
    assert payload["service"] == "api"
    assert payload["level"] == "info"
