"""Distributed inference lease held through the call, then a completion cooldown.

Scope is one stack sharing this table, not unrelated account clients. AWS clocks
must be synchronized. Lease duration exceeds the deployed Lambda timeout (60s).
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
import os
import threading
import time
import uuid

INTERVAL_MS = 1100
LEASE_MS = 180_000
GATE_KEY = "model-inference"
_local_lock = threading.Lock()
_local_next = float("-inf")


_request_deadline = ContextVar("inference_request_deadline", default=None)


class InferenceTimeout(RuntimeError):
    """The request budget expired, possibly after a remote inference started."""

    def __init__(self, message, *, may_be_running=False):
        super().__init__(message)
        self.inference_may_be_running = may_be_running


@contextmanager
def request_budget(seconds=55):
    token = _request_deadline.set(time.monotonic() + max(0, seconds))
    try:
        yield
    finally:
        _request_deadline.reset(token)


def remaining_budget():
    deadline = _request_deadline.get()
    return 55.0 if deadline is None else max(0, deadline - time.monotonic())


class RateLimitError(RuntimeError):
    pass


@contextmanager
def acquire(region: str):
    """Use `with acquire(region):` around the entire synchronous model call.

    Table partition key: pk (String). Only UpdateItem permission is required.
    On release failure, the lease remains closed until expiry. A crashed Lambda
    cannot retry inference after 60s; the 180s lease prevents premature takeover.
    """
    wait_seconds = min(18, remaining_budget() - 8)
    if wait_seconds <= 0:
        raise InferenceTimeout("Insufficient request time to start inference")
    table = os.environ.get("YOUTHSCOPE_RATE_TABLE", "").strip()
    if not table:
        if os.environ.get("AWS_EXECUTION_ENV"):
            raise RateLimitError("Lambda requires YOUTHSCOPE_RATE_TABLE")
        global _local_next
        deadline = time.monotonic() + wait_seconds
        if not _local_lock.acquire(timeout=wait_seconds):
            raise RateLimitError("Inference gate busy")
        entered = False
        try:
            delay = max(0, _local_next - time.monotonic())
            if time.monotonic() + delay > deadline:
                raise RateLimitError("Inference gate busy")
            if delay:
                time.sleep(delay)
            entered = True
            try:
                yield
            except BaseException as exc:
                if getattr(exc, "inference_may_be_running", False):
                    _local_next = time.monotonic() + LEASE_MS / 1000
                    entered = False
                raise
        finally:
            if entered:
                _local_next = time.monotonic() + INTERVAL_MS / 1000
            _local_lock.release()
        return

    import boto3
    from botocore.config import Config
    client = boto3.client("dynamodb", region_name=region,
                          config=Config(retries={"total_max_attempts": 1},
                                        connect_timeout=1, read_timeout=1))
    deadline = time.monotonic() + wait_seconds
    owner = uuid.uuid4().hex
    while time.monotonic() < deadline:
        now = int(time.time() * 1000)
        lease_until = now + LEASE_MS
        try:
            client.update_item(
                TableName=table, Key={"pk": {"S": GATE_KEY}},
                UpdateExpression="SET #owner = :owner, #lease = :lease",
                ConditionExpression="(attribute_not_exists(#lease) OR #lease < :now) AND (attribute_not_exists(#next) OR #next < :now)",
                ExpressionAttributeNames={"#owner": "owner", "#lease": "lease_until", "#next": "next_allowed_at"},
                ExpressionAttributeValues={":owner": {"S": owner}, ":lease": {"N": str(lease_until)}, ":now": {"N": str(now)}},
            )
            break
        except Exception as exc:
            code = getattr(exc, "response", {}).get("Error", {}).get("Code")
            if code != "ConditionalCheckFailedException":
                raise RateLimitError("Inference lease unavailable; request not sent") from exc
            if time.monotonic() + 0.2 >= deadline:
                raise RateLimitError("Inference lease busy; retry later") from exc
            time.sleep(0.2)
    else:
        raise RateLimitError("Inference lease busy; retry later")
    uncertain = False
    try:
        # A delayed acquisition response must never authorize a call after lease
        # expiry. Keep a full invocation plus cleanup margin under the lease.
        if int(time.time() * 1000) + 60_000 >= lease_until:
            raise RateLimitError("Inference lease response arrived too late")
        yield
    except BaseException as exc:
        uncertain = getattr(exc, "inference_may_be_running", False)
        raise
    finally:
        if not uncertain:
            _release(client, table, owner)


def _release(client, table, owner):
    try:
        client.update_item(
            TableName=table, Key={"pk": {"S": GATE_KEY}},
            UpdateExpression="SET #next = :next REMOVE #owner, #lease",
            ConditionExpression="#owner = :owner",
            ExpressionAttributeNames={"#owner": "owner", "#lease": "lease_until", "#next": "next_allowed_at"},
            ExpressionAttributeValues={":owner": {"S": owner}, ":next": {"N": str(int(time.time() * 1000) + INTERVAL_MS)}},
        )
    except Exception as exc:
        raise RateLimitError("Inference lease release failed; gate remains closed until expiry") from exc
