"""Propagate the request correlation id from the web process into Celery tasks and back into logs."""

from celery.signals import before_task_publish, task_postrun, task_prerun

from .context import get_request_id, request_id_var, task_id_var

HEADER = "maral_request_id"
_tokens: dict[str, tuple] = {}


@before_task_publish.connect
def attach_request_id(headers=None, **kwargs):
    request_id = get_request_id()
    if request_id and headers is not None:
        headers[HEADER] = request_id


@task_prerun.connect
def bind_context(task_id=None, task=None, **kwargs):
    request_id = getattr(task.request, HEADER, None) or task_id
    _tokens[task_id] = (request_id_var.set(request_id), task_id_var.set(task_id))


@task_postrun.connect
def clear_context(task_id=None, **kwargs):
    tokens = _tokens.pop(task_id, None)
    if tokens:
        request_id_var.reset(tokens[0])
        task_id_var.reset(tokens[1])
