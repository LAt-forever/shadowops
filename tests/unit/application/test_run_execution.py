from copy import deepcopy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID

import pytest

from shadowops.application.run_execution import RunExecutionService
from shadowops.domain.errors import ClaimLostError, OptimisticConcurrencyError
from shadowops.domain.runs import AuditRun, OutboxEvent, RunState, RunStep, StepStatus

RUN_ID = UUID("11111111-1111-4111-8111-111111111111")
EVENT_ID = UUID("22222222-2222-4222-8222-222222222222")
STEP_ID = UUID("33333333-3333-4333-8333-333333333333")
FIRST_TOKEN = UUID("44444444-4444-4444-8444-444444444444")
SECOND_TOKEN = UUID("55555555-5555-4555-8555-555555555555")
NEXT_EVENT_ID = UUID("66666666-6666-4666-8666-666666666666")
STARTED_AT = datetime(2026, 8, 25, 3, 0, tzinfo=UTC)


class MemoryStore:
    def __init__(self) -> None:
        self.runs: dict[UUID, AuditRun] = {}
        self.steps: dict[str, RunStep] = {}
        self.events: dict[UUID, OutboxEvent] = {}
        self.report_requires_approval = False


class MemoryRunRepository:
    def __init__(self, store: MemoryStore) -> None:
        self._store = store

    def get(self, run_id: UUID) -> AuditRun | None:
        run = self._store.runs.get(run_id)
        return None if run is None else deepcopy(run)

    def save(self, run: AuditRun, *, expected_version: int) -> None:
        current = self._store.runs[run.id]
        if current.version != expected_version:
            raise OptimisticConcurrencyError(run.id, expected_version)
        self._store.runs[run.id] = deepcopy(run)


class MemoryStepRepository:
    def __init__(self, store: MemoryStore) -> None:
        self._store = store

    def claim(self, candidate: RunStep) -> RunStep | None:
        existing = self._store.steps.get(candidate.step_key)
        if existing is None:
            self._store.steps[candidate.step_key] = deepcopy(candidate)
            return deepcopy(candidate)
        if existing.status is not StepStatus.RUNNING:
            return None
        if (
            existing.lease_expires_at is not None
            and existing.lease_expires_at > candidate.started_at
        ):
            return None
        existing.attempt += 1
        existing.worker_id = candidate.worker_id
        existing.claim_token = candidate.claim_token
        existing.heartbeat_at = candidate.heartbeat_at
        existing.lease_expires_at = candidate.lease_expires_at
        return deepcopy(existing)

    def get_current(self, run_id: UUID) -> RunStep | None:
        matches = [
            step
            for step in self._store.steps.values()
            if step.run_id == run_id and step.status is StepStatus.RUNNING
        ]
        return None if not matches else deepcopy(matches[-1])

    def heartbeat(
        self,
        step_id: UUID,
        *,
        claim_token: UUID,
        heartbeat_at: datetime,
        lease_expires_at: datetime,
    ) -> bool:
        step = next(item for item in self._store.steps.values() if item.id == step_id)
        if step.claim_token != claim_token or step.status is not StepStatus.RUNNING:
            return False
        step.heartbeat_at = heartbeat_at
        step.lease_expires_at = lease_expires_at
        return True

    def complete(
        self,
        step_id: UUID,
        *,
        claim_token: UUID,
        resulting_run_version: int,
        finished_at: datetime,
        final_state: RunState,
    ) -> bool:
        step = next(item for item in self._store.steps.values() if item.id == step_id)
        if step.claim_token != claim_token or step.status is not StepStatus.RUNNING:
            return False
        step.status = StepStatus.SUCCEEDED
        step.to_state = final_state
        step.resulting_run_version = resulting_run_version
        step.finished_at = finished_at
        step.lease_expires_at = None
        return True

    def fail(
        self,
        step_id: UUID,
        *,
        claim_token: UUID,
        resulting_run_version: int,
        finished_at: datetime,
        error_code: str,
        error_detail: str,
    ) -> bool:
        step = next(item for item in self._store.steps.values() if item.id == step_id)
        if step.claim_token != claim_token or step.status is not StepStatus.RUNNING:
            return False
        step.status = StepStatus.FAILED
        step.to_state = RunState.FAILED
        step.resulting_run_version = resulting_run_version
        step.finished_at = finished_at
        step.lease_expires_at = None
        step.error_code = error_code
        step.error_detail = error_detail
        return True


class MemoryOutboxRepository:
    def __init__(self, store: MemoryStore) -> None:
        self._store = store

    def get(self, event_id: UUID) -> OutboxEvent | None:
        event = self._store.events.get(event_id)
        return None if event is None else deepcopy(event)

    def add(self, event: OutboxEvent) -> None:
        self._store.events[event.id] = deepcopy(event)


