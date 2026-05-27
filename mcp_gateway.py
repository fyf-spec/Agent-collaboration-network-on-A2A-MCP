"""MCP Gateway for JSON-RPC forwarding, cache, rate limit, and circuit breaking."""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
import threading
import time
from typing import Any

import common.logger
from common.config import MCP_GATEWAY, MCP_SERVERS
from common.http_client import HttpJsonClientError, post_json
from common.logger import log_network_event


logger = logging.getLogger("mcp_gateway")

JSONRPC_PARSE_ERROR = -32700
JSONRPC_INVALID_REQUEST = -32600
JSONRPC_METHOD_NOT_FOUND = -32601
JSONRPC_INVALID_PARAMS = -32602
JSONRPC_INTERNAL_ERROR = -32603
JSONRPC_CIRCUIT_OPEN = -32001
JSONRPC_BUSY = -32002
JSONRPC_UPSTREAM_ERROR = -32003


@dataclass
class CacheEntry:
    result: dict[str, Any]
    expires_at: float


@dataclass
class InflightCall:
    event: threading.Event = field(default_factory=threading.Event)
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None


class TTLCache:
    def __init__(self, ttl_seconds: float) -> None:
        self.ttl_seconds = ttl_seconds
        self._items: dict[str, CacheEntry] = {}
        self._lock = threading.RLock()

    def get(self, key: str) -> dict[str, Any] | None:
        now = time.monotonic()
        with self._lock:
            item = self._items.get(key)
            if item is None:
                return None
            if item.expires_at <= now:
                self._items.pop(key, None)
                return None
            return dict(item.result)

    def set(self, key: str, result: dict[str, Any]) -> None:
        with self._lock:
            self._items[key] = CacheEntry(
                result=dict(result),
                expires_at=time.monotonic() + self.ttl_seconds,
            )

    def size(self) -> int:
        now = time.monotonic()
        with self._lock:
            expired = [key for key, item in self._items.items() if item.expires_at <= now]
            for key in expired:
                self._items.pop(key, None)
            return len(self._items)


class RateLimiter:
    def __init__(self, method_names: list[str], max_concurrent: int) -> None:
        self._semaphores = {
            method: threading.BoundedSemaphore(max(1, max_concurrent))
            for method in method_names
        }

    def acquire(self, method: str, timeout: float) -> bool:
        semaphore = self._semaphores.get(method)
        if semaphore is None:
            return True
        return semaphore.acquire(timeout=timeout)

    def release(self, method: str) -> None:
        semaphore = self._semaphores.get(method)
        if semaphore is not None:
            semaphore.release()


class CircuitBreaker:
    def __init__(self, *, failure_threshold: int, cooldown_seconds: float) -> None:
        self.failure_threshold = max(1, failure_threshold)
        self.cooldown_seconds = cooldown_seconds
        self.state = "closed"
        self.failure_count = 0
        self.opened_until = 0.0
        self._half_open_probe_running = False
        self._lock = threading.RLock()

    def before_request(self) -> tuple[bool, str | None]:
        now = time.monotonic()
        with self._lock:
            if self.state == "open":
                if now < self.opened_until:
                    return False, "circuit_open"
                self.state = "half_open"
                self._half_open_probe_running = False

            if self.state == "half_open":
                if self._half_open_probe_running:
                    return False, "half_open_probe_running"
                self._half_open_probe_running = True
                return True, None

            return True, None

    def record_success(self) -> None:
        with self._lock:
            self.state = "closed"
            self.failure_count = 0
            self.opened_until = 0.0
            self._half_open_probe_running = False

    def record_failure(self) -> None:
        now = time.monotonic()
        with self._lock:
            self._half_open_probe_running = False
            self.failure_count += 1
            if self.state == "half_open" or self.failure_count >= self.failure_threshold:
                self.state = "open"
                self.opened_until = now + self.cooldown_seconds

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            retry_after_ms = max(0.0, (self.opened_until - time.monotonic()) * 1000)
            return {
                "state": self.state,
                "failure_count": self.failure_count,
                "retry_after_ms": round(retry_after_ms, 2),
            }


