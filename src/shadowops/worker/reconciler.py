"""Recovery of stale outbox deliveries and expired run steps."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from shadowops.application.ports import UnitOfWork
from shadowops.domain.runs import RunState, RunStep, StepStatus


@dataclass(frozen=True)
class ReconcileResult:
    reopened: int
    failed: int


class RunReconciler:
    def __init__(
        self,
        uow_factory: Callable[[], UnitOfWork],
        *,
        clock: Callable[[], datetime] | None = None,
        uuid_factory: Callable[[], UUID] | None = None,
        stale_after: timedelta = timedelta(seconds=10),
        max_attempts: int = 5,
    ) -> None:
        self._uow_factory = uow_factory
        self._clock = clock or (lambda: datetime.now(UTC))
        self._uuid_factory = uuid_factory or uuid4
        self._stale_after = stale_after
        self._max_attempts = max_attempts

    def reconcile_batch(self, *, limit: int) -> ReconcileResult:
        now = self._clock()
        reopened = 0
        failed = 0
        with self._uow_factory() as uow:
            events = uow.outbox.lock_stale_deliveries(
                now=now, stale_before=now - self._stale_after, limit=limit
            )
            for event in events:
                run = uow.runs.get(event.aggregate_id)
                if run is None or run.version != event.aggregate_version:
                    continue
                if event.publish_attempts >= self._max_attempts:
                    source_state = run.state
                    expected_version = run.version
                    run.failure_code = "RECOVERY_EXHAUSTED"
                    run.failure_detail = "Stale delivery exceeded the configured recovery budget"
                    run.transition(RunState.FAILED, now=now)
                    uow.steps.add(
                        RunStep(
                            id=self._uuid_factory(),
                            run_id=run.id,
                            step_key=f"{run.id}:FAILED:recovery:{expected_version}",
                            from_state=source_state,
                            to_state=RunState.FAILED,
                            generation=1,
                            attempt=event.publish_attempts,
                            status=StepStatus.FAILED,
                            expected_run_version=expected_version,
                            resulting_run_version=run.version,
                            handler_version="m1.reconciler.v1",
                            started_at=now,
                            finished_at=now,
                            error_code=run.failure_code,
                            error_detail=run.failure_detail,
                        )
                    )
                    uow.runs.save(run, expected_version=expected_version)
                    failed += 1
                elif uow.outbox.reopen(
                    event.id,
                    available_at=now,
                    reason="RECOVERED_STALE_DELIVERY",
                ):
                    reopened += 1
            uow.commit()
        return ReconcileResult(reopened=reopened, failed=failed)
