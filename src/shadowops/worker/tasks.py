"""Celery tasks for reliable run execution."""

from typing import Any
from uuid import UUID

from shadowops.domain.errors import ClaimLostError, RepositoryInputError
from shadowops.worker.celery_app import celery_app
from shadowops.worker.runtime import (
    get_execution_service,
    get_outbox_dispatcher,
    get_run_reconciler,
    get_stage_handlers,
)


class RunCancellationRequested(Exception):
    """Internal control signal raised at cooperative stage checkpoints."""


@celery_app.task(name="shadowops.maintenance.dispatch_outbox")  # type: ignore[untyped-decorator]
def dispatch_outbox() -> dict[str, int]:
    settings = celery_app.conf
    limit = int(settings.get("shadowops_outbox_batch_size", 50))
    return {"published": get_outbox_dispatcher().dispatch_batch(limit=limit)}


@celery_app.task(name="shadowops.maintenance.reconcile_runs")  # type: ignore[untyped-decorator]
def reconcile_runs() -> dict[str, int]:
    settings = celery_app.conf
    limit = int(settings.get("shadowops_reconcile_batch_size", 50))
    result = get_run_reconciler().reconcile_batch(limit=limit)
    return {"reopened": result.reopened, "failed": result.failed}


@celery_app.task(  # type: ignore[untyped-decorator]
    bind=True,
    name="shadowops.runs.process_event",
    acks_late=True,
    reject_on_worker_lost=True,
)
def process_run_event(self: Any, event_id: str) -> dict[str, str | int]:
    """Execute one fenced stage and advance or fail its audit run."""
    service = get_execution_service()
    parsed_event_id = UUID(event_id)
    worker_id = getattr(self.request, "hostname", None) or "shadowops-worker"
    claim = service.claim(parsed_event_id, worker_id=str(worker_id))
    if claim is None:
        return {"status": "ignored", "event_id": event_id}
    if not service.heartbeat(claim):
        return {"status": "ignored", "event_id": event_id}
    try:
        run = service.get_run_for_claim(claim)
        if run.cancel_requested_at is None:
            handler = get_stage_handlers()[claim.to_state]

            def checkpoint() -> None:
                if not service.heartbeat(claim):
                    raise ClaimLostError(claim.id)
                if service.get_run_for_claim(claim).cancel_requested_at is not None:
                    raise RunCancellationRequested

            handler.execute(run, checkpoint=checkpoint)
            checkpoint()
        run = service.finalize(claim)
    except RunCancellationRequested:
        try:
            run = service.finalize(claim)
        except ClaimLostError:
            return {"status": "ignored", "event_id": event_id}
    except RepositoryInputError as error:
        try:
            run = service.fail(claim, error_code=error.code, error_detail=str(error))
        except ClaimLostError:
            return {"status": "ignored", "event_id": event_id}
        return {
            "status": "failed",
            "event_id": event_id,
            "run_id": str(run.id),
            "state": run.state.value,
            "version": run.version,
            "error_code": error.code,
        }
    except ClaimLostError:
        return {"status": "ignored", "event_id": event_id}
    return {
        "status": "completed",
        "event_id": event_id,
        "run_id": str(run.id),
        "state": run.state.value,
        "version": run.version,
    }