class GatewayMetrics:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.total_requests = 0
        self.upstream_calls = 0
        self.cache_hits = 0
        self.cache_misses = 0
        self.coalesced_requests = 0
        self.rate_limited = 0
        self.circuit_open = 0
        self.error_count = 0
        self.total_latency_ms = 0.0
        self.method_stats: dict[str, dict[str, float | int]] = {}

    def record_request(self, method: str) -> None:
        with self._lock:
            self.total_requests += 1
            self._stats(method)["requests"] += 1

    def record_latency(self, method: str, elapsed_ms: float) -> None:
        with self._lock:
            self.total_latency_ms += elapsed_ms
            self._stats(method)["total_latency_ms"] += elapsed_ms

    def increment(self, method: str, field_name: str) -> None:
        with self._lock:
            current = getattr(self, field_name)
            setattr(self, field_name, current + 1)
            self._stats(method)[field_name] += 1

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            avg_latency = self.total_latency_ms / self.total_requests if self.total_requests else 0.0
            method_stats: dict[str, Any] = {}
            for method, stats in self.method_stats.items():
                requests = int(stats.get("requests", 0))
                total_latency = float(stats.get("total_latency_ms", 0.0))
                view = dict(stats)
                view["avg_latency_ms"] = round(total_latency / requests, 2) if requests else 0.0
                view.pop("total_latency_ms", None)
                method_stats[method] = view

            return {
                "total_requests": self.total_requests,
                "upstream_calls": self.upstream_calls,
                "cache_hits": self.cache_hits,
                "cache_misses": self.cache_misses,
                "coalesced_requests": self.coalesced_requests,
                "rate_limited": self.rate_limited,
                "circuit_open": self.circuit_open,
                "error_count": self.error_count,
                "avg_latency_ms": round(avg_latency, 2),
                "method_stats": method_stats,
            }

    def _stats(self, method: str) -> dict[str, float | int]:
        if method not in self.method_stats:
            self.method_stats[method] = {
                "requests": 0,
                "upstream_calls": 0,
                "cache_hits": 0,
                "cache_misses": 0,
                "coalesced_requests": 0,
                "rate_limited": 0,
                "circuit_open": 0,
                "error_count": 0,
                "total_latency_ms": 0.0,
            }
        return self.method_stats[method]


