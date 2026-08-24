import pytest
from sqlalchemy import create_engine

import shadowops.infrastructure.health as health


def test_database_health_succeeds_when_database_accepts_query() -> None:
    check_type = getattr(health, "DatabaseHealthCheck", None)
    assert check_type is not None
    engine = create_engine("sqlite+pysqlite:///:memory:")

    check_type(engine)()


class RedisClient:
    def __init__(self, response: bool) -> None:
        self._response = response

    def ping(self) -> bool:
        return self._response


def test_redis_health_succeeds_for_true_ping() -> None:
    check_type = getattr(health, "RedisHealthCheck", None)
    assert check_type is not None

    check_type(RedisClient(True))()


def test_redis_health_rejects_non_true_ping() -> None:
    check_type = getattr(health, "RedisHealthCheck", None)
    assert check_type is not None

    with pytest.raises(ConnectionError, match="non-true"):
        check_type(RedisClient(False))()
