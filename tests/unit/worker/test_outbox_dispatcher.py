from datetime import UTC, datetime, timedelta
from uuid import UUID

from shadowops.domain.runs import OutboxEvent
from shadowops.worker.outbox import CeleryEventPublisher, OutboxDispatcher

NOW = datetime(2026, 8, 25, 4, 0, tzinfo=UTC)


def _event(number: int) -> OutboxEvent:
    return OutboxEvent(
        id=UUID(f"00000000-0000-4000-8000-{number:012d}"),
        aggregate_id=UUID(f"10000000-0000-4000-8000-{number:012d}"),
        aggregate_version=1,
        topic="run.advance.requested.v1",
        payload={"run_id": f"run-{number}"},
        available_at=NOW,
        created_at=NOW,
    )


class MemoryOutboxRepository:
    def __init__(self, events: list[OutboxEvent]) -> None:
        self.events = {event.id: event for event in events}

    def lock_unpublished(self, *, now: datetime, limit: int) -> list[OutboxEvent]:
        return [
            event
            for event in self.events.values()
            if event.published_at is None and event.available_at <= now
        ][:limit]

    def mark_published(self, event_id: UUID, *, published_at: datetime) -> bool:
        event = self.events[event_id]
        event.published_at = published_at
        event.publish_attempts += 1
        event.last_error = None
        return True

    def mark_failed(self, event_id: UUID, *, error: str, available_at: datetime) -> bool:
        event = self.events[event_id]
        event.publish_attempts += 1
        event.last_error = error
        event.available_at = available_at
        return True


class MemoryUnitOfWork:
    def __init__(self, repository: MemoryOutboxRepository) -> None:
        self.outbox = repository
        self.committed = False

    def __enter__(self) -> "MemoryUnitOfWork":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def commit(self) -> None:
        self.committed = True


class RecordingPublisher:
    def __init__(self, failing_id: UUID | None = None) -> None:
        self.failing_id = failing_id
        self.published: list[UUID] = []

    def publish(self, event: OutboxEvent) -> None:
        if event.id == self.failing_id:
            raise ConnectionError("broker offline")
        self.published.append(event.id)


def test_dispatcher_marks_success_only_after_publish() -> None:
    event = _event(1)
    repository = MemoryOutboxRepository([event])
    publisher = RecordingPublisher()
    uow = MemoryUnitOfWork(repository)
    dispatcher = OutboxDispatcher(
        lambda: uow,
        publisher,
        clock=lambda: NOW,
        retry_base=timedelta(seconds=2),
        retry_max=timedelta(seconds=30),
    )

    published = dispatcher.dispatch_batch(limit=10)

    assert published == 1
    assert publisher.published == [event.id]
    assert event.published_at == NOW
    assert event.publish_attempts == 1
    assert uow.committed is True


def test_dispatcher_failure_is_backed_off_without_blocking_other_events() -> None:
    failing = _event(1)
    succeeding = _event(2)
    repository = MemoryOutboxRepository([failing, succeeding])
    publisher = RecordingPublisher(failing.id)
    dispatcher = OutboxDispatcher(
        lambda: MemoryUnitOfWork(repository),
        publisher,
        clock=lambda: NOW,
        retry_base=timedelta(seconds=2),
        retry_max=timedelta(seconds=30),
    )

    published = dispatcher.dispatch_batch(limit=10)

    assert published == 1
    assert failing.published_at is None
    assert failing.publish_attempts == 1
    assert failing.last_error == "broker offline"
    assert failing.available_at == NOW + timedelta(seconds=2)
    assert succeeding.published_at == NOW


class FakeCeleryApp:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def send_task(self, name: str, **options: object) -> None:
        self.calls.append({"name": name, **options})


def test_celery_publisher_uses_event_id_as_the_stable_task_id() -> None:
    app = FakeCeleryApp()
    event = _event(7)

    CeleryEventPublisher(app).publish(event)

    assert app.calls == [
        {
            "name": "shadowops.runs.process_event",
            "args": [str(event.id)],
            "task_id": str(event.id),
        }
    ]
