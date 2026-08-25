from shadowops.config import Settings
from shadowops.worker.celery_app import create_celery_app


def test_celery_uses_redis_for_delivery_not_result_truth() -> None:
    settings = Settings(redis_url="redis://example.test:6379/4", _env_file=None)

    application = create_celery_app(settings)

    assert application.conf.broker_url == "redis://example.test:6379/4"
    assert application.conf.result_backend is None
    assert application.conf.task_serializer == "json"
    assert application.conf.accept_content == ["json"]
    assert application.conf.task_acks_late is True
    assert application.conf.task_reject_on_worker_lost is True
    assert application.conf.worker_prefetch_multiplier == 1
    assert application.conf.timezone == "UTC"
    assert application.conf.enable_utc is True
    assert application.conf.broker_connection_retry_on_startup is True
    assert application.conf.worker_hijack_root_logger is False
    assert application.conf.worker_cancel_long_running_tasks_on_connection_loss is True
    assert "shadowops.worker.tasks" in application.conf.include
    assert application.conf.shadowops_outbox_batch_size == 50
    assert application.conf.beat_schedule["dispatch-outbox"] == {
        "task": "shadowops.maintenance.dispatch_outbox",
        "schedule": 0.5,
    }
