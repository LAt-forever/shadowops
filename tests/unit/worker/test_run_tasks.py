from datetime import UTC, datetime
from uuid import UUID

from shadowops.domain.errors import RepositoryInputError
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
        self.failed = False

    def claim(self, event_id: UUID, *, worker_id: str) -> RunStep | None:
        return self._claim

    def heartbeat(self, claim: RunStep) -> bool:
        return True

    def get_run_for_claim(self, claim: RunStep) -> AuditRun:
        return AuditRun(
            id=RUN_ID,
            state=RunState.QUEUED,
            version=1,
            repository_path="projects/demo",
            created_at=NOW,
            updated_at=NOW,
        )

    def finalize(self, claim: RunStep) -> AuditRun:
        self.finalized = True
        return AuditRun(
            id=RUN_ID,
            state=RunState.DISCOVERING,
            version=2,
            created_at=NOW,
            updated_at=NOW,
        )

    def fail(self, claim: RunStep, *, error_code: str, error_detail: str) -> AuditRun:
        self.failed = True
        return AuditRun(
            id=RUN_ID,
            state=RunState.FAILED,
            version=2,
            failure_code=error_code,
            failure_detail=error_detail,
            created_at=NOW,
            updated_at=NOW,
        )


class FakeHandler:
    def __init__(self) -> None:
        self.executed = False

    def execute(self, run: AuditRun, *, checkpoint) -> None:
        self.executed = True
        checkpoint()


class FailingHandler:
    def execute(self, run: AuditRun, *, checkpoint) -> None:
        raise RepositoryInputError("REPOSITORY_NOT_FOUND", "Repository was not found")


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
    handler = FakeHandler()
    monkeypatch.setattr(tasks, "get_execution_service", lambda: service)
    monkeypatch.setattr(tasks, "get_stage_handlers", lambda: {RunState.DISCOVERING: handler})

    result = tasks.process_run_event.run(str(EVENT_ID))

    assert result == {
        "status": "completed",
        "event_id": str(EVENT_ID),
        "run_id": str(RUN_ID),
        "state": "DISCOVERING",
        "version": 2,
    }
    assert service.finalized is True
    assert handler.executed is True


def test_process_event_turns_trusted_input_error_into_reliable_failure(monkeypatch) -> None:
    service = FakeExecutionService(_claim())
    monkeypatch.setattr(tasks, "get_execution_service", lambda: service)
    monkeypatch.setattr(
        tasks, "get_stage_handlers", lambda: {RunState.DISCOVERING: FailingHandler()}
    )

    result = tasks.process_run_event.run(str(EVENT_ID))

    assert result == {
        "status": "failed",
        "event_id": str(EVENT_ID),
        "run_id": str(RUN_ID),
        "state": "FAILED",
        "version": 2,
        "error_code": "REPOSITORY_NOT_FOUND",
    }
    assert service.failed is True
