"""External dependency health checks."""

from typing import Any

from sqlalchemy import Engine, text


class DatabaseHealthCheck:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def __call__(self) -> None:
        with self._engine.connect() as connection:
            connection.execute(text("SELECT 1"))


class RedisHealthCheck:
    def __init__(self, client: Any) -> None:
        self._client = client

    def __call__(self) -> None:
        if self._client.ping() is not True:
            raise ConnectionError("Redis ping returned a non-true response")
