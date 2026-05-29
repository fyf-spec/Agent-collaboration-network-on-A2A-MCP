from __future__ import annotations

from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import re
from socketserver import BaseRequestHandler, ThreadingTCPServer
import threading
import time
from typing import Any
from urllib import request

from common.config import (
    A2A_TCP_TIMEOUT_SECONDS,
    AGENTS,
    REGISTRY_HOST,
    REGISTRY_PORT,
    COORDINATOR_NAME,
    MCP_GATEWAY,
    MCP_HTTP_TIMEOUT_SECONDS,
    MCP_SERVERS,
)
from common.http_client import HttpJsonClientError, post_json
import common.logger
import logging
from common.logger import log_network_event
from common.schemas import (
    PayloadValidationError,
    RESULT_SUCCESS,
    build_error_result_payload,
    build_result_payload,
    error_response,
    success_response,
    validate_task_payload,
)
from common.tcp_a2a import (
    TYPE_ERROR,
    TYPE_RESULT_ACK,
    TYPE_TASK_ACK,
    TYPE_TASK_REQUEST,
    TYPE_TASK_RESULT,
    TcpA2AError,
    build_envelope,
    build_error_envelope,
    parse_tcp_url,
    recv_frame,
    request_frame,
    send_frame,
    tcp_url,
    validate_envelope,
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
        self._send_json(HTTPStatus.NOT_FOUND, error_response("not_found", f"unknown path: {self.path}"))

    def do_POST(self) -> None:
        if self.path == "/execute_task":
            self._handle_execute_task()
            return
        self._send_json(HTTPStatus.NOT_FOUND, error_response("not_found", f"unknown path: {self.path}"))

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
            self._send_json(HTTPStatus.BAD_REQUEST, error_response("invalid_json", str(exc)))
            return
        except PayloadValidationError as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, error_response("invalid_task", str(exc)))
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
            success_response({"accepted": True, "agent": self.server.agent.agent_name, "task_id": task_id}),
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


