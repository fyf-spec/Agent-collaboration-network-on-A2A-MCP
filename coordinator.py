"""Coordinator process for the local HTTP-based A2A demo.

Run:
    python coordinator.py

Main endpoints:
    POST /submit_task   {"question": "...", "timeout": 10}
    POST /task_result   Agent callback with the shared A2A result payload
    GET  /health
    GET  /tasks?task_id=<id>
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import time
from typing import Any
from urllib.parse import parse_qs, urlparse

from common.config import (
    AGENTS,
    COORDINATOR_HOST,
    COORDINATOR_NAME,
    COORDINATOR_PORT,
    DEFAULT_TASK_TIMEOUT_SECONDS,
    DISPATCH_HTTP_TIMEOUT_SECONDS,
    MAX_TASK_TIMEOUT_SECONDS,
    TRAVEL_KEYWORDS,
)
from common.http_client import HttpJsonClientError, post_json
from common.logger import log_network_event
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


@dataclass
class TaskRecord:
    task_id: str
    question: str
    targets: list[str]
    created_at: str
    timeout_seconds: float
    status: str = TASK_PENDING
    results: dict[str, dict[str, Any]] = field(default_factory=dict)
    dispatch_errors: dict[str, str] = field(default_factory=dict)
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
            "results": self.results,
            "dispatch_errors": self.dispatch_errors,
            "pending_targets": self.pending_targets(),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class CoordinatorState:
    def __init__(self, *, host: str, port: int) -> None:
        self.host = host
        self.port = port
        self._tasks: dict[str, TaskRecord] = {}
        self._condition = threading.Condition(threading.RLock())

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    @property
    def reply_to(self) -> str:
        return f"{self.base_url}/task_result"

    def create_task(self, question: str, targets: list[str], timeout_seconds: float) -> TaskRecord:
        record = TaskRecord(
            task_id=new_task_id(),
            question=question,
            targets=targets,
            created_at=utc_now_iso(),
            timeout_seconds=timeout_seconds,
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


class CoordinatorHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, server_address: tuple[str, int], handler_class: type[BaseHTTPRequestHandler]) -> None:
        super().__init__(server_address, handler_class)
        self.state = CoordinatorState(host=server_address[0], port=server_address[1])

    def dispatch_to_agent(self, record: TaskRecord, target: str) -> None:
        agent = AGENTS[target]
        url = _agent_execute_url(agent)
        payload = build_task_payload(
            source=COORDINATOR_NAME,
            target=target,
            task_id=record.task_id,
            instruction=record.question,
            reply_to=self.state.reply_to,
            created_at=record.created_at,
            context={
                "selected_by": "rule",
                "agent_capabilities": agent.get("capabilities", []),
            },
        )
        log_network_event(
            event="dispatch_task",
            direction="outbound",
            source=COORDINATOR_NAME,
            target=target,
            method="POST",
            url=url,
            task_id=record.task_id,
            payload=payload,
        )
        try:
            response = post_json(url, payload, timeout=DISPATCH_HTTP_TIMEOUT_SECONDS)
        except HttpJsonClientError as exc:
            message = str(exc)
            self.state.mark_dispatch_error(record.task_id, target, message)
            log_network_event(
                event="dispatch_failed",
                direction="inbound",
                source=target,
                target=COORDINATOR_NAME,
                method="POST",
                url=exc.url,
                task_id=record.task_id,
                elapsed_ms=exc.elapsed_ms,
                error=message,
            )
            return

        log_network_event(
            event="dispatch_response",
            direction="inbound",
            source=target,
            target=COORDINATOR_NAME,
            method="POST",
            url=url,
            task_id=record.task_id,
            status_code=response.status_code,
            elapsed_ms=response.elapsed_ms,
            payload=response.data,
        )
        if not response.ok:
            self.state.mark_dispatch_error(
                record.task_id,
                target,
                f"agent returned HTTP {response.status_code}",
            )
            return
        if _looks_like_result_payload(response.data):
            try:
                self.state.add_result(response.data)
            except (KeyError, PayloadValidationError) as exc:
                self.state.mark_dispatch_error(record.task_id, target, f"invalid immediate result: {exc}")


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
                        "agents": _enabled_agents_view(),
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
            self._handle_submit_task()
            return
        if parsed.path == "/task_result":
            self._handle_task_result()
            return
        self._send_json(HTTPStatus.NOT_FOUND, error_response("not_found", f"unknown path: {parsed.path}"))

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _handle_submit_task(self) -> None:
        try:
            payload = self._read_json()
            question = str(payload.get("question", "")).strip()
            if not question:
                self._send_json(HTTPStatus.BAD_REQUEST, error_response("invalid_request", "question is required"))
                return
            timeout_seconds = _normalize_timeout(payload.get("timeout"))
        except ValueError as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, error_response("invalid_json", str(exc)))
            return

        targets = select_targets(question)
        record = self.server.state.create_task(question, targets, timeout_seconds)
        log_network_event(
            event="submit_task",
            direction="inbound",
            source="user",
            target=COORDINATOR_NAME,
            method="POST",
            url="/submit_task",
            task_id=record.task_id,
            payload=payload,
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

    def _handle_task_result(self) -> None:
        try:
            payload = self._read_json()
            log_network_event(
                event="task_result",
                direction="inbound",
                source=str(payload.get("source", "unknown")),
                target=COORDINATOR_NAME,
                method="POST",
                url="/task_result",
                task_id=str(payload.get("task_id", "")) or None,
                payload=payload,
            )
            record = self.server.state.add_result(payload)
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

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        raw_body = self.rfile.read(length).decode("utf-8") if length else ""
        try:
            payload = json.loads(raw_body or "{}")
        except json.JSONDecodeError as exc:
            raise ValueError(f"request body must be valid JSON: {exc.msg}") from exc
        if not isinstance(payload, dict):
            raise ValueError("request body must be a JSON object")
        return payload

    def _send_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(int(status))
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def select_targets(question: str) -> list[str]:
    lowered = question.lower()
    enabled_agents = [name for name, agent in AGENTS.items() if agent.get("enabled", True)]
    if _contains_any(lowered, TRAVEL_KEYWORDS):
        return enabled_agents

    selected: list[str] = []
    for name in enabled_agents:
        keywords = AGENTS[name].get("keywords", [])
        if _contains_any(lowered, keywords):
            selected.append(name)
    return selected or enabled_agents


def run(host: str = COORDINATOR_HOST, port: int = COORDINATOR_PORT) -> None:
    server = CoordinatorHTTPServer((host, port), CoordinatorRequestHandler)
    print(f"Coordinator listening on http://{host}:{port}", flush=True)
    print("Endpoints: POST /submit_task, POST /task_result, GET /health, GET /tasks", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nCoordinator shutting down.", flush=True)
    finally:
        server.server_close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the local A2A coordinator.")
    parser.add_argument("--host", default=COORDINATOR_HOST)
    parser.add_argument("--port", type=int, default=COORDINATOR_PORT)
    args = parser.parse_args()
    run(host=args.host, port=args.port)


def _agent_execute_url(agent: dict[str, Any]) -> str:
    return f"http://{agent['host']}:{agent['port']}{agent.get('execute_path', '/execute_task')}"


def _enabled_agents_view() -> dict[str, Any]:
    return {
        name: {
            "url": _agent_execute_url(agent),
            "capabilities": agent.get("capabilities", []),
            "enabled": agent.get("enabled", True),
        }
        for name, agent in AGENTS.items()
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


def _looks_like_result_payload(value: Any) -> bool:
    return isinstance(value, dict) and {"source", "target", "task_id", "status"}.issubset(value)


if __name__ == "__main__":
    main()
