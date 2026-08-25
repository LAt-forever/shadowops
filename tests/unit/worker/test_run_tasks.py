from datetime import UTC, datetime
from uuid import UUID

from shadowops.domain.runs import AuditRun, RunState, RunStep, StepStatus
from shadowops.worker import tasks

EVENT_ID = UUID("22222222-2222-4222-8222-222222222222")
RUN_ID = UUID("11111111-1111-4111-8111-111111111111")
STEP_ID = UUID("33333333-3333-4333-8333-333333333333")
TOKEN = UUID("44444444-4444-4444-8444-444444444444")
NOW = datetime(2026, 8, 25, 3, 0, tzinfo=UTC)


class FakeExecutionService:
    def __init__(self, claim: RunStep | None) -> None:
        self._claim = claim
        self.finalized = False

    def claim(self, event_id: UUID, *, worker_id: str) -> RunStep | None:
        return self._claim

    def heartbeat(self, claim: RunStep) -> bool:
        return True

    def finalize(self, claim: RunStep) -> AuditRun:
        self.finalized = True
        return AuditRun(
            id=RUN_ID,
            state=RunState.DISCOVERING,
            version=2,
            created_at=NOW,
            updated_at=NOW,
        )


def _claim() -> RunStep:
    return RunStep(
        id=STEP_ID,
        run_id=RUN_ID,
        step_key=f"{RUN_ID}:DISCOVERING:1",
        from_state=RunState.QUEUED,
        to_state=RunState.DISCOVERING,
        generation=1,
        attempt=1,
        status=StepStatus.RUNNING,
        expected_run_version=1,
        handler_version="m1.noop.v1",
        worker_id="worker-a",
        claim_token=TOKEN,
        started_at=NOW,
    )


def test_process_event_reports_ignored_when_no_step_is_claimed(monkeypatch) -> None:
    service = FakeExecutionService(None)
    monkeypatch.setattr(tasks, "get_execution_service", lambda: service)

    result = tasks.process_run_event.run(str(EVENT_ID))

    assert result == {"status": "ignored", "event_id": str(EVENT_ID)}
    assert service.finalized is False


def test_process_event_finalizes_the_claimed_noop_stage(monkeypatch) -> None:
    service = FakeExecutionService(_claim())
    monkeypatch.setattr(tasks, "get_execution_service", lambda: service)

    result = tasks.process_run_event.run(str(EVENT_ID))

    assert result == {
        "status": "completed",
        "event_id": str(EVENT_ID),
        "run_id": str(RUN_ID),
        "state": "DISCOVERING",
        "version": 2,
    }
    assert service.finalized is True