class AgentA2ATCPServer(ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(
        self,
        server_address: tuple[str, int],
        handler_class: type[BaseRequestHandler],
        agent: "BaseAgent",
    ) -> None:
        super().__init__(server_address, handler_class)
        self.agent = agent


class AgentA2ATCPRequestHandler(BaseRequestHandler):
    server: AgentA2ATCPServer

    def handle(self) -> None:
        self.request.settimeout(A2A_TCP_TIMEOUT_SECONDS)
        frame_data: dict[str, Any] | None = None
        task_id = "unknown"
        source = "unknown"
        trace_id: str | None = None
        span_id: str | None = None
        try:
            frame = recv_frame(self.request)
            frame_data = frame.data
            validate_envelope(frame_data, expected_type=TYPE_TASK_REQUEST)
            task_id = str(frame_data["task_id"])
            source = str(frame_data["source"])
            trace_id = str(frame_data["trace_id"])
            span_id = str(frame_data["span_id"])

            payload = frame_data["payload"]
            validate_task_payload(payload)
            target = str(payload["target"])
            if target != self.server.agent.agent_name:
                raise PayloadValidationError(
                    f"target mismatch: expected {self.server.agent.agent_name}, got {target}"
                )

            context = payload.setdefault("context", {})
            context["trace_id"] = trace_id
            context["parent_span_id"] = span_id

            log_network_event(
                event="agent_receive_task",
                direction="inbound",
                source=source,
                target=self.server.agent.agent_name,
                method="TCP",
                url=tcp_url(self.server.agent.host, self.server.agent.port),
                task_id=task_id,
                payload=frame_data,
                payload_size=frame.length,
            )

            worker = threading.Thread(
                target=self.server.agent.process_task,
                args=(payload,),
                name=f"{self.server.agent.agent_name}-{task_id[:8]}",
                daemon=True,
            )
            worker.start()

            ack = build_envelope(
                message_type=TYPE_TASK_ACK,
                source=self.server.agent.agent_name,
                target=source,
                task_id=task_id,
                trace_id=trace_id,
                parent_span_id=span_id,
                payload={"accepted": True, "agent": self.server.agent.agent_name, "task_id": task_id},
            )
            send_frame(self.request, ack)
        except Exception as exc:
            error_task_id = str(frame_data.get("task_id")) if frame_data and frame_data.get("task_id") else task_id
            error_target = source if source != "unknown" else COORDINATOR_NAME
            error_frame = build_error_envelope(
                source=self.server.agent.agent_name,
                target=error_target,
                task_id=error_task_id,
                trace_id=trace_id,
                parent_span_id=span_id,
                error=str(exc),
            )
            log_network_event(
                event="agent_receive_task_failed",
                direction="inbound",
                source=source,
                target=self.server.agent.agent_name,
                method="TCP",
                url=tcp_url(self.server.agent.host, self.server.agent.port),
                task_id=None if error_task_id == "unknown" else error_task_id,
                payload=frame_data,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            try:
                send_frame(self.request, error_frame)
            except Exception:
                return


class BaseAgent:
    agent_name: str = "base_agent"
    capability: str = "base"
    mcp_server_key: str = ""
    prompt_role: str = ""

    def __init__(self, *, host: str, port: int) -> None:
        self.host = host
        self.port = port

    def run(self) -> None:
        config = AGENTS.get(self.agent_name, {})
        protocol = config.get("protocol", "tcp")
        self._register_with_registry()
        
        threading.Thread(target=self._heartbeat_loop, daemon=True).start()
        
        if protocol == "http":
            self._run_http()
        else:
            self._run_tcp()

    def _run_http(self) -> None:
        server = AgentHTTPServer((self.host, self.port), AgentRequestHandler, self)
        logger.info(f"{self.agent_name} listening on http://{self.host}:{self.port}")
        logger.info("Endpoints: POST /execute_task, GET /health")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            logger.info(f"\n{self.agent_name} shutting down.")
        finally:
            server.server_close()

    def _run_tcp(self) -> None:
        server = AgentA2ATCPServer((self.host, self.port), AgentA2ATCPRequestHandler, self)
        logger.info(f"{self.agent_name} A2A TCP listening on {tcp_url(self.host, self.port)}")
        logger.info("Protocol: 4-byte big-endian length prefix + UTF-8 JSON body")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            logger.info(f"\n{self.agent_name} shutting down.")
        finally:
            server.server_close()

    def _heartbeat_loop(self) -> None:
        registry_url = f"http://{REGISTRY_HOST}:{REGISTRY_PORT}/heartbeat"
        payload = json.dumps({"agent_name": self.agent_name}, ensure_ascii=False).encode("utf-8")
        
        while True:
            try:
                req = request.Request(
                    registry_url,
                    data=payload,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with request.urlopen(req, timeout=1.0) as response:
                    pass
            except Exception as exc:
                logger.debug(f"{self.agent_name} failed to send heartbeat: {exc}")
            time.sleep(2.0)

    def _register_with_registry(self) -> None:
        try:
            registry_url = f"http://{REGISTRY_HOST}:{REGISTRY_PORT}/register"
            payload = self.registration_payload()
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
        except Exception as exc:
            logger.error(f"{self.agent_name} failed to register: {exc}")

    def registration_payload(self) -> dict[str, Any]:
        config = AGENTS.get(self.agent_name, {})
        return {
            "agent_name": self.agent_name,
            "host": self.host,
            "port": self.port,
            "protocol": config.get("protocol", "tcp"),
            "execute_path": config.get("execute_path", "/execute_task"),
            "enabled": config.get("enabled", True),
            "capabilities": config.get("capabilities", [self.capability]),
            "keywords": config.get("keywords", []),
        }

    def process_task(self, task_payload: dict[str, Any]) -> None:
        task_id = str(task_payload["task_id"])
        started = time.perf_counter()
        try:
            mcp_result = self.call_mcp_server(task_payload)
            prompt = self.build_prompt(task_payload, mcp_result)
            if _demo_fast_mode_enabled():
                llm_error = "demo_fast_mode"
                agent_answer = self.build_demo_answer(task_payload, mcp_result)
            else:
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
                    "mcp_gateway": MCP_GATEWAY["name"],
                    "mcp_result": mcp_result,
                    "llm_error": llm_error,
                    "elapsed_ms": round(elapsed_ms, 2),
                },
            )
        except Exception as exc:
            elapsed_ms = (time.perf_counter() - started) * 1000
            result_payload = build_error_result_payload(
                source=self.agent_name,
                target=COORDINATOR_NAME,
                task_id=task_id,
                message=str(exc),
                error_code="agent_execution_failed",
                http_status=500,
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
        url = f"http://{MCP_GATEWAY['host']}:{MCP_GATEWAY['port']}{MCP_GATEWAY.get('path', '/')}"
        network_target = str(MCP_GATEWAY["name"])
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
            target=network_target,
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
                source=network_target,
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
            source=network_target,
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
        reply_to = str(task_payload["reply_to"])
        if reply_to.startswith("tcp://"):
            self._send_result_to_coordinator_tcp(task_payload, result_payload)
        else:
            self._send_result_to_coordinator_http(task_payload, result_payload)

    def _send_result_to_coordinator_tcp(self, task_payload: dict[str, Any], result_payload: dict[str, Any]) -> None:
        task_id = str(task_payload["task_id"])
        reply_to = str(task_payload["reply_to"])
        context = task_payload.get("context", {})
        trace_id = str(context.get("trace_id") or f"trace-{task_id}")
        parent_span_id = context.get("parent_span_id")
        frame = build_envelope(
            message_type=TYPE_TASK_RESULT,
            source=self.agent_name,
            target=COORDINATOR_NAME,
            task_id=task_id,
            trace_id=trace_id,
            parent_span_id=str(parent_span_id) if parent_span_id else None,
            payload=result_payload,
        )
        log_network_event(
            event="agent_callback_result",
            direction="outbound",
            source=self.agent_name,
            target=COORDINATOR_NAME,
            method="TCP",
            url=reply_to,
            task_id=task_id,
            payload=frame,
            payload_size=len(json.dumps(frame, ensure_ascii=False, default=str).encode("utf-8")),
        )
        try:
            host, port = parse_tcp_url(reply_to)
            response = request_frame(host=host, port=port, payload=frame, timeout=A2A_TCP_TIMEOUT_SECONDS)
        except TcpA2AError as exc:
            log_network_event(
                event="agent_callback_failed",
                direction="inbound",
                source=COORDINATOR_NAME,
                target=self.agent_name,
                method="TCP",
                url=reply_to,
                task_id=task_id,
                error=str(exc),
                error_type=_infer_error_type(exc),
            )
            return
        log_network_event(
            event="agent_callback_response",
            direction="inbound",
            source=COORDINATOR_NAME,
            target=self.agent_name,
            method="TCP",
            url=reply_to,
            task_id=task_id,
            latency_ms=response.elapsed_ms,
            payload_size=response.received_length,
            payload=response.data,
        )
        try:
            validate_envelope(response.data)
        except TcpA2AError as exc:
            logger.error(f"{self.agent_name} received invalid callback ack: {exc}")
            return
        if response.data.get("type") == TYPE_ERROR:
            error_payload = response.data.get("payload", {})
            logger.error(f"{self.agent_name} callback rejected: {error_payload.get('error')}")
        elif response.data.get("type") != TYPE_RESULT_ACK:
            logger.error(f"{self.agent_name} callback got unexpected TCP response: {response.data.get('type')}")

    def _send_result_to_coordinator_http(self, task_payload: dict[str, Any], result_payload: dict[str, Any]) -> None:
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
            response = post_json(reply_to, result_payload, timeout=A2A_TCP_TIMEOUT_SECONDS)
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

    def build_demo_answer(self, task_payload: dict[str, Any], mcp_result: dict[str, Any]) -> str:
        return (
            f"{self.agent_name} 已获得 MCP 数据。"
            f"当前为演示快速模式，跳过外部 LLM 调用。"
            f"原始 MCP 数据为：{json.dumps(mcp_result, ensure_ascii=False)}。"
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


def _demo_fast_mode_enabled() -> bool:
    return os.getenv("A2A_DEMO_FAST", "").strip().lower() in {"1", "true", "yes", "on"}
