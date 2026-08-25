from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from shadowops.domain.errors import (
    InvalidStateTransition,
    OptimisticConcurrencyError,
    TerminalRunError,
)
from shadowops.domain.runs import MAIN_STATE_PATH, AuditRun, RunState


def test_run_advances_only_through_the_main_state_path() -> None:
    started_at = datetime(2026, 8, 25, 1, 0, tzinfo=UTC)
    run = AuditRun(id=uuid4(), state=RunState.QUEUED, version=1, updated_at=started_at)

    for offset, target in enumerate(MAIN_STATE_PATH[1:], start=1):
        now = started_at + timedelta(seconds=offset)
        run.transition(target, now=now)

        assert run.state is target
        assert run.version == offset + 1
        assert run.updated_at == now

    assert run.state is RunState.COMPLETED
    assert run.completed_at == started_at + timedelta(seconds=len(MAIN_STATE_PATH) - 1)


def test_run_rejects_an_illegal_state_jump_without_mutating() -> None:
    updated_at = datetime(2026, 8, 25, 1, 0, tzinfo=UTC)
    run = AuditRun(id=uuid4(), state=RunState.QUEUED, version=7, updated_at=updated_at)

    with pytest.raises(InvalidStateTransition) as error:
        run.transition(RunState.COMPLETED, now=updated_at + timedelta(seconds=1))

    assert error.value.code == "ILLEGAL_STATE_TRANSITION"
    assert run.state is RunState.QUEUED
    assert run.version == 7
    assert run.updated_at == updated_at


@pytest.mark.parametrize("target", [RunState.FAILED, RunState.CANCELLED])
def test_any_nonterminal_state_can_stop_safely(target: RunState) -> None:
    now = datetime(2026, 8, 25, 1, 0, tzinfo=UTC)
    run = AuditRun(id=uuid4(), state=RunState.APPLYING, version=4, updated_at=now)

    run.transition(target, now=now + timedelta(seconds=1))

    assert run.state is target
    assert run.version == 5
    assert run.completed_at == now + timedelta(seconds=1)


@pytest.mark.parametrize(
    "terminal",
    [RunState.COMPLETED, RunState.FAILED, RunState.CANCELLED, RunState.APPROVED, RunState.REJECTED],
)
def test_terminal_state_cannot_be_left(terminal: RunState) -> None:
    now = datetime(2026, 8, 25, 1, 0, tzinfo=UTC)
    run = AuditRun(id=uuid4(), state=terminal, version=3, updated_at=now, completed_at=now)

    with pytest.raises(InvalidStateTransition):
        run.transition(RunState.DISCOVERING, now=now + timedelta(seconds=1))


def test_cancel_request_is_optimistic_without_advancing_the_state_version() -> None:
    now = datetime(2026, 8, 25, 1, 0, tzinfo=UTC)
    requested_at = now + timedelta(seconds=1)
    run = AuditRun(id=uuid4(), state=RunState.APPLYING, version=4, updated_at=now)

    run.request_cancel(expected_version=4, now=requested_at)

    assert run.state is RunState.APPLYING
    assert run.version == 4
    assert run.cancel_requested_at == requested_at
    assert run.updated_at == requested_at


def test_cancel_request_rejects_a_stale_expected_version() -> None:
    now = datetime(2026, 8, 25, 1, 0, tzinfo=UTC)
    run = AuditRun(id=uuid4(), state=RunState.APPLYING, version=4, updated_at=now)

    with pytest.raises(OptimisticConcurrencyError):
        run.request_cancel(expected_version=3, now=now)


def test_cancel_request_is_idempotent_after_cancellation() -> None:
    now = datetime(2026, 8, 25, 1, 0, tzinfo=UTC)
    run = AuditRun(
        id=uuid4(),
        state=RunState.CANCELLED,
        version=5,
        updated_at=now,
        completed_at=now,
        cancel_requested_at=now,
    )

    run.request_cancel(expected_version=1, now=now + timedelta(seconds=1))

    assert run.version == 5
    assert run.cancel_requested_at == now


def test_cancel_request_rejects_a_normally_completed_run() -> None:
    now = datetime(2026, 8, 25, 1, 0, tzinfo=UTC)
    run = AuditRun(
        id=uuid4(), state=RunState.COMPLETED, version=12, updated_at=now, completed_at=now
    )

    with pytest.raises(TerminalRunError) as error:
        run.request_cancel(expected_version=12, now=now)

    assert error.value.code == "RUN_TERMINAL"
