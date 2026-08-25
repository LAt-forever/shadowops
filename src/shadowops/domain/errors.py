"""Stable framework-free domain errors."""

from typing import Any


class DomainError(Exception):
    """Base error that application adapters can map to a stable code."""

    code = "DOMAIN_ERROR"


class InvalidStateTransition(DomainError):
    """Raised when a run attempts to leave the declared lifecycle graph."""

    code = "ILLEGAL_STATE_TRANSITION"

    def __init__(self, source: Any, target: Any) -> None:
        source_value = getattr(source, "value", str(source))
        target_value = getattr(target, "value", str(target))
        super().__init__(f"Cannot transition audit run from {source_value} to {target_value}")
