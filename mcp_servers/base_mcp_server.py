"""Shared HTTP JSON-RPC 2.0 server helpers for local MCP servers."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import time
from typing import Any

from common.logger import log_network_event
import logging

logger = logging.getLogger("base_mcp_server")


JsonTool = Callable[..., dict[str, Any]]


@dataclass(frozen=True)
class MCPTool:
    name: str
    handler: JsonTool
    description: str


class MCPHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        server_address: tuple[str, int],
        handler_class: type[BaseHTTPRequestHandler],
        *,
        server_name: str,
        tools: dict[str, MCPTool],
        delay: float = 0.0,
    ) -> None:
        super().__init__(server_address, handler_class)
        self.server_name = server_name
        self.tools = tools
        self.delay = delay


class MCPRequestHandler(BaseHTTPRequestHandler):
    server: MCPHTTPServer

    def do_GET(self) -> None:
        if self.path == "/health":
            self._send_json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "role": self.server.server_name,
                    "status": "ok",
                    "protocol": "HTTP JSON-RPC 2.0",
                    "methods": sorted(self.server.tools),
                },
            )
            return
        if self.path == "/methods":
            self._send_json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "methods": [
                        {"name": tool.name, "description": tool.description}
                        for tool in self.server.tools.values()
                    ],
                },
            )
            return
        self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": f"unknown path: {self.path}"})

    def do_POST(self) -> None:
        started = time.perf_counter()
        request_id: Any = None
        payload: dict[str, Any] | None = None

        try:
            payload = self._read_json()
            request_id = payload.get("id")
            response = self._handle_json_rpc(payload)
            status = HTTPStatus.OK
        except ValueError as exc:
            response = _json_rpc_error(None, -32700, f"Parse error: {exc}")
            status = HTTPStatus.BAD_REQUEST
        except TypeError as exc:
            response = _json_rpc_error(request_id, -32600, str(exc))
            status = HTTPStatus.OK

        elapsed_ms = (time.perf_counter() - started) * 1000
        log_network_event(
            event="mcp_jsonrpc_request",
            direction="inbound",
            source="worker_agent",
            target=self.server.server_name,
            method="POST",
            url=self.path,
            task_id=str(request_id) if request_id is not None else None,
            payload=payload,
            status_code=int(status),
            elapsed_ms=elapsed_ms,
            error=response.get("error", {}).get("message") if "error" in response else None,
        )
        self._send_json(status, response)

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _handle_json_rpc(self, payload: dict[str, Any]) -> dict[str, Any]:
        request_id = payload.get("id")
        if payload.get("jsonrpc") != "2.0":
            return _json_rpc_error(request_id, -32600, "Invalid Request: jsonrpc must be '2.0'")

        method = payload.get("method")
        if not isinstance(method, str) or not method:
            return _json_rpc_error(request_id, -32600, "Invalid Request: method is required")

        tool = self.server.tools.get(method)
        if tool is None:
            return _json_rpc_error(request_id, -32601, f"Method not found: {method}")

        params = payload.get("params", {})
        if params is None:
            params = {}
        if not isinstance(params, dict):
            return _json_rpc_error(request_id, -32602, "Invalid params: params must be an object")

        try:
            result = tool.handler(**params)
        except TypeError as exc:
            return _json_rpc_error(request_id, -32602, f"Invalid params: {exc}")
        except Exception as exc:
            return _json_rpc_error(request_id, -32603, f"Internal error: {exc}")

        if self.server.delay > 0:
            time.sleep(self.server.delay)

        return {"jsonrpc": "2.0", "result": result, "id": request_id}

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        raw_body = self.rfile.read(length).decode("utf-8") if length else ""
        try:
            payload = json.loads(raw_body or "{}")
        except json.JSONDecodeError as exc:
            raise ValueError(exc.msg) from exc

        if not isinstance(payload, dict):
            raise TypeError("Invalid Request: body must be a JSON object")
        return payload

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



def run_mcp_server(*, name: str, host: str, port: int, tools: dict[str, MCPTool], delay: float = 0.0) -> None:
    server = MCPHTTPServer(
        (host, port),
        MCPRequestHandler,
        server_name=name,
        tools=tools,
        delay=delay,
    )
    logger.info(f"{name} listening on http://{host}:{port}")
    logger.info("Endpoints: POST /, GET /health, GET /methods")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.critical(f"\n{name} shutting down.")
    finally:
        server.server_close()


def _json_rpc_error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "error": {
            "code": code,
            "message": message,
        },
        "id": request_id,
    }
