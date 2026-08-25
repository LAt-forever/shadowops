"""Framework-free audit run lifecycle."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
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
_NORMAL_TRANSITIONS[RunState.AWAITING_APPROVAL] = frozenset(
    {RunState.APPROVED, RunState.REJECTED}
)


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
