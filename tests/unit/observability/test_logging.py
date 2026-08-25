import json
import logging

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


def test_standard_library_logger_emits_json(capsys) -> None:
    logging_config.configure_logging("INFO")

    logging.getLogger("uvicorn.error").info("server_started")

    payload = json.loads(capsys.readouterr().out)
    assert payload["event"] == "server_started"
    assert payload["logger"] == "uvicorn.error"
    assert payload["level"] == "info"


def test_standard_library_exception_contains_readable_traceback(capsys) -> None:
    logging_config.configure_logging("INFO")

    try:
        raise ValueError("broken migration")
    except ValueError:
        logging.getLogger("uvicorn.error").exception("request_failed")

    payload = json.loads(capsys.readouterr().out)
    assert "ValueError: broken migration" in payload["exception"]
    assert "<traceback object" not in payload["exception"]
