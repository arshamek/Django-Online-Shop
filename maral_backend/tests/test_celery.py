from apps.common import tasks
from apps.common.context import request_id_var


def test_ping_task_executes():
    result = tasks.ping.delay()
    assert result.successful()
    assert result.get()["pong"] is True


def test_heartbeat_sets_cache_key():
    from django.core.cache import cache

    tasks.heartbeat.delay()
    assert cache.get(tasks.HEARTBEAT_CACHE_KEY)


def test_transient_failure_retries_then_succeeds():
    result = tasks.debug_fail.apply(kwargs={"mode": "transient", "fail_times": 2})
    assert result.successful()
    assert result.get() == "succeeded after 2 retries"


def test_permanent_failure_is_not_retried_and_is_recorded():
    result = tasks.debug_fail.apply(kwargs={"mode": "permanent"})
    assert result.failed()
    assert isinstance(result.result, ValueError)


def test_retry_policy_configuration():
    assert tasks.ping.max_retries == 5
    assert tasks.ping.retry_backoff and tasks.ping.retry_jitter
    assert tasks.ping.acks_late is True
    assert tasks.ping.autoretry_for


def test_beat_schedule_points_to_registered_tasks():
    from django.conf import settings

    from config.celery import app

    app.loader.import_default_modules()
    for entry in settings.CELERY_BEAT_SCHEDULE.values():
        assert entry["task"] in app.tasks


def test_queues_declared():
    from django.conf import settings

    assert settings.CELERY_TASK_DEFAULT_QUEUE in settings.CELERY_QUEUE_NAMES


def test_request_id_header_attached_on_publish():
    from apps.common.celery_signals import HEADER, attach_request_id

    token = request_id_var.set("req-abc")
    try:
        headers = {}
        attach_request_id(headers=headers)
    finally:
        request_id_var.reset(token)
    assert headers == {HEADER: "req-abc"}
