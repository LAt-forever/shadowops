"""Celery application factory."""

from celery import Celery  # type: ignore[import-untyped]

from shadowops.config import Settings, get_settings


def create_celery_app(settings: Settings | None = None) -> Celery:
    """Create a worker that uses Redis only for task delivery."""
    resolved = settings or get_settings()
    application = Celery("shadowops", broker=resolved.redis_url)
    application.conf.update(
        result_backend=None,
        task_serializer="json",
        accept_content=["json"],
        task_acks_late=True,
        worker_prefetch_multiplier=1,
        timezone="UTC",
        enable_utc=True,
        broker_connection_retry_on_startup=True,
    )
    return application


celery_app = create_celery_app()
