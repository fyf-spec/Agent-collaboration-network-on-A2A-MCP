"""Small JSON-over-HTTP client utilities for local A2A calls."""

from __future__ import annotations

from dataclasses import dataclass
import json
import time
from typing import Any
from urllib import error, request


@dataclass(frozen=True)
class HttpJsonResponse:
    url: str
    status_code: int
    data: Any
    raw_body: str
    elapsed_ms: float

    @property
    def ok(self) -> bool:
        return 200 <= self.status_code < 300


class HttpJsonClientError(RuntimeError):
    def __init__(self, message: str, *, url: str, elapsed_ms: float | None = None) -> None:
        super().__init__(message)
        self.url = url
        self.elapsed_ms = elapsed_ms


def post_json(url: str, payload: dict[str, Any], *, timeout: float) -> HttpJsonResponse:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    http_request = request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "Accept": "application/json",
        },
    )

    started = time.perf_counter()
    try:
        with request.urlopen(http_request, timeout=timeout) as response:
            raw_body = response.read().decode("utf-8")
            elapsed_ms = (time.perf_counter() - started) * 1000
            return HttpJsonResponse(
                url=url,
                status_code=response.status,
                data=_decode_json(raw_body),
                raw_body=raw_body,
                elapsed_ms=elapsed_ms,
            )
    except error.HTTPError as exc:
        raw_body = exc.read().decode("utf-8", errors="replace")
        elapsed_ms = (time.perf_counter() - started) * 1000
        return HttpJsonResponse(
            url=url,
            status_code=exc.code,
            data=_decode_json(raw_body),
            raw_body=raw_body,
            elapsed_ms=elapsed_ms,
        )
    except TimeoutError as exc:
        elapsed_ms = (time.perf_counter() - started) * 1000
        raise HttpJsonClientError(f"request timed out after {timeout}s", url=url, elapsed_ms=elapsed_ms) from exc
    except error.URLError as exc:
        elapsed_ms = (time.perf_counter() - started) * 1000
        reason = getattr(exc, "reason", exc)
        raise HttpJsonClientError(f"request failed: {reason}", url=url, elapsed_ms=elapsed_ms) from exc


def _decode_json(raw_body: str) -> Any:
    if not raw_body:
        return None
    try:
        return json.loads(raw_body)
    except json.JSONDecodeError:
        return None
