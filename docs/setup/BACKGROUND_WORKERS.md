# Background processing (Celery + Redis)

Broker: Redis (`CELERY_BROKER_URL`). Results and failed-task records: database via `django-celery-results` (`CELERY_RESULT_BACKEND=django-db`). Config lives in `config/settings/base.py` (`CELERY_*`) and `config/celery.py`.

## Processes
```bash
celery -A config worker -l INFO -n worker@%h -Q default,imports,translations,pricing,notifications
celery -A config beat   -l INFO --schedule /tmp/celerybeat-schedule     # exactly ONE beat instance
celery -A config flower --port=5555                                      # optional dashboard (dev requirements)
```
Queues from the architecture are declared (`default`, `imports`, `translations`, `pricing`, `notifications`); only `default` carries tasks in Phase 2.

## Reliability settings
`acks_late` + `reject_on_worker_lost` (a task is redelivered if its worker dies, so **tasks must be idempotent**), prefetch 1, soft/hard time limits 270 s / 300 s, JSON serialisation only, `visibility_timeout` 1 h (must exceed the longest task), broker connection retry on startup, task-started tracking, task events on (Flower).

## Retry behaviour (`apps/common/tasks.py: MaralTask`)
Use `@shared_task(base=MaralTask, name="app.task")`. Exceptions in `TransientError`, `ConnectionError`, `TimeoutError` are retried with exponential backoff (2 s base, max 600 s, jitter, max 5 retries). Anything else fails immediately. Raise `TransientError` for retryable conditions.

## Beat scheduling
Static schedule in `CELERY_BEAT_SCHEDULE` (no `DatabaseScheduler`, so no extra tables). It has one entry: `common.heartbeat` every `BEAT_HEARTBEAT_SECONDS` (300) which writes `celery:beat:heartbeat` to the cache — if that key is stale, beat or the worker is down. Result cleanup (`celery.backend_cleanup`) is added by Celery automatically; results expire after 30 days.

## Monitoring and failed tasks
* Flower (compose profile `monitoring`, bound to localhost; no authentication).
* Failed tasks: `django_celery_results.TaskResult` rows with `status="FAILURE"` (task name, args, traceback, worker; browsable in Django admin), plus a structured `task_failed` log event (arguments are not logged).
```python
from django_celery_results.models import TaskResult
TaskResult.objects.filter(status="FAILURE").order_by("-date_done")
```
* Correlation: the web request id travels to the task (message header `maral_request_id`) and appears as `request_id` / `task_id` in worker logs; tasks started by beat use their task id.

## Test tasks
| Task | Use |
|---|---|
| `common.ping` | broker → worker → result round trip. `python manage.py check_celery` |
| `common.heartbeat` | scheduled by beat |
| `common.debug_fail` | verification aid: `debug_fail.delay(mode="permanent")` (recorded as FAILURE), `mode="transient", fail_times=2` (exercises backoff) |

## Verified in Phase 2
With a real Redis 7 and live worker + beat (database swapped for SQLite because no SQL Server was available): ping executed, transient failure retried ("Retry in 2s") then succeeded, permanent failure recorded as `FAILURE`, request id propagated into worker logs, beat fired the heartbeat. `django_celery_results` on SQL Server is **not** yet verified.

## Redis requirements
Use `maxmemory-policy noeviction` for the broker instance (`check_redis` warns otherwise) and decide persistence/HA before production (decision D-18).
