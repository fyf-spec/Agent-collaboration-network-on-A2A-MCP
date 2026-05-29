from __future__ import annotations

import json
from pathlib import Path
import socket
from socketserver import BaseRequestHandler, ThreadingTCPServer
import struct
import sys
import threading
import time
import unittest
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


import agents.base_agent as base_agent_module
import coordinator as coordinator_module
from agents.base_agent import BaseAgent
from common.schemas import (
    RESULT_ERROR,
    RESULT_SUCCESS,
    build_result_payload,
    build_task_payload,
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
    recv_frame,
    request_frame,
    send_frame,
    validate_envelope,
)
from coordinator import (
    CoordinatorA2ATCPRequestHandler,
    CoordinatorA2ATCPServer,
    CoordinatorHTTPServer,
    CoordinatorRequestHandler,
    CoordinatorState,
)
from llm_client import LLMClientError


_ORIGINAL_COORDINATOR_LOG = coordinator_module.log_network_event
_ORIGINAL_AGENT_LOG = base_agent_module.log_network_event


def _noop_log_network_event(**kwargs: Any) -> dict[str, Any]:
    return dict(kwargs)


def setUpModule() -> None:
    coordinator_module.log_network_event = _noop_log_network_event
    base_agent_module.log_network_event = _noop_log_network_event


def tearDownModule() -> None:
    coordinator_module.log_network_event = _ORIGINAL_COORDINATOR_LOG
    base_agent_module.log_network_event = _ORIGINAL_AGENT_LOG


def _pack_frame(payload: dict[str, Any]) -> bytes:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    return struct.pack("!I", len(body)) + body


class TcpFramingTests(unittest.TestCase):
    def test_back_to_back_frames_do_not_merge_or_truncate(self) -> None:
        left, right = socket.socketpair()
        try:
            first = {"version": "1.0", "type": "TEST", "seq": 1, "payload": {"text": "alpha"}}
            second = {"version": "1.0", "type": "TEST", "seq": 2, "payload": {"text": "beta"}}
            left.sendall(_pack_frame(first) + _pack_frame(second))

            received_first = recv_frame(right)
            received_second = recv_frame(right)

            self.assertEqual(first, received_first.data)
            self.assertEqual(second, received_second.data)
        finally:
            left.close()
            right.close()

    def test_fragmented_frame_is_reassembled_by_recv_exact(self) -> None:
        left, right = socket.socketpair()
        try:
            payload = {
                "version": "1.0",
                "type": "TEST",
                "seq": 3,
                "payload": {"text": "fragmented"},
            }
            wire = _pack_frame(payload)

            def sender() -> None:
                for chunk in (wire[:1], wire[1:4], wire[4:8], wire[8:]):
                    left.sendall(chunk)

            thread = threading.Thread(target=sender)
            thread.start()
            received = recv_frame(right)
            thread.join(timeout=1)

            self.assertEqual(payload, received.data)
        finally:
            left.close()
            right.close()

    def test_validate_envelope_rejects_wrong_type_and_missing_payload(self) -> None:
        frame = build_envelope(
            message_type=TYPE_TASK_REQUEST,
            source="coordinator",
            target="weather_agent",
            task_id="task-1",
            payload={"instruction": "query weather"},
        )
        validate_envelope(frame, expected_type=TYPE_TASK_REQUEST)

        with self.assertRaises(TcpA2AError):
            validate_envelope(frame, expected_type=TYPE_TASK_RESULT)

        bad_frame = dict(frame)
        bad_frame.pop("payload")
        with self.assertRaises(TcpA2AError):
            validate_envelope(bad_frame)


