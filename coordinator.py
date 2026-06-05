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
from datetime import date as date_type, datetime, timedelta
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
    BACKUP_REGISTRY_HOST,
    BACKUP_REGISTRY_PORT,
    COORDINATOR_HOST,
    COORDINATOR_NAME,
    COORDINATOR_PORT,
    DEFAULT_TASK_TIMEOUT_SECONDS,
    DISPATCH_HTTP_TIMEOUT_SECONDS,
    MAX_TASK_TIMEOUT_SECONDS,
    MCP_SERVERS,
)
from common.http_client import HttpJsonClientError, post_json
from common.internal_values import (
    display_budget_level,
    display_transport_preference,
    is_iso_date as is_internal_iso_date,
    is_unknown,
    normalize_budget_level,
    normalize_transport_preference,
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
        '''
        这个task计划使用的agent数量
        '''
        return len(self.targets)

    def terminal_count(self) -> int:
        '''
        所有成功返回结果的agent数量 + 所有调度失败的agent数量
        '''
        return len(self.results) + len(self.dispatch_errors)

    def success_count(self) -> int:
        # 获取成功返回结果的agent数量
        return sum(1 for item in self.results.values() if item.get("status") == RESULT_SUCCESS)

    def pending_targets(self) -> list[str]:
        # 获取尚未返回结果的target列表
        finished = set(self.results) | set(self.dispatch_errors)
        return [target for target in self.targets if target not in finished]

    def refresh_status(self) -> None:
        # 刷新任务状态
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
        '''
        更新整个record的status，是success、partial还是failed
        '''
        if self.terminal_count() >= self.expected_count():
            self.refresh_status()
        elif self.success_count() > 0:
            self.status = TASK_PARTIAL
            self.updated_at = utc_now_iso()
        else:
            self.status = TASK_FAILED
            self.updated_at = utc_now_iso()

    def snapshot(self) -> dict[str, Any]:
        # 获取快照
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
        # 初始化
        self.host = host
        self.port = port
        self.tcp_host = tcp_host or COORDINATOR_A2A_TCP_HOST
        self.tcp_port = tcp_port or COORDINATOR_A2A_TCP_PORT
        self._tasks: dict[str, TaskRecord] = {}
        self._condition = threading.Condition(threading.RLock())

    @property
    def base_url(self) -> str:
        # 获取基础URL
        return f"http://{self.host}:{self.port}"

    @property
    def reply_to(self) -> str:
        # 获取A2A TCP回调地址
        return tcp_url(self.tcp_host, self.tcp_port)

    @property
    def http_reply_to(self) -> str:
        # 获取HTTP回调地址
        return f"{self.base_url}/task_result"

    def create_task(
        self,
        question: str,
        targets: list[str],
        timeout_seconds: float,
        plan: dict[str, Any] | None = None,
        task_id: str | None = None,
    ) -> TaskRecord:
        # 创建新任务记录
        record = TaskRecord(
            task_id=task_id or new_task_id(),
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
        # 根据task_id获取任务记录
        with self._condition:
            return self._tasks.get(task_id)

    def list_tasks(self) -> list[dict[str, Any]]:
        # 获取所有任务快照列表
        with self._condition:
            return [task.snapshot() for task in self._tasks.values()]

    def add_result(self, payload: dict[str, Any]) -> TaskRecord:
        # 添加任务结果并刷新状态
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
        '''
        如果某个target的agent无法被调度，比如调度时agent不在线，或者TCP请求失败，将其在record的dispatch_errors里记录错误信息
        '''
        with self._condition:
            record = self._tasks.get(task_id)
            if record is None:
                return
            record.dispatch_errors[target] = message
            record.results.pop(target, None)
            record.refresh_status()
            self._condition.notify_all()

    def wait_for_any_target(self, task_id: str, targets: set[str], timeout_seconds: float) -> dict[str, Any]:
        '''
        心跳，检查当前被调度出去的target agents返回了结果或者调度失败了
        如果有，立即返回snapshot，如果没有，等待timeout_seconds后返回
        '''
        deadline = time.monotonic() + max(0.1, timeout_seconds)
        with self._condition:
            record = self._tasks[task_id]
            record.status = TASK_WAITING
            record.updated_at = utc_now_iso()
            while not any(t in record.results or t in record.dispatch_errors for t in targets):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._condition.wait(timeout=remaining)
            return record.snapshot()

    def wait_for_target(self, task_id: str, target: str, timeout_seconds: float) -> dict[str, Any]:
        # 等待指定target返回结果
        deadline = time.monotonic() + max(0.0, timeout_seconds)
        with self._condition:
            record = self._tasks[task_id]
            record.status = TASK_WAITING
            record.updated_at = utc_now_iso()
            while target not in record.results and target not in record.dispatch_errors:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    record.dispatch_errors[target] = f"{target} callback timed out after {timeout_seconds:.2f}s"
                    record.refresh_status()
                    break
                self._condition.wait(timeout=remaining)
            self._condition.notify_all()
            return record.snapshot()

    def wait_for_task(self, task_id: str, timeout_seconds: float) -> dict[str, Any]:
        '''
        检查这个task的所有target agents是否都返回了结果或者调度失败了
        如果都返回了，立即返回snapshot，如果没有，等待timeout_seconds后强制返回
        '''
        wait_seconds = max(0.1, timeout_seconds)
        deadline = time.monotonic() + wait_seconds
        with self._condition:
            record = self._tasks[task_id]
            record.status = TASK_WAITING
            while record.terminal_count() < record.expected_count():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._condition.wait(timeout=remaining)
            for target in record.pending_targets():
                record.dispatch_errors[target] = f"{target} callback timed out after {timeout_seconds:.2f}s"
            record.finalize_after_wait()
            self._condition.notify_all()
            return record.snapshot()

    def set_final_answer(self, task_id: str, final_answer: str) -> dict[str, Any]:
        # 设置最终答案
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
        # 初始化
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
        '''
        负责同agent取得联系，把task payload发出；
        如果agent挂掉了（不在注册中心），或者回复的ack超过A2A_TCP_TIMEOUT_SECONDS，或者TCP报文不对，就返回dispatch_error；
        只关注分发那一刻，与agent内部处理任务和返回结果的时长无关
        '''
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
        # 初始化
        super().__init__(server_address, handler_class)
        self.state = state


class CoordinatorA2ATCPRequestHandler(BaseRequestHandler):
    server: CoordinatorA2ATCPServer

    def handle(self) -> None:
        # 处理TCP连接请求
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
        # 处理GET请求
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
                        "dependency_chain": "Dynamic DAG",
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
                        "flow": "Dynamic LLM-based DAG scheduler" ,
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
        # 处理POST请求
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
        # 禁止默认日志输出
        return

    def _handle_submit_task(self) -> None:
        # 处理提交任务请求
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

        from common.schemas import new_task_id
        temp_tid = new_task_id()

        # 预创建任务记录（空 targets），让客户端能立即轮询
        init_record = self.server.state.create_task(question, [], timeout_seconds, {}, task_id=temp_tid)

        def full_workflow(tid: str) -> None:
            try:
                available_agents = _fetch_discovered_agents()
                travel_task, workflow_dag = extract_travel_task(question, available_agents)
                targets = [node["agent"] for node in workflow_dag]
                plan = build_dependency_plan(question, targets, travel_task, workflow_dag)
                # 更新任务记录（替换空的 targets 和 plan）
                with self.server.state._condition:
                    record = self.server.state._tasks[tid]
                    record.targets = targets
                    record.plan = plan
                    record.updated_at = utc_now_iso()
                    self.server.state._condition.notify_all()
                workflow_deadline = time.monotonic() + timeout_seconds

                log_network_event(event="submit_task", direction="inbound", source="user",
                    target=COORDINATOR_NAME, method="POST", url="/submit_task",
                    task_id=tid, payload=payload, payload_size=payload_size)

                completed_nodes: set[str] = set()
                dispatched_nodes: set[str] = set()
                while len(completed_nodes) < len(workflow_dag) and time.monotonic() < workflow_deadline:
                    for node in workflow_dag:
                        if node["node_id"] in dispatched_nodes:
                            continue
                        deps = set(node.get("depends_on", []))
                        if not deps.issubset(completed_nodes):
                            continue
                        rec = self.server.state.get_task(tid)
                        snapshot = rec.snapshot()
                        inputs = build_dynamic_inputs(snapshot.get("results", {}), deps, workflow_dag)
                        context = build_node_context(record=rec, node_id=node["node_id"],
                            stage=f"{node['node_id']}_processing", dependencies=list(deps),
                            inputs=inputs, target=node["agent"])
                        log_network_event(event=f"dependency_dispatch_{node['node_id']}", direction="internal",
                            source=COORDINATOR_NAME, target=node["agent"], task_id=tid,
                            payload={"reason": f"dependencies {list(deps)} satisfied" if deps else "no dependencies",
                                     "node_id": node["node_id"]})
                        self.server.dispatch_to_agent(rec, node["agent"], context=context,
                            event=f"dispatch_{node['node_id']}_task")
                        dispatched_nodes.add(node["node_id"])

                    pending_nodes = dispatched_nodes - completed_nodes
                    if not pending_nodes:
                        break
                    pending_agents = {n["agent"] for n in workflow_dag if n["node_id"] in pending_nodes}
                    self.server.state.wait_for_any_target(tid, pending_agents, timeout_seconds=A2A_TCP_TIMEOUT_SECONDS)
                    snapshot = self.server.state.get_task(tid).snapshot()
                    results = snapshot.get("results", {})
                    errors = snapshot.get("dispatch_errors", {})
                    for node in workflow_dag:
                        if node["node_id"] in pending_nodes and (node["agent"] in results or node["agent"] in errors):
                            completed_nodes.add(node["node_id"])
                            log_network_event(event=f"node_completed_{node['node_id']}", direction="internal",
                                source=COORDINATOR_NAME, target="coordinator", task_id=tid,
                                payload={"node_id": node["node_id"], "agent": node["agent"],
                                         "status": results.get(node["agent"], {}).get("status")
                                         if node["agent"] in results else "dispatch_error"})

                remaining = max(2.0, workflow_deadline - time.monotonic())
                snapshot = self.server.state.wait_for_task(tid, remaining)
                final_answer = build_final_answer(question, snapshot)
                snapshot = self.server.state.set_final_answer(tid, final_answer)
                log_network_event(event="dependency_workflow_finished", direction="internal",
                    source=COORDINATOR_NAME, target="user", task_id=tid, payload=snapshot)
            except Exception as exc:
                logger.exception(f"DAG worker failed for {tid}: {exc}")

        log_network_event(event="submit_task_queued", direction="inbound", source="user",
            target=COORDINATOR_NAME, method="POST", url="/submit_task",
            payload={"question": question, "timeout": timeout_seconds})
        threading.Thread(target=full_workflow, args=(temp_tid,), daemon=True).start()
        self._send_json(HTTPStatus.ACCEPTED, success_response({"task": {
            "task_id": temp_tid, "status": "pending", "question": question,
        }}))

    def _handle_task_result(self) -> None:
        # 处理任务结果回调
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
        # 处理查询任务请求
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
        # 读取请求体并解析JSON
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
        # 发送JSON响应
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
        # 在后台线程刷新最终答案
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
    """向注册中心查找所有健康的agents，主节点失败则尝试备用节点，都没有则返回默认
    """
    # 尝试主注册中心
    try:
        primary_url = f"http://{REGISTRY_HOST}:{REGISTRY_PORT}/discover"
        start = time.time()
        log_network_event(
            event="registry_discover_request",
            direction="outbound",
            source="coordinator",
            target="registry_center_primary",
            method="GET",
            url=primary_url,
        )
        req = request.Request(primary_url, method="GET")
        with request.urlopen(req, timeout=1.5) as response:
            if response.status == 200:
                data = json.loads(response.read().decode("utf-8"))
                discovered = data.get("agents", {})
                if isinstance(discovered, dict) and discovered:
                    log_network_event(
                        event="registry_discover_response",
                        direction="inbound",
                        source="registry_center_primary",
                        target="coordinator",
                        method="GET",
                        url=primary_url,
                        payload={"agent_count": len(discovered), "agents": sorted(discovered.keys())},
                        status_code=response.status,
                        elapsed_ms=(time.time() - start) * 1000,
                    )
                    return discovered
    except Exception as e:
        log_network_event(
            event="registry_discover_error",
            direction="inbound",
            source="registry_center_primary",
            target="coordinator",
            method="GET",
            url=f"http://{REGISTRY_HOST}:{REGISTRY_PORT}/discover",
            error=str(e),
            error_type=type(e).__name__,
        )
        logger.warning(f"Coordinator failed to discover agents from primary registry: {e}")

    # 尝试备用注册中心
    try:
        backup_url = f"http://{BACKUP_REGISTRY_HOST}:{BACKUP_REGISTRY_PORT}/discover"
        start = time.time()
        log_network_event(
            event="registry_discover_request",
            direction="outbound",
            source="coordinator",
            target="registry_center_backup",
            method="GET",
            url=backup_url,
        )
        req = request.Request(backup_url, method="GET")
        with request.urlopen(req, timeout=1.5) as response:
            if response.status == 200:
                data = json.loads(response.read().decode("utf-8"))
                discovered = data.get("agents", {})
                if isinstance(discovered, dict):
                    log_network_event(
                        event="registry_discover_response",
                        direction="inbound",
                        source="registry_center_backup",
                        target="coordinator",
                        method="GET",
                        url=backup_url,
                        payload={"agent_count": len(discovered), "agents": sorted(discovered.keys())},
                        status_code=response.status,
                        elapsed_ms=(time.time() - start) * 1000,
                    )
                    logger.info("Successfully discovered agents from backup registry")
                    return discovered
    except Exception as e:
        log_network_event(
            event="registry_discover_error",
            direction="inbound",
            source="registry_center_backup",
            target="coordinator",
            method="GET",
            url=f"http://{BACKUP_REGISTRY_HOST}:{BACKUP_REGISTRY_PORT}/discover",
            error=str(e),
            error_type=type(e).__name__,
        )
        logger.error(f"Coordinator failed to discover agents from backup registry: {e}, fall back to local config")
        
    return dict(AGENTS)


def _infer_error_type(exc: Exception) -> str:
    # 推断异常类型名称
    cause = exc.__cause__
    if cause is None:
        return type(exc).__name__
    reason = getattr(cause, "reason", None)
    if reason is not None:
        return type(reason).__name__
    return type(cause).__name__


def build_dependency_plan(question: str, targets: list[str], travel_task: dict[str, Any], workflow_dag: list[dict[str, Any]]) -> dict[str, Any]:
    '''
    整合成一个规范化的plan，包括task，要用到的agent和工作流
    '''
    return {
        "selected_by": travel_task.get("_parser", "fixed_travel_dependency_chain"),
        "workflow": "travel_dependency",
        "selected_targets": targets,
        "dependency_chain": workflow_dag,
        "task_request": {
            "raw_instruction": question,
            "split_policy": "coordinator_only_splits_request",
            "agent_parse_policy": "each_agent_extracts_mcp_params_from_instruction",
        },
        "available_agents": _enabled_agents_view(),
        "routing_policy": "dynamic LLM-based DAG scheduler",
        "llm": llm.info(),
    }


def build_dynamic_inputs(results: dict[str, Any], deps: set[str], workflow_dag: list[dict[str, Any]]) -> dict[str, Any]:
    """基于已完成agent的结果，动态构建下一个agent的输入"""
    inputs: dict[str, Any] = {}
    upstream_results = {}
    
    for dep_node_id in deps:
        # 找到依赖的节点对应的 agent_name
        agent_name = None
        for node in workflow_dag:
            if node["node_id"] == dep_node_id:
                agent_name = node["agent"]
                break
                
        if agent_name and agent_name in results:
            res = results[agent_name]
            if res.get("status") == "success":
                upstream_results[agent_name] = {
                    "result": res.get("result"),
                    "structured": res.get("metadata", {}).get("structured_result", {})
                }
                
    inputs["upstream_results"] = upstream_results
    return inputs


def build_node_context(
    *,
    record: TaskRecord,
    node_id: str,
    stage: str,
    dependencies: list[str],
    inputs: dict[str, Any],
    target: str,
) -> dict[str, Any]:
    # 构建agent节点的上下文信息
    agent_config = _fetch_discovered_agents().get(target, {})
    node_goal = _agent_node_goal(target)
    return {
        "workflow": "travel_dependency",
        "node_id": node_id,
        "stage": stage,
        "dependencies": dependencies,
        "request": {
            "original_instruction": record.question,
            "node_goal": node_goal,
            "agent_instruction": f"{node_goal}。请在 Agent 内部解析原始请求，并转换为对应 MCP 方法的 JSON-RPC params。",
        },
        "inputs": inputs,
        "coordinator_plan": record.plan,
        "agent_capabilities": agent_config.get("capabilities", []),
    }


def _agent_node_goal(target: str) -> str:
    return {
        "weather_agent": "解析目的地、日期和天数，查询天气并生成天气约束",
        "attraction_agent": "解析目的地、天数、预算、必去景点和偏好，查询并规划景点",
        "hotel_agent": "解析目的地、预算、住宿偏好，并结合景点结果选择住宿区域和酒店",
        "traffic_agent": "解析出发地、目的地、交通偏好和预算，并结合景点/酒店结果生成路线",
        "packing_agent": "解析目的地、天数和出行约束，并结合天气结果生成行李清单",
    }.get(target, "解析原始用户请求并完成本节点任务")


def extract_travel_task(question: str, available_agents: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """
    Coordinator only splits the request into a workflow DAG.

    The returned first dict is split metadata, not MCP-callable travel params.
    Each downstream Agent must parse the original instruction and build its own
    MCP JSON-RPC params.
    """
    default_dag = _default_travel_dag(question, available_agents)
    if _demo_fast_mode_enabled():
        fallback = {
            "_parser": "demo_fast_request_split",
            "_raw_question": question,
            "split_only": True,
        }
        return fallback, default_dag
    try:
        logger.info("正在请求大模型生成总体规划和动态 DAG 工作流，请耐心等待...")
        llm_json = llm.chat_json(
            _task_analysis_prompt(question, available_agents),
            max_tokens=900,
            temperature=0.0,
            timeout_seconds=60.0,
        )
        request_split = llm_json.get("request_split") if isinstance(llm_json.get("request_split"), dict) else {}
        if not isinstance(request_split, dict):
            request_split = {}
        workflow_dag = llm_json.get("workflow_dag", default_dag)
        logger.info(f"【大模型提取工作流】：{workflow_dag}")
        if not isinstance(workflow_dag, list):
            workflow_dag = default_dag
        workflow_dag = _ensure_travel_workflow_dag(workflow_dag, default_dag, available_agents)
        request_split.setdefault("_parser", "coordinator_llm_request_splitter")
        request_split.setdefault("_raw_question", question)
        request_split.setdefault("split_only", True)
        return request_split, workflow_dag
    except Exception as exc:
        logger.warning(f"【大模型提取工作流失败】，回退到规则提取: {type(exc).__name__}: {exc}")
        import traceback
        traceback.print_exc()
        fallback = {
            "_parser": "rule_fallback_request_split",
            "_parser_error": str(exc),
            "_raw_question": question,
            "split_only": True,
        }
        return fallback, default_dag


def _task_analysis_prompt(question: str, available_agents: dict[str, Any]) -> str:
    '''
    构造给llm的提示词，给出期望的输出格式（travel_task和workflow_dag）。

    关键设计：output_schema 中的枚举值仅为 LLM 提供参考范围，
    但 raw_constraints 字段才是自由文本核心——LLM 把用户的真实意图、
    偏好、约束用自己的语言总结出来，这个字段会被传给每个 Agent，
    让 Agent 的 LLM 在自己的领域内自行解读。

    例如"舒适为主、预算不限" → raw_constraints 应写：
    "用户追求舒适体验，不在乎花费。应优先选择高质量、高评分的选项，
    避免因省钱而牺牲体验。适合推荐高端或奢华类型。"
    '''
    split_payload = {
        "question": question,
        "available_agents_from_registry": {
            name: {
                "capabilities": agent.get("capabilities", []),
            }
            for name, agent in available_agents.items()
        },
        "output_schema": {
            "request_split": {
                "split_only": True,
                "summary": "只概括用户请求用于调度展示，不解析 MCP 参数",
                "agent_tasks": {
                    "weather_agent": "本节点需要从原始请求中自行解析天气查询参数",
                    "attraction_agent": "本节点需要从原始请求中自行解析景点查询参数",
                    "hotel_agent": "本节点需要从原始请求中自行解析酒店查询参数",
                    "traffic_agent": "本节点需要从原始请求中自行解析交通查询参数",
                    "packing_agent": "本节点需要从原始请求中自行解析行李查询参数",
                },
            },
            "workflow_dag": [
                {
                    "node_id": "agent_domain_name",
                    "agent": "actual_agent_name",
                    "depends_on": ["upstream_node_id"],
                }
            ],
        },
    }
    return "\n".join(
        [
            "你是 Coordinator 的请求拆分器，只负责选择需要调用的 Agent 和 DAG 依赖关系。",
            "禁止解析或输出 origin_city、destination_city、days、start_date、budget_level、transport_preference 等 MCP 参数字段。",
            "这些业务字段必须由各 Agent 在收到原始 instruction 后自行解析，并转换为 Gateway/MCP 可调用的 JSON-RPC params。",
            "请参考 available_agents_from_registry，只选择当前在线且与用户请求相关的 Agent。",
            "一般旅行规划需要 weather、attraction、hotel、traffic、packing；若用户明确排除某类任务，可不选择对应 Agent。",
            "依赖关系建议：attraction 依赖 weather；hotel 依赖 attraction；traffic 依赖 attraction 和 hotel；packing 依赖 weather。",
            "只输出合法 JSON，不要 Markdown，不要解释。",
            json.dumps(split_payload, ensure_ascii=False, default=str),
        ]
    )

    payload = {
        "question": question,
        "current_date": date_type.today().isoformat(),
        "available_agents_from_registry": {
            name: {
                "capabilities": agent.get("capabilities", []),
            }
            for name, agent in available_agents.items()
        },
        "output_schema": {
            "travel_task": {
                "origin_city": "出发城市 or null",
                "destination_city": "目的城市",
                "days": 5,
                "start_date": "YYYY-MM-DD 必须把相对日期（明天/下周二/中秋节假期）换算成具体日期",
                "end_date": "YYYY-MM-DD or null",
                "date_text": "用户原始日期表述，如 下周二、中秋节假期",
                "budget_level": "high|normal|low|unknown",
                "transport_preference": "fastest|comfort|public_transport|cheapest|taxi|normal",
                "must_visit": ["用户点名必须去的景点"],
                "preferences": ["用户偏好的标签"],
                "avoid": ["用户明确拒绝的"],
                "raw_constraints": "【重要】用 2-4 句自然语言总结用户的核心诉求和约束，不要压缩成枚举值。"
                                  "例如：用户预算充裕追求舒适，希望高档酒店和专车出行，"
                                  "行程节奏要轻松不赶，景点偏好历史文化类，对价格不敏感。"
                                  "这个字段会被传递给下游 Agent 的 LLM 做决策。",
                "constraints": {
                    "attractions": {
                        "must_visit": ["用户点名必去的景点"],
                        "preferred_types": ["nature", "museum", "history", "food_street", "shopping", "entertainment"],
                        "avoid": [],
                        "pace": "relaxed|normal|packed|unknown",
                    },
                    "traffic": {
                        "preference": "fastest|comfort|public_transport|cheapest|taxi|normal",
                        "avoid": [],
                        "max_transfer": None,
                        "walking_tolerance": "low|normal|high|unknown",
                    },
                    "hotel": {
                        "preferred_features": ["quiet", "good_environment", "near_subway", "luxury", "spacious", "breakfast"],
                        "preferred_area": "用户偏好的住宿区域 or null",
                        "hotel_type": "luxury|high_end|comfort|economy|any —— 根据预算和舒适度要求推断，高预算+舒适→luxury/high_end",
                    },
                    "general": {
                        "budget_level": "high|normal|low|unknown",
                        "travel_style": "comfort|balanced|budget|unknown",
                        "special_needs": [],
                    },
                },
            },
            "workflow_dag": [
                {
                    "node_id": "agent_domain_name",
                    "agent": "actual_agent_name",
                    "depends_on": ["upstream_node_id"]
                }
            ]
        },
    }
    return "\n".join([
        "你是 Coordinator 的任务解析器，只把用户自然语言解析成 travel_task JSON 和 workflow_dag。",
        "请参考 payload 中的 available_agents_from_registry 字段，那里列出了当前存活可用的 Agent 及其 capabilities（能力）。",
        "你需要根据用户的实际提问和当前存活的 Agent，动态规划需要调用的 agent 及其依赖关系。",
        "",
        "【推理链规则】请根据用户描述推断合理的约束值，而非机械填枚举：",
        "  - '预算不限、舒适为主' → budget_level=high, travel_style=comfort,",
        "    hotel_type=luxury 或 high_end, transport_preference=taxi（舒适优先应少走路多打车）,",
        "    pace=relaxed, walking_tolerance=low",
        "  - '最顶级的服务、奢华' → budget_level=high, travel_style=comfort,",
        "    hotel_type=luxury, transport_preference=taxi, pace=relaxed,",
        "    walking_tolerance=low（全程打车、专车接送、不挤公交地铁）",
        "  - '打车为主、不想挤地铁' → transport_preference=taxi, walking_tolerance=low",
        "  - '优先地铁、公交为主' → transport_preference=public_transport, walking_tolerance=high",
        "  - '穷游、省钱' → budget_level=low, travel_style=budget,",
        "    hotel_type=economy, transport_preference=cheapest 或 public_transport",
        "  - '性价比、适中' → budget_level=normal, travel_style=balanced,",
        "    hotel_type=comfort, transport_preference=normal",
        "",
        "【raw_constraints 字段】这是最重要的字段。用自然语言总结用户的核心诉求，",
        "不要压缩成枚举值。下游 Agent 的 LLM 会读取这个字段来做领域内决策。",
        "写清楚：预算态度、舒适度要求、节奏偏好、交通偏好、住宿档次、特殊需求。",
        "目的地必须来自用户问题本身；如果用户说的是省份、区域或非典型城市，也保留用户原词，绝对不要默认成北京。",
        "",
        "【DAG 规则】例如：景点 Agent depends_on 天气 Agent；",
        "交通 Agent depends_on 酒店 Agent + 景点 Agent；",
        "行李 Agent depends_on 天气 Agent。",
        "Workflow rule: 一般旅行规划请求应启用所有在线的旅行 Agent。",
        "",
        "Date rule: 使用 payload.current_date 作为今天。把 明天/后天/下周/下下周/下周二",
        "等相对日期换算为 ISO YYYY-MM-DD。绝对不要对相对日期输出 unspecified 或 null。",
        "如果日期是模糊表达（如 X月初、暑假、国庆附近），请在 start_date 给出最合理的 ISO 近似日期，date_text 保留用户原文。",
        "日期不确定时也必须继续解析 origin_city、destination_city、days、约束和 workflow_dag。",
        "Weather MCP 只接受 ISO 日期，不要把 下周、中秋节 等词传入 MCP 字段。",
        "",
        "不要输出 Markdown；只输出 JSON。",
        json.dumps(payload, ensure_ascii=False, default=str),
    ])


def _default_travel_dag(question: str, available_agents: dict[str, Any]) -> list[dict[str, Any]]:
    # 生成默认旅行DAG工作流
    excluded = _explicitly_excluded_agents(question)
    candidates = [
        {"node_id": "weather_agent", "agent": "weather_agent", "depends_on": []},
        {"node_id": "packing_agent", "agent": "packing_agent", "depends_on": ["weather_agent"]},
        {"node_id": "attraction_agent", "agent": "attraction_agent", "depends_on": ["weather_agent"]},
        {"node_id": "hotel_agent", "agent": "hotel_agent", "depends_on": ["weather_agent", "attraction_agent"]},
        {"node_id": "traffic_agent", "agent": "traffic_agent", "depends_on": ["weather_agent", "attraction_agent", "hotel_agent"]},
    ]
    result: list[dict[str, Any]] = []
    included_node_ids: set[str] = set()
    for node in candidates:
        agent = node["agent"]
        if agent in excluded:
            continue
        if available_agents and agent not in available_agents:
            continue
        deps = [dep for dep in node.get("depends_on", []) if dep in included_node_ids]
        result.append({"node_id": node["node_id"], "agent": agent, "depends_on": deps})
        included_node_ids.add(node["node_id"])
    return result


def _explicitly_excluded_agents(question: str) -> set[str]:
    # 识别用户明确排除的agent
    text = str(question or "")
    excluded: set[str] = set()
    if any(marker in text for marker in ["不考虑酒店", "不需要酒店", "不订酒店", "不用酒店", "不考虑住宿", "不需要住宿", "不用住宿", "不安排住宿"]):
        excluded.add("hotel_agent")
    if any(marker in text for marker in ["不考虑交通", "不需要交通", "不用交通", "不安排交通", "不用规划交通", "不查路线", "不考虑路线"]):
        excluded.add("traffic_agent")
    return excluded


def _llm_missed_required_iso_date(question: str, travel_task: dict[str, Any]) -> bool:
    # 检查LLM是否遗漏ISO日期转换
    days = _safe_task_days(travel_task.get("days"))
    if not _parse_date_constraint(question, days=days):
        return False
    return not _is_iso_date(travel_task.get("start_date"))


def _is_iso_date(value: Any) -> bool:
    # 判断值是否为ISO格式日期
    return is_internal_iso_date(value)


def _ensure_travel_workflow_dag(
    workflow_dag: list[Any],
    default_dag: list[dict[str, Any]],
    available_agents: dict[str, Any],
) -> list[dict[str, Any]]:
    # 规范化LLM生成的DAG工作流
    normalized: list[dict[str, Any]] = []
    seen_agents: set[str] = set()
    for node in workflow_dag:
        if not isinstance(node, dict):
            continue
        agent = str(node.get("agent") or node.get("node_id") or "").strip()
        if not agent or agent in seen_agents:
            continue
        if available_agents and agent not in available_agents:
            continue
        node_id = str(node.get("node_id") or agent).strip()
        depends_on = node.get("depends_on")
        normalized.append(
            {
                "node_id": node_id,
                "agent": agent,
                "depends_on": [str(item) for item in depends_on] if isinstance(depends_on, list) else [],
            }
        )
        seen_agents.add(agent)

    known_node_ids = {node["node_id"] for node in normalized}
    for default_node in default_dag:
        agent = default_node["agent"]
        if agent in seen_agents:
            continue
        if available_agents and agent not in available_agents:
            continue
        deps = [dep for dep in default_node.get("depends_on", []) if dep in known_node_ids]
        normalized.append({"node_id": default_node["node_id"], "agent": agent, "depends_on": deps})
        known_node_ids.add(default_node["node_id"])
        seen_agents.add(agent)
    return normalized or list(default_dag)


def _normalize_travel_task(
    value: Any,
    fallback: dict[str, Any],
    *,
    parser: str,
    apply_rule_date_fallback: bool = True,
) -> dict[str, Any]:
    '''
    对llm输出的task做规范化
    返回规范化后的task
    '''
    if not isinstance(value, dict):
        result = dict(fallback)
        result["_parser"] = "rule_fallback_invalid_llm"
        _resolve_task_dates(result, str(fallback.get("_raw_question") or ""))
        return result
    result = dict(fallback)
    for key in ["origin_city", "destination_city", "budget_level", "transport_preference", "raw_constraints"]:
        if isinstance(value.get(key), str) and value[key].strip() and value[key] != "null":
            result[key] = value[key].strip()
    result["budget_level"] = normalize_budget_level(result.get("budget_level"))
    result["transport_preference"] = normalize_transport_preference(result.get("transport_preference"))
    if _is_valid_date_label(value.get("start_date")):
        result["start_date"] = str(value["start_date"]).strip()
    if _is_valid_date_label(value.get("end_date")):
        result["end_date"] = str(value["end_date"]).strip()
    if isinstance(value.get("date_text"), str) and value["date_text"].strip():
        result["date_text"] = value["date_text"].strip()
    if isinstance(value.get("days"), int) and 1 <= value["days"] <= 30:
        result["days"] = value["days"]
    elif isinstance(value.get("days"), str) and value["days"].isdigit():
        result["days"] = int(value["days"])
    for key in ["must_visit", "preferences", "avoid"]:
        if isinstance(value.get(key), list):
            result[key] = [str(item).strip() for item in value[key] if str(item).strip()]
    # 新增
    result["constraints"] = _normalize_task_constraints(value.get("constraints"), result)
    _sync_legacy_fields_from_constraints(result)
    if apply_rule_date_fallback:
        _resolve_task_dates(result, str(fallback.get("_raw_question") or ""))
    result.setdefault("avoid", [])
    result["_parser"] = parser
    # 关键词覆盖：用户明确说了"打车"等，覆盖 LLM 解析
    raw_q = str(fallback.get("_raw_question") or "")
    if raw_q:
        origin_from_text = _extract_origin_city(raw_q)
        destination_from_text = _extract_destination_city(raw_q, origin_from_text)
        if origin_from_text:
            result["origin_city"] = origin_from_text
        if not is_unknown(destination_from_text):
            result["destination_city"] = destination_from_text
        if any(kw in raw_q for kw in ["打车", "出租车", "专车", "taxi"]):
            result["transport_preference"] = "taxi"
        elif any(kw in raw_q for kw in ["地铁", "公交", "公共交通"]):
            result["transport_preference"] = "public_transport"
    return result


def _extract_travel_task_by_rules(question: str) -> dict[str, Any]:
    '''
    作为fallback: 输入问题，根据规则返回task
    '''
    origin_city = _extract_origin_city(question)
    destination_city = _extract_destination_city(question, origin_city)
    days = _extract_days(question)
    budget_level = _infer_budget_level(question)
    transport_preference = (
        "taxi"
        if any(word in question for word in ["打车", "出租车", "专车"])
        else (
            "public_transport"
            if any(word in question for word in ["公共交通", "地铁", "公交", "少打车", "不打车"])
            else "normal"
        )
    )
    must_visit = _extract_must_visit(question)
    date_info = _parse_date_constraint(question, days=days)
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
        "start_date": date_info.get("start_date") or "未指定",
        "end_date": date_info.get("end_date"),
        "date_text": date_info.get("date_text"),
        "date_source": date_info.get("date_source"),
        "date_confidence": date_info.get("date_confidence"),
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
        "_raw_question": question,
    }


def _infer_budget_level(question: str, default: str = "normal") -> str:
    # 从问题中推断预算等级
    text = str(question or "")
    low_markers = ["\u4f4e\u9884\u7b97", "\u7701\u94b1", "\u4fbf\u5b9c", "\u7a77\u6e38"]
    high_markers = [
        "\u9884\u7b97\u5145\u8db3",
        "\u9884\u7b97\u9ad8",
        "\u9ad8\u9884\u7b97",
        "\u8212\u9002",
        "\u8212\u670d",
        "\u54c1\u8d28",
        "\u9ad8\u7aef",
        "\u5962\u534e",
    ]
    if any(marker in text for marker in low_markers):
        return "low"
    if any(marker in text for marker in high_markers):
        return "high"
    return default


def _infer_travel_style(question: str, budget_level: str) -> str:
    # 从问题中推断旅行风格
    if budget_level == "low":
        return "budget"
    if budget_level == "high" or any(
        marker in str(question or "")
        for marker in ["\u8212\u9002", "\u8212\u670d", "\u54c1\u8d28", "\u9ad8\u7aef", "\u5962\u534e"]
    ):
        return "comfort"
    return "balanced"


def _is_valid_date_label(value: Any) -> bool:
    # 判断日期标签是否有效
    return not is_unknown(value)


def _resolve_task_dates(task: dict[str, Any], question: str) -> None:
    # 解析并填充任务日期
    days = _safe_task_days(task.get("days"))
    date_info = _parse_date_constraint(question, days=days)
    if not date_info:
        date_info = _parse_date_constraint(
            " ".join(
                part
                for part in [
                    str(task.get("date_text") or ""),
                    str(task.get("start_date") or ""),
                ]
                if part
            ),
            days=days,
        )
    if date_info.get("start_date"):
        task["start_date"] = date_info["start_date"]
        task["end_date"] = date_info["end_date"]
        task["date_text"] = date_info["date_text"]
        task["date_source"] = date_info["date_source"]
        task["date_confidence"] = date_info["date_confidence"]
        return
    if not _is_valid_date_label(task.get("start_date")):
        task["start_date"] = "未指定"
        task.pop("end_date", None)
        task["date_source"] = "unresolved"
        task["date_confidence"] = 0.0


def _parse_date_constraint(text: str, *, days: int) -> dict[str, Any]:
    # 从文本中解析日期约束
    source = str(text or "")
    today = date_type.today()
    start: date_type | None = None
    date_text = ""
    date_source = "unresolved"
    confidence = 0.0

    iso_match = re.search(r"\b(20\d{2})[-/.](\d{1,2})[-/.](\d{1,2})\b", source)
    if iso_match:
        year, month, day = (int(part) for part in iso_match.groups())
        try:
            start = date_type(year, month, day)
            date_text = iso_match.group(0)
            date_source = "absolute_date_rule"
            confidence = 0.95
        except ValueError:
            start = None

    if start is None:
        relative_rules = [
            ("下下周末", _week_start(today, 2) + timedelta(days=5), "relative_weekend_rule", 0.85),
            ("下周末", _week_start(today, 1) + timedelta(days=5), "relative_weekend_rule", 0.85),
            ("下下周", _week_start(today, 2), "relative_week_rule", 0.8),
            ("下周", _week_start(today, 1), "relative_week_rule", 0.8),
            ("后天", today + timedelta(days=2), "relative_day_rule", 0.95),
            ("明天", today + timedelta(days=1), "relative_day_rule", 0.95),
            ("今天", today, "relative_day_rule", 0.95),
        ]
        for phrase, candidate, source_name, score in relative_rules:
            if phrase in source:
                start = candidate
                date_text = phrase
                date_source = source_name
                confidence = score
                break

    if start is None and "中秋" in source:
        festival = _mid_autumn_date(today.year)
        if festival < today:
            festival = _mid_autumn_date(today.year + 1)
        start = festival
        date_text = "中秋节假期"
        date_source = "holiday_rule"
        confidence = 0.75

    if start is None:
        return {}

    day_count = max(1, days)
    end = start + timedelta(days=day_count - 1)
    return {
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "date_text": date_text,
        "date_source": date_source,
        "date_confidence": confidence,
    }


def _week_start(today: date_type, weeks_ahead: int) -> date_type:
    # 计算指定周数后的周一日期
    monday = today - timedelta(days=today.weekday())
    return monday + timedelta(days=7 * weeks_ahead)


def _mid_autumn_date(year: int) -> date_type:
    # 获取指定年份的中秋节日期
    known = {
        2026: date_type(2026, 9, 25),
        2027: date_type(2027, 9, 15),
        2028: date_type(2028, 10, 3),
    }
    return known.get(year, date_type(year, 9, 15))


def _safe_task_days(value: Any) -> int:
    # 安全获取并限制天数范围
    try:
        days = int(value)
    except (TypeError, ValueError):
        return 3
    return min(max(days, 1), 30)


def _normalize_task_constraints(value: Any, flat_task: dict[str, Any]) -> dict[str, Any]:
    '''
    对llm输出的task.constraints做规范化
    '''
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
    '''
    fallback的task constraints
    '''
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
    travel_style = _infer_travel_style(question, budget_level)
    if travel_style == "comfort":
        hotel_features.extend(["环境好", "舒适"])

    return {
        "attractions": {
            "must_visit": list(dict.fromkeys(must_visit)),
            "preferred_types": list(dict.fromkeys(preferred_types)),
            "avoid": avoid,
            "pace": "relaxed" if travel_style == "comfort" else "normal",
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
            "travel_style": travel_style,
            "special_needs": [],
        },
    }


def _sync_legacy_fields_from_constraints(task: dict[str, Any]) -> None:
    '''
    从constraints同步一些老版本的字段，保持兼容性
    '''
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
        task["budget_level"] = normalize_budget_level(general["budget_level"])
        general["budget_level"] = task["budget_level"]
    if isinstance(traffic.get("preference"), str) and traffic.get("preference"):
        task["transport_preference"] = normalize_transport_preference(traffic["preference"])
        traffic["preference"] = task["transport_preference"]


def _as_clean_list(value: Any) -> list[str]:
    # 清理并规范化列表元素
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


KNOWN_TRAVEL_LOCATIONS = [
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


def _clean_extracted_location(value: str) -> str:
    # 清理“去云南玩3天”这类短语里跟在地点后的动作、时长和约束词。
    text = str(value or "").strip(" \t\r\n，,。.!！？?；;、")
    text = re.sub(r"(?:\d+\s*天|[一二两三四五六七八九十]+天).*$", "", text)
    for marker in ["玩", "游玩", "旅游", "旅行", "自由行", "出差", "住宿", "住", "待", "逛", "看", "要求", "并且", "同时", "尽量", "必须"]:
        index = text.find(marker)
        if index > 0:
            text = text[:index]
    return text.strip(" \t\r\n，,。.!！？?；;、")


def _extract_origin_city(text: str) -> str | None:
    # 兜底解析出发地。主路径由 LLM 完成。
    match = re.search(r"从([\u4e00-\u9fa5]{2,12}?)(?:去|到|出发|$|[，,。；;\s])", text)
    if match:
        return match.group(1).strip()

    return None


def _extract_destination_city(text: str, origin_city: str | None = None) -> str:
    # 兜底解析目的地。主路径由 LLM 完成，这里只避免 LLM 不可用时默认到错误城市。
    generic_match = re.search(r"(?:去|到)([\u4e00-\u9fa5]{2,12}?)(?:玩|旅游|旅行|看看|看|住|$|[，,。；;\s])", text)
    if generic_match:
        destination = _clean_place_name(generic_match.group(1))
        if destination != origin_city:
            return destination
    return "未指定"


def _clean_place_name(value: str) -> str:
    text = value.strip()
    text = re.sub(r"(?:的)?(?:\d+|[一二两三四五六七八九十])天.*$", "", text)
    text = re.sub(r"(?:的)?(?:低预算|高预算|穷游|省钱|经济|舒适|豪华).*$", "", text)
    text = re.sub(r"(?:的)?(?:旅行|旅游|行程|计划).*$", "", text)
    text = re.sub(r"\u7684$", "", text)
    return text.strip()




def _extract_days(text: str) -> int:
    # 从文本中提取旅行天数
    cn_digits = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7}
    match = re.search(r"(\d+)\s*天", text)
    if match:
        return int(match.group(1))
    for key, value in cn_digits.items():
        if f"{key}天" in text:
            return value
    return 3


def _extract_must_visit(text: str) -> list[str]:
    # 从文本中提取必去景点
    known_spots = ["天安门广场", "天安门", "故宫", "国家博物馆", "天坛", "颐和园", "圆明园", "什刹海", "南锣鼓巷"]
    return [spot for spot in known_spots if spot in text]





def build_collaboration_contracts(state: CoordinatorState) -> dict[str, Any]:
    # 构建协作接口文档
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
                    "targets": ["<dynamic_targets>"],
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
            "dependency_order": "<dynamic_dag>",
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
    status = snapshot.get("status")
    grounded_answer = _grounded_final_answer(question, snapshot)
    if status == TASK_PARTIAL:
        failure_note = _format_partial_failure_note(snapshot)
        if failure_note:
            return _clean_final_answer_text(
                f"{grounded_answer}\n\n当前缺失信息\n{failure_note}\n\n"
                "说明：以上方案只基于已成功返回的数据生成，缺失模块恢复后可重新提交以补全。"
            )
        return grounded_answer
    if status != TASK_COMPLETED:
        return _fallback_final_answer(question, snapshot, "")
    if _demo_fast_mode_enabled():
        return grounded_answer

    results = snapshot.get("results", {}) or {}
    dispatch_errors = snapshot.get("dispatch_errors", {}) or {}
    pending_targets = snapshot.get("pending_targets", []) or []
    all_success = (
        not dispatch_errors
        and not pending_targets
        and all(
            isinstance(results.get(agent), dict) and results[agent].get("status") == RESULT_SUCCESS
            for agent in snapshot.get("targets", [])
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
            for agent in snapshot.get("targets", [])
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
    budget_text = display_budget_level(travel_task.get("budget_level"))
    transport_text = display_transport_preference(travel_task.get("transport_preference"))

    lines: list[str] = []
    if travel_task.get("origin_city"):
        lines.append(f"下面是从{origin}到{dest}的{days}天{budget_text}旅行方案，整体按{transport_text}安排。")
    else:
        lines.append(f"下面是{dest}{days}天{budget_text}旅行方案，整体按{transport_text}安排。")

    weather_constraints = weather.get("weather_constraints", {}) or {}
    packing_list = facts.get("packing_list", [])
    
    if weather_constraints or packing_list:
        lines.append("\n一、天气与出行约束")
        if weather_constraints:
            raw_condition = weather_constraints.get("raw_condition") or ""
            schedule = weather_constraints.get("schedule_advice") or "适合正常出行"
            clothing = weather_constraints.get("clothing_advice")
            date = weather_constraints.get("date") or ""
            weather_line = f"- {dest}{date}天气{raw_condition}，{schedule}。"
            if clothing:
                weather_line += f"{_format_advice_text(clothing)}。"
            weather_by_day = weather_constraints.get("weather_by_day")
            if not (isinstance(weather_by_day, list) and weather_by_day):
                lines.append(weather_line)
            weather_by_day = weather_constraints.get("weather_by_day")
            if isinstance(weather_by_day, list) and weather_by_day:
                lines.extend(_format_weather_constraint_lines(dest, weather_constraints))
        
        if packing_list:
            lines.append("- 行李准备建议：")
            for item in packing_list:
                cat = item.get("category", "")
                items = "、".join(item.get("items", []))
                reason = item.get("reason", "")
                lines.append(f"  * {cat}：{items}（{reason}）")

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
                area_text = str(day.get("area"))
                # 如果区域名是区级（以"区"结尾）且不含城市名，前缀城市名
                if area_text.endswith("区") and dest not in area_text:
                    area_text = f"{dest}{area_text}"
                line += f"区域：{area_text}。"
            if day.get("estimated_ticket_cost"):
                line += f"预计门票：{day.get('estimated_ticket_cost')}。"
            reservations = day.get("reservation_required") or []
            if reservations:
                line += f"需提前预约：{'、'.join(str(x) for x in reservations)}。"
            lines.append(line)
        if ticket_total:
            lines.append(f"景点门票小计：{_format_money_range(ticket_total)}。")

    if isinstance(hotel_plan, dict) and hotel_plan:
        lines.append("\n三、住宿建议")
        selected = hotel_plan.get("selected_hotel", {}) if isinstance(hotel_plan.get("selected_hotel"), dict) else {}
        area = hotel_plan.get('recommended_area')
        if area and area != '待确认':
            lines.append(f"- 建议住宿区域：{area}。")
        if selected:
            name = selected.get('name', '待定')
            typ = selected.get('type', '经济型住宿')
            subway = selected.get('nearest_subway')
            price = selected.get('price_per_night')
            parts = [f"- 推荐酒店：{name}", f"类型：{typ}"]
            if subway and subway != '待确认':
                parts.append(f"最近地铁：{subway}")
            if price and price != '待确认':
                parts.append(f"参考价格：{price}元/晚")
            lines.append("，".join(parts) + "。")
        if hotel_total:
            lines.append(f"住宿费用小计：{_format_money_range(hotel_total)}。")

    if isinstance(intercity_transport, dict) and intercity_transport:
        lines.append("\n四、城市间交通方案")
        option = intercity_transport.get("recommended_option", {})
        alternatives = intercity_transport.get("alternatives", [])
        if isinstance(option, dict):
            one_way = intercity_transport.get("one_way_cost") or _format_money_range(_parse_cost_range_list(option.get("cost_yuan_range")))
            mode = option.get('mode', '')
            dur = option.get('duration', '')
            detail = "，".join(p for p in [mode, dur, f"单程{one_way}"] if p)
            lines.append(f"- 推荐：{intercity_transport.get('origin_city', origin)} -> {intercity_transport.get('destination_city', dest)}，{detail}。")
            if intercity_total:
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
                )
        if isinstance(traffic_summary, dict) and traffic_summary.get('total_estimated_local_transport_cost'):
            lines.append(
                f"- 预计市内交通费用：{traffic_summary.get('total_estimated_local_transport_cost')}。"
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
    # 提取门票费用总计
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
    # 提取住宿费用总计
    if not isinstance(hotel_plan, dict):
        return None
    return _parse_money_range(hotel_plan.get("estimated_total_hotel_cost"))


def _extract_intercity_total(intercity_transport: dict[str, Any]) -> tuple[float, float] | None:
    # 提取城际交通费用总计
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
    # 提取市内交通费用总计
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
    # 解析费用范围列表
    if (
        isinstance(value, list)
        and len(value) >= 2
        and isinstance(value[0], (int, float))
        and isinstance(value[1], (int, float))
    ):
        return float(value[0]), float(value[1])
    return None


def _parse_money_range(value: Any) -> tuple[float, float] | None:
    # 解析费用金额范围
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
    # 汇总多个费用范围
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
    # 格式化费用显示文本
    if not value:
        return ""
    low, high = value
    low_text = _format_amount(low)
    high_text = _format_amount(high)
    if low_text == high_text:
        return f"约{low_text}元"
    return f"约{low_text}-{high_text}元"


def _format_amount(value: float) -> str:
    # 格式化金额数值
    return str(int(value)) if float(value).is_integer() else f"{value:.1f}".rstrip("0").rstrip(".")


def _format_advice_text(value: Any) -> str:
    # 格式化建议文本
    text = str(value).strip()
    if text.startswith("建议"):
        return text
    return f"建议{text}"


def _format_weather_constraint_lines(dest: str, weather_constraints: dict[str, Any]) -> list[str]:
    # 格式化天气约束显示行
    result: list[str] = []
    weather_by_day = weather_constraints.get("weather_by_day")
    if not isinstance(weather_by_day, list):
        return result
    if weather_constraints.get("forecast_unavailable"):
        result.append(f"- 天气提示：出行日期距离现在太远（{len(weather_by_day)}天），"
                      "天气 API 无法准确预测，请临近出行前再确认。")
        return result
    # 只展示实际有预报内容的天数，跳过纯 recheck 标记
    real_forecasts = [item for item in weather_by_day if isinstance(item, dict) and not item.get("needs_weather_recheck")]
    recheck_count = len(weather_by_day) - len(real_forecasts)
    for item in real_forecasts:
        day = item.get("day") or "day?"
        date = item.get("date") or ""
        condition = item.get("condition") or "天气待确认"
        temp_parts = [str(item.get(key)) for key in ("temp_min", "temp_max") if item.get(key)]
        temp_text = f"，气温{'-'.join(temp_parts)}" if temp_parts else ""
        advice = "适合户外游玩" if item.get("outdoor_suitable") else "建议减少长时间户外安排"
        result.append(f"- {dest}{day}{f'({date})' if date else ''}：{condition}{temp_text}，{advice}。")
    if recheck_count > 0:
        result.append(f"- 其余{recheck_count}天天气待确认，请临近出行前复查。")
    return result


def _format_transport_mode(value: Any) -> str:
    # 格式化交通方式显示名称
    mode_map = {
        "walk": "步行",
        "bus": "公交",
        "subway": "地铁",
        "taxi": "打车",
    }
    text = str(value).strip()
    return mode_map.get(text, text)


def _format_intercity_alternatives(alternatives: Any) -> str:
    # 格式化备选交通方案
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
    # 清理最终答案文本
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
    # 启动服务
    state = CoordinatorState(host=host, port=port, tcp_host=tcp_host, tcp_port=tcp_port)
    tcp_server = CoordinatorA2ATCPServer((tcp_host, tcp_port), CoordinatorA2ATCPRequestHandler, state)
    tcp_thread = threading.Thread(target=tcp_server.serve_forever, name="coordinator-a2a-tcp", daemon=True)
    tcp_thread.start()

    server = CoordinatorHTTPServer((host, port), CoordinatorRequestHandler, state)
    logger.info(f"Coordinator user API listening on http://{host}:{port}")
    logger.info(f"Coordinator A2A TCP listening on {tcp_url(tcp_host, tcp_port)}")
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
    # 命令行入口
    parser = argparse.ArgumentParser(description="Run the local A2A coordinator.")
    parser.add_argument("--host", default=COORDINATOR_HOST)
    parser.add_argument("--port", type=int, default=COORDINATOR_PORT)
    parser.add_argument("--tcp-host", default=COORDINATOR_A2A_TCP_HOST)
    parser.add_argument("--tcp-port", type=int, default=COORDINATOR_A2A_TCP_PORT)
    args = parser.parse_args()
    run(host=args.host, port=args.port, tcp_host=args.tcp_host, tcp_port=args.tcp_port)


def _agent_execute_url(agent: dict[str, Any]) -> str:
    # 构建agent的HTTP执行URL
    return f"http://{agent['host']}:{agent['port']}{agent.get('execute_path', '/execute_task')}"


def _agent_tcp_url(agent: dict[str, Any]) -> str:
    # 构建agent的TCP地址
    return tcp_url(str(agent["host"]), int(agent["port"]))


def _enabled_agents_view() -> dict[str, Any]:
    '''
    向注册中心查询健康的agent，并放回他们的详细信息
    '''
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
    # 获取MCP服务器信息视图
    return {
        name: {
            "name": server["name"],
            "url": f"http://{server['host']}:{server['port']}{server.get('path', '/')}",
            "jsonrpc_method": server["method"],
        }
        for name, server in MCP_SERVERS.items()
    }


def _normalize_timeout(value: Any) -> float:
    # 规范化超时时间
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
    # 计算剩余秒数
    return max(0.1, deadline - time.monotonic())


def _stage_wait_seconds(deadline: float, minimum_seconds: float = 45.0) -> float:
    """Wait long enough for one downstream Agent even if earlier LLM calls were slow.

    The user-facing timeout controls the overall request preference, but in this
    demo each Agent may make a short structured LLM call. If we always use the
    remaining global deadline, a slow Weather/Attraction LLM can leave Hotel only
    a few seconds and cause Traffic to start without hotel_plan. This helper
    keeps dependency order correct for the demo.
    """
    return min(MAX_TASK_TIMEOUT_SECONDS, minimum_seconds, _remaining_seconds(deadline))


def _looks_like_result_payload(value: Any) -> bool:
    # 判断是否为任务结果负载
    return isinstance(value, dict) and {"source", "target", "task_id", "status"}.issubset(value)


def _demo_fast_mode_enabled() -> bool:
    # 判断是否为快速演示模式
    return os.getenv("A2A_DEMO_FAST", "").strip().lower() in {"1", "true", "yes", "on"}


def _coordinator_plan_prompt(question: str, targets: list[str]) -> str:
    # 构建agent选择提示词
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
    travel_task = plan.get("travel_task", {}) or _travel_task_from_agent_results(results)

    weather = results.get("weather_agent", {}) if isinstance(results.get("weather_agent"), dict) else {}
    attraction = results.get("attraction_agent", {}) if isinstance(results.get("attraction_agent"), dict) else {}
    hotel = results.get("hotel_agent", {}) if isinstance(results.get("hotel_agent"), dict) else {}
    traffic = results.get("traffic_agent", {}) if isinstance(results.get("traffic_agent"), dict) else {}
    packing = results.get("packing_agent", {}) if isinstance(results.get("packing_agent"), dict) else {}

    weather_meta = weather.get("metadata", {}) if isinstance(weather, dict) else {}
    weather_constraints = (
        weather_meta.get("weather_constraints")
        or (weather_meta.get("structured_result", {}) or {}).get("weather_constraints") # 走这里
        or {}
    )
    raw_weather = weather_meta.get("mcp_result") or {}

    attraction_meta = attraction.get("metadata", {}) if isinstance(attraction, dict) else {}
    attraction_structured = attraction_meta.get("structured_result", {}) if isinstance(attraction_meta, dict) else {}
    daily_plan = (
        attraction_structured.get("daily_plan")
        or attraction_structured.get("daily_plan_skeleton") # 走这里
        or attraction_meta.get("daily_plan_skeleton")
        or {}
    )
    estimated_cost = attraction_structured.get("estimated_cost") or attraction_meta.get("estimated_cost") or {} # 走第一个
    rejected_spots = attraction_structured.get("rejected_spots") or []

    hotel_meta = hotel.get("metadata", {}) if isinstance(hotel, dict) else {}
    hotel_structured = hotel_meta.get("structured_result", {}) if isinstance(hotel_meta, dict) else {}
    hotel_plan = hotel_structured.get("hotel_plan") or hotel_meta.get("hotel_plan") or {} # 走第一个

    traffic_meta = traffic.get("metadata", {}) if isinstance(traffic, dict) else {}
    traffic_structured = traffic_meta.get("structured_result", {}) if isinstance(traffic_meta, dict) else {}
    traffic_plan = traffic_structured.get("traffic_plan") or traffic_meta.get("traffic_plan") or {}
    traffic_summary = traffic_structured.get("traffic_summary") or traffic_meta.get("traffic_summary") or {}
    intercity_transport = (
        traffic_structured.get("intercity_transport")
        or traffic_meta.get("intercity_transport")
        or {}
    )

    packing_meta = packing.get("metadata", {}) if isinstance(packing, dict) else {}
    packing_structured = packing_meta.get("structured_result", {}) if isinstance(packing_meta, dict) else {}
    packing_list = packing_structured.get("packing_list", [])

    dispatch_errors = snapshot.get("dispatch_errors", {}) or {}
    pending_targets = snapshot.get("pending_targets", []) or []
    all_success = (
        not dispatch_errors
        and not pending_targets
        and all(
            isinstance(results.get(agent), dict) and results[agent].get("status") == RESULT_SUCCESS
            for agent in snapshot.get("targets", [])
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
        "packing_list": packing_list,
        "user_visible_warnings": warnings,
    }


def _travel_task_from_agent_results(results: dict[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for agent_name in ["weather_agent", "attraction_agent", "hotel_agent", "traffic_agent", "packing_agent"]:
        payload = results.get(agent_name)
        if not isinstance(payload, dict):
            continue
        metadata = payload.get("metadata")
        if not isinstance(metadata, dict):
            continue
        travel_task = metadata.get("travel_task")
        if not isinstance(travel_task, dict):
            continue
        for key, value in travel_task.items():
            if value in (None, "", [], {}, "未指定"):
                continue
            merged.setdefault(key, value)
    return merged


def _coordinator_summary_prompt(question: str, snapshot: dict[str, Any]) -> str:
    # 构建最终总结提示词
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
            "如果 packing_list 非空，必须根据其中内容写行李准备清单（可与天气建议合并或单独列出），注意不要啰嗦，挑重点建议即可。",
            "不要编造给定 JSON 之外的班次、价格、天气或景点。",
            "输出中文，结构清楚，包含：天气与行李准备建议、每日景点、住宿建议、交通方案、费用/预约提醒。",
            json.dumps(clean_payload, ensure_ascii=False, default=str),
        ]
    )


def _fallback_final_answer(question: str, snapshot: dict[str, Any], llm_response: str) -> str:
    # 生成降级最终答案
    results = snapshot.get("results", {}) or {}
    dispatch_errors = snapshot.get("dispatch_errors", {}) or {}
    pending_targets = snapshot.get("pending_targets", []) or []
    targets = snapshot.get("targets", []) or []

    successful: list[str] = []
    failed: list[str] = []

    for source in targets:
        payload = results.get(source)
        label = _agent_display_name(source)
        if isinstance(payload, dict) and payload.get("status") == RESULT_SUCCESS:
            successful.append(f"- {label}: {_short_agent_result(source, payload)}")
        elif isinstance(payload, dict):
            failed.append(f"- {label}: {_user_friendly_error(payload.get('error') or '执行失败')}")

    for source, err in dispatch_errors.items():
        failed.append(f"- {_agent_display_name(source)}: {_user_friendly_error(err)}")

    for source in pending_targets:
        failed.append(f"- {_agent_display_name(str(source))}: 未返回结果")

    lines = [
        "任务未完整完成，以下只基于已成功返回的信息给出最低限度回答。",
        f"用户问题：{question}",
        "",
        "已获取信息：",
    ]
    lines.extend(successful or ["- 暂无可用信息"])
    lines.extend(["", "失败或缺失信息："])
    lines.extend(failed or ["- 暂无"])
    lines.extend(["", "最低限度回答：", _minimal_partial_answer(successful, failed)])
    return "\n".join(lines)


def _agent_display_name(source: str) -> str:
    # 获取agent的中文显示名称
    return {
        "weather_agent": "天气",
        "attraction_agent": "景点",
        "hotel_agent": "住宿",
        "traffic_agent": "交通",
        "packing_agent": "行李",
    }.get(source, source)


def _short_agent_result(source: str, payload: dict[str, Any]) -> str:
    # 生成agent结果的简短摘要
    metadata = payload.get("metadata", {}) if isinstance(payload, dict) else {}
    structured = metadata.get("structured_result", {}) if isinstance(metadata, dict) else {}

    if source == "attraction_agent":
        daily_plan = structured.get("daily_plan_skeleton")
        if isinstance(daily_plan, dict) and daily_plan:
            days = []
            for day, plan in list(daily_plan.items())[:3]:
                if isinstance(plan, dict):
                    spots = plan.get("spots")
                    spot_text = "、".join(map(str, spots[:3])) if isinstance(spots, list) else "景点待定"
                    days.append(f"{day}: {spot_text}")
            if days:
                return _short_text("；".join(days))

    if source == "hotel_agent":
        hotel_plan = structured.get("hotel_plan")
        if isinstance(hotel_plan, dict) and hotel_plan:
            area = hotel_plan.get("recommended_area")
            hotel = hotel_plan.get("selected_hotel")
            hotel_name = hotel.get("name") if isinstance(hotel, dict) else None
            return _short_text("，".join(str(item) for item in [area, hotel_name] if item))

    if source == "packing_agent":
        packing_list = structured.get("packing_list")
        if isinstance(packing_list, list) and packing_list:
            return _short_text(_format_packing_summary(packing_list))

    return _short_text(payload.get("result") or "已返回结果")


def _format_partial_failure_note(snapshot: dict[str, Any]) -> str:
    results = snapshot.get("results", {}) or {}
    dispatch_errors = snapshot.get("dispatch_errors", {}) or {}
    pending_targets = snapshot.get("pending_targets", []) or []
    targets = snapshot.get("targets", []) or []

    lines: list[str] = []
    for source in targets:
        payload = results.get(source)
        if isinstance(payload, dict) and payload.get("status") == RESULT_SUCCESS:
            continue
        label = _agent_display_name(str(source))
        if isinstance(payload, dict):
            lines.append(f"- {label}: {_user_friendly_error(payload.get('error') or '执行失败')}")

    for source, err in dispatch_errors.items():
        lines.append(f"- {_agent_display_name(str(source))}: {_user_friendly_error(err)}")

    for source in pending_targets:
        lines.append(f"- {_agent_display_name(str(source))}: 未返回结果")

    return "\n".join(dict.fromkeys(lines))


def _format_packing_summary(packing_list: list[Any], max_items: int = 5) -> str:
    formatted: list[str] = []
    for item in packing_list[:max_items]:
        if isinstance(item, dict):
            category = str(item.get("category") or "").strip()
            raw_items = item.get("items")
            if isinstance(raw_items, list):
                item_text = "、".join(str(value).strip() for value in raw_items if str(value).strip())
            else:
                item_text = str(raw_items or "").strip()
            reason = str(item.get("reason") or "").strip()
            main_text = f"{category}：{item_text}" if category and item_text else category or item_text
            if reason:
                main_text = f"{main_text}（{reason}）" if main_text else reason
            if main_text:
                formatted.append(main_text)
            continue
        text = str(item).strip()
        if text:
            formatted.append(text)
    return "；".join(formatted)


def _minimal_partial_answer(successful: list[str], failed: list[str]) -> str:
    # 生成最低限度部分回答
    if not successful:
        return "当前没有足够信息生成旅行方案，请先恢复关键 Agent 和 MCP 节点后重新提交。"
    if failed:
        return "可以先参考上方已获取的信息做初步判断；缺失模块恢复前，不应给出完整行程、住宿、交通或行李清单。"
    return "已获取的信息可作为临时参考，但该任务未标记为完整完成，建议恢复节点后重新生成正式方案。"


def _user_friendly_error(raw: Any) -> str:
    """把内部错误信息转成用户可读的简短提示"""
    text = str(raw or "").strip()
    if not text:
        return "暂时无法获取数据，请稍后重试"
    # MCP 超时
    if "timed out" in text.lower():
        return "数据服务响应超时，请稍后重试"
    # MCP 上游错误
    if "upstream" in text.lower() or "-32003" in text:
        return "数据服务暂时不可用"
    # 调度超时
    if "callback timed out" in text.lower():
        return "处理超时，该模块未能及时返回结果"
    # TCP 错误
    if "tcp" in text.lower() or "connection" in text.lower():
        return "服务连接异常"
    # 兜底：截短
    if len(text) > 40:
        return text[:40] + "…"
    return text


def _short_error(value: Any, max_chars: int = 90) -> str:
    # 截断错误信息
    return _short_text(value or "未知错误", max_chars=max_chars)


def _short_text(value: Any, max_chars: int = 110) -> str:
    # 截断文本至指定长度
    text = " ".join(str(value).split())
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1] + "…"


if __name__ == "__main__":
    main()
