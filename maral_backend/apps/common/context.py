"""Request / task correlation context, readable from logs, error responses and Celery."""

from contextvars import ContextVar

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
task_id_var: ContextVar[str | None] = ContextVar("task_id", default=None)


def get_request_id() -> str | None:
    return request_id_var.get()
