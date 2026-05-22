from __future__ import annotations

from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import re
import threading
import time
from typing import Any
from urllib import request, error

from common.config import (
    AGENTS,
    REGISTRY_HOST,
    REGISTRY_PORT,
    COORDINATOR_NAME,
    DISPATCH_HTTP_TIMEOUT_SECONDS,
    MCP_HTTP_TIMEOUT_SECONDS,
    MCP_SERVERS,
)
from common.http_client import HttpJsonClientError, post_json
import common.logger
import logging
from common.logger import log_network_event
from common.schemas import (
    PayloadValidationError,
    RESULT_ERROR,
    RESULT_SUCCESS,
    build_result_payload,
    error_response,
    success_response,
    validate_task_payload,
)
from llm_client import LLMClientError, llm
logger = logging.getLogger("base_agent")

KNOWN_CITIES = [
    "北京",
    "上海",
    "广州",
    "深圳",
    "杭州",
    "南京",
    "成都",
    "重庆",
    "武汉",
    "西安",
    "苏州",
    "天津",
]


class AgentHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        server_address: tuple[str, int],
        handler_class: type[BaseHTTPRequestHandler],
        agent: "BaseAgent",
    ) -> None:
        super().__init__(server_address, handler_class)
        self.agent = agent


class AgentRequestHandler(BaseHTTPRequestHandler):
    server: AgentHTTPServer

    def do_GET(self) -> None:
        if self.path == "/health":
            self._send_json(
                HTTPStatus.OK,
                success_response(
                    {
                        "role": self.server.agent.agent_name,
                        "status": "ok",
                        "capability": self.server.agent.capability,
                        "mcp_server_key": self.server.agent.mcp_server_key,
                    }
                ),
            )
            return

        self._send_json(
            HTTPStatus.NOT_FOUND,
            error_response("not_found", f"unknown path: {self.path}"),
        )

    def do_POST(self) -> None:
        if self.path == "/execute_task":
            self._handle_execute_task()
            return

        self._send_json(
            HTTPStatus.NOT_FOUND,
            error_response("not_found", f"unknown path: {self.path}"),
        )

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _handle_execute_task(self) -> None:
        try:
            payload, payload_size = self._read_json_with_size()
            validate_task_payload(payload)

            target = str(payload["target"])
            if target != self.server.agent.agent_name:
                raise PayloadValidationError(
                    f"target mismatch: expected {self.server.agent.agent_name}, got {target}"
                )

        except ValueError as exc:
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                error_response("invalid_json", str(exc)),
            )
            return
        except PayloadValidationError as exc:
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                error_response("invalid_task", str(exc)),
            )
            return

        task_id = str(payload["task_id"])

        log_network_event(
            event="agent_receive_task",
            direction="inbound",
            source=str(payload.get("source", "unknown")),
            target=self.server.agent.agent_name,
            method="POST",
            url="/execute_task",
            task_id=task_id,
            payload=payload,
            payload_size=payload_size,
        )

        worker = threading.Thread(
            target=self.server.agent.process_task,
            args=(payload,),
            name=f"{self.server.agent.agent_name}-{task_id[:8]}",
            daemon=True,
        )
        worker.start()

        self._send_json(
            HTTPStatus.OK,
            success_response(
                {
                    "accepted": True,
                    "agent": self.server.agent.agent_name,
                    "task_id": task_id,
                }
            ),
        )

    def _read_json_with_size(self) -> tuple[dict[str, Any], int]:
        length = int(self.headers.get("Content-Length", "0"))
        raw_body = self.rfile.read(length).decode("utf-8") if length else ""

        try:
            payload = json.loads(raw_body or "{}")
        except json.JSONDecodeError as exc:
            raise ValueError(f"request body must be valid JSON: {exc.msg}") from exc

        if not isinstance(payload, dict):
            raise ValueError("request body must be a JSON object")

        return payload, length

    def _send_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(int(status))
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class BaseAgent:
    agent_name: str = "base_agent"
    capability: str = "base"
    mcp_server_key: str = ""
    prompt_role: str = ""

    def __init__(self, *, host: str, port: int) -> None:
        self.host = host
        self.port = port

    def run(self) -> None:
        server = AgentHTTPServer((self.host, self.port), AgentRequestHandler, self)
        logger.info(
            f"{self.agent_name} listening on http://{self.host}:{self.port}"
        )
        logger.info("Endpoints: POST /execute_task, GET /health")

        try:
            registry_url = f"http://{REGISTRY_HOST}:{REGISTRY_PORT}/register"
            payload = AGENTS.get(self.agent_name, {}).copy()
            payload["agent_name"] = self.agent_name
            payload["host"] = self.host
            payload["port"] = self.port
            payload["execute_path"] = "/execute_task"
            
            req = request.Request(
                registry_url,
                data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with request.urlopen(req, timeout=3.0) as response:
                if response.status == 200:
                    logger.info(f"{self.agent_name} successfully registered to {registry_url}")
                else:
                    logger.warning(f"{self.agent_name} registry warning: {response.status}")
        except Exception as e:
            logger.error(f"{self.agent_name} failed to register: {e}")

        try:
            server.serve_forever()
        except KeyboardInterrupt:
            logger.info(f"\n{self.agent_name} shutting down.")
        finally:
            server.server_close()

    def process_task(self, task_payload: dict[str, Any]) -> None:
        task_id = str(task_payload["task_id"])
        started = time.perf_counter()

        try:
            mcp_result = self.call_mcp_server(task_payload)
            prompt = self.build_prompt(task_payload, mcp_result)

            try:
                agent_answer = llm.chat(prompt)
                llm_error = None
            except LLMClientError as exc:
                llm_error = str(exc)
                agent_answer = self.build_fallback_answer(task_payload, mcp_result, llm_error)

            elapsed_ms = (time.perf_counter() - started) * 1000

            result_payload = build_result_payload(
                source=self.agent_name,
                target=COORDINATOR_NAME,
                task_id=task_id,
                status=RESULT_SUCCESS,
                result=agent_answer,
                metadata={
                    "agent": self.agent_name,
                    "capability": self.capability,
                    "mcp_server": MCP_SERVERS[self.mcp_server_key]["name"],
                    "mcp_method": MCP_SERVERS[self.mcp_server_key]["method"],
                    "mcp_result": mcp_result,
                    "llm_error": llm_error,
                    "elapsed_ms": round(elapsed_ms, 2),
                },
            )

        except Exception as exc:
            elapsed_ms = (time.perf_counter() - started) * 1000

            result_payload = build_result_payload(
                source=self.agent_name,
                target=COORDINATOR_NAME,
                task_id=task_id,
                status=RESULT_ERROR,
                result=None,
                error=str(exc),
                metadata={
                    "agent": self.agent_name,
                    "capability": self.capability,
                    "elapsed_ms": round(elapsed_ms, 2),
                },
            )

        self.send_result_to_coordinator(task_payload, result_payload)

    def call_mcp_server(self, task_payload: dict[str, Any]) -> dict[str, Any]:
        task_id = str(task_payload["task_id"])
        server = MCP_SERVERS[self.mcp_server_key]
        url = f"http://{server['host']}:{server['port']}{server.get('path', '/')}"
        method = str(server["method"])

        rpc_payload = {
            "jsonrpc": "2.0",
            "id": task_id,
            "method": method,
            "params": self.build_mcp_params(task_payload),
        }

        log_network_event(
            event="agent_call_mcp",
            direction="outbound",
            source=self.agent_name,
            target=server["name"],
            method="POST",
            url=url,
            task_id=task_id,
            payload=rpc_payload,
            payload_size=len(json.dumps(rpc_payload, ensure_ascii=False, default=str).encode("utf-8")),
        )

        try:
            response = post_json(url, rpc_payload, timeout=MCP_HTTP_TIMEOUT_SECONDS)
        except HttpJsonClientError as exc:
            log_network_event(
                event="agent_mcp_failed",
                direction="inbound",
                source=server["name"],
                target=self.agent_name,
                method="POST",
                url=exc.url,
                task_id=task_id,
                latency_ms=exc.elapsed_ms,
                error=str(exc),
                error_type=_infer_error_type(exc),
            )
            raise RuntimeError(f"MCP request failed: {exc}") from exc

        log_network_event(
            event="agent_mcp_response",
            direction="inbound",
            source=server["name"],
            target=self.agent_name,
            method="POST",
            url=url,
            task_id=task_id,
            status_code=response.status_code,
            latency_ms=response.elapsed_ms,
            payload_size=len(response.raw_body.encode("utf-8")),
            payload=response.data,
        )

        if not response.ok:
            raise RuntimeError(f"MCP server returned HTTP {response.status_code}")

        if not isinstance(response.data, dict):
            raise RuntimeError("MCP response body must be a JSON object")

        if "error" in response.data and response.data["error"]:
            raise RuntimeError(f"MCP JSON-RPC error: {response.data['error']}")

        if "result" not in response.data:
            raise RuntimeError("MCP JSON-RPC response missing result")

        result = response.data["result"]
        if not isinstance(result, dict):
            raise RuntimeError("MCP result must be a JSON object")

        return result

    def send_result_to_coordinator(
        self,
        task_payload: dict[str, Any],
        result_payload: dict[str, Any],
    ) -> None:
        task_id = str(task_payload["task_id"])
        reply_to = str(task_payload["reply_to"])

        log_network_event(
            event="agent_callback_result",
            direction="outbound",
            source=self.agent_name,
            target=COORDINATOR_NAME,
            method="POST",
            url=reply_to,
            task_id=task_id,
            payload=result_payload,
            payload_size=len(json.dumps(result_payload, ensure_ascii=False, default=str).encode("utf-8")),
        )

        try:
            response = post_json(
                reply_to,
                result_payload,
                timeout=DISPATCH_HTTP_TIMEOUT_SECONDS,
            )
        except HttpJsonClientError as exc:
            log_network_event(
                event="agent_callback_failed",
                direction="inbound",
                source=COORDINATOR_NAME,
                target=self.agent_name,
                method="POST",
                url=exc.url,
                task_id=task_id,
                latency_ms=exc.elapsed_ms,
                error=str(exc),
                error_type=_infer_error_type(exc),
            )
            return

        log_network_event(
            event="agent_callback_response",
            direction="inbound",
            source=COORDINATOR_NAME,
            target=self.agent_name,
            method="POST",
            url=reply_to,
            task_id=task_id,
            status_code=response.status_code,
            latency_ms=response.elapsed_ms,
            payload_size=len(response.raw_body.encode("utf-8")),
            payload=response.data,
        )

    def build_mcp_params(self, task_payload: dict[str, Any]) -> dict[str, Any]:
        instruction = str(task_payload.get("instruction", ""))
        context = task_payload.get("context", {})
        city = extract_city(instruction, context)
        return {"city": city}

    def build_prompt(self, task_payload: dict[str, Any], mcp_result: dict[str, Any]) -> str:
        raise NotImplementedError

    def build_fallback_answer(
        self,
        task_payload: dict[str, Any],
        mcp_result: dict[str, Any],
        llm_error: str,
    ) -> str:
        return (
            f"{self.agent_name} 已获得 MCP 数据，但 LLM 调用失败。"
            f"原始 MCP 数据为：{json.dumps(mcp_result, ensure_ascii=False)}。"
            f"LLM 错误：{llm_error}"
        )


def extract_city(instruction: str, context: dict[str, Any] | None = None) -> str:
    context_text = json.dumps(context or {}, ensure_ascii=False)
    full_text = instruction + "\n" + context_text

    for city in KNOWN_CITIES:
        if city in full_text:
            return city

    match = re.search(r"去([\u4e00-\u9fa5]{2,4})", full_text)
    if match:
        return match.group(1)

    return "北京"


def _infer_error_type(exc: Exception) -> str:
    cause = exc.__cause__
    if cause is None:
        return type(exc).__name__
    reason = getattr(cause, "reason", None)
    if reason is not None:
        return type(reason).__name__
    return type(cause).__name__
