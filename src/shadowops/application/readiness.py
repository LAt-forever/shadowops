"""Dependency readiness aggregation."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class ReadinessResult:
    ready: bool
    dependencies: dict[str, str]


class ReadinessService:
    def __init__(self, checks: Mapping[str, Callable[[], None]]) -> None:
        self._checks = dict(checks)

    def run(self) -> ReadinessResult:
        dependencies: dict[str, str] = {}
        for name, check in self._checks.items():
            try:
                check()
            except Exception:
                dependencies[name] = "unavailable"
            else:
                dependencies[name] = "ok"
        return ReadinessResult(
            ready=all(status == "ok" for status in dependencies.values()),
            dependencies=dependencies,
        )
