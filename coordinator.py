"""Coordinator process for the local HTTP-based A2A demo.

Run:
    python coordinator.py

Main endpoints:
    POST /submit_task   {"question": "...", "timeout": 10}
    TCP 9001           Agent callback with a length-prefixed A2A result frame
    GET  /health
    GET  /tasks?task_id=<id>
    GET  /contracts     Interfaces for worker-agent and MCP teammates
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from socketserver import BaseRequestHandler, ThreadingTCPServer
import threading
import time
from typing import Any
from urllib.parse import parse_qs, urlparse
from urllib import request
import logging

from common.config import (
    A2A_TCP_TIMEOUT_SECONDS,
    AGENTS,
    REGISTRY_HOST,
    REGISTRY_PORT,
    COORDINATOR_A2A_TCP_HOST,
    COORDINATOR_A2A_TCP_PORT,
    COORDINATOR_HOST,
    COORDINATOR_NAME,
    COORDINATOR_PORT,
    DEFAULT_TASK_TIMEOUT_SECONDS,
    MAX_TASK_TIMEOUT_SECONDS,
    MCP_SERVERS,
    TRAVEL_KEYWORDS,
)
from common.logger import log_network_event
from common.tcp_a2a import (
    TYPE_ERROR,
    TYPE_RESULT_ACK,
    TYPE_TASK_ACK,
    TYPE_TASK_REQUEST,
    TYPE_TASK_RESULT,
    TcpA2AError,
    build_envelope,
    build_error_envelope,
    request_frame,
    send_frame,
    recv_frame,
    tcp_url,
    validate_envelope,
)


logger = logging.getLogger("coordinator")

from common.schemas import (
    PayloadValidationError,
    RESULT_SUCCESS,
    TASK_COMPLETED,
    TASK_FAILED,
    TASK_PARTIAL,
    TASK_PENDING,
    TASK_WAITING,
    build_task_payload,
    error_response,
    new_task_id,
    success_response,
    utc_now_iso,
    validate_task_result,
)
from llm_client import LLMClientError, llm


COORDINATOR_DISPATCH_FLOW = [
    {
        "step": 1,
        "name": "user_submit",
        "required_flow": "用户提问 -> Coordinator",
        "network": "HTTP POST /submit_task",
        "owner": "coordinator",
    },
    {
        "step": 2,
        "name": "plan_and_select_agents",
        "required_flow": "Coordinator 根据问题选择工作 Agent",
        "network": "internal only; no direct Agent function call",
        "owner": "coordinator",
    },
    {
        "step": 3,
        "name": "dispatch_to_worker_agents",
        "required_flow": "Coordinator -> 工作 Agent",
        "network": "TCP A2A TASK_REQUEST frame with 4-byte length prefix",
        "owner": "coordinator sends; worker agents implement receivers",
    },
    {
        "step": 4,
        "name": "worker_agent_calls_mcp",
        "required_flow": "工作 Agent -> MCP Server -> 工作 Agent",
        "network": "HTTP POST JSON-RPC 2.0",
        "owner": "worker agent and MCP teammates",
    },
    {
        "step": 5,
        "name": "worker_agent_callback",
        "required_flow": "工作 Agent -> Coordinator",
        "network": "TCP A2A TASK_RESULT frame with 4-byte length prefix",
        "owner": "worker agents send; coordinator receives",
    },
    {
        "step": 6,
        "name": "aggregate_final_plan",
        "required_flow": "Coordinator 汇总输出最终旅行方案",
        "network": "HTTP response to /submit_task",
        "owner": "coordinator",
    },
]


@dataclass
class TaskRecord:
    task_id: str
    question: str
    targets: list[str]
    created_at: str
    timeout_seconds: float
    plan: dict[str, Any] = field(default_factory=dict)
    status: str = TASK_PENDING
    results: dict[str, dict[str, Any]] = field(default_factory=dict)
    dispatch_errors: dict[str, str] = field(default_factory=dict)
    final_answer: str | None = None
    updated_at: str = field(default_factory=utc_now_iso)

    def expected_count(self) -> int:
        return len(self.targets)

    def terminal_count(self) -> int:
        return len(self.results) + len(self.dispatch_errors)

    def success_count(self) -> int:
        return sum(1 for item in self.results.values() if item.get("status") == RESULT_SUCCESS)

    def pending_targets(self) -> list[str]:
        finished = set(self.results) | set(self.dispatch_errors)
        return [target for target in self.targets if target not in finished]

    def refresh_status(self) -> None:
        if not self.targets:
            self.status = TASK_FAILED
        elif self.terminal_count() < self.expected_count():
            self.status = TASK_WAITING
        elif self.success_count() == self.expected_count():
            self.status = TASK_COMPLETED
        elif self.success_count() > 0:
            self.status = TASK_PARTIAL
        else:
            self.status = TASK_FAILED
        self.updated_at = utc_now_iso()

    def finalize_after_wait(self) -> None:
        if self.terminal_count() >= self.expected_count():
            self.refresh_status()
        elif self.success_count() > 0:
            self.status = TASK_PARTIAL
            self.updated_at = utc_now_iso()
        else:
            self.status = TASK_FAILED
            self.updated_at = utc_now_iso()

    def snapshot(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "question": self.question,
            "status": self.status,
            "targets": self.targets,
            "expected_count": self.expected_count(),
            "success_count": self.success_count(),
            "plan": self.plan,
            "results": self.results,
            "dispatch_errors": self.dispatch_errors,
            "pending_targets": self.pending_targets(),
            "final_answer": self.final_answer,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class CoordinatorState:
    def __init__(self, *, host: str, port: int, tcp_host: str, tcp_port: int) -> None:
        self.host = host
        self.port = port
        self.tcp_host = tcp_host
        self.tcp_port = tcp_port
        self._tasks: dict[str, TaskRecord] = {}
        self._condition = threading.Condition(threading.RLock())

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    @property
    def reply_to(self) -> str:
        return tcp_url(self.tcp_host, self.tcp_port)

    def create_task(
        self,
        question: str,
        targets: list[str],
        timeout_seconds: float,
        plan: dict[str, Any] | None = None,
    ) -> TaskRecord:
        record = TaskRecord(
            task_id=new_task_id(),
            question=question,
            targets=targets,
            created_at=utc_now_iso(),
            timeout_seconds=timeout_seconds,
            plan=plan or {},
        )
        with self._condition:
            self._tasks[record.task_id] = record
            self._condition.notify_all()
        return record

    def get_task(self, task_id: str) -> TaskRecord | None:
        with self._condition:
            return self._tasks.get(task_id)

    def list_tasks(self) -> list[dict[str, Any]]:
        with self._condition:
            return [task.snapshot() for task in self._tasks.values()]

    def add_result(self, payload: dict[str, Any]) -> TaskRecord:
        validate_task_result(payload)
        task_id = str(payload["task_id"])
        source = str(payload["source"])
        with self._condition:
            record = self._tasks.get(task_id)
            if record is None:
                raise KeyError(task_id)
            if source not in record.targets:
                raise PayloadValidationError(f"unexpected result source: {source}")
            record.results[source] = payload
            record.dispatch_errors.pop(source, None)
            record.refresh_status()
            self._condition.notify_all()
            return record

    def mark_dispatch_error(self, task_id: str, target: str, message: str) -> None:
        with self._condition:
            record = self._tasks.get(task_id)
            if record is None:
                return
            record.dispatch_errors[target] = message
            record.results.pop(target, None)
            record.refresh_status()
            self._condition.notify_all()

    def wait_for_task(self, task_id: str, timeout_seconds: float) -> dict[str, Any]:
        deadline = time.monotonic() + timeout_seconds
        with self._condition:
            record = self._tasks[task_id]
            record.status = TASK_WAITING
            while record.terminal_count() < record.expected_count():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._condition.wait(timeout=remaining)
            record.finalize_after_wait()
            return record.snapshot()

    def set_final_answer(self, task_id: str, final_answer: str) -> dict[str, Any]:
        with self._condition:
            record = self._tasks[task_id]
            record.final_answer = final_answer
            record.updated_at = utc_now_iso()
            self._condition.notify_all()
            return record.snapshot()


class CoordinatorHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        server_address: tuple[str, int],
        handler_class: type[BaseHTTPRequestHandler],
        state: CoordinatorState,
    ) -> None:
        super().__init__(server_address, handler_class)
        self.state = state

    def dispatch_to_agent(self, record: TaskRecord, target: str) -> None:
        agent_config = _fetch_discovered_agents().get(target)
        if not agent_config:
            self.state.mark_dispatch_error(record.task_id, target, f"target agent not found: {target}")
            return
        if agent_config.get("protocol", "tcp") != "tcp":
            self.state.mark_dispatch_error(
                record.task_id,
                target,
                f"unsupported A2A protocol: {agent_config.get('protocol')}",
            )
            return

        url = _agent_tcp_url(agent_config)
        trace_id = f"trace-{record.task_id}"
        span_id = f"span-{COORDINATOR_NAME}-dispatch-{target}"
        task_payload = build_task_payload(
            source=COORDINATOR_NAME,
            target=target,
            task_id=record.task_id,
            instruction=record.question,
            reply_to=self.state.reply_to,
            created_at=record.created_at,
            context={
                "selected_by": record.plan.get("selected_by", "rule"),
                "coordinator_plan": record.plan,
                "agent_capabilities": agent_config.get("capabilities", []),
                "trace_id": trace_id,
                "parent_span_id": span_id,
            },
        )
        frame = build_envelope(
            message_type=TYPE_TASK_REQUEST,
            source=COORDINATOR_NAME,
            target=target,
            task_id=record.task_id,
            trace_id=trace_id,
            span_id=span_id,
            deadline_ms=int(record.timeout_seconds * 1000),
            payload=task_payload,
        )
        log_network_event(
            event="dispatch_task",
            direction="outbound",
            source=COORDINATOR_NAME,
            target=target,
            method="TCP",
            url=url,
            task_id=record.task_id,
            payload=frame,
            payload_size=len(json.dumps(frame, ensure_ascii=False, default=str).encode("utf-8")),
        )
        try:
            response = request_frame(
                host=str(agent_config["host"]),
                port=int(agent_config["port"]),
                payload=frame,
                timeout=A2A_TCP_TIMEOUT_SECONDS,
            )
        except TcpA2AError as exc:
            message = str(exc)
            self.state.mark_dispatch_error(record.task_id, target, message)
            log_network_event(
                event="dispatch_failed",
                direction="inbound",
                source=target,
                target=COORDINATOR_NAME,
                method="TCP",
                url=url,
                task_id=record.task_id,
                error=message,
                error_type=_infer_error_type(exc),
            )
            return

        log_network_event(
            event="dispatch_response",
            direction="inbound",
            source=target,
            target=COORDINATOR_NAME,
            method="TCP",
            url=url,
            task_id=record.task_id,
            latency_ms=response.elapsed_ms,
            payload_size=response.received_length,
            payload=response.data,
        )
        try:
            validate_envelope(response.data)
        except TcpA2AError as exc:
            self.state.mark_dispatch_error(record.task_id, target, f"invalid TCP response: {exc}")
            return

        response_type = response.data.get("type")
        if response_type == TYPE_ERROR:
            error_payload = response.data.get("payload", {})
            self.state.mark_dispatch_error(
                record.task_id,
                target,
                str(error_payload.get("error", "agent returned TCP ERROR")),
            )
            return
        if response_type == TYPE_TASK_RESULT:
            try:
                self.state.add_result(response.data["payload"])
            except (KeyError, PayloadValidationError) as exc:
                self.state.mark_dispatch_error(record.task_id, target, f"invalid immediate result: {exc}")
            return
        if response_type != TYPE_TASK_ACK:
            self.state.mark_dispatch_error(record.task_id, target, f"unexpected TCP response type: {response_type}")


class CoordinatorA2ATCPServer(ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(
        self,
        server_address: tuple[str, int],
        handler_class: type[BaseRequestHandler],
        state: CoordinatorState,
    ) -> None:
        super().__init__(server_address, handler_class)
        self.state = state


class CoordinatorA2ATCPRequestHandler(BaseRequestHandler):
    server: CoordinatorA2ATCPServer

    def handle(self) -> None:
        frame_data: dict[str, Any] | None = None
        task_id = "unknown"
        source = "unknown"
        trace_id: str | None = None
        span_id: str | None = None
        try:
            frame = recv_frame(self.request)
            frame_data = frame.data
            validate_envelope(frame_data, expected_type=TYPE_TASK_RESULT)
            task_id = str(frame_data["task_id"])
            source = str(frame_data["source"])
            trace_id = str(frame_data["trace_id"])
            span_id = str(frame_data["span_id"])
            payload = frame_data["payload"]

            log_network_event(
                event="task_result",
                direction="inbound",
                source=source,
                target=COORDINATOR_NAME,
                method="TCP",
                url=self.server.state.reply_to,
                task_id=task_id,
                payload=frame_data,
                payload_size=frame.length,
            )

            record = self.server.state.add_result(payload)
            ack = build_envelope(
                message_type=TYPE_RESULT_ACK,
                source=COORDINATOR_NAME,
                target=source,
                task_id=task_id,
                trace_id=trace_id,
                parent_span_id=span_id,
                payload={
                    "received": True,
                    "task_id": record.task_id,
                    "task_status": record.status,
                },
            )
            send_frame(self.request, ack)
        except Exception as exc:
            error_task_id = task_id
            if frame_data and frame_data.get("task_id"):
                error_task_id = str(frame_data["task_id"])
            error_source = source if source != "unknown" else "tcp_peer"
            error_frame = build_error_envelope(
                source=COORDINATOR_NAME,
                target=error_source,
                task_id=error_task_id,
                trace_id=trace_id,
                parent_span_id=span_id,
                error=str(exc),
            )
            log_network_event(
                event="task_result_failed",
                direction="inbound",
                source=error_source,
                target=COORDINATOR_NAME,
                method="TCP",
                url=self.server.state.reply_to,
                task_id=None if error_task_id == "unknown" else error_task_id,
                payload=frame_data,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            try:
                send_frame(self.request, error_frame)
            except Exception:
                return


class CoordinatorRequestHandler(BaseHTTPRequestHandler):
    server: CoordinatorHTTPServer

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            self._send_json(
                HTTPStatus.OK,
                success_response(
                    {
                        "role": COORDINATOR_NAME,
                        "status": "ok",
                        "base_url": self.server.state.base_url,
                        "a2a_tcp_url": self.server.state.reply_to,
                        "agents": _enabled_agents_view(),
                        "mcp_servers": _mcp_servers_view(),
                        "llm": llm.info(),
                        "contracts_url": f"{self.server.state.base_url}/contracts",
                    }
                ),
            )
            return
        if parsed.path == "/contracts":
            self._send_json(
                HTTPStatus.OK,
                success_response(
                    {
                        "flow": COORDINATOR_DISPATCH_FLOW,
                        "interfaces": build_collaboration_contracts(self.server.state),
                    }
                ),
            )
            return
        if parsed.path == "/tasks":
            self._handle_get_tasks(parsed.query)
            return
        self._send_json(HTTPStatus.NOT_FOUND, error_response("not_found", f"unknown path: {parsed.path}"))

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/submit_task":
            try:
                self._handle_submit_task()
            except Exception as e:
                logger.exception("Crash in _handle_submit_task")
                self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, error_response("internal_error", str(e)))
            return
        self._send_json(HTTPStatus.NOT_FOUND, error_response("not_found", f"unknown path: {parsed.path}"))

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _handle_submit_task(self) -> None:
        try:
            payload, payload_size = self._read_json_with_size()
            question = str(payload.get("question", "")).strip()
            if not question:
                self._send_json(HTTPStatus.BAD_REQUEST, error_response("invalid_request", "question is required"))
                return
            timeout_seconds = _normalize_timeout(payload.get("timeout"))
        except ValueError as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, error_response("invalid_json", str(exc)))
            return

        targets = select_targets(question)
        plan = build_coordinator_plan(question, targets)
        record = self.server.state.create_task(question, targets, timeout_seconds, plan)
        log_network_event(
            event="submit_task",
            direction="inbound",
            source="user",
            target=COORDINATOR_NAME,
            method="POST",
            url="/submit_task",
            task_id=record.task_id,
            payload=payload,
            payload_size=payload_size,
        )

        for target in targets:
            thread = threading.Thread(
                target=self.server.dispatch_to_agent,
                args=(record, target),
                name=f"dispatch-{record.task_id[:8]}-{target}",
                daemon=True,
            )
            thread.start()

        snapshot = self.server.state.wait_for_task(record.task_id, timeout_seconds)
        final_answer = build_final_answer(question, snapshot)
        snapshot = self.server.state.set_final_answer(record.task_id, final_answer)
        log_network_event(
            event="task_finished",
            direction="internal",
            source=COORDINATOR_NAME,
            target="user",
            task_id=record.task_id,
            payload=snapshot,
        )
        http_status = HTTPStatus.OK if snapshot["status"] != TASK_FAILED else HTTPStatus.GATEWAY_TIMEOUT
        self._send_json(http_status, success_response({"task": snapshot}))

    def _handle_get_tasks(self, query: str) -> None:
        params = parse_qs(query)
        task_id = params.get("task_id", [None])[0]
        if task_id:
            record = self.server.state.get_task(task_id)
            if record is None:
                self._send_json(
                    HTTPStatus.NOT_FOUND,
                    error_response("unknown_task", f"task_id not found: {task_id}"),
                )
                return
            self._send_json(HTTPStatus.OK, success_response({"task": record.snapshot()}))
            return
        self._send_json(HTTPStatus.OK, success_response({"tasks": self.server.state.list_tasks()}))

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


def _fetch_discovered_agents() -> dict[str, Any]:
    try:
        url = f"http://{REGISTRY_HOST}:{REGISTRY_PORT}/discover"
        req = request.Request(url, method="GET")
        with request.urlopen(req, timeout=3.0) as response:
            if response.status == 200:
                data = json.loads(response.read().decode("utf-8"))
                return data.get("agents", {})
    except Exception as e:
        logger.error(f"Coordinator failed to discover agents from registry: {e}, fall back to config")
    # Fallback to local config if registry is unreachable
    return AGENTS


def _infer_error_type(exc: Exception) -> str:
    cause = exc.__cause__
    if cause is None:
        return type(exc).__name__
    reason = getattr(cause, "reason", None)
    if reason is not None:
        return type(reason).__name__
    return type(cause).__name__

def select_targets(question: str) -> list[str]:
    discovered_agents = _fetch_discovered_agents()
    lowered = question.lower()
    enabled_agents = [name for name, agent in discovered_agents.items() if agent.get("enabled", True)]
    if _contains_any(lowered, TRAVEL_KEYWORDS):
        return enabled_agents

    selected: list[str] = []
    for name in enabled_agents:
        keywords = discovered_agents[name].get("keywords", [])
        if _contains_any(lowered, keywords):
            selected.append(name)
    return selected or enabled_agents


def build_coordinator_plan(question: str, targets: list[str]) -> dict[str, Any]:
    prompt = _coordinator_plan_prompt(question, targets)
    try:
        llm_response = llm.chat(prompt)
        llm_error = None
    except LLMClientError as exc:
        llm_response = ""
        llm_error = str(exc)

    plan: dict[str, Any] = {
        "selected_by": "rule_fallback" if llm_error else "llm_assisted_rules",
        "selected_targets": targets,
        "available_agents": _enabled_agents_view(),
        "dispatch_flow": COORDINATOR_DISPATCH_FLOW,
        "routing_policy": "keyword rules in v1; replace with parsed LLM plan later",
        "llm": llm.info(),
        "llm_response": llm_response,
    }
    if llm_error:
        plan["llm_error"] = llm_error
    return plan


def build_final_answer(question: str, snapshot: dict[str, Any]) -> str:
    prompt = _coordinator_summary_prompt(question, snapshot)
    try:
        llm_response = llm.chat(prompt)
    except LLMClientError as exc:
        llm_response = f"LLM_ERROR: {exc}"

    if llm_response and not llm_response.startswith("LLM_ERROR:"):
        return llm_response.strip()
    return _fallback_final_answer(question, snapshot, llm_response)


def build_collaboration_contracts(state: CoordinatorState) -> dict[str, Any]:
    enabled_agents = _enabled_agents_view()
    return {
        "user_to_coordinator": {
            "owner": "coordinator",
            "protocol": "HTTP JSON",
            "method": "POST",
            "path": "/submit_task",
            "url": f"{state.base_url}/submit_task",
            "reason_to_keep_http": "This is the user/control-plane API, not the A2A data-plane link required by the TCP proposal.",
            "request_example": {
                "question": "Plan a trip to Guangzhou tomorrow with weather and traffic considerations.",
                "timeout": DEFAULT_TASK_TIMEOUT_SECONDS,
            },
        },
        "coordinator_to_worker_agent": {
            "owner": "coordinator sends; worker agents receive",
            "protocol": "TCP A2A",
            "wire_format": "4-byte unsigned big-endian length prefix followed by a UTF-8 JSON object body",
            "frame_type": TYPE_TASK_REQUEST,
            "urls": {name: view["url"] for name, view in enabled_agents.items()},
            "frame_example": {
                "version": "1.0",
                "type": TYPE_TASK_REQUEST,
                "trace_id": "<trace_id>",
                "span_id": "<dispatch_span_id>",
                "parent_span_id": None,
                "source": COORDINATOR_NAME,
                "target": "weather_agent",
                "task_id": "<same task_id>",
                "deadline_ms": 3000,
                "payload": {
                    "source": COORDINATOR_NAME,
                    "target": "weather_agent",
                    "task_id": "<same task_id>",
                    "instruction": "query weather",
                    "context": {"agent_capabilities": ["weather"]},
                    "reply_to": state.reply_to,
                    "created_at": "<utc iso time>",
                },
            },
            "required_response": f"{TYPE_TASK_ACK} frame on the same TCP connection.",
        },
        "worker_agent_to_mcp_server": {
            "owner": "worker agents",
            "protocol": "HTTP JSON-RPC 2.0",
            "servers": _mcp_servers_view(),
            "request_example": {
                "jsonrpc": "2.0",
                "method": "get_weather",
                "params": {"city": "Guangzhou"},
                "id": "<task_id or request id>",
            },
        },
        "worker_agent_to_coordinator": {
            "owner": "worker agents send; coordinator receives",
            "protocol": "TCP A2A",
            "wire_format": "4-byte unsigned big-endian length prefix followed by a UTF-8 JSON object body",
            "url": state.reply_to,
            "frame_type": TYPE_TASK_RESULT,
            "frame_example": {
                "version": "1.0",
                "type": TYPE_TASK_RESULT,
                "trace_id": "<trace_id>",
                "span_id": "<agent_result_span_id>",
                "parent_span_id": "<dispatch_span_id>",
                "source": "weather_agent",
                "target": COORDINATOR_NAME,
                "task_id": "<same task_id>",
                "deadline_ms": None,
                "payload": {
                    "source": "weather_agent",
                    "target": COORDINATOR_NAME,
                    "task_id": "<same task_id>",
                    "status": "success",
                    "result": "weather answer",
                    "error": None,
                    "metadata": {"mcp_server": "weather_mcp_server"},
                },
            },
            "required_response": f"{TYPE_RESULT_ACK} frame on the same TCP connection.",
        },
    }


def run(
    host: str = COORDINATOR_HOST,
    port: int = COORDINATOR_PORT,
    tcp_host: str = COORDINATOR_A2A_TCP_HOST,
    tcp_port: int = COORDINATOR_A2A_TCP_PORT,
) -> None:
    state = CoordinatorState(host=host, port=port, tcp_host=tcp_host, tcp_port=tcp_port)
    tcp_server = CoordinatorA2ATCPServer((tcp_host, tcp_port), CoordinatorA2ATCPRequestHandler, state)
    tcp_thread = threading.Thread(target=tcp_server.serve_forever, name="coordinator-a2a-tcp", daemon=True)
    tcp_thread.start()

    server = CoordinatorHTTPServer((host, port), CoordinatorRequestHandler, state)
    logger.info(f"Coordinator user API listening on http://{host}:{port}")
    logger.info(f"Coordinator A2A TCP listening on {tcp_url(tcp_host, tcp_port)}")
    logger.info("Endpoints: POST /submit_task, GET /health, GET /tasks, GET /contracts")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.critical("Coordinator shutting down.")
    finally:
        tcp_server.shutdown()
        tcp_server.server_close()
        server.server_close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the local A2A coordinator.")
    parser.add_argument("--host", default=COORDINATOR_HOST)
    parser.add_argument("--port", type=int, default=COORDINATOR_PORT)
    parser.add_argument("--tcp-host", default=COORDINATOR_A2A_TCP_HOST)
    parser.add_argument("--tcp-port", type=int, default=COORDINATOR_A2A_TCP_PORT)
    args = parser.parse_args()
    run(host=args.host, port=args.port, tcp_host=args.tcp_host, tcp_port=args.tcp_port)


def _agent_tcp_url(agent: dict[str, Any]) -> str:
    return tcp_url(str(agent["host"]), int(agent["port"]))


def _enabled_agents_view() -> dict[str, Any]:
    discovered_agents = _fetch_discovered_agents()
    return {
        name: {
            "url": _agent_tcp_url(agent),
            "capabilities": agent.get("capabilities", []),
            "enabled": agent.get("enabled", True),
            "protocol": agent.get("protocol", "tcp"),
        }
        for name, agent in discovered_agents.items()
    }


def _mcp_servers_view() -> dict[str, Any]:
    return {
        name: {
            "name": server["name"],
            "url": f"http://{server['host']}:{server['port']}{server.get('path', '/')}",
            "jsonrpc_method": server["method"],
        }
        for name, server in MCP_SERVERS.items()
    }


def _contains_any(text: str, keywords: list[str]) -> bool:
    return any(keyword.lower() in text for keyword in keywords)


def _normalize_timeout(value: Any) -> float:
    if value is None:
        return DEFAULT_TASK_TIMEOUT_SECONDS
    try:
        timeout = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("timeout must be a number") from exc
    if timeout <= 0:
        raise ValueError("timeout must be greater than 0")
    return min(timeout, MAX_TASK_TIMEOUT_SECONDS)


def _coordinator_plan_prompt(question: str, targets: list[str]) -> str:
    return "\n".join(
        [
            "A2A_COORDINATOR_PLAN",
            "你是分布式多智能体系统的主控 Agent。",
            "根据用户问题选择需要唤醒的工作 Agent，并给出简短理由。",
            f"用户问题: {question}",
            f"规则初选 Agent: {targets}",
            f"可用 Agent: {json.dumps(_enabled_agents_view(), ensure_ascii=False)}",
            "输出应包含 selected_targets 和 reason。",
        ]
    )


def _coordinator_summary_prompt(question: str, snapshot: dict[str, Any]) -> str:
    summary_payload = {
        "question": question,
        "status": snapshot.get("status"),
        "results": snapshot.get("results", {}),
        "dispatch_errors": snapshot.get("dispatch_errors", {}),
    }
    return "\n".join(
        [
            "A2A_COORDINATOR_SUMMARY",
            "你是分布式多智能体系统的主控 Agent。",
            "请根据各工作 Agent 的返回结果，生成面向用户的简短最终答复。",
            json.dumps(summary_payload, ensure_ascii=False, default=str),
        ]
    )


def _fallback_final_answer(question: str, snapshot: dict[str, Any], llm_response: str) -> str:
    results = snapshot.get("results", {})
    dispatch_errors = snapshot.get("dispatch_errors", {})
    if not results:
        return (
            "最终旅行方案暂不可生成：未获得可用 Agent 结果。"
            "请确认对应工作 Agent 和 MCP Server 已启动，并查看 dispatch_errors。"
        )

    lines = [f"最终旅行方案：用户问题为「{question}」。"]
    for source, payload in results.items():
        result = payload.get("result")
        status = payload.get("status")
        if status == RESULT_SUCCESS:
            lines.append(f"- {source}: {result}")
        else:
            lines.append(f"- {source}: 执行失败，错误为 {payload.get('error')}")

    if dispatch_errors:
        for source, error in dispatch_errors.items():
            lines.append(f"- {source}: 分发失败，错误为 {error}")

    if llm_response:
        lines.append(f"- llm: {llm_response}")
    return "\n".join(lines)


if __name__ == "__main__":
    main()
