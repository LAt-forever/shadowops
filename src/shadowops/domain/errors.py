"""Stable framework-free domain errors."""

from typing import Any


class DomainError(Exception):
    """Base error that application adapters can map to a stable code."""

    code = "DOMAIN_ERROR"


class RepositoryInputError(DomainError):
    """A stable, non-sensitive failure at the repository trust boundary."""

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        super().__init__(detail)


class ImmutableResultConflict(RepositoryInputError):
    """An idempotent result key was reused for different immutable content."""

    code = "SNAPSHOT_INTEGRITY_FAILED"

    def __init__(self, result: str) -> None:
        super().__init__(
            self.code,
            f"Existing {result} does not match the computed immutable result",
        )


class InvalidStateTransition(DomainError):
    """Raised when a run attempts to leave the declared lifecycle graph."""

    code = "ILLEGAL_STATE_TRANSITION"

    def __init__(self, source: Any, target: Any) -> None:
        source_value = getattr(source, "value", str(source))
        target_value = getattr(target, "value", str(target))
        super().__init__(f"Cannot transition audit run from {source_value} to {target_value}")


class OptimisticConcurrencyError(DomainError):
    """Raised when a stale writer attempts to update an aggregate."""

    code = "OPTIMISTIC_CONCURRENCY_CONFLICT"

    def __init__(self, aggregate_id: Any, expected_version: int) -> None:
        super().__init__(
            f"Aggregate {aggregate_id} is no longer at expected version {expected_version}"
        )


class IdempotencyConflictError(DomainError):
    """Raised when a key is reused for a different normalized request."""

    code = "IDEMPOTENCY_CONFLICT"

    def __init__(self, key: str) -> None:
        super().__init__(f"Idempotency key {key!r} is already bound to another request")


class RunNotFoundError(DomainError):
    """Raised when a requested audit run does not exist."""

    code = "RUN_NOT_FOUND"

    def __init__(self, run_id: Any) -> None:
        super().__init__(f"Audit run {run_id} was not found")


class StaticReportNotReadyError(DomainError):
    """Raised when a known run has not committed its static report yet."""

    code = "STATIC_REPORT_NOT_READY"

    def __init__(self, run_id: Any) -> None:
        super().__init__(f"Static report for audit run {run_id} is not ready")


class ClaimLostError(DomainError):
    """Raised when an expired worker attempts to mutate a reclaimed step."""

    code = "STEP_CLAIM_LOST"

    def __init__(self, step_id: Any) -> None:
        super().__init__(f"Run step {step_id} is no longer owned by this claim")


class TerminalRunError(DomainError):
    """Raised when a command cannot apply to an already terminal run."""

    code = "RUN_TERMINAL"

    def __init__(self, run_id: Any, state: Any) -> None:
        state_value = getattr(state, "value", str(state))
        super().__init__(f"Audit run {run_id} is already terminal in state {state_value}")
