"""Celery application factory."""

import structlog
from celery import Celery  # type: ignore[import-untyped]

from shadowops import __version__
from shadowops.config import Settings, get_settings
from shadowops.observability.logging import configure_logging


def create_celery_app(settings: Settings | None = None) -> Celery:
    """Create a worker that uses Redis only for task delivery."""
    resolved = settings or get_settings()
    configure_logging(resolved.log_level)
    application = Celery("shadowops", broker=resolved.redis_url, include=["shadowops.worker.tasks"])
    application.conf.update(
        result_backend=None,
        task_serializer="json",
        accept_content=["json"],
        task_acks_late=True,
        task_reject_on_worker_lost=True,
        worker_prefetch_multiplier=1,
        timezone="UTC",
        enable_utc=True,
        broker_connection_retry_on_startup=True,
        worker_hijack_root_logger=False,
        worker_cancel_long_running_tasks_on_connection_loss=True,
        shadowops_outbox_batch_size=resolved.outbox_batch_size,
        beat_schedule={
            "dispatch-outbox": {
                "task": "shadowops.maintenance.dispatch_outbox",
                "schedule": resolved.outbox_poll_interval_seconds,
            }
        },
    )
    structlog.get_logger(__name__).info(
        "service_configured",
        service="worker",
        version=__version__,
    )
    return application


celery_app = create_celery_app()
