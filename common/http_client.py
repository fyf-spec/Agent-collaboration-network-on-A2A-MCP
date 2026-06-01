"""Small JSON-over-HTTP client utilities for local A2A calls."""

from __future__ import annotations

from dataclasses import dataclass
import json
import socket
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
        # 判断 HTTP 状态码是否表示成功（2xx）
        return 200 <= self.status_code < 300


class HttpJsonClientError(RuntimeError):
    def __init__(self, message: str, *, url: str, elapsed_ms: float | None = None) -> None:
        # 初始化错误信息、请求 URL 和耗时
        super().__init__(message)
        self.url = url
        self.elapsed_ms = elapsed_ms


def post_json(url: str, payload: dict[str, Any], *, timeout: float) -> HttpJsonResponse:
    # 发送 HTTP POST JSON 请求并返回结构化响应
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
    except (TimeoutError, socket.timeout) as exc:
        elapsed_ms = (time.perf_counter() - started) * 1000
        raise HttpJsonClientError(f"request timed out after {timeout}s", url=url, elapsed_ms=elapsed_ms) from exc
    except error.URLError as exc:
        elapsed_ms = (time.perf_counter() - started) * 1000
        reason = getattr(exc, "reason", exc)
        raise HttpJsonClientError(f"request failed: {reason}", url=url, elapsed_ms=elapsed_ms) from exc
    except OSError as exc:
        elapsed_ms = (time.perf_counter() - started) * 1000
        raise HttpJsonClientError(f"request failed: {exc}", url=url, elapsed_ms=elapsed_ms) from exc


def _decode_json(raw_body: str) -> Any:
    # 安全解析 JSON 字符串，解析失败返回 None
    if not raw_body:
        return None
    try:
        return json.loads(raw_body)
    except json.JSONDecodeError:
        return None