class MCPGatewayState:
    def __init__(self, *, host: str, port: int) -> None:
        self.host = host
        self.port = port
        self.name = str(MCP_GATEWAY["name"])
        self.routes = {
            str(server["method"]): server_key
            for server_key, server in MCP_SERVERS.items()
        }
        self.routes["get_intercity_transport"] = "traffic"
        self.cache = TTLCache(float(MCP_GATEWAY["cache_ttl_seconds"]))
        self.metrics = GatewayMetrics()
        self.rate_limiter = RateLimiter(
            list(self.routes),
            int(MCP_GATEWAY["max_concurrent_per_method"]),
        )
        self.breakers = {
            method: CircuitBreaker(
                failure_threshold=int(MCP_GATEWAY["circuit_failure_threshold"]),
                cooldown_seconds=float(MCP_GATEWAY["circuit_cooldown_seconds"]),
            )
            for method in self.routes
        }
        self._inflight: dict[str, InflightCall] = {}
        self._inflight_lock = threading.RLock()

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def health(self) -> dict[str, Any]:
        return {
            "role": self.name,
            "status": "ok",
            "base_url": self.base_url,
            "protocol": "HTTP JSON-RPC 2.0",
            "routes": self.route_view(),
            "cache_size": self.cache.size(),
            "circuit_breakers": self.breaker_view(),
        }

    def route_view(self) -> dict[str, Any]:
        return {
            method: {
                "upstream": MCP_SERVERS[server_key]["name"],
                "url": _server_url(MCP_SERVERS[server_key]),
            }
            for method, server_key in self.routes.items()
        }

    def breaker_view(self) -> dict[str, Any]:
        return {
            method: breaker.snapshot()
            for method, breaker in self.breakers.items()
        }

    def metrics_view(self) -> dict[str, Any]:
        return {
            **self.metrics.snapshot(),
            "cache_size": self.cache.size(),
            "circuit_breakers": self.breaker_view(),
        }

    def handle_json_rpc(self, payload: dict[str, Any]) -> tuple[HTTPStatus, dict[str, Any]]:
        request_id = payload.get("id")
        method = payload.get("method")
        started = time.perf_counter()

        if payload.get("jsonrpc") != "2.0":
            return HTTPStatus.OK, _json_rpc_error(
                request_id,
                JSONRPC_INVALID_REQUEST,
                "Invalid Request: jsonrpc must be '2.0'",
            )
        if not isinstance(method, str) or not method:
            return HTTPStatus.OK, _json_rpc_error(
                request_id,
                JSONRPC_INVALID_REQUEST,
                "Invalid Request: method is required",
            )
        params = payload.get("params", {})
        if params is None:
            params = {}
        if not isinstance(params, dict):
            return HTTPStatus.OK, _json_rpc_error(
                request_id,
                JSONRPC_INVALID_PARAMS,
                "Invalid params: params must be an object",
            )
        if method not in self.routes:
            return HTTPStatus.OK, _json_rpc_error(
                request_id,
                JSONRPC_METHOD_NOT_FOUND,
                f"Method not found: {method}",
            )

        self.metrics.record_request(method)
        cache_key = _cache_key(method, params)
        cached_result = self.cache.get(cache_key)
        if cached_result is not None:
            self.metrics.increment(method, "cache_hits")
            self.metrics.record_latency(method, _elapsed_ms(started))
            log_network_event(
                event="gateway_cache_hit",
                direction="internal",
                source=self.name,
                target=method,
                task_id=str(request_id) if request_id is not None else None,
                payload={"cache_key": cache_key},
            )
            return HTTPStatus.OK, _json_rpc_result(request_id, cached_result)

        self.metrics.increment(method, "cache_misses")
        inflight, is_leader = self._get_or_create_inflight(cache_key)
        if not is_leader:
            return self._wait_for_inflight(method, request_id, cache_key, inflight, started)

        try:
            result, rpc_error = self._call_upstream(method, payload)
            if result is not None:
                self.cache.set(cache_key, result)
                inflight.result = result
                response = _json_rpc_result(request_id, result)
            else:
                inflight.error = rpc_error or _error_body(
                    JSONRPC_INTERNAL_ERROR,
                    "Gateway internal error",
                )
                response = _json_rpc_error_from_body(request_id, inflight.error)
            return HTTPStatus.OK, response
        finally:
            inflight.event.set()
            self._clear_inflight(cache_key)
            self.metrics.record_latency(method, _elapsed_ms(started))

    def _get_or_create_inflight(self, cache_key: str) -> tuple[InflightCall, bool]:
        with self._inflight_lock:
            inflight = self._inflight.get(cache_key)
            if inflight is not None:
                return inflight, False
            inflight = InflightCall()
            self._inflight[cache_key] = inflight
            return inflight, True

    def _wait_for_inflight(
        self,
        method: str,
        request_id: Any,
        cache_key: str,
        inflight: InflightCall,
        started: float,
    ) -> tuple[HTTPStatus, dict[str, Any]]:
        self.metrics.increment(method, "coalesced_requests")
        timeout = float(MCP_GATEWAY["coalesce_wait_seconds"])
        waited = inflight.event.wait(timeout=timeout)
        self.metrics.record_latency(method, _elapsed_ms(started))

        if not waited:
            self.metrics.increment(method, "error_count")
            return HTTPStatus.OK, _json_rpc_error(
                request_id,
                JSONRPC_UPSTREAM_ERROR,
                f"Gateway coalescing timeout after {timeout}s",
            )

        if inflight.result is not None:
            log_network_event(
                event="gateway_coalesced_result",
                direction="internal",
                source=self.name,
                target=method,
                task_id=str(request_id) if request_id is not None else None,
                payload={"cache_key": cache_key},
            )
            return HTTPStatus.OK, _json_rpc_result(request_id, inflight.result)

        error_body = inflight.error or _error_body(
            JSONRPC_INTERNAL_ERROR,
            "Gateway inflight call ended without result",
        )
        return HTTPStatus.OK, _json_rpc_error_from_body(request_id, error_body)

    def _clear_inflight(self, cache_key: str) -> None:
        with self._inflight_lock:
            self._inflight.pop(cache_key, None)

    def _call_upstream(
        self,
        method: str,
        payload: dict[str, Any],
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        request_id = payload.get("id")
        server = MCP_SERVERS[self.routes[method]]
        server_name = str(server["name"])
        url = _server_url(server)
        breaker = self.breakers[method]
        allowed, reason = breaker.before_request()

        if not allowed:
            self.metrics.increment(method, "circuit_open")
            self.metrics.increment(method, "error_count")
            log_network_event(
                event="gateway_circuit_open",
                direction="internal",
                source=self.name,
                target=server_name,
                method="POST",
                url=url,
                task_id=str(request_id) if request_id is not None else None,
                error=reason or "circuit_open",
                error_type="CircuitOpen",
            )
            return None, _error_body(
                JSONRPC_CIRCUIT_OPEN,
                f"{reason or 'circuit_open'}: {server_name} unavailable",
            )

        wait_seconds = float(MCP_GATEWAY["rate_limit_wait_seconds"])
        if not self.rate_limiter.acquire(method, timeout=wait_seconds):
            self.metrics.increment(method, "rate_limited")
            self.metrics.increment(method, "error_count")
            return None, _error_body(
                JSONRPC_BUSY,
                f"Gateway busy: too many concurrent requests for {method}",
            )

        try:
            self.metrics.increment(method, "upstream_calls")
            log_network_event(
                event="gateway_call_mcp",
                direction="outbound",
                source=self.name,
                target=server_name,
                method="POST",
                url=url,
                task_id=str(request_id) if request_id is not None else None,
                payload=payload,
            )
            response = post_json(
                url,
                payload,
                timeout=float(MCP_GATEWAY["upstream_timeout_seconds"]),
            )
        except HttpJsonClientError as exc:
            breaker.record_failure()
            self.metrics.increment(method, "error_count")
            log_network_event(
                event="gateway_mcp_failed",
                direction="inbound",
                source=server_name,
                target=self.name,
                method="POST",
                url=exc.url,
                task_id=str(request_id) if request_id is not None else None,
                latency_ms=exc.elapsed_ms,
                error=str(exc),
                error_type=_infer_error_type(exc),
            )
            return None, _error_body(
                JSONRPC_UPSTREAM_ERROR,
                f"Upstream MCP request failed: {exc}",
            )
        finally:
            self.rate_limiter.release(method)

        log_network_event(
            event="gateway_mcp_response",
            direction="inbound",
            source=server_name,
            target=self.name,
            method="POST",
            url=url,
            task_id=str(request_id) if request_id is not None else None,
            status_code=response.status_code,
            latency_ms=response.elapsed_ms,
            payload_size=len(response.raw_body.encode("utf-8")),
            payload=response.data,
        )

        if not response.ok:
            breaker.record_failure()
            self.metrics.increment(method, "error_count")
            return None, _error_body(
                JSONRPC_UPSTREAM_ERROR,
                f"Upstream MCP returned HTTP {response.status_code}",
            )
        if not isinstance(response.data, dict):
            breaker.record_failure()
            self.metrics.increment(method, "error_count")
            return None, _error_body(
                JSONRPC_UPSTREAM_ERROR,
                "Upstream MCP response body must be a JSON object",
            )
        if response.data.get("error"):
            breaker.record_failure()
            self.metrics.increment(method, "error_count")
            error_body = response.data["error"]
            if isinstance(error_body, dict):
                return None, error_body
            return None, _error_body(JSONRPC_UPSTREAM_ERROR, str(error_body))
        result = response.data.get("result")
        if not isinstance(result, dict):
            breaker.record_failure()
            self.metrics.increment(method, "error_count")
            return None, _error_body(
                JSONRPC_UPSTREAM_ERROR,
                "Upstream MCP result must be a JSON object",
            )

        breaker.record_success()
        return result, None


class MCPGatewayHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, server_address: tuple[str, int], handler_class: type[BaseHTTPRequestHandler]) -> None:
        super().__init__(server_address, handler_class)
        self.state = MCPGatewayState(host=server_address[0], port=server_address[1])


