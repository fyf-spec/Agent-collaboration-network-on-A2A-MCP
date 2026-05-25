from __future__ import annotations

import json
import socket
import struct
import threading
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


from common.tcp_a2a import recv_frame, send_frame


def _pack(payload: dict[str, object]) -> bytes:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    return struct.pack("!I", len(body)) + body


def test_back_to_back_frames() -> None:
    left, right = socket.socketpair()
    try:
        first = {"version": "1.0", "type": "TEST", "seq": 1, "payload": {"text": "alpha"}}
        second = {"version": "1.0", "type": "TEST", "seq": 2, "payload": {"text": "beta"}}
        left.sendall(_pack(first) + _pack(second))

        got_first = recv_frame(right)
        got_second = recv_frame(right)

        assert got_first.data == first
        assert got_second.data == second
        assert got_first.length == len(json.dumps(first, ensure_ascii=False).encode("utf-8"))
        assert got_second.length == len(json.dumps(second, ensure_ascii=False).encode("utf-8"))
    finally:
        left.close()
        right.close()


def test_fragmented_frame() -> None:
    left, right = socket.socketpair()
    try:
        payload = {"version": "1.0", "type": "TEST", "seq": 3, "payload": {"text": "fragmented"}}
        wire = _pack(payload)

        def sender() -> None:
            for chunk in (wire[:2], wire[2:4], wire[4:9], wire[9:]):
                left.sendall(chunk)

        thread = threading.Thread(target=sender)
        thread.start()
        received = recv_frame(right)
        thread.join(timeout=1)

        assert received.data == payload
        assert received.length == len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    finally:
        left.close()
        right.close()


def test_send_frame_round_trip() -> None:
    left, right = socket.socketpair()
    try:
        payload = {"version": "1.0", "type": "TEST", "seq": 4, "payload": {"text": "round-trip"}}
        sent_length = send_frame(left, payload)
        received = recv_frame(right)

        assert received.data == payload
        assert received.length == sent_length
    finally:
        left.close()
        right.close()


def main() -> None:
    test_back_to_back_frames()
    test_fragmented_frame()
    test_send_frame_round_trip()
    print("TCP framing tests passed: back-to-back frames, fragmented frame, send/receive round trip.")


if __name__ == "__main__":
    main()
