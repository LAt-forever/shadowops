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
    assert application.conf.worker_prefetch_multiplier == 1
    assert application.conf.timezone == "UTC"
    assert application.conf.enable_utc is True
