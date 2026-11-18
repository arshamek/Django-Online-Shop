import json

import structlog

from apps.common.context import request_id_var
from apps.common.logging_config import redact_sensitive


def test_redaction_masks_credential_like_keys():
    event = redact_sensitive(
        None, "info", {"event": "x", "password": "p", "Authorization": "Bearer abc", "api_key": "k", "path": "/a"}
    )
    assert event["password"] == event["Authorization"] == event["api_key"] == "[REDACTED]"
    assert event["path"] == "/a"


def test_json_log_contains_request_id_and_keeps_persian(capsys, settings):
    import logging.config

    from apps.common.logging_config import build_logging_config, configure_structlog

    configure_structlog()
    logging.config.dictConfig(build_logging_config(level="INFO", json_logs=True))
    token = request_id_var.set("rid-1")
    try:
        structlog.get_logger("maral.test").info("سلام", password="hunter2")
    finally:
        request_id_var.reset(token)
    line = capsys.readouterr().out.strip().splitlines()[-1]
    data = json.loads(line)
    assert data["request_id"] == "rid-1"
    assert data["event"] == "سلام"
    assert data["password"] == "[REDACTED]"
    assert data["level"] == "info" and "timestamp" in data


def test_request_log_event_emitted(api_client, capsys):
    import logging.config

    from apps.common.logging_config import build_logging_config

    logging.config.dictConfig(build_logging_config(level="INFO", json_logs=True))
    api_client.get("/api/v1/?secret=1")
    lines = [json.loads(row) for row in capsys.readouterr().out.splitlines() if row.startswith("{")]
    request_event = next(row for row in lines if row["event"] == "request")
    assert request_event["path"] == "/api/v1/"  # query string never logged
    assert request_event["status_code"] == 200
    assert "duration_ms" in request_event and request_event["request_id"]
