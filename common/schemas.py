"""Shared A2A JSON payload helpers.

The project intentionally keeps these helpers small and dependency-free so
business agents can reuse the same wire contract without importing the
coordinator implementation.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


RESULT_SUCCESS = "success"
RESULT_ERROR = "error"

TASK_PENDING = "pending"
TASK_WAITING = "waiting"
TASK_COMPLETED = "completed"
TASK_PARTIAL = "partial"
TASK_FAILED = "failed"


class PayloadValidationError(ValueError):
    """Raised when an inbound JSON payload misses required fields."""


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_task_id() -> str:
    return uuid4().hex


def require_fields(payload: dict[str, Any], fields: list[str]) -> None:
    missing = [field for field in fields if field not in payload]
    if missing:
        raise PayloadValidationError(f"missing required field(s): {', '.join(missing)}")


def build_task_payload(
    *,
    source: str,
    target: str,
    task_id: str,
    instruction: str,
    reply_to: str,
    context: dict[str, Any] | None = None,
    created_at: str | None = None,
) -> dict[str, Any]:
    return {
        "source": source,
        "target": target,
        "task_id": task_id,
        "instruction": instruction,
        "context": context or {},
        "reply_to": reply_to,
        "created_at": created_at or utc_now_iso(),
    }


def build_result_payload(
    *,
    source: str,
    target: str,
    task_id: str,
    status: str,
    result: Any = None,
    error: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "source": source,
        "target": target,
        "task_id": task_id,
        "status": status,
        "result": result,
        "error": error,
        "metadata": metadata or {},
    }


def validate_task_result(payload: dict[str, Any]) -> None:
    require_fields(payload, ["source", "target", "task_id", "status"])
    if payload["status"] not in {RESULT_SUCCESS, RESULT_ERROR}:
        raise PayloadValidationError("status must be 'success' or 'error'")
    if payload["status"] == RESULT_ERROR and not payload.get("error"):
        raise PayloadValidationError("error result must include an error message")


def success_response(data: dict[str, Any]) -> dict[str, Any]:
    return {"ok": True, **data}


def error_response(
    code: str,
    message: str,
    *,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "ok": False,
        "error": {
            "code": code,
            "message": message,
        },
    }
    if details:
        body["error"]["details"] = details
    return body