class MCPGatewayRequestHandler(BaseHTTPRequestHandler):
    server: MCPGatewayHTTPServer

    def do_GET(self) -> None:
        if self.path == "/health":
            self._send_json(HTTPStatus.OK, {"ok": True, **self.server.state.health()})
            return
        if self.path == "/metrics":
            self._send_json(HTTPStatus.OK, {"ok": True, "metrics": self.server.state.metrics_view()})
            return
        if self.path == "/methods":
            self._send_json(HTTPStatus.OK, {"ok": True, "methods": self.server.state.route_view()})
            return
        self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": f"unknown path: {self.path}"})

    def do_POST(self) -> None:
        if self.path != str(MCP_GATEWAY.get("path", "/")):
            self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": f"unknown path: {self.path}"})
            return

        request_id: Any = None
        payload: dict[str, Any] | None = None
        try:
            payload, payload_size = self._read_json_with_size()
            request_id = payload.get("id")
            log_network_event(
                event="gateway_jsonrpc_request",
                direction="inbound",
                source="worker_agent",
                target=self.server.state.name,
                method="POST",
                url=self.path,
                task_id=str(request_id) if request_id is not None else None,
                payload=payload,
                payload_size=payload_size,
            )
            status, response = self.server.state.handle_json_rpc(payload)
        except ValueError as exc:
            status = HTTPStatus.BAD_REQUEST
            response = _json_rpc_error(None, JSONRPC_PARSE_ERROR, f"Parse error: {exc}")
        except Exception as exc:
            logger.exception("Gateway crashed while handling request")
            status = HTTPStatus.OK
            response = _json_rpc_error(request_id, JSONRPC_INTERNAL_ERROR, str(exc))

        self._send_json(status, response)

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _read_json_with_size(self) -> tuple[dict[str, Any], int]:
        length = int(self.headers.get("Content-Length", "0"))
        raw_body = self.rfile.read(length).decode("utf-8") if length else ""
        try:
            payload = json.loads(raw_body or "{}")
        except json.JSONDecodeError as exc:
            raise ValueError(exc.msg) from exc
        if not isinstance(payload, dict):
            raise ValueError("body must be a JSON object")
        return payload, length

    def _send_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        try:
            self.send_response(int(status))
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
            pass


