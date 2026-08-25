import shadowops.application.readiness as readiness


def test_readiness_is_ready_when_all_checks_succeed() -> None:
    service_type = getattr(readiness, "ReadinessService", None)
    assert service_type is not None
    result = service_type({"database": lambda: None, "redis": lambda: None}).run()

    assert result.ready is True
    assert result.dependencies == {"database": "ok", "redis": "ok"}


def test_readiness_reports_each_failed_dependency() -> None:
    def fail() -> None:
        raise ConnectionError("offline")

    service_type = getattr(readiness, "ReadinessService", None)
    assert service_type is not None
    result = service_type({"database": fail, "redis": lambda: None}).run()

    assert result.ready is False
    assert result.dependencies == {"database": "unavailable", "redis": "ok"}
