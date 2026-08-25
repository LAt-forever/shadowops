"""Framework-free audit run lifecycle."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from shadowops.domain.errors import InvalidStateTransition


class RunState(StrEnum):
    QUEUED = "QUEUED"
    DISCOVERING = "DISCOVERING"
    STATIC_ANALYSIS = "STATIC_ANALYSIS"
    PLANNING = "PLANNING"
    PROVISIONING = "PROVISIONING"
    BASELINE_READY = "BASELINE_READY"
    APPLYING = "APPLYING"
    SEEDING = "SEEDING"
    SMOKE_TESTING = "SMOKE_TESTING"
    ROLLBACK_VERIFYING = "ROLLBACK_VERIFYING"
    REPORTING = "REPORTING"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    COMPLETED = "COMPLETED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class StepStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class CleanupStatus(StrEnum):
    NOT_REQUIRED = "NOT_REQUIRED"
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


MAIN_STATE_PATH: tuple[RunState, ...] = (
    RunState.QUEUED,
    RunState.DISCOVERING,
    RunState.STATIC_ANALYSIS,
    RunState.PLANNING,
    RunState.PROVISIONING,
    RunState.BASELINE_READY,
    RunState.APPLYING,
    RunState.SEEDING,
    RunState.SMOKE_TESTING,
    RunState.ROLLBACK_VERIFYING,
    RunState.REPORTING,
    RunState.COMPLETED,
)

TERMINAL_STATES = frozenset(
    {
        RunState.COMPLETED,
        RunState.APPROVED,
        RunState.REJECTED,
        RunState.FAILED,
        RunState.CANCELLED,
    }
)

_NORMAL_TRANSITIONS: dict[RunState, frozenset[RunState]] = {
    source: frozenset({target})
    for source, target in zip(MAIN_STATE_PATH[:-1], MAIN_STATE_PATH[1:], strict=True)
}
_NORMAL_TRANSITIONS[RunState.REPORTING] = frozenset(
    {RunState.COMPLETED, RunState.AWAITING_APPROVAL}
)
_NORMAL_TRANSITIONS[RunState.AWAITING_APPROVAL] = frozenset({RunState.APPROVED, RunState.REJECTED})


def next_main_state(state: RunState) -> RunState | None:
    """Return the deterministic M1 successor, or none for a terminal state."""
    try:
        position = MAIN_STATE_PATH.index(state)
    except ValueError:
        return None
    if position == len(MAIN_STATE_PATH) - 1:
        return None
    return MAIN_STATE_PATH[position + 1]


@dataclass
class AuditRun:
    id: UUID
    state: RunState
    version: int
    updated_at: datetime
    created_at: datetime | None = None
    completed_at: datetime | None = None
    cancel_requested_at: datetime | None = None
    repository_path: str = ""
    diff_mode: str = "WORKING_TREE"
    base_ref: str | None = None
    head_ref: str | None = None
    idempotency_key: str = ""
    request_fingerprint: str = ""
    cleanup_status: CleanupStatus = CleanupStatus.NOT_REQUIRED
    heartbeat_at: datetime | None = None
    failure_code: str | None = None
    failure_detail: str | None = None

    def transition(self, target: RunState, *, now: datetime) -> None:
        """Advance through an allowed edge and increment the optimistic version."""
        allowed = set(_NORMAL_TRANSITIONS.get(self.state, ()))
        if self.state not in TERMINAL_STATES:
            allowed.update({RunState.FAILED, RunState.CANCELLED})
        if target not in allowed:
            raise InvalidStateTransition(self.state, target)

        self.state = target
        self.version += 1
        self.updated_at = now
        if target in TERMINAL_STATES:
            self.completed_at = now


@dataclass
class RunStep:
    id: UUID
    run_id: UUID
    step_key: str
    from_state: RunState
    to_state: RunState
    generation: int
    attempt: int
    status: StepStatus
    expected_run_version: int
    handler_version: str
    started_at: datetime
    resulting_run_version: int | None = None
    worker_id: str | None = None
    claim_token: UUID | None = None
    heartbeat_at: datetime | None = None
    lease_expires_at: datetime | None = None
    finished_at: datetime | None = None
    error_code: str | None = None
    error_detail: str | None = None


@dataclass
class OutboxEvent:
    id: UUID
    aggregate_id: UUID
    aggregate_version: int
    topic: str
    payload: dict[str, Any]
    available_at: datetime
    created_at: datetime
    published_at: datetime | None = None
    publish_attempts: int = 0
    last_error: str | None = None