class MemoryUnitOfWork:
    def __init__(self, store: MemoryStore) -> None:
        self.runs = MemoryRunRepository(store)
        self.steps = MemoryStepRepository(store)
        self.outbox = MemoryOutboxRepository(store)
        self.risk_reports = SimpleNamespace(
            get_for_run=lambda run_id: (
                SimpleNamespace(requires_approval=True) if store.report_requires_approval else None
            )
        )

    def __enter__(self) -> "MemoryUnitOfWork":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def commit(self) -> None:
        return None


def _store() -> MemoryStore:
    store = MemoryStore()
    store.runs[RUN_ID] = AuditRun(
        id=RUN_ID,
        state=RunState.QUEUED,
        version=1,
        repository_path="projects/demo",
        idempotency_key="request-1",
        request_fingerprint="a" * 64,
        created_at=STARTED_AT,
        updated_at=STARTED_AT,
    )
    store.events[EVENT_ID] = OutboxEvent(
        id=EVENT_ID,
        aggregate_id=RUN_ID,
        aggregate_version=1,
        topic="run.advance.requested.v1",
        payload={"run_id": str(RUN_ID), "expected_state": "QUEUED", "expected_version": 1},
        available_at=STARTED_AT,
        created_at=STARTED_AT,
    )
    return store


def _service(store: MemoryStore, now: datetime, ids: list[UUID]) -> RunExecutionService:
    identifiers = iter(ids)
    return RunExecutionService(
        lambda: MemoryUnitOfWork(store),
        clock=lambda: now,
        uuid_factory=lambda: next(identifiers),
        lease_duration=timedelta(seconds=10),
    )


def test_claim_and_finalize_advance_exactly_one_state_and_enqueue_the_next_event() -> None:
    store = _store()
    service = _service(store, STARTED_AT, [STEP_ID, FIRST_TOKEN, NEXT_EVENT_ID])

    claim = service.claim(EVENT_ID, worker_id="worker-a")
    assert claim is not None
    assert claim.status is StepStatus.RUNNING
    assert claim.to_state is RunState.DISCOVERING
    assert claim.claim_token == FIRST_TOKEN

    run = service.finalize(claim)

    assert run.state is RunState.DISCOVERING
    assert run.version == 2
    stored_step = store.steps[claim.step_key]
    assert stored_step.status is StepStatus.SUCCEEDED
    assert stored_step.resulting_run_version == 2
    next_events = [event for event in store.events.values() if event.id != EVENT_ID]
    assert len(next_events) == 1
    assert next_events[0].payload == {
        "run_id": str(RUN_ID),
        "expected_state": "DISCOVERING",
        "expected_version": 2,
    }


def test_static_analysis_claim_uses_the_real_m2_handler_version() -> None:
    store = _store()
    store.runs[RUN_ID].state = RunState.DISCOVERING
    store.runs[RUN_ID].version = 2
    store.events[EVENT_ID].aggregate_version = 2
    store.events[EVENT_ID].payload = {
        "run_id": str(RUN_ID),
        "expected_state": "DISCOVERING",
        "expected_version": 2,
    }

    claim = _service(store, STARTED_AT, [STEP_ID, FIRST_TOKEN]).claim(
        EVENT_ID, worker_id="worker-a"
    )

    assert claim is not None
    assert claim.to_state is RunState.STATIC_ANALYSIS
    assert claim.handler_version == "m2.static-analysis.v1"


def test_planning_claim_uses_the_bounded_m3_handler_version() -> None:
    store = _store()
    store.runs[RUN_ID].state = RunState.STATIC_ANALYSIS
    store.runs[RUN_ID].version = 3
    store.events[EVENT_ID].aggregate_version = 3
    store.events[EVENT_ID].payload = {
        "run_id": str(RUN_ID),
        "expected_state": "STATIC_ANALYSIS",
        "expected_version": 3,
    }

    claim = _service(store, STARTED_AT, [STEP_ID, FIRST_TOKEN]).claim(
        EVENT_ID, worker_id="worker-a"
    )

    assert claim is not None
    assert claim.to_state is RunState.PLANNING
    assert claim.handler_version == "m3.planning.v1"


def test_duplicate_message_after_finalize_is_an_idempotent_noop() -> None:
    store = _store()
    service = _service(store, STARTED_AT, [STEP_ID, FIRST_TOKEN, NEXT_EVENT_ID])
    claim = service.claim(EVENT_ID, worker_id="worker-a")
    assert claim is not None
    service.finalize(claim)

    duplicate = _service(store, STARTED_AT, [SECOND_TOKEN]).claim(EVENT_ID, worker_id="worker-b")

    assert duplicate is None
    assert len(store.steps) == 1
    assert len(store.events) == 2