def run(host: str | None = None, port: int | None = None) -> None:
    host = host or str(MCP_GATEWAY["host"])
    port = port or int(MCP_GATEWAY["port"])
    server = MCPGatewayHTTPServer((host, port), MCPGatewayRequestHandler)
    logger.info(f"{MCP_GATEWAY['name']} listening on http://{host}:{port}")
    logger.info("Endpoints: POST /, GET /health, GET /methods, GET /metrics")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.critical(f"\n{MCP_GATEWAY['name']} shutting down.")
    finally:
        server.server_close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run MCP Gateway.")
    parser.add_argument("--host", default=MCP_GATEWAY["host"])
    parser.add_argument("--port", type=int, default=MCP_GATEWAY["port"])
    args = parser.parse_args()
    run(host=args.host, port=args.port)


def _server_url(server: dict[str, Any]) -> str:
    return f"http://{server['host']}:{server['port']}{server.get('path', '/')}"


def _cache_key(method: str, params: dict[str, Any]) -> str:
    return method + ":" + json.dumps(params, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _elapsed_ms(started: float) -> float:
    return (time.perf_counter() - started) * 1000


def _json_rpc_result(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "result": result, "id": request_id}


def _json_rpc_error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "error": _error_body(code, message), "id": request_id}


def _json_rpc_error_from_body(request_id: Any, error_body: dict[str, Any]) -> dict[str, Any]:
    code = int(error_body.get("code", JSONRPC_INTERNAL_ERROR))
    message = str(error_body.get("message", "Gateway error"))
    return _json_rpc_error(request_id, code, message)


def _error_body(code: int, message: str) -> dict[str, Any]:
    return {"code": code, "message": message}


def _infer_error_type(exc: Exception) -> str:
    cause = exc.__cause__
    if cause is None:
        return type(exc).__name__
    reason = getattr(cause, "reason", None)
    if reason is not None:
        return type(reason).__name__
    return type(cause).__name__


if __name__ == "__main__":
    main()
