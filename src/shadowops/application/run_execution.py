"""Reliable claim, heartbeat, and finalize use cases for one run stage."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from shadowops.application.ports import UnitOfWork
from shadowops.domain.errors import ClaimLostError
from shadowops.domain.runs import (
    TERMINAL_STATES,
    AuditRun,
    OutboxEvent,
    RunState,
    RunStep,
    StepStatus,
    next_main_state,
)


class RunExecutionService:
    def __init__(
        self,
        uow_factory: Callable[[], UnitOfWork],
        *,
        clock: Callable[[], datetime] | None = None,
        uuid_factory: Callable[[], UUID] | None = None,
        lease_duration: timedelta = timedelta(seconds=30),
    ) -> None:
        self._uow_factory = uow_factory
        self._clock = clock or (lambda: datetime.now(UTC))
        self._uuid_factory = uuid_factory or uuid4
        self._lease_duration = lease_duration

    def claim(self, event_id: UUID, *, worker_id: str) -> RunStep | None:
        now = self._clock()
        with self._uow_factory() as uow:
            event = uow.outbox.get(event_id)
            if event is None or event.topic != "run.advance.requested.v1":
                return None
            run = uow.runs.get(event.aggregate_id)
            if run is None or run.state in TERMINAL_STATES:
                return None
            expected_state = RunState(str(event.payload.get("expected_state")))
            expected_version = int(str(event.payload.get("expected_version")))
            if run.state is not expected_state or run.version != expected_version:
                return None
            target = (
                RunState.CANCELLED
                if run.cancel_requested_at is not None
                else next_main_state(run.state)
            )
            if target is None:
                return None

            candidate = RunStep(
                id=self._uuid_factory(),
                run_id=run.id,
                step_key=f"{run.id}:{target.value}:1",
                from_state=run.state,
                to_state=target,
                generation=1,
                attempt=1,
                status=StepStatus.RUNNING,
                expected_run_version=run.version,
                handler_version="m1.noop.v1",
                worker_id=worker_id,
                claim_token=self._uuid_factory(),
                heartbeat_at=now,
                lease_expires_at=now + self._lease_duration,
                started_at=now,
            )
            claim = uow.steps.claim(candidate)
            if claim is not None:
                uow.commit()
            return claim

    def heartbeat(self, claim: RunStep) -> bool:
        if claim.claim_token is None:
            return False
        now = self._clock()
        with self._uow_factory() as uow:
            renewed = uow.steps.heartbeat(
                claim.id,
                claim_token=claim.claim_token,
                heartbeat_at=now,
                lease_expires_at=now + self._lease_duration,
            )
            if renewed:
                uow.commit()
            return renewed

    def finalize(self, claim: RunStep) -> AuditRun:
        if claim.claim_token is None:
            raise ClaimLostError(claim.id)
        now = self._clock()
        with self._uow_factory() as uow:
            run = uow.runs.get(claim.run_id)
            if run is None or run.version != claim.expected_run_version:
                raise ClaimLostError(claim.id)
            final_state = (
                RunState.CANCELLED if run.cancel_requested_at is not None else claim.to_state
            )
            resulting_version = run.version + 1
            if not uow.steps.complete(
                claim.id,
                claim_token=claim.claim_token,
                resulting_run_version=resulting_version,
                finished_at=now,
                final_state=final_state,
            ):
                raise ClaimLostError(claim.id)

            expected_version = run.version
            run.transition(final_state, now=now)
            uow.runs.save(run, expected_version=expected_version)
            if run.state not in TERMINAL_STATES:
                uow.outbox.add(self._next_event(run, now))
            uow.commit()
            return run

    def _next_event(self, run: AuditRun, now: datetime) -> OutboxEvent:
        return OutboxEvent(
            id=self._uuid_factory(),
            aggregate_id=run.id,
            aggregate_version=run.version,
            topic="run.advance.requested.v1",
            payload={
                "run_id": str(run.id),
                "expected_state": run.state.value,
                "expected_version": run.version,
            },
            available_at=now,
            created_at=now,
        )
