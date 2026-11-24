"""Idempotent POST: the record is written in the SAME transaction as the work, so there is never a record without
orders or orders without a record. A failed attempt leaves nothing behind (the client may retry with the same key)."""

import hashlib
import json

from django.db import IntegrityError, transaction

from apps.common.exceptions import Conflict

from ..models import IdempotencyRecord


def hash_request(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str, separators=(",", ":")).encode()).hexdigest()


def _replay(record: IdempotencyRecord, request_hash: str):
    if record.request_hash != request_hash:
        raise Conflict("This Idempotency-Key was already used with a different request.", code="idempotency_conflict")
    if record.response_status == 0:
        raise Conflict("The first request with this key is still being processed.", code="idempotency_in_progress")
    return record.response_status, record.response_body, True


def run(user, scope: str, key: str, request_hash: str, work):
    """`work()` -> (status, body). Returns (status, body, replayed)."""
    lookup = {"user": user, "scope": scope, "key": key}
    existing = IdempotencyRecord.objects.filter(**lookup).first()
    if existing is not None:
        return _replay(existing, request_hash)
    try:
        with transaction.atomic():
            record = IdempotencyRecord.objects.create(**lookup, request_hash=request_hash)
            status, body = work()
            safe = json.loads(json.dumps(body, default=str))
            record.response_status, record.response_body = status, safe
            record.save(update_fields=["response_status", "response_body"])
            return status, safe, False
    except IntegrityError:
        record = IdempotencyRecord.objects.filter(**lookup).first()
        if record is None:
            raise
        return _replay(record, request_hash)