def test_expired_claim_is_reclaimed_with_a_new_fencing_token() -> None:
    store = _store()
    first_service = _service(store, STARTED_AT, [STEP_ID, FIRST_TOKEN])
    first = first_service.claim(EVENT_ID, worker_id="worker-a")
    assert first is not None
    second_service = _service(store, STARTED_AT + timedelta(seconds=11), [STEP_ID, SECOND_TOKEN])

    second = second_service.claim(EVENT_ID, worker_id="worker-b")

    assert second is not None
    assert second.id == first.id
    assert second.attempt == 2
    assert second.claim_token == SECOND_TOKEN
    assert second.worker_id == "worker-b"


def test_reclaimed_step_fences_the_old_worker_out_of_finalize() -> None:
    store = _store()
    first_service = _service(store, STARTED_AT, [STEP_ID, FIRST_TOKEN])
    first = first_service.claim(EVENT_ID, worker_id="worker-a")
    assert first is not None
    second = _service(store, STARTED_AT + timedelta(seconds=11), [STEP_ID, SECOND_TOKEN]).claim(
        EVENT_ID, worker_id="worker-b"
    )
    assert second is not None

    with pytest.raises(ClaimLostError) as error:
        first_service.finalize(first)

    assert error.value.code == "STEP_CLAIM_LOST"
    assert store.runs[RUN_ID].state is RunState.QUEUED


def test_heartbeat_renews_only_the_current_claim() -> None:
    store = _store()
    service = _service(store, STARTED_AT, [STEP_ID, FIRST_TOKEN])
    claim = service.claim(EVENT_ID, worker_id="worker-a")
    assert claim is not None

    renewed = service.heartbeat(claim)
    stale = deepcopy(claim)
    stale.claim_token = SECOND_TOKEN

    assert renewed is True
    assert service.heartbeat(stale) is False


def test_cancel_requested_during_execution_wins_at_finalize_checkpoint() -> None:
    store = _store()
    service = _service(store, STARTED_AT, [STEP_ID, FIRST_TOKEN])
    claim = service.claim(EVENT_ID, worker_id="worker-a")
    assert claim is not None
    store.runs[RUN_ID].cancel_requested_at = STARTED_AT

    run = service.finalize(claim)

    assert run.state is RunState.CANCELLED
    assert store.steps[claim.step_key].to_state is RunState.CANCELLED
    assert len(store.events) == 1


def test_cancel_redelivery_does_not_claim_beside_an_active_stage() -> None:
    store = _store()
    service = _service(store, STARTED_AT, [STEP_ID, FIRST_TOKEN])
    claim = service.claim(EVENT_ID, worker_id="worker-a")
    assert claim is not None
    store.runs[RUN_ID].cancel_requested_at = STARTED_AT

    duplicate = _service(store, STARTED_AT, []).claim(EVENT_ID, worker_id="worker-b")

    assert duplicate is None
    assert list(store.steps) == [claim.step_key]


def test_fail_fences_step_and_terminates_run_without_next_event() -> None:
    store = _store()
    service = _service(store, STARTED_AT, [STEP_ID, FIRST_TOKEN])
    claim = service.claim(EVENT_ID, worker_id="worker-a")
    assert claim is not None

    run = service.fail(
        claim,
        error_code="REPOSITORY_NOT_FOUND",
        error_detail="Repository was not found",
    )

    assert run.state is RunState.FAILED
    assert run.version == 2
    assert run.failure_code == "REPOSITORY_NOT_FOUND"
    step = store.steps[claim.step_key]
    assert step.status is StepStatus.FAILED
    assert step.error_code == "REPOSITORY_NOT_FOUND"
    assert len(store.events) == 1


def test_high_risk_report_routes_completion_to_awaiting_approval() -> None:
    store = _store()
    store.report_requires_approval = True
    store.runs[RUN_ID].state = RunState.REPORTING
    store.runs[RUN_ID].version = 11
    store.events[EVENT_ID].aggregate_version = 11
    store.events[EVENT_ID].payload = {
        "run_id": str(RUN_ID),
        "expected_state": "REPORTING",
        "expected_version": 11,
    }
    service = _service(store, STARTED_AT, [STEP_ID, FIRST_TOKEN])

    claim = service.claim(EVENT_ID, worker_id="worker-a")
    assert claim is not None
    assert claim.to_state is RunState.COMPLETED
    assert claim.handler_version == "m6.risk-reporting.v1"

    run = service.finalize(claim)

    assert run.state is RunState.AWAITING_APPROVAL
    assert store.steps[claim.step_key].to_state is RunState.AWAITING_APPROVAL