class CoordinatorTcpCallbackTests(unittest.TestCase):
    def setUp(self) -> None:
        self.state = CoordinatorState(
            host="127.0.0.1",
            port=9000,
            tcp_host="127.0.0.1",
            tcp_port=0,
        )
        self.server = CoordinatorA2ATCPServer(
            ("127.0.0.1", 0),
            CoordinatorA2ATCPRequestHandler,
            self.state,
        )
        self.state.tcp_port = int(self.server.server_address[1])
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=1)

    def test_valid_task_result_frame_updates_task_and_returns_result_ack(self) -> None:
        record = self.state.create_task(
            question="query weather",
            targets=["weather_agent"],
            timeout_seconds=1.0,
        )
        result_payload = build_result_payload(
            source="weather_agent",
            target="coordinator",
            task_id=record.task_id,
            status=RESULT_SUCCESS,
            result="weather answer",
        )
        frame = build_envelope(
            message_type=TYPE_TASK_RESULT,
            source="weather_agent",
            target="coordinator",
            task_id=record.task_id,
            payload=result_payload,
        )

        response = request_frame(
            host="127.0.0.1",
            port=self.state.tcp_port,
            payload=frame,
            timeout=1.0,
        )

        self.assertEqual(TYPE_RESULT_ACK, response.data["type"])
        snapshot = self.state.get_task(record.task_id).snapshot()  # type: ignore[union-attr]
        self.assertEqual("completed", snapshot["status"])
        self.assertEqual("weather answer", snapshot["results"]["weather_agent"]["result"])

    def test_unexpected_result_source_returns_error_frame(self) -> None:
        record = self.state.create_task(
            question="query weather",
            targets=["weather_agent"],
            timeout_seconds=1.0,
        )
        result_payload = build_result_payload(
            source="traffic_agent",
            target="coordinator",
            task_id=record.task_id,
            status=RESULT_SUCCESS,
            result="wrong source",
        )
        frame = build_envelope(
            message_type=TYPE_TASK_RESULT,
            source="traffic_agent",
            target="coordinator",
            task_id=record.task_id,
            payload=result_payload,
        )

        response = request_frame(
            host="127.0.0.1",
            port=self.state.tcp_port,
            payload=frame,
            timeout=1.0,
        )

        self.assertEqual(TYPE_ERROR, response.data["type"])
        self.assertIn("unexpected result source", response.data["payload"]["error"])
        snapshot = self.state.get_task(record.task_id).snapshot()  # type: ignore[union-attr]
        self.assertEqual({}, snapshot["results"])


class CoordinatorTimeoutTests(unittest.TestCase):
    def test_wait_for_target_marks_callback_timeout_as_dispatch_error(self) -> None:
        state = CoordinatorState(
            host="127.0.0.1",
            port=0,
            tcp_host="127.0.0.1",
            tcp_port=0,
        )
        record = state.create_task(
            question="query weather",
            targets=["weather_agent", "traffic_agent"],
            timeout_seconds=1.0,
        )

        snapshot = state.wait_for_target(record.task_id, "weather_agent", 0.01)

        self.assertIn("weather_agent", snapshot["dispatch_errors"])
        self.assertIn("timed out", snapshot["dispatch_errors"]["weather_agent"])
        self.assertEqual(["traffic_agent"], snapshot["pending_targets"])

    def test_wait_for_task_marks_remaining_targets_on_timeout(self) -> None:
        state = CoordinatorState(
            host="127.0.0.1",
            port=0,
            tcp_host="127.0.0.1",
            tcp_port=0,
        )
        record = state.create_task(
            question="query weather and traffic",
            targets=["weather_agent", "traffic_agent"],
            timeout_seconds=1.0,
        )

        snapshot = state.wait_for_task(record.task_id, 0.01)

        self.assertEqual("failed", snapshot["status"])
        self.assertEqual([], snapshot["pending_targets"])
        self.assertEqual({"weather_agent", "traffic_agent"}, set(snapshot["dispatch_errors"]))

    def test_stage_wait_does_not_extend_beyond_workflow_deadline(self) -> None:
        deadline = time.monotonic() + 0.2

        wait_seconds = coordinator_module._stage_wait_seconds(deadline, minimum_seconds=75.0)

        self.assertLessEqual(wait_seconds, 0.25)


class RateLimitTcpHandler(BaseRequestHandler):
    def handle(self) -> None:
        request = recv_frame(self.request)
        task_id = str(request.data["task_id"])
        response = build_error_envelope(
            source="rate_limited_agent",
            target="coordinator",
            task_id=task_id,
            trace_id=str(request.data["trace_id"]),
            parent_span_id=str(request.data["span_id"]),
            error="rate_limited: too many A2A requests",
        )
        response["payload"]["code"] = "rate_limited"
        response["payload"]["retry_after_ms"] = 250
        send_frame(self.request, response)


