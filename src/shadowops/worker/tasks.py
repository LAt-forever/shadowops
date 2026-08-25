"""Celery tasks for reliable run execution."""

from typing import Any
from uuid import UUID

from shadowops.domain.errors import ClaimLostError
from shadowops.worker.celery_app import celery_app
from shadowops.worker.runtime import get_execution_service


@celery_app.task(  # type: ignore[untyped-decorator]
    bind=True,
    name="shadowops.runs.process_event",
    acks_late=True,
    reject_on_worker_lost=True,
)
def process_run_event(self: Any, event_id: str) -> dict[str, str | int]:
    """Claim and finalize one explicit M1 no-op stage."""
    service = get_execution_service()
    parsed_event_id = UUID(event_id)
    worker_id = getattr(self.request, "hostname", None) or "shadowops-worker"
    claim = service.claim(parsed_event_id, worker_id=str(worker_id))
    if claim is None:
        return {"status": "ignored", "event_id": event_id}
    if not service.heartbeat(claim):
        return {"status": "ignored", "event_id": event_id}
    try:
        run = service.finalize(claim)
    except ClaimLostError:
        return {"status": "ignored", "event_id": event_id}
    return {
        "status": "completed",
        "event_id": event_id,
        "run_id": str(run.id),
        "state": run.state.value,
        "version": run.version,
    }
