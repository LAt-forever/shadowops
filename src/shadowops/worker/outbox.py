"""Transactional outbox dispatch to Celery."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from shadowops.application.ports import UnitOfWork
from shadowops.domain.runs import OutboxEvent


class EventPublisher(Protocol):
    def publish(self, event: OutboxEvent) -> None: ...


class CeleryEventPublisher:
    def __init__(self, celery_app: Any) -> None:
        self._celery_app = celery_app

    def publish(self, event: OutboxEvent) -> None:
        self._celery_app.send_task(
            "shadowops.runs.process_event",
            args=[str(event.id)],
            task_id=str(event.id),
        )


class OutboxDispatcher:
    def __init__(
        self,
        uow_factory: Callable[[], UnitOfWork],
        publisher: EventPublisher,
        *,
        clock: Callable[[], datetime] | None = None,
        retry_base: timedelta = timedelta(seconds=1),
        retry_max: timedelta = timedelta(seconds=30),
    ) -> None:
        self._uow_factory = uow_factory
        self._publisher = publisher
        self._clock = clock or (lambda: datetime.now(UTC))
        self._retry_base = retry_base
        self._retry_max = retry_max

    def dispatch_batch(self, *, limit: int) -> int:
        now = self._clock()
        published = 0
        with self._uow_factory() as uow:
            events = uow.outbox.lock_unpublished(now=now, limit=limit)
            for event in events:
                try:
                    self._publisher.publish(event)
                except Exception as error:
                    uow.outbox.mark_failed(
                        event.id,
                        error=str(error)[:1000],
                        available_at=now + self._retry_delay(event.publish_attempts),
                    )
                else:
                    if uow.outbox.mark_published(event.id, published_at=now):
                        published += 1
            uow.commit()
        return published

    def _retry_delay(self, prior_attempts: int) -> timedelta:
        seconds = min(
            self._retry_max.total_seconds(),
            self._retry_base.total_seconds() * (2**prior_attempts),
        )
        return timedelta(seconds=seconds)
