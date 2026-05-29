"""Coordinator process for the local HTTP-based A2A travel dependency demo.

Run:
    python coordinator.py

Main endpoints:
    POST /submit_task   {"question": "...", "timeout": 10}
    POST /task_result   Agent callback with the shared A2A result payload
    GET  /health
    GET  /tasks?task_id=<id>
    GET  /contracts     Interfaces for worker-agent and MCP teammates

This version uses the latest travel workflow by default:
    Coordinator -> Weather Agent -> Attraction Agent -> Hotel Agent -> Traffic Agent -> Coordinator
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
import os
import re
from socketserver import BaseRequestHandler, ThreadingTCPServer
import threading
import time
from typing import Any
from urllib import request
from urllib.parse import parse_qs, urlparse

from common.config import (
    A2A_TCP_TIMEOUT_SECONDS,
    AGENTS,
    COORDINATOR_A2A_TCP_HOST,
    COORDINATOR_A2A_TCP_PORT,
    REGISTRY_HOST,
    REGISTRY_PORT,
    COORDINATOR_HOST,
    COORDINATOR_NAME,
    COORDINATOR_PORT,
    DEFAULT_TASK_TIMEOUT_SECONDS,
    DISPATCH_HTTP_TIMEOUT_SECONDS,
    MAX_TASK_TIMEOUT_SECONDS,
    MCP_SERVERS,
)
from common.http_client import HttpJsonClientError, post_json
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
    recv_frame,
    send_frame,
    tcp_url,
    validate_envelope,
)
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


logger = logging.getLogger("coordinator")

DEPENDENCY_CHAIN = ["weather_agent", "attraction_agent", "hotel_agent", "traffic_agent"]

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
        "name": "dispatch_weather_agent",
        "required_flow": "Coordinator -> Weather Agent",
        "network": "TCP A2A TASK_REQUEST frame with 4-byte length prefix",
        "owner": "coordinator sends; weather agent receives",
    },
    {
        "step": 3,
        "name": "weather_callback",
        "required_flow": "Weather Agent -> Coordinator",
        "network": "TCP A2A TASK_RESULT frame with 4-byte length prefix",
        "owner": "weather agent sends; coordinator receives",
    },
    {
        "step": 4,
        "name": "dispatch_attraction_agent_after_weather",
        "required_flow": "Coordinator 等待 Weather 结果后，再唤醒 Attraction Agent",
        "network": "TCP A2A TASK_REQUEST frame with weather_result in context.inputs",
        "owner": "coordinator sends; attraction agent receives",
    },
    {
        "step": 5,
        "name": "attraction_callback",
        "required_flow": "Attraction Agent -> Coordinator",
        "network": "TCP A2A TASK_RESULT frame with 4-byte length prefix",
        "owner": "attraction agent sends; coordinator receives",
    },
    {
        "step": 6,
        "name": "dispatch_hotel_agent_after_attraction",
        "required_flow": "Coordinator 等待 Attraction 结果后，再唤醒 Hotel Agent",
        "network": "TCP A2A TASK_REQUEST frame with daily_plan_skeleton in context.inputs",
        "owner": "coordinator sends; hotel agent receives",
    },
    {
        "step": 7,
        "name": "hotel_callback",
        "required_flow": "Hotel Agent -> Coordinator",
        "network": "TCP A2A TASK_RESULT frame with 4-byte length prefix",
        "owner": "hotel agent sends; coordinator receives",
    },
    {
        "step": 8,
        "name": "dispatch_traffic_agent_after_hotel",
        "required_flow": "Coordinator 等待 Hotel 结果后，再唤醒 Traffic Agent",
        "network": "TCP A2A TASK_REQUEST frame with daily_plan_skeleton and hotel_plan in context.inputs",
        "owner": "coordinator sends; traffic agent receives",
    },
    {
        "step": 9,
        "name": "traffic_callback",
        "required_flow": "Traffic Agent -> Coordinator",
        "network": "TCP A2A TASK_RESULT frame with 4-byte length prefix",
        "owner": "traffic agent sends; coordinator receives",
    },
    {
        "step": 10,
        "name": "aggregate_final_plan",
        "required_flow": "Coordinator 汇总天气、景点、住宿、交通结果，生成最终旅行方案",
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
    def __init__(self, *, host: str, port: int, tcp_host: str | None = None, tcp_port: int | None = None) -> None:
        self.host = host
        self.port = port
        self.tcp_host = tcp_host or COORDINATOR_A2A_TCP_HOST
        self.tcp_port = tcp_port or COORDINATOR_A2A_TCP_PORT
        self._tasks: dict[str, TaskRecord] = {}
        self._condition = threading.Condition(threading.RLock())

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    @property
    def reply_to(self) -> str:
        return tcp_url(self.tcp_host, self.tcp_port)

    @property
    def http_reply_to(self) -> str:
        return f"{self.base_url}/task_result"

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

    def wait_for_target(self, task_id: str, target: str, timeout_seconds: float) -> dict[str, Any]:
        wait_seconds = max(0.1, timeout_seconds)
        deadline = time.monotonic() + wait_seconds
        with self._condition:
            record = self._tasks[task_id]
            record.status = TASK_WAITING
            record.updated_at = utc_now_iso()
            while target not in record.results and target not in record.dispatch_errors:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    record.dispatch_errors[target] = (
                        f"{target} timed out after {wait_seconds:.1f}s waiting for result callback"
                    )
                    record.results.pop(target, None)
                    record.refresh_status()
                    self._condition.notify_all()
                    break
                self._condition.wait(timeout=remaining)
            return record.snapshot()

    def wait_for_task(self, task_id: str, timeout_seconds: float) -> dict[str, Any]:
        wait_seconds = max(0.1, timeout_seconds)
        deadline = time.monotonic() + wait_seconds
        with self._condition:
            record = self._tasks[task_id]
            record.status = TASK_WAITING
            while record.terminal_count() < record.expected_count():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    for target in record.pending_targets():
                        record.dispatch_errors[target] = (
                            f"{target} timed out after {wait_seconds:.1f}s waiting for workflow completion"
                        )
                    break
                self._condition.wait(timeout=remaining)
            record.finalize_after_wait()
            self._condition.notify_all()
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
        state: CoordinatorState | None = None,
    ) -> None:
        super().__init__(server_address, handler_class)
        self.state = state or CoordinatorState(host=server_address[0], port=server_address[1])

    def dispatch_to_agent(
        self,
        record: TaskRecord,
        target: str,
        *,
        context: dict[str, Any] | None = None,
        event: str = "dispatch_task",
    ) -> None:
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
        task_context = dict(
            context
            or {
                "workflow": "travel_dependency",
                "coordinator_plan": record.plan,
                "agent_capabilities": agent_config.get("capabilities", []),
            }
        )
        task_context.setdefault("trace_id", trace_id)
        task_context.setdefault("parent_span_id", span_id)
        payload = build_task_payload(
            source=COORDINATOR_NAME,
            target=target,
            task_id=record.task_id,
            instruction=record.question,
            reply_to=self.state.reply_to,
            created_at=record.created_at,
            context=task_context,
        )
        frame = build_envelope(
            message_type=TYPE_TASK_REQUEST,
            source=COORDINATOR_NAME,
            target=target,
            task_id=record.task_id,
            trace_id=trace_id,
            span_id=span_id,
            deadline_ms=int(record.timeout_seconds * 1000),
            payload=payload,
        )
        log_network_event(
            event=event,
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
            self.state.mark_dispatch_error(record.task_id, target, f"invalid TCP ACK: {exc}")
            return
        if response.data.get("type") == TYPE_ERROR:
            payload_error = response.data.get("payload", {}).get("error")
            self.state.mark_dispatch_error(record.task_id, target, str(payload_error or "agent rejected task"))
            return
        if response.data.get("type") != TYPE_TASK_ACK:
            self.state.mark_dispatch_error(record.task_id, target, f"unexpected TCP response: {response.data.get('type')}")


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
        self.request.settimeout(A2A_TCP_TIMEOUT_SECONDS)
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
                url=tcp_url(self.server.state.tcp_host, self.server.state.tcp_port),
                task_id=task_id,
                payload=frame_data,
                payload_size=frame.length,
            )
            record = self.server.state.add_result(payload)
            _refresh_final_answer_async(self.server.state, record.task_id)
            ack = build_envelope(
                message_type=TYPE_RESULT_ACK,
                source=COORDINATOR_NAME,
                target=source,
                task_id=task_id,
                trace_id=trace_id,
                parent_span_id=span_id,
                payload={"received": True, "task_id": task_id, "task_status": record.status},
            )
            send_frame(self.request, ack)
        except Exception as exc:
            error_task_id = str(frame_data.get("task_id")) if frame_data and frame_data.get("task_id") else task_id
            error_target = source if source != "unknown" else "worker_agent"
            log_network_event(
                event="task_result_failed",
                direction="inbound",
                source=source,
                target=COORDINATOR_NAME,
                method="TCP",
                url=tcp_url(self.server.state.tcp_host, self.server.state.tcp_port),
                task_id=None if error_task_id == "unknown" else error_task_id,
                payload=frame_data,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            error_frame = build_error_envelope(
                source=COORDINATOR_NAME,
                target=error_target,
                task_id=error_task_id,
                trace_id=trace_id,
                parent_span_id=span_id,
                error=str(exc),
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
                        "workflow": "travel_dependency",
                        "dependency_chain": DEPENDENCY_CHAIN,
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
        if parsed.path == "/task_result":
            self._handle_task_result()
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

        targets = list(DEPENDENCY_CHAIN)
        travel_task = extract_travel_task(question)
        plan = build_dependency_plan(question, targets, travel_task)
        record = self.server.state.create_task(question, targets, timeout_seconds, plan)
        workflow_deadline = time.monotonic() + timeout_seconds

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

        # 1. Coordinator -> Weather Agent
        weather_context = build_node_context(
            record=record,
            node_id="weather",
            stage="weather_analysis",
            dependencies=[],
            travel_task=travel_task,
            inputs={},
            target="weather_agent",
        )
        log_network_event(
            event="dependency_dispatch_weather",
            direction="internal",
            source=COORDINATOR_NAME,
            target="weather_agent",
            task_id=record.task_id,
            payload={"reason": "first node in dependency chain", "node_id": "weather"},
        )
        self.server.dispatch_to_agent(
            record,
            "weather_agent",
            context=weather_context,
            event="dispatch_weather_task",
        )
        self.server.state.wait_for_target(record.task_id, "weather_agent", _stage_wait_seconds(workflow_deadline, minimum_seconds=75.0))
        snapshot = self.server.state.get_task(record.task_id).snapshot()
        weather_result = snapshot["results"].get("weather_agent")
        weather_constraints = build_weather_constraints(weather_result, travel_task)
        log_network_event(
            event="dependency_satisfied_weather_to_attraction",
            direction="internal",
            source=COORDINATOR_NAME,
            target="attraction_agent",
            task_id=record.task_id,
            payload={
                "dependency": "weather_agent",
                "next": "attraction_agent",
                "weather_status": weather_result.get("status") if weather_result else None,
                "weather_constraints": weather_constraints,
                "dispatch_errors": snapshot.get("dispatch_errors", {}),
            },
        )

        # 2. Coordinator -> Attraction Agent after Weather result
        attraction_context = build_node_context(
            record=record,
            node_id="attraction",
            stage="attraction_planning",
            dependencies=["weather"],
            travel_task=travel_task,
            inputs={
                "travel_task": travel_task,
                "weather_result": weather_result,
                "weather_constraints": weather_constraints,
            },
            target="attraction_agent",
        )
        log_network_event(
            event="dependency_dispatch_attraction",
            direction="internal",
            source=COORDINATOR_NAME,
            target="attraction_agent",
            task_id=record.task_id,
            payload={"reason": "weather result received; now trigger attraction planning", "node_id": "attraction"},
        )
        self.server.dispatch_to_agent(
            record,
            "attraction_agent",
            context=attraction_context,
            event="dispatch_attraction_task",
        )
        self.server.state.wait_for_target(record.task_id, "attraction_agent", _stage_wait_seconds(workflow_deadline, minimum_seconds=100.0))
        snapshot = self.server.state.get_task(record.task_id).snapshot()
        attraction_result = snapshot["results"].get("attraction_agent")
        daily_plan_skeleton = extract_daily_plan_skeleton(attraction_result)
        constraints_for_traffic = extract_constraints_for_traffic(attraction_result)
        log_network_event(
            event="dependency_satisfied_attraction_to_hotel",
            direction="internal",
            source=COORDINATOR_NAME,
            target="hotel_agent",
            task_id=record.task_id,
            payload={
                "dependency": "attraction_agent",
                "next": "hotel_agent",
                "attraction_status": attraction_result.get("status") if attraction_result else None,
                "daily_plan_days": list(daily_plan_skeleton.keys()) if isinstance(daily_plan_skeleton, dict) else [],
                "dispatch_errors": snapshot.get("dispatch_errors", {}),
            },
        )

        # 3. Coordinator -> Hotel Agent after Attraction result
        hotel_context = build_node_context(
            record=record,
            node_id="hotel",
            stage="hotel_selection",
            dependencies=["weather", "attraction"],
            travel_task=travel_task,
            inputs={
                "travel_task": travel_task,
                "weather_result": weather_result,
                "weather_constraints": weather_constraints,
                "attraction_result": attraction_result,
                "daily_plan_skeleton": daily_plan_skeleton,
                "constraints_for_hotel": [
                    "根据每日景点区域选择交通便利的住宿区域",
                    "低预算下优先经济型酒店或青旅",
                    "住宿位置需要服务后续交通规划",
                ],
            },
            target="hotel_agent",
        )
        log_network_event(
            event="dependency_dispatch_hotel",
            direction="internal",
            source=COORDINATOR_NAME,
            target="hotel_agent",
            task_id=record.task_id,
            payload={"reason": "attraction plan received; now trigger hotel selection", "node_id": "hotel"},
        )
        self.server.dispatch_to_agent(
            record,
            "hotel_agent",
            context=hotel_context,
            event="dispatch_hotel_task",
        )
        self.server.state.wait_for_target(record.task_id, "hotel_agent", _stage_wait_seconds(workflow_deadline, minimum_seconds=150.0))
        snapshot = self.server.state.get_task(record.task_id).snapshot()
        hotel_result = snapshot["results"].get("hotel_agent")
        if hotel_result is None:
            # Hotel is a hard dependency of Traffic. Do not start Traffic with an
            # empty hotel_plan; otherwise final_answer will claim lodging/traffic
            # is incomplete. Give slow LLM callbacks one more generous window.
            logger.warning("hotel_agent not ready after first wait; waiting again before traffic dispatch")
            self.server.state.wait_for_target(record.task_id, "hotel_agent", 120.0)
            snapshot = self.server.state.get_task(record.task_id).snapshot()
            hotel_result = snapshot["results"].get("hotel_agent")
        hotel_plan = extract_hotel_plan(hotel_result)
        hotel_constraints_for_traffic = extract_hotel_constraints_for_traffic(hotel_result)

        log_network_event(
            event="dependency_satisfied_hotel_to_traffic",
            direction="internal",
            source=COORDINATOR_NAME,
            target="traffic_agent",
            task_id=record.task_id,
            payload={
                "dependency": "hotel_agent",
                "next": "traffic_agent",
                "hotel_status": hotel_result.get("status") if hotel_result else None,
                "recommended_area": hotel_plan.get("recommended_area") if isinstance(hotel_plan, dict) else None,
                "dispatch_errors": snapshot.get("dispatch_errors", {}),
            },
        )

        # 4. Coordinator -> Traffic Agent after Hotel result
        traffic_context = build_node_context(
            record=record,
            node_id="traffic",
            stage="traffic_planning",
            dependencies=["weather", "attraction", "hotel"],
            travel_task=travel_task,
            inputs={
                "travel_task": travel_task,
                "weather_result": weather_result,
                "weather_constraints": weather_constraints,
                "attraction_result": attraction_result,
                "daily_plan_skeleton": daily_plan_skeleton,
                "constraints_for_traffic": constraints_for_traffic,
                "hotel_result": hotel_result,
                "hotel_plan": hotel_plan,
                "hotel_constraints_for_traffic": hotel_constraints_for_traffic,
            },
            target="traffic_agent",
        )
        log_network_event(
            event="dependency_dispatch_traffic",
            direction="internal",
            source=COORDINATOR_NAME,
            target="traffic_agent",
            task_id=record.task_id,
            payload={"reason": "hotel selected; now trigger traffic planning", "node_id": "traffic"},
        )
        self.server.dispatch_to_agent(
            record,
            "traffic_agent",
            context=traffic_context,
            event="dispatch_traffic_task",
        )
        self.server.state.wait_for_target(record.task_id, "traffic_agent", _stage_wait_seconds(workflow_deadline, minimum_seconds=150.0))
        snapshot = self.server.state.get_task(record.task_id).snapshot()
        if "traffic_agent" not in (snapshot.get("results") or {}):
            logger.warning("traffic_agent not ready after first wait; waiting again before final answer")
            self.server.state.wait_for_target(record.task_id, "traffic_agent", 120.0)

        snapshot = self.server.state.wait_for_task(record.task_id, _stage_wait_seconds(workflow_deadline, minimum_seconds=60.0))
        final_answer = build_final_answer(question, snapshot)
        snapshot = self.server.state.set_final_answer(record.task_id, final_answer)
        log_network_event(
            event="dependency_workflow_finished",
            direction="internal",
            source=COORDINATOR_NAME,
            target="user",
            task_id=record.task_id,
            payload=snapshot,
        )
        http_status = HTTPStatus.OK if snapshot["status"] != TASK_FAILED else HTTPStatus.GATEWAY_TIMEOUT
        self._send_json(http_status, success_response({"task": snapshot}))

    def _handle_task_result(self) -> None:
        try:
            payload, payload_size = self._read_json_with_size()
            log_network_event(
                event="task_result",
                direction="inbound",
                source=str(payload.get("source", "unknown")),
                target=COORDINATOR_NAME,
                method="POST",
                url="/task_result",
                task_id=str(payload.get("task_id", "")) or None,
                payload=payload,
                payload_size=payload_size,
            )
            record = self.server.state.add_result(payload)
            _refresh_final_answer_async(self.server.state, record.task_id)
        except ValueError as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, error_response("invalid_json", str(exc)))
            return
        except KeyError as exc:
            self._send_json(
                HTTPStatus.NOT_FOUND,
                error_response("unknown_task", f"task_id not found: {exc.args[0]}"),
            )
            return
        except PayloadValidationError as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, error_response("invalid_result", str(exc)))
            return

        self._send_json(
            HTTPStatus.OK,
            success_response(
                {
                    "received": True,
                    "task_id": record.task_id,
                    "task_status": record.status,
                }
            ),
        )

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


def _refresh_final_answer_async(state: CoordinatorState, task_id: str) -> None:
    """Refresh final_answer after a callback without blocking /task_result ACK.

    Agent callbacks use a short HTTP timeout. If Coordinator calls the LLM
    synchronously inside /task_result, the Agent may time out before receiving
    the ACK. Therefore final summary refresh is moved to a background thread.
    """

    def worker() -> None:
        record = state.get_task(task_id)
        if record is None:
            return
        snapshot = record.snapshot()
        if snapshot.get("status") != TASK_COMPLETED:
            return
        try:
            final_answer = build_final_answer(record.question, snapshot)
            state.set_final_answer(task_id, final_answer)
        except Exception as exc:
            logger.warning(f"failed to refresh final_answer after callback: {exc}")

    threading.Thread(
        target=worker,
        name=f"refresh-final-{task_id[:8]}",
        daemon=True,
    ).start()


def _fetch_discovered_agents() -> dict[str, Any]:
    """Return registry-discovered agents merged with local config.

    Local config is kept as a fallback so manually added agents, especially
    attraction_agent, remain reachable even if they do not register themselves.
    Registry entries override local config for agents that do register.
    """
    agents = dict(AGENTS)
    try:
        url = f"http://{REGISTRY_HOST}:{REGISTRY_PORT}/discover"
        req = request.Request(url, method="GET")
        with request.urlopen(req, timeout=3.0) as response:
            if response.status == 200:
                data = json.loads(response.read().decode("utf-8"))
                discovered = data.get("agents", {})
                if isinstance(discovered, dict):
                    agents.update(discovered)
    except Exception as e:
        logger.error(f"Coordinator failed to discover agents from registry: {e}, fall back to local config")
    return agents


def _infer_error_type(exc: Exception) -> str:
    cause = exc.__cause__
    if cause is None:
        return type(exc).__name__
    reason = getattr(cause, "reason", None)
    if reason is not None:
        return type(reason).__name__
    return type(cause).__name__


def build_dependency_plan(question: str, targets: list[str], travel_task: dict[str, Any]) -> dict[str, Any]:
    return {
        "selected_by": travel_task.get("_parser", "fixed_travel_dependency_chain"),
        "workflow": "travel_dependency",
        "selected_targets": targets,
        "dependency_chain": [
            {"node_id": "weather", "agent": "weather_agent", "depends_on": []},
            {"node_id": "attraction", "agent": "attraction_agent", "depends_on": ["weather"]},
            {"node_id": "hotel", "agent": "hotel_agent", "depends_on": ["weather", "attraction"]},
            {"node_id": "traffic", "agent": "traffic_agent", "depends_on": ["weather", "attraction", "hotel"]},
        ],
        "travel_task": travel_task,
        "dispatch_flow": COORDINATOR_DISPATCH_FLOW,
        "available_agents": _enabled_agents_view(),
        "routing_policy": "fixed DAG in latest version: Weather -> Attraction -> Hotel -> Traffic",
        "llm": llm.info(),
    }


def build_node_context(
    *,
    record: TaskRecord,
    node_id: str,
    stage: str,
    dependencies: list[str],
    travel_task: dict[str, Any],
    inputs: dict[str, Any],
    target: str,
) -> dict[str, Any]:
    agent_config = _fetch_discovered_agents().get(target, {})
    return {
        "workflow": "travel_dependency",
        "node_id": node_id,
        "stage": stage,
        "dependencies": dependencies,
        "travel_task": travel_task,
        "inputs": inputs,
        "coordinator_plan": record.plan,
        "agent_capabilities": agent_config.get("capabilities", []),
    }


def extract_travel_task(question: str) -> dict[str, Any]:
    """Use LLM for natural-language task parsing, with rule fallback.

    Coordinator only extracts task constraints and chooses workflow; it does not
    perform weather, attraction, hotel, or traffic domain decisions.
    """
    fallback = _extract_travel_task_by_rules(question)
    try:
        llm_json = llm.chat_json(
            _task_analysis_prompt(question, fallback),
            max_tokens=450,
            temperature=0.0,
            timeout_seconds=12.0,
        )
        travel_task = llm_json.get("travel_task") if isinstance(llm_json.get("travel_task"), dict) else llm_json
        return _normalize_travel_task(travel_task, fallback, parser="coordinator_llm_task_parser")
    except Exception as exc:
        fallback["_parser"] = "rule_fallback"
        fallback["_parser_error"] = str(exc)
        return fallback


def _task_analysis_prompt(question: str, fallback: dict[str, Any]) -> str:
    payload = {
        "question": question,
        "rule_fallback": fallback,
        "output_schema": {
            "travel_task": {
                "origin_city": "city or null",
                "destination_city": "city",
                "days": 5,
                "start_date": "tomorrow|unspecified|specific date",
                "budget_level": "low|normal|high|unknown",
                "transport_preference": "public_transport|fastest|cheapest|taxi|normal",
                "must_visit": ["legacy attraction names for compatibility"],
                "preferences": ["legacy preference tags for compatibility"],
                "avoid": ["legacy avoid tags for compatibility"],
                "constraints": {
                    "attractions": {
                        "must_visit": ["attraction names user explicitly requires"],
                        "preferred_types": ["nature", "museum", "history", "food street"],
                        "avoid": [],
                        "pace": "relaxed|normal|packed|unknown",
                    },
                    "traffic": {
                        "preference": "public_transport|fastest|cheapest|taxi|normal",
                        "avoid": [],
                        "max_transfer": None,
                        "walking_tolerance": "low|normal|high|unknown",
                    },
                    "hotel": {
                        "preferred_features": ["quiet", "good environment", "near subway"],
                        "preferred_area": None,
                        "hotel_type": None,
                    },
                    "general": {
                        "budget_level": "low|normal|high|unknown",
                        "travel_style": "budget|comfort|balanced|unknown",
                        "special_needs": [],
                    },
                },
            }
        },
    }
    return "\n".join([
        "你是 Coordinator 的任务解析器，只把用户自然语言解析成 travel_task JSON。",
        "不要安排天气、景点或交通方案；不要输出 Markdown；只输出 JSON。",
        json.dumps(payload, ensure_ascii=False, default=str),
    ])


def _normalize_travel_task(value: Any, fallback: dict[str, Any], *, parser: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        result = dict(fallback)
        result["_parser"] = "rule_fallback_invalid_llm"
        return result
    result = dict(fallback)
    for key in ["origin_city", "destination_city", "start_date", "budget_level", "transport_preference"]:
        if isinstance(value.get(key), str) and value[key].strip() and value[key] != "null":
            result[key] = value[key].strip()
    if isinstance(value.get("days"), int) and 1 <= value["days"] <= 30:
        result["days"] = value["days"]
    elif isinstance(value.get("days"), str) and value["days"].isdigit():
        result["days"] = int(value["days"])
    for key in ["must_visit", "preferences", "avoid"]:
        if isinstance(value.get(key), list):
            result[key] = [str(item).strip() for item in value[key] if str(item).strip()]
    result["constraints"] = _normalize_task_constraints(value.get("constraints"), result)
    _sync_legacy_fields_from_constraints(result)
    result.setdefault("avoid", [])
    result["_parser"] = parser
    return result


def _extract_travel_task_by_rules(question: str) -> dict[str, Any]:
    origin_city = _extract_origin_city(question)
    destination_city = _extract_destination_city(question, origin_city)
    days = _extract_days(question)
    budget_level = "low" if any(word in question for word in ["低预算", "省钱", "便宜", "穷游"]) else "normal"
    transport_preference = (
        "public_transport"
        if any(word in question for word in ["公共交通", "地铁", "公交", "少打车", "不打车"])
        else "normal"
    )
    must_visit = _extract_must_visit(question)
    preferences = ["经典景点"]
    if budget_level == "low":
        preferences.append("低预算")
    if transport_preference == "public_transport":
        preferences.append("公共交通方便")
    return {
        "origin_city": origin_city,
        "destination_city": destination_city,
        "days": days,
        "start_date": "明天" if "明天" in question else "未指定",
        "budget_level": budget_level,
        "transport_preference": transport_preference,
        "must_visit": must_visit,
        "preferences": preferences,
        "avoid": [],
        "constraints": _build_task_constraints(
            must_visit=must_visit,
            preferences=preferences,
            avoid=[],
            budget_level=budget_level,
            transport_preference=transport_preference,
            question=question,
        ),
        "_parser": "rule_fallback",
    }


def _normalize_task_constraints(value: Any, flat_task: dict[str, Any]) -> dict[str, Any]:
    fallback = _build_task_constraints(
        must_visit=_as_clean_list(flat_task.get("must_visit")),
        preferences=_as_clean_list(flat_task.get("preferences")),
        avoid=_as_clean_list(flat_task.get("avoid")),
        budget_level=str(flat_task.get("budget_level") or "normal"),
        transport_preference=str(flat_task.get("transport_preference") or "normal"),
        question="",
    )
    if not isinstance(value, dict):
        return fallback

    result = dict(fallback)
    for section in ["attractions", "traffic", "hotel", "general"]:
        incoming = value.get(section)
        if not isinstance(incoming, dict):
            continue
        merged = dict(result.get(section, {}))
        for key, item in incoming.items():
            if isinstance(item, list):
                merged[key] = [str(x).strip() for x in item if str(x).strip()]
            elif isinstance(item, (str, int, float, bool)) or item is None:
                merged[key] = item
        result[section] = merged
    return result


def _build_task_constraints(
    *,
    must_visit: list[str],
    preferences: list[str],
    avoid: list[str],
    budget_level: str,
    transport_preference: str,
    question: str,
) -> dict[str, Any]:
    preferred_types = [item for item in preferences if item not in {"低预算", "公共交通方便"}]
    if any(word in question for word in ["自然", "风景", "山水", "湖"]):
        preferred_types.append("自然风景")
    if any(word in question for word in ["博物馆", "历史", "文化"]):
        preferred_types.append("历史文化")

    traffic_avoid: list[str] = []
    if any(word in question for word in ["不打车", "少打车"]):
        traffic_avoid.append("taxi")

    hotel_features: list[str] = []
    if any(word in question for word in ["环境好", "安静", "舒适", "干净"]):
        hotel_features.append("环境好")
    if any(word in question for word in ["近地铁", "地铁方便", "交通方便"]):
        hotel_features.append("近地铁")

    return {
        "attractions": {
            "must_visit": list(dict.fromkeys(must_visit)),
            "preferred_types": list(dict.fromkeys(preferred_types)),
            "avoid": avoid,
            "pace": "normal",
        },
        "traffic": {
            "preference": transport_preference or "normal",
            "avoid": traffic_avoid,
            "max_transfer": None,
            "walking_tolerance": "normal",
        },
        "hotel": {
            "preferred_features": list(dict.fromkeys(hotel_features)),
            "preferred_area": None,
            "hotel_type": None,
        },
        "general": {
            "budget_level": budget_level or "normal",
            "travel_style": "budget" if budget_level == "low" else "balanced",
            "special_needs": [],
        },
    }


def _sync_legacy_fields_from_constraints(task: dict[str, Any]) -> None:
    constraints = task.get("constraints")
    if not isinstance(constraints, dict):
        return
    attractions = constraints.get("attractions") if isinstance(constraints.get("attractions"), dict) else {}
    traffic = constraints.get("traffic") if isinstance(constraints.get("traffic"), dict) else {}
    general = constraints.get("general") if isinstance(constraints.get("general"), dict) else {}
    if isinstance(attractions.get("must_visit"), list):
        task["must_visit"] = _as_clean_list(attractions.get("must_visit"))
    if isinstance(attractions.get("preferred_types"), list):
        task["preferences"] = _as_clean_list(attractions.get("preferred_types"))
    if isinstance(general.get("budget_level"), str) and general.get("budget_level"):
        task["budget_level"] = str(general["budget_level"])
    if isinstance(traffic.get("preference"), str) and traffic.get("preference"):
        task["transport_preference"] = str(traffic["preference"])


def _as_clean_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _extract_origin_city(text: str) -> str | None:
    for city in ["北京", "上海", "广州", "深圳", "杭州", "南京", "成都", "重庆", "武汉", "西安", "苏州", "天津"]:
        if f"从{city}" in text:
            return city
    return None


def _extract_destination_city(text: str, origin_city: str | None = None) -> str:
    cities = ["北京", "上海", "广州", "深圳", "杭州", "南京", "成都", "重庆", "武汉", "西安", "苏州", "天津"]
    for city in cities:
        if f"去{city}" in text or f"到{city}" in text:
            return city
    for city in cities:
        if city in text and city != origin_city:
            return city
    return "北京"


def _extract_days(text: str) -> int:
    cn_digits = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7}
    match = re.search(r"(\d+)\s*天", text)
    if match:
        return int(match.group(1))
    for key, value in cn_digits.items():
        if f"{key}天" in text:
            return value
    return 3


def _extract_must_visit(text: str) -> list[str]:
    known_spots = ["天安门广场", "天安门", "故宫", "国家博物馆", "天坛", "颐和园", "圆明园", "什刹海", "南锣鼓巷"]
    return [spot for spot in known_spots if spot in text]


def build_weather_constraints(weather_result: dict[str, Any] | None, travel_task: dict[str, Any]) -> dict[str, Any]:
    days = int(travel_task.get("days") or 3)
    all_days = [f"day{i}" for i in range(1, days + 1)]
    constraints = {
        "outdoor_good_days": all_days,
        "outdoor_suitable_days": all_days,
        "indoor_preferred_days": [],
        "rainy_days": [],
        "weather_by_day": [
            {
                "day": day,
                "outdoor_suitable": True,
                "indoor_preferred": False,
            }
            for day in all_days
        ],
        "source": "coordinator_from_weather_mcp_result",
    }
    if not isinstance(weather_result, dict):
        constraints["source"] = "coordinator_default_no_weather_result"
        return constraints

    metadata = weather_result.get("metadata") or {}
    if isinstance(metadata, dict):
        structured = metadata.get("structured_result")
        if isinstance(structured, dict) and isinstance(structured.get("weather_constraints"), dict):
            return structured["weather_constraints"]
        if isinstance(metadata.get("weather_constraints"), dict):
            return metadata["weather_constraints"]

    mcp_result = metadata.get("mcp_result") if isinstance(metadata, dict) else None
    if isinstance(mcp_result, dict):
        condition = str(mcp_result.get("condition", ""))
        if any(word in condition for word in ["雨", "雪", "大风", "雷"]):
            constraints["rainy_days"] = ["day1"]
            constraints["indoor_preferred_days"] = ["day1"]
            constraints["outdoor_good_days"] = [day for day in all_days if day != "day1"]
            constraints["outdoor_suitable_days"] = constraints["outdoor_good_days"]
            constraints["weather_by_day"] = [
                {
                    "day": day,
                    "condition": condition,
                    "outdoor_suitable": day != "day1",
                    "indoor_preferred": day == "day1",
                }
                for day in all_days
            ]
        constraints["raw_condition"] = condition
        constraints["city"] = mcp_result.get("city")
        constraints["date"] = mcp_result.get("date")
    return constraints


def extract_daily_plan_skeleton(attraction_result: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(attraction_result, dict):
        return {}
    metadata = attraction_result.get("metadata") or {}
    if isinstance(metadata, dict):
        structured = metadata.get("structured_result")
        if isinstance(structured, dict):
            value = structured.get("daily_plan") or structured.get("daily_plan_skeleton")
            if isinstance(value, dict):
                return value
        value = metadata.get("daily_plan_skeleton") or metadata.get("daily_plan")
        if isinstance(value, dict):
            return value
    return {}


def extract_constraints_for_traffic(attraction_result: dict[str, Any] | None) -> list[Any]:
    if not isinstance(attraction_result, dict):
        return []
    metadata = attraction_result.get("metadata") or {}
    if isinstance(metadata, dict):
        structured = metadata.get("structured_result")
        if isinstance(structured, dict) and isinstance(structured.get("constraints_for_traffic"), list):
            return structured["constraints_for_traffic"]
        value = metadata.get("constraints_for_traffic")
        if isinstance(value, list):
            return value
    return []


def extract_hotel_plan(hotel_result: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(hotel_result, dict):
        return {}
    metadata = hotel_result.get("metadata") or {}
    if isinstance(metadata, dict):
        structured = metadata.get("structured_result")
        if isinstance(structured, dict) and isinstance(structured.get("hotel_plan"), dict):
            return structured["hotel_plan"]
        value = metadata.get("hotel_plan")
        if isinstance(value, dict):
            return value
    return {}


def extract_hotel_constraints_for_traffic(hotel_result: dict[str, Any] | None) -> list[Any]:
    if not isinstance(hotel_result, dict):
        return []
    metadata = hotel_result.get("metadata") or {}
    if isinstance(metadata, dict):
        structured = metadata.get("structured_result")
        if isinstance(structured, dict) and isinstance(structured.get("constraints_for_traffic"), list):
            return structured["constraints_for_traffic"]
        value = metadata.get("constraints_for_traffic")
        if isinstance(value, list):
            return value
    return []


def build_collaboration_contracts(state: CoordinatorState) -> dict[str, Any]:
    return {
        "user_to_coordinator": {
            "owner": "coordinator teammate",
            "method": "POST",
            "path": "/submit_task",
            "url": f"{state.base_url}/submit_task",
            "request_example": {
                "question": "请帮我规划从上海去北京的五天低预算旅行计划，尽量公共交通，故宫和天安门一定要去。",
                "timeout": DEFAULT_TASK_TIMEOUT_SECONDS,
            },
            "response_shape": {
                "ok": True,
                "task": {
                    "task_id": "<generated>",
                    "status": "completed|partial|failed|waiting",
                    "targets": DEPENDENCY_CHAIN,
                    "results": {},
                    "dispatch_errors": {},
                    "final_answer": "<coordinator generated travel plan>",
                },
            },
        },
        "coordinator_to_worker_agent": {
            "owner": "weather/attraction/hotel/traffic agent teammates",
            "method": "POST",
            "required_handler": "/execute_task",
            "shared_validator": "common.schemas.validate_task_payload",
            "urls": {name: view["url"] for name, view in _enabled_agents_view().items()},
            "dependency_order": DEPENDENCY_CHAIN,
            "payload_example": {
                "source": COORDINATOR_NAME,
                "target": "attraction_agent",
                "task_id": "<same task_id>",
                "instruction": "请帮我规划从上海去北京的五天低预算旅行计划...",
                "context": {
                    "workflow": "travel_dependency",
                    "node_id": "attraction",
                    "stage": "attraction_planning",
                    "dependencies": ["weather"],
                    "travel_task": {"origin_city": "上海", "destination_city": "北京", "days": 5},
                    "inputs": {"weather_result": "<Weather Agent result>"},
                },
                "reply_to": state.reply_to,
                "created_at": "<utc iso time>",
            },
            "required_response": "HTTP 2xx ack is enough; worker agent callbacks /task_result.",
        },
        "worker_agent_to_mcp_server": {
            "owner": "worker agent and MCP teammates",
            "protocol": "HTTP JSON-RPC 2.0",
            "servers": _mcp_servers_view(),
            "request_example": {
                "jsonrpc": "2.0",
                "method": "search_attractions",
                "params": {"city": "北京", "days": 5},
                "id": "<task_id or request id>",
            },
            "response_example": {
                "jsonrpc": "2.0",
                "result": {"city": "北京", "spots": []},
                "id": "<same id>",
            },
        },
        "worker_agent_to_coordinator": {
            "owner": "weather/attraction/hotel/traffic agent teammates",
            "method": "POST",
            "path": "/task_result",
            "url": state.reply_to,
            "shared_builder": "common.schemas.build_result_payload",
            "payload_example": {
                "source": "attraction_agent",
                "target": COORDINATOR_NAME,
                "task_id": "<same task_id>",
                "status": "success",
                "result": "已生成景点安排骨架。",
                "error": None,
                "metadata": {
                    "mcp_server": "attraction_mcp_server",
                    "mcp_method": "search_attractions",
                    "daily_plan_skeleton": {},
                },
            },
        },
    }


def build_final_answer(question: str, snapshot: dict[str, Any]) -> str:
    """Build final user-facing answer from clean travel facts only.

    v8 rule: when every dependency Agent has succeeded, prefer the deterministic
    grounded answer. This prevents the final LLM from reintroducing old
    user-visible warnings such as "住宿信息不完整" or "市内交通信息不完整" after
    a late callback has already provided the missing data. The LLM can still be
    used in partial cases, but it never receives workflow/debug fields.
    """
    grounded_answer = _grounded_final_answer(question, snapshot)
    if _demo_fast_mode_enabled() or snapshot.get("status") != TASK_COMPLETED:
        return _fallback_final_answer(question, snapshot, "")

    results = snapshot.get("results", {}) or {}
    dispatch_errors = snapshot.get("dispatch_errors", {}) or {}
    pending_targets = snapshot.get("pending_targets", []) or []
    all_success = (
        not dispatch_errors
        and not pending_targets
        and all(
            isinstance(results.get(agent), dict) and results[agent].get("status") == RESULT_SUCCESS
            for agent in DEPENDENCY_CHAIN
        )
    )
    if all_success:
        return grounded_answer

    prompt = _coordinator_summary_prompt(question, snapshot)
    try:
        llm_response = llm.chat(prompt, max_tokens=1200, temperature=0.0, timeout_seconds=20.0)
    except LLMClientError as exc:
        llm_response = f"LLM_ERROR: {exc}"

    if not llm_response or llm_response.startswith("LLM_ERROR:"):
        return grounded_answer

    llm_response = llm_response.strip()
    if _final_answer_conflicts_with_snapshot(llm_response, snapshot):
        logger.warning("LLM final answer conflicts with real task snapshot; using grounded fallback answer")
        return grounded_answer
    return llm_response


def _final_answer_conflicts_with_snapshot(answer: str, snapshot: dict[str, Any]) -> bool:
    """Detect final-answer hallucinations that leak workflow state or contradict data.

    The final answer is user-facing travel advice. It should not discuss Agent,
    MCP, callbacks, dispatch status, timeouts, or internal metadata. Those fields
    are useful for debugging and reports, but not for the user's travel plan.
    """
    text = answer.lower()

    # User-facing final answer should be a travel plan, not a system-status report.
    internal_terms = [
        "agent",
        "mcp",
        "callback",
        "dispatch",
        "metadata",
        "task_status",
        "pending_targets",
        "success_count",
        "workflow",
        "系统状态",
        "回调",
        "调度",
        "接口",
        "日志",
    ]
    if any(term in text for term in internal_terms):
        return True

    results = snapshot.get("results", {}) or {}
    dispatch_errors = snapshot.get("dispatch_errors", {}) or {}
    pending_targets = snapshot.get("pending_targets", []) or []
    all_success = (
        not dispatch_errors
        and not pending_targets
        and all(
            isinstance(results.get(agent), dict) and results[agent].get("status") == RESULT_SUCCESS
            for agent in DEPENDENCY_CHAIN
        )
    )

    if all_success:
        forbidden = [
            "超时",
            "失败",
            "暂缺",
            "缺失",
            "不完整",
            "暂未",
            "未能获取",
            "无法提供",
            "无法获取",
            "未返回",
            "降级",
            "交通方案因数据超时",
            "自行查询上海到北京",
            "自行使用地图",
            "住宿信息不完整",
            "市内交通信息不完整",
            "建议出行前通过旅行平台确认",
            "建议出行前使用地图软件查询",
            "项目内示例数据",
            "出行前用地图软件再次确认路线",
            "需要出行前用地图软件",
        ]
        if any(word in answer for word in forbidden):
            return True

    return False


def _grounded_final_answer(question: str, snapshot: dict[str, Any]) -> str:
    """Deterministic user-facing travel plan, without workflow/status wording."""
    facts = _build_clean_final_plan_payload(question, snapshot)
    travel_task = facts.get("travel_task", {}) or {}
    weather = facts.get("weather", {}) or {}
    daily_plan = facts.get("daily_plan", {}) or {}
    hotel = facts.get("hotel", {}) or {}
    hotel_plan = hotel.get("hotel_plan", {}) or {}
    traffic = facts.get("traffic", {}) or {}
    intercity_transport = traffic.get("intercity_transport", {}) or {}
    traffic_plan = traffic.get("traffic_plan", {}) or {}
    traffic_summary = traffic.get("traffic_summary", {}) or {}
    estimated_cost = facts.get("estimated_cost", {}) or {}
    warnings = facts.get("user_visible_warnings", []) or []

    origin = travel_task.get("origin_city") or "出发地"
    dest = travel_task.get("destination_city") or "目的地"
    days = travel_task.get("days") or (len(daily_plan) if isinstance(daily_plan, dict) and daily_plan else "若干")
    budget_text = "低预算" if travel_task.get("budget_level") == "low" else "普通预算"
    transport_text = "公共交通优先" if travel_task.get("transport_preference") == "public_transport" else "按用户偏好安排交通"

    lines: list[str] = []
    lines.append(f"下面是从{origin}到{dest}的{days}天{budget_text}旅行方案，整体按{transport_text}安排。")

    weather_constraints = weather.get("weather_constraints", {}) or {}
    if weather_constraints:
        raw_condition = weather_constraints.get("raw_condition") or ""
        indoor_days = weather_constraints.get("indoor_preferred_days") or []
        outdoor_days = weather_constraints.get("outdoor_suitable_days") or weather_constraints.get("outdoor_good_days") or []
        schedule = (
            f"{'、'.join(str(day) for day in indoor_days)}优先安排室内或 mixed 景点"
            if indoor_days
            else f"{'、'.join(str(day) for day in outdoor_days) or '多数日期'}适合安排户外景点"
        )
        date = weather_constraints.get("date") or ""
        lines.append("\n一、天气与出行约束")
        weather_line = f"- {dest}{date}天气{raw_condition}，{schedule}。"
        lines.append(weather_line)

    ticket_total = _extract_ticket_total(estimated_cost, daily_plan)
    hotel_total = _extract_hotel_total(hotel_plan)
    intercity_total = _extract_intercity_total(intercity_transport)
    traffic_total = _extract_traffic_total(traffic_summary, traffic_plan)

    if isinstance(daily_plan, dict) and daily_plan:
        lines.append("\n二、每日景点安排")
        for day_key in sorted(daily_plan.keys()):
            day = daily_plan.get(day_key) or {}
            if not isinstance(day, dict):
                continue
            spots = "、".join(str(x) for x in day.get("spots", []))
            line = f"- {day_key}: {day.get('theme', '')}。景点：{spots}。"
            if day.get("area"):
                line += f"区域：{day.get('area')}。"
            if day.get("estimated_ticket_cost"):
                line += f"预计门票：{day.get('estimated_ticket_cost')}。"
            reservations = day.get("reservation_required") or []
            if reservations:
                line += f"需提前预约：{'、'.join(str(x) for x in reservations)}。"
            notes = day.get("notes") or []
            if notes:
                line += f"备注：{'；'.join(str(x) for x in notes)}。"
            lines.append(line)
        lines.append(f"景点门票小计：{_format_money_range(ticket_total)}。")

    if isinstance(hotel_plan, dict) and hotel_plan:
        lines.append("\n三、住宿建议")
        selected = hotel_plan.get("selected_hotel", {}) if isinstance(hotel_plan.get("selected_hotel"), dict) else {}
        lines.append(
            f"- 建议住宿区域：{hotel_plan.get('recommended_area', '待确认')}。"
            f"理由：{hotel_plan.get('area_reason', '方便连接每日景点')}。"
        )
        if selected:
            lines.append(
                f"- 推荐酒店：{selected.get('name', '待确认')}，"
                f"类型：{selected.get('type', '经济型住宿')}，"
                f"最近地铁：{selected.get('nearest_subway', '待确认')}，"
                f"参考价格：{selected.get('price_per_night', '待确认')}元/晚。"
                f"选择理由：{hotel_plan.get('hotel_reason', '兼顾低预算和交通便利')}。"
            )
        lines.append(f"住宿费用小计：{_format_money_range(hotel_total)}。")

    if isinstance(intercity_transport, dict) and intercity_transport:
        lines.append("\n四、城市间交通方案")
        option = intercity_transport.get("recommended_option", {})
        alternatives = intercity_transport.get("alternatives", [])
        if isinstance(option, dict):
            one_way = intercity_transport.get("one_way_cost") or _format_money_range(_parse_cost_range_list(option.get("cost_yuan_range")))
            lines.append(
                f"- 推荐：{intercity_transport.get('origin_city', origin)} -> {intercity_transport.get('destination_city', dest)}，"
                f"{option.get('mode', '交通方式待确认')}，{option.get('duration', '耗时待确认')}，单程{one_way}。"
            )
            lines.append(f"- 往返估算：{_format_money_range(intercity_total)}。")
        alt_text = _format_intercity_alternatives(alternatives)
        if alt_text:
            lines.append(f"- 备选：{alt_text}")
        lines.append(f"城市间交通小计：{_format_money_range(intercity_total)}。")

    if isinstance(traffic_plan, dict) and traffic_plan:
        lines.append("\n五、市内交通方案")
        for day_key in sorted(traffic_plan.keys()):
            segments = traffic_plan.get(day_key) or []
            if not segments:
                continue
            lines.append(f"- {day_key}:")
            for segment in segments:
                if not isinstance(segment, dict):
                    continue
                lines.append(
                    f"  - {segment.get('from', '起点')} -> {segment.get('to', '终点')}: "
                    f"{_format_transport_mode(segment.get('selected_mode', ''))}，{segment.get('route', '')}，"
                    f"约{segment.get('estimated_duration_minutes', '未知')}分钟，"
                    f"费用约{segment.get('estimated_cost_yuan', '未知')}元。"
                    f"选择理由：{segment.get('reason', '')}。"
                )
        if isinstance(traffic_summary, dict) and traffic_summary:
            lines.append(
                f"- 交通总体策略：{traffic_summary.get('main_strategy', '公共交通优先')}；"
                f"预计市内交通费用：{traffic_summary.get('total_estimated_local_transport_cost', '待确认')}。"
            )
        lines.append(f"市内交通小计：{_format_money_range(traffic_total)}。")

    total = _sum_money_ranges(ticket_total, hotel_total, intercity_total, traffic_total)
    lines.append("\n六、费用总计")
    lines.append(f"- 景点门票：{_format_money_range(ticket_total)}")
    lines.append(f"- 住宿费用：{_format_money_range(hotel_total)}")
    lines.append(f"- 城市间交通：{_format_money_range(intercity_total)}")
    lines.append(f"- 市内交通：{_format_money_range(traffic_total)}")
    lines.append(f"- 合计：{_format_money_range(total)}")
    lines.append("- 说明：以上为示例估算，实际票价、酒店价格和交通费用以出行当天平台信息为准；餐饮和购物未计入。")

    lines.append("\n七、提醒")
    lines.append("- 需要预约的热门景点请提前通过官方渠道预约，并以当天开放信息为准。")
    lines.append("- 交通耗时、费用和住宿价格为估算结果，实际出行前以当天平台信息为准。")
    for warning in warnings:
        # Safety guard: final user answer should not contain stale data-missing
        # messages when the corresponding Agent result is already available.
        if "不完整" in str(warning) or "缺失" in str(warning):
            continue
        lines.append(f"- {warning}")

    return _clean_final_answer_text("\n".join(lines))


def _extract_ticket_total(estimated_cost: dict[str, Any], daily_plan: dict[str, Any]) -> tuple[float, float] | None:
    if isinstance(estimated_cost, dict) and estimated_cost.get("ticket_total"):
        return _parse_money_range(estimated_cost.get("ticket_total"))
    if not isinstance(daily_plan, dict):
        return None
    return _sum_money_ranges(
        *[
            _parse_money_range(day.get("estimated_ticket_cost"))
            for day in daily_plan.values()
            if isinstance(day, dict) and day.get("estimated_ticket_cost")
        ]
    )


def _extract_hotel_total(hotel_plan: dict[str, Any]) -> tuple[float, float] | None:
    if not isinstance(hotel_plan, dict):
        return None
    return _parse_money_range(hotel_plan.get("estimated_total_hotel_cost"))


def _extract_intercity_total(intercity_transport: dict[str, Any]) -> tuple[float, float] | None:
    if not isinstance(intercity_transport, dict):
        return None
    total = _parse_money_range(intercity_transport.get("estimated_intercity_cost"))
    if total:
        return total
    option = intercity_transport.get("recommended_option")
    if isinstance(option, dict):
        one_way = _parse_cost_range_list(option.get("cost_yuan_range"))
        if one_way:
            return one_way[0] * 2, one_way[1] * 2
    return None


def _extract_traffic_total(
    traffic_summary: dict[str, Any],
    traffic_plan: dict[str, Any],
) -> tuple[float, float] | None:
    if isinstance(traffic_summary, dict):
        total = _parse_money_range(traffic_summary.get("total_estimated_local_transport_cost"))
        if total:
            return total
    if not isinstance(traffic_plan, dict):
        return None
    costs: list[tuple[float, float] | None] = []
    for segments in traffic_plan.values():
        if not isinstance(segments, list):
            continue
        for segment in segments:
            if isinstance(segment, dict):
                costs.append(_parse_money_range(segment.get("estimated_cost_yuan")))
    return _sum_money_ranges(*costs)


def _parse_cost_range_list(value: Any) -> tuple[float, float] | None:
    if (
        isinstance(value, list)
        and len(value) >= 2
        and isinstance(value[0], (int, float))
        and isinstance(value[1], (int, float))
    ):
        return float(value[0]), float(value[1])
    return None


def _parse_money_range(value: Any) -> tuple[float, float] | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        amount = float(value)
        return amount, amount

    text = str(value)
    total_low = 0.0
    total_high = 0.0
    found = False
    pattern = re.compile(r"(\d+(?:\.\d+)?)\s*(?:-|~|至|到)\s*(\d+(?:\.\d+)?)\s*元|(\d+(?:\.\d+)?)\s*元")
    for match in pattern.finditer(text):
        found = True
        if match.group(3) is not None:
            low = high = float(match.group(3))
        else:
            low = float(match.group(1))
            high = float(match.group(2))
        total_low += low
        total_high += high
    return (total_low, total_high) if found else None


def _sum_money_ranges(*ranges: tuple[float, float] | None) -> tuple[float, float] | None:
    total_low = 0.0
    total_high = 0.0
    found = False
    for item in ranges:
        if not item:
            continue
        found = True
        total_low += item[0]
        total_high += item[1]
    return (total_low, total_high) if found else None


def _format_money_range(value: tuple[float, float] | None) -> str:
    if not value:
        return "待确认"
    low, high = value
    low_text = _format_amount(low)
    high_text = _format_amount(high)
    if low_text == high_text:
        return f"约{low_text}元"
    return f"约{low_text}-{high_text}元"


def _format_amount(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else f"{value:.1f}".rstrip("0").rstrip(".")


def _format_advice_text(value: Any) -> str:
    text = str(value).strip()
    if text.startswith("建议"):
        return text
    return f"建议{text}"


def _format_transport_mode(value: Any) -> str:
    mode_map = {
        "walk": "步行",
        "bus": "公交",
        "subway": "地铁",
        "taxi": "打车",
    }
    text = str(value).strip()
    return mode_map.get(text, text)


def _format_intercity_alternatives(alternatives: Any) -> str:
    if not isinstance(alternatives, list):
        return ""
    parts: list[str] = []
    for item in alternatives:
        if not isinstance(item, dict):
            continue
        mode = str(item.get("mode") or "").strip()
        reason = str(item.get("reason") or "").strip()
        if not mode:
            continue
        if "高铁" in mode:
            continue
        if "普速" in mode:
            parts.append("普速火车更省钱但耗时更长")
        elif "飞机" in mode:
            parts.append("飞机可能更快但价格波动较大且机场通勤成本更高")
        elif reason:
            parts.append(f"{mode}{reason}")
        else:
            parts.append(mode)
    return "；".join(dict.fromkeys(parts)) + ("。" if parts else "")


def _clean_final_answer_text(text: str) -> str:
    replacements = {
        "建议：建议": "建议",
        "。。": "。",
        "。 ": "。",
        "walk": "步行",
        "bus": "公交",
        "subway": "地铁",
        "taxi": "打车",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    while "。。" in text:
        text = text.replace("。。", "。")
    return text


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
    logger.info("Latest workflow: Weather Agent -> Attraction Agent -> Hotel Agent -> Traffic Agent")
    logger.info("Endpoints: POST /submit_task, POST /task_result, GET /health, GET /tasks, GET /contracts")
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


def _agent_execute_url(agent: dict[str, Any]) -> str:
    return f"http://{agent['host']}:{agent['port']}{agent.get('execute_path', '/execute_task')}"


def _agent_tcp_url(agent: dict[str, Any]) -> str:
    return tcp_url(str(agent["host"]), int(agent["port"]))


def _enabled_agents_view() -> dict[str, Any]:
    discovered_agents = _fetch_discovered_agents()
    return {
        name: {
            "url": _agent_tcp_url(agent) if agent.get("protocol", "tcp") == "tcp" else _agent_execute_url(agent),
            "protocol": agent.get("protocol", "tcp"),
            "capabilities": agent.get("capabilities", []),
            "enabled": agent.get("enabled", True),
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


def _remaining_seconds(deadline: float) -> float:
    return max(0.0, deadline - time.monotonic())


def _stage_wait_seconds(deadline: float, minimum_seconds: float = 45.0) -> float:
    """Return a stage wait bounded by the user-facing workflow timeout.

    The minimum_seconds parameter is kept for backward-compatible call sites,
    but fault-tolerance runs must never extend beyond the submit_task timeout.
    """
    return max(0.1, min(MAX_TASK_TIMEOUT_SECONDS, _remaining_seconds(deadline)))


def _looks_like_result_payload(value: Any) -> bool:
    return isinstance(value, dict) and {"source", "target", "task_id", "status"}.issubset(value)


def _demo_fast_mode_enabled() -> bool:
    return os.getenv("A2A_DEMO_FAST", "").strip().lower() in {"1", "true", "yes", "on"}


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


def _build_clean_final_plan_payload(question: str, snapshot: dict[str, Any]) -> dict[str, Any]:
    """Extract only travel facts for the final LLM.

    This intentionally removes workflow/debug fields such as status,
    success_count, dispatch_errors, Agent names, MCP names, callback metadata,
    elapsed_ms, and llm_error. The final LLM should answer the travel plan only.
    """
    results = snapshot.get("results", {}) or {}
    plan = snapshot.get("plan", {}) or {}
    travel_task = plan.get("travel_task", {}) or {}

    weather = results.get("weather_agent", {}) if isinstance(results.get("weather_agent"), dict) else {}
    attraction = results.get("attraction_agent", {}) if isinstance(results.get("attraction_agent"), dict) else {}
    hotel = results.get("hotel_agent", {}) if isinstance(results.get("hotel_agent"), dict) else {}
    traffic = results.get("traffic_agent", {}) if isinstance(results.get("traffic_agent"), dict) else {}

    weather_meta = weather.get("metadata", {}) if isinstance(weather, dict) else {}
    weather_constraints = (
        weather_meta.get("weather_constraints")
        or (weather_meta.get("structured_result", {}) or {}).get("weather_constraints")
        or {}
    )
    raw_weather = weather_meta.get("mcp_result") or {}

    attraction_meta = attraction.get("metadata", {}) if isinstance(attraction, dict) else {}
    attraction_structured = attraction_meta.get("structured_result", {}) if isinstance(attraction_meta, dict) else {}
    daily_plan = (
        attraction_structured.get("daily_plan")
        or attraction_structured.get("daily_plan_skeleton")
        or attraction_meta.get("daily_plan_skeleton")
        or {}
    )
    estimated_cost = attraction_structured.get("estimated_cost") or attraction_meta.get("estimated_cost") or {}
    rejected_spots = attraction_structured.get("rejected_spots") or []

    hotel_meta = hotel.get("metadata", {}) if isinstance(hotel, dict) else {}
    hotel_structured = hotel_meta.get("structured_result", {}) if isinstance(hotel_meta, dict) else {}
    hotel_plan = hotel_structured.get("hotel_plan") or hotel_meta.get("hotel_plan") or {}

    traffic_meta = traffic.get("metadata", {}) if isinstance(traffic, dict) else {}
    traffic_structured = traffic_meta.get("structured_result", {}) if isinstance(traffic_meta, dict) else {}
    traffic_plan = traffic_structured.get("traffic_plan") or traffic_meta.get("traffic_plan") or {}
    traffic_summary = traffic_structured.get("traffic_summary") or traffic_meta.get("traffic_summary") or {}
    intercity_transport = (
        traffic_structured.get("intercity_transport")
        or traffic_meta.get("intercity_transport")
        or {}
    )

    results = snapshot.get("results", {}) or {}
    dispatch_errors = snapshot.get("dispatch_errors", {}) or {}
    pending_targets = snapshot.get("pending_targets", []) or []
    all_success = (
        not dispatch_errors
        and not pending_targets
        and all(
            isinstance(results.get(agent), dict) and results[agent].get("status") == RESULT_SUCCESS
            for agent in DEPENDENCY_CHAIN
        )
    )

    warnings: list[str] = []
    # Only expose data-missing warnings when the workflow is genuinely partial.
    # If every Agent has succeeded, never let old/partial warning text leak into
    # the user-facing final answer. The debugging JSON still contains full status.
    if not all_success:
        if not weather_constraints:
            warnings.append("天气约束信息不完整，建议出行前再次确认天气。")
        if not daily_plan:
            warnings.append("景点行程信息不完整，需要补充景点数据后再细化。")
        if not hotel_plan:
            warnings.append("住宿信息不完整，需要出行前再次确认酒店位置和价格。")
        if not traffic_plan:
            warnings.append("市内交通信息不完整，需要出行前用地图软件再次确认路线。")

    return {
        "question": question,
        "travel_task": travel_task,
        "weather": {
            "raw_weather": raw_weather,
            "weather_constraints": weather_constraints,
        },
        "daily_plan": daily_plan,
        "estimated_cost": estimated_cost,
        "rejected_spots": rejected_spots,
        "hotel": {
            "hotel_plan": hotel_plan,
        },
        "traffic": {
            "intercity_transport": intercity_transport,
            "traffic_plan": traffic_plan,
            "traffic_summary": traffic_summary,
        },
        "user_visible_warnings": warnings,
    }


def _coordinator_summary_prompt(question: str, snapshot: dict[str, Any]) -> str:
    clean_payload = _build_clean_final_plan_payload(question, snapshot)
    return "\n".join(
        [
            "你是旅行方案整理助手，只负责把结构化旅行数据整理成最终用户方案。",
            "只输出旅行方案，不要输出系统运行说明。",
            "禁止提到：Agent、MCP、workflow、callback、metadata、dispatch、系统状态、调用过程、接口、日志、超时、失败、降级。",
            "只有当 user_visible_warnings 非空时，才可以写与用户出行相关的注意事项；不要说任何内部模块失败。",
            "如果 user_visible_warnings 为空，禁止写住宿信息不完整、市内交通信息不完整、建议自行确认酒店或建议自行查询地图。",
            "即使要写提醒，也只能提醒预约、穿衣和预算；不要写数据缺失类提醒。",
            "如果 hotel.hotel_plan 非空，必须根据其中内容写住宿区域和推荐酒店，不得说住宿信息缺失。",
            "如果 traffic.traffic_plan 非空，必须根据其中内容写具体交通方案，不得说交通方案缺失或暂缺。",
            "如果 daily_plan 非空，必须根据其中内容写每日景点安排，不得说景点信息缺失。",
            "如果 weather.weather_constraints 非空，必须根据其中内容写天气与出行建议。",
            "不要编造给定 JSON 之外的班次、价格、天气或景点。",
            "输出中文，结构清楚，包含：天气建议、每日景点、住宿建议、交通方案、费用/预约提醒。",
            json.dumps(clean_payload, ensure_ascii=False, default=str),
        ]
    )


def _fallback_final_answer(question: str, snapshot: dict[str, Any], llm_response: str) -> str:
    results = snapshot.get("results", {})
    dispatch_errors = snapshot.get("dispatch_errors", {})
    if not results:
        return (
            "最终旅行方案暂不可生成：未获得可用 Agent 结果。"
            "请确认 Weather、Attraction、Hotel、Traffic Agent 及对应 MCP Server 已启动，并查看 dispatch_errors。"
        )

    lines = [f"最终旅行方案：用户问题为「{question}」。"]
    lines.append("本次任务按照 Weather Agent -> Attraction Agent -> Hotel Agent -> Traffic Agent 的网络依赖顺序执行。")

    for source in DEPENDENCY_CHAIN:
        payload = results.get(source)
        if not payload:
            continue
        result = payload.get("result")
        status = payload.get("status")
        if status == RESULT_SUCCESS:
            lines.append(f"- {source}: {result}")
        else:
            lines.append(f"- {source}: 执行失败，错误为 {payload.get('error')}")

    attraction = results.get("attraction_agent", {})
    attraction_meta = attraction.get("metadata", {}) if isinstance(attraction, dict) else {}
    daily_plan = attraction_meta.get("daily_plan_skeleton") if isinstance(attraction_meta, dict) else None
    if isinstance(daily_plan, dict) and daily_plan:
        lines.append("景点行程骨架：")
        for day, plan in daily_plan.items():
            if isinstance(plan, dict):
                spots = "、".join(plan.get("spots", [])) if isinstance(plan.get("spots"), list) else "待定"
                lines.append(f"- {day}: {spots}（{plan.get('area', '区域待定')}）")

    if dispatch_errors:
        for source, err in dispatch_errors.items():
            lines.append(f"- {source} [DISPATCH_ERROR]: {err}")

    if llm_response:
        lines.append(f"- llm: {llm_response}")
    return "\n".join(lines)


if __name__ == "__main__":
    main()
