"""Length-prefixed JSON over TCP helpers for A2A communication.

Wire format:
    4-byte unsigned big-endian body length + UTF-8 JSON object body.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import socket
import struct
import time
from typing import Any
from uuid import uuid4
from urllib.parse import urlparse


A2A_TCP_VERSION = "1.0"
FRAME_HEADER_BYTES = 4
MAX_FRAME_BYTES = 4 * 1024 * 1024

TYPE_TASK_REQUEST = "TASK_REQUEST"
TYPE_TASK_ACK = "TASK_ACK"
TYPE_TASK_RESULT = "TASK_RESULT"
TYPE_RESULT_ACK = "RESULT_ACK"
TYPE_ERROR = "ERROR"


class TcpA2AError(RuntimeError):
    """Raised when a TCP A2A frame cannot be sent, received, or validated."""


class TcpA2AConnectionClosed(TcpA2AError):
    """Raised when the peer closes the connection before a complete frame."""


@dataclass(frozen=True)
class TcpA2AFrame:
    data: dict[str, Any]
    length: int
    raw_body: str


@dataclass(frozen=True)
class TcpA2AResponse:
    url: str
    data: dict[str, Any]
    sent_length: int
    received_length: int
    raw_body: str
    elapsed_ms: float

    @property
    def ok(self) -> bool:
        return self.data.get("type") != TYPE_ERROR


def tcp_url(host: str, port: int) -> str:
    return f"tcp://{host}:{port}"


def parse_tcp_url(value: str) -> tuple[str, int]:
    parsed = urlparse(value)
    if parsed.scheme != "tcp" or not parsed.hostname or not parsed.port:
        raise TcpA2AError(f"invalid tcp url: {value}")
    return parsed.hostname, int(parsed.port)


def build_envelope(
    *,
    message_type: str,
    source: str,
    target: str,
    task_id: str,
    payload: dict[str, Any],
    trace_id: str | None = None,
    span_id: str | None = None,
    parent_span_id: str | None = None,
    deadline_ms: int | None = None,
) -> dict[str, Any]:
    return {
        "version": A2A_TCP_VERSION,
        "type": message_type,
        "trace_id": trace_id or f"trace-{task_id}",
        "span_id": span_id or f"span-{source}-{uuid4().hex[:8]}",
        "parent_span_id": parent_span_id,
        "source": source,
        "target": target,
        "task_id": task_id,
        "deadline_ms": deadline_ms,
        "payload": payload,
    }


def build_error_envelope(
    *,
    source: str,
    target: str,
    task_id: str,
    error: str,
    trace_id: str | None = None,
    parent_span_id: str | None = None,
) -> dict[str, Any]:
    return build_envelope(
        message_type=TYPE_ERROR,
        source=source,
        target=target,
        task_id=task_id,
        trace_id=trace_id or f"trace-{task_id}",
        parent_span_id=parent_span_id,
        payload={"error": error},
    )


def validate_envelope(frame: dict[str, Any], *, expected_type: str | None = None) -> None:
    required = [
        "version",
        "type",
        "trace_id",
        "span_id",
        "source",
        "target",
        "task_id",
        "payload",
    ]
    missing = [field for field in required if field not in frame]
    if missing:
        raise TcpA2AError(f"missing A2A TCP field(s): {', '.join(missing)}")
    if frame["version"] != A2A_TCP_VERSION:
        raise TcpA2AError(f"unsupported A2A TCP version: {frame['version']}")
    if expected_type is not None and frame["type"] != expected_type:
        raise TcpA2AError(f"expected {expected_type}, got {frame['type']}")
    if not isinstance(frame["payload"], dict):
        raise TcpA2AError("A2A TCP payload must be a JSON object")


def send_frame(sock: socket.socket, payload: dict[str, Any]) -> int:
    body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
    length = len(body)
    if length <= 0:
        raise TcpA2AError("empty TCP A2A frame body")
    if length > MAX_FRAME_BYTES:
        raise TcpA2AError(f"TCP A2A frame too large: {length} bytes")
    sock.sendall(struct.pack("!I", length) + body)
    return length


def recv_frame(sock: socket.socket) -> TcpA2AFrame:
    header = recv_exact(sock, FRAME_HEADER_BYTES)
    length = struct.unpack("!I", header)[0]
    if length <= 0:
        raise TcpA2AError("invalid TCP A2A frame length: 0")
    if length > MAX_FRAME_BYTES:
        raise TcpA2AError(f"TCP A2A frame length exceeds limit: {length}")

    body = recv_exact(sock, length)
    raw_body = body.decode("utf-8")
    try:
        data = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        raise TcpA2AError(f"TCP A2A body must be valid JSON: {exc.msg}") from exc
    if not isinstance(data, dict):
        raise TcpA2AError("TCP A2A body must be a JSON object")
    return TcpA2AFrame(data=data, length=length, raw_body=raw_body)


def recv_exact(sock: socket.socket, size: int) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        try:
            chunk = sock.recv(remaining)
        except socket.timeout as exc:
            raise TcpA2AError(f"timed out while reading {size} bytes") from exc
        if not chunk:
            received = size - remaining
            raise TcpA2AConnectionClosed(
                f"connection closed while reading frame: expected {size} bytes, got {received}"
            )
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def request_frame(
    *,
    host: str,
    port: int,
    payload: dict[str, Any],
    timeout: float,
) -> TcpA2AResponse:
    url = tcp_url(host, port)
    started = time.perf_counter()
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            sock.settimeout(timeout)
            sent_length = send_frame(sock, payload)
            response = recv_frame(sock)
    except OSError as exc:
        elapsed_ms = (time.perf_counter() - started) * 1000
        raise TcpA2AError(f"TCP request failed to {url}: {exc}") from exc
    elapsed_ms = (time.perf_counter() - started) * 1000
    return TcpA2AResponse(
        url=url,
        data=response.data,
        sent_length=sent_length,
        received_length=response.length,
        raw_body=response.raw_body,
        elapsed_ms=elapsed_ms,
    )