class A2ARateLimitTests(unittest.TestCase):
    def test_coordinator_records_tcp_rate_limit_error_without_crashing(self) -> None:
        rate_limit_server = ThreadingTCPServer(("127.0.0.1", 0), RateLimitTcpHandler)
        rate_limit_server.daemon_threads = True
        server_thread = threading.Thread(target=rate_limit_server.serve_forever, daemon=True)
        server_thread.start()

        state = CoordinatorState(
            host="127.0.0.1",
            port=0,
            tcp_host="127.0.0.1",
            tcp_port=0,
        )
        coordinator_server = CoordinatorHTTPServer(
            ("127.0.0.1", 0),
            CoordinatorRequestHandler,
            state,
        )
        record = state.create_task(
            question="trigger rate limit",
            targets=["rate_limited_agent"],
            timeout_seconds=1.0,
        )

        original_discovery = coordinator_module._fetch_discovered_agents
        coordinator_module._fetch_discovered_agents = lambda: {
            "rate_limited_agent": {
                "host": "127.0.0.1",
                "port": int(rate_limit_server.server_address[1]),
                "protocol": "tcp",
                "enabled": True,
                "capabilities": ["test"],
            }
        }
        try:
            coordinator_server.dispatch_to_agent(record, "rate_limited_agent")
        finally:
            coordinator_module._fetch_discovered_agents = original_discovery
            coordinator_server.server_close()
            rate_limit_server.shutdown()
            rate_limit_server.server_close()
            server_thread.join(timeout=1)

        snapshot = state.get_task(record.task_id).snapshot()  # type: ignore[union-attr]
        self.assertEqual("failed", snapshot["status"])
        self.assertIn("rate_limited_agent", snapshot["dispatch_errors"])
        self.assertIn("rate_limited", snapshot["dispatch_errors"]["rate_limited_agent"])

    def test_agent_llm_rate_limit_becomes_successful_fallback_result(self) -> None:
        class DummyAgent(BaseAgent):
            agent_name = "dummy_agent"
            capability = "dummy"
            mcp_server_key = "weather"

            def __init__(self) -> None:
                super().__init__(host="127.0.0.1", port=0)
                self.sent_result: dict[str, Any] | None = None

            def call_mcp_server(self, task_payload: dict[str, Any]) -> dict[str, Any]:
                return {"city": "Guangzhou", "condition": "rain"}

            def build_prompt(self, task_payload: dict[str, Any], mcp_result: dict[str, Any]) -> str:
                return "dummy prompt"

            def build_fallback_answer(
                self,
                task_payload: dict[str, Any],
                mcp_result: dict[str, Any],
                llm_error: str,
            ) -> str:
                return f"fallback after rate limit: {llm_error}"

            def send_result_to_coordinator(
                self,
                task_payload: dict[str, Any],
                result_payload: dict[str, Any],
            ) -> None:
                self.sent_result = result_payload

        agent = DummyAgent()
        task_payload = build_task_payload(
            source="coordinator",
            target="dummy_agent",
            task_id="task-rate-limit",
            instruction="query",
            reply_to="tcp://127.0.0.1:9001",
        )

        original_chat = base_agent_module.llm.chat

        def raise_rate_limit(prompt: str) -> str:
            raise LLMClientError("429 rate limit: retry later")

        base_agent_module.llm.chat = raise_rate_limit
        try:
            agent.process_task(task_payload)
        finally:
            base_agent_module.llm.chat = original_chat

        self.assertIsNotNone(agent.sent_result)
        result = agent.sent_result or {}
        self.assertEqual(RESULT_SUCCESS, result["status"])
        self.assertIn("fallback after rate limit", result["result"])
        self.assertIn("429 rate limit", result["metadata"]["llm_error"])

    def test_agent_execution_failure_returns_standard_error_result(self) -> None:
        class FailingAgent(BaseAgent):
            agent_name = "failing_agent"
            capability = "dummy"
            mcp_server_key = "weather"

            def __init__(self) -> None:
                super().__init__(host="127.0.0.1", port=0)
                self.sent_result: dict[str, Any] | None = None

            def call_mcp_server(self, task_payload: dict[str, Any]) -> dict[str, Any]:
                raise RuntimeError("simulated MCP outage")

            def build_prompt(self, task_payload: dict[str, Any], mcp_result: dict[str, Any]) -> str:
                return "unreachable"

            def send_result_to_coordinator(
                self,
                task_payload: dict[str, Any],
                result_payload: dict[str, Any],
            ) -> None:
                self.sent_result = result_payload

        agent = FailingAgent()
        task_payload = build_task_payload(
            source="coordinator",
            target="failing_agent",
            task_id="task-error-packet",
            instruction="query",
            reply_to="tcp://127.0.0.1:9001",
        )

        agent.process_task(task_payload)

        self.assertIsNotNone(agent.sent_result)
        result = agent.sent_result or {}
        self.assertEqual(RESULT_ERROR, result["status"])
        self.assertIn("simulated MCP outage", result["error"])
        self.assertEqual(500, result["metadata"]["error_report"]["http_status"])
        self.assertEqual("agent_execution_failed", result["metadata"]["error_report"]["code"])


if __name__ == "__main__":
    unittest.main()
