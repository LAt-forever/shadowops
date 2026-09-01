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
            current = uow.steps.get_current(run.id)
            if (
                run.cancel_requested_at is not None
                and current is not None
                and current.expected_run_version == run.version
                and current.lease_expires_at is not None
                and current.lease_expires_at > now
            ):
                # The in-flight owner observes cancellation through its heartbeat
                # checkpoint. A redelivered outbox event must not create a second
                # CANCELLED step that finalizes Docker resources underneath it.
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
                handler_version=self._handler_version(target),
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

    def get_run_for_claim(self, claim: RunStep) -> AuditRun:
        """Load the exact aggregate version fenced by a claimed step."""
        with self._uow_factory() as uow:
            run = uow.runs.get(claim.run_id)
        if run is None or run.version != claim.expected_run_version:
            raise ClaimLostError(claim.id)
        return run

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

    def fail(self, claim: RunStep, *, error_code: str, error_detail: str) -> AuditRun:
        """Atomically fence a failed step and terminate its run."""
        if claim.claim_token is None:
            raise ClaimLostError(claim.id)
        now = self._clock()
        with self._uow_factory() as uow:
            run = uow.runs.get(claim.run_id)
            if run is None or run.version != claim.expected_run_version:
                raise ClaimLostError(claim.id)
            resulting_version = run.version + 1
            if not uow.steps.fail(
                claim.id,
                claim_token=claim.claim_token,
                resulting_run_version=resulting_version,
                finished_at=now,
                error_code=error_code,
                error_detail=error_detail,
            ):
                raise ClaimLostError(claim.id)
            expected_version = run.version
            run.failure_code = error_code
            run.failure_detail = error_detail
            run.transition(RunState.FAILED, now=now)
            uow.runs.save(run, expected_version=expected_version)
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

    @staticmethod
    def _handler_version(target: RunState) -> str:
        if target is RunState.DISCOVERING:
            return "m2.discovery.v1"
        if target is RunState.STATIC_ANALYSIS:
            return "m2.static-analysis.v1"
        if target is RunState.PLANNING:
            return "m3.planning.v1"
        if target is RunState.PROVISIONING:
            return "m4.provision-shadow.v1"
        if target is RunState.BASELINE_READY:
            return "m4.baseline-upgrade.v1"
        if target is RunState.APPLYING:
            return "m4.apply-target.v1"
        if target is RunState.SEEDING:
            return "m5.seed-data.v1"
        if target is RunState.SMOKE_TESTING:
            return "m5.smoke-checks.v1"
        if target is RunState.ROLLBACK_VERIFYING:
            return "m5.rollback-roundtrip.v1"
        if target is RunState.REPORTING:
            return "m5.collect-evidence.v1"
        return "m1.noop.v1"
