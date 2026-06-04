import streamlit as st
import streamlit.components.v1 as components
import requests
import time
import subprocess
import os
import signal
import json
from collections import deque
from math import atan2, degrees, hypot
from textwrap import dedent
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from typing import Dict, Any
from urllib.parse import urlparse

st.set_page_config(page_title="A2A 旅行工作流 Agent", page_icon="✈️", layout="wide")

COORDINATOR_URL = "http://127.0.0.1:9000/submit_task"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOG_FILE = PROJECT_ROOT / "logs" / "demo_log.jsonl"
TOPOLOGY_COMPONENT_DIR = PROJECT_ROOT / "scripts" / "topology_component"
topology_component = components.declare_component("topology_control", path=str(TOPOLOGY_COMPONENT_DIR))
PACKET_COMPONENT_DIR = PROJECT_ROOT / "scripts" / "packet_component"
packet_component = components.declare_component("packet_inspector", path=str(PACKET_COMPONENT_DIR))
TOPOLOGY_REFRESH_SECONDS = 0.35
TRANSFER_HISTORY_SECONDS = 30.0
TRANSFER_PULSE_SECONDS = 1.6
GENERATED_CONTENT_STATE_KEYS = [
    "current_task_id",
    "task_start_time",
    "task_transfer_active",
    "task_highlight_cleared_for",
    "last_recent_network_events",
    "selected_packet_event_key",
    "selected_packet_event_index",
    "last_packet_click_nonce",
    "topology_edge_pulses",
    "topology_failed_edge_pulses",
    "topology_node_pulses",
    "topology_seen_event_keys",
]

import sys
import atexit


@st.cache_resource
def get_process_store() -> dict[str, subprocess.Popen[str]]:
    return {}


# --- 状态管理：保存进程和配置 ---
# Streamlit 在退出时会销毁 st.session_state，导致 atexit 钩子报错
# 因此将进程字典提取为真正的全局变量，仅供后台管理使用
if "GLOBAL_PROCESSES" not in st.session_state:
    st.session_state.GLOBAL_PROCESSES = get_process_store()

# 方便后续代码引用全局字典
_processes = st.session_state.GLOBAL_PROCESSES


def clear_generated_content_state() -> None:
    for key in GENERATED_CONTENT_STATE_KEYS:
        st.session_state.pop(key, None)
    st.session_state.generated_content_cleared_at = time.time()

# --- 服务定义与启动逻辑 ---
SERVICES = {
    "registry_center_primary": [sys.executable, "registry_center.py"],
    "registry_center_backup": [sys.executable, "registry_center.py", "--port", "7001"],
    "weather_mcp_server": [sys.executable, "mcp_servers/weather_mcp_server.py"],
    "traffic_mcp_server": [sys.executable, "mcp_servers/traffic_mcp_server.py"],
    "attraction_mcp_server": [sys.executable, "mcp_servers/attraction_mcp_server.py"],
    "hotel_mcp_server": [sys.executable, "mcp_servers/hotel_mcp_server.py"],
    "packing_mcp_server": [sys.executable, "mcp_servers/packing_mcp_server.py"],
    "mcp_gateway": [sys.executable, "mcp_gateway.py"],
    "weather_agent": [sys.executable, "agents/weather_agent.py"],
    "attraction_agent": [sys.executable, "agents/attraction_agent.py"],
    "hotel_agent": [sys.executable, "agents/hotel_agent.py"],
    "traffic_agent": [sys.executable, "agents/traffic_agent.py"],
    "packing_agent": [sys.executable, "agents/packing_agent.py"],
    "coordinator": [sys.executable, "coordinator.py"],
}

SERVICE_PORTS = {
    "registry_center_primary": [7000],
    "registry_center_backup": [7001],
    "weather_mcp_server": [8001],
    "traffic_mcp_server": [8002],
    "attraction_mcp_server": [8003],
    "hotel_mcp_server": [8004],
    "packing_mcp_server": [8005],
    "mcp_gateway": [8100],
    "weather_agent": [9010],
    "attraction_agent": [9030],
    "hotel_agent": [9040],
    "traffic_agent": [9020],
    "packing_agent": [9060],
    "coordinator": [9000, 9001],
}

MCP_SERVICE_LABELS = {
    "weather_mcp_server": "Weather MCP",
    "traffic_mcp_server": "Traffic MCP",
    "attraction_mcp_server": "Attraction MCP",
    "hotel_mcp_server": "Hotel MCP",
    "packing_mcp_server": "Packing MCP",
}

SERVICE_META = {
    "registry_center_primary": {"label": "Primary Registry", "port": "7000", "kind": "registry"},
    "registry_center_backup": {"label": "Backup Registry", "port": "7001", "kind": "registry"},
    "weather_mcp_server": {"label": "Weather MCP", "port": "8001", "kind": "mcp_weather"},
    "traffic_mcp_server": {"label": "Traffic MCP", "port": "8002", "kind": "mcp_traffic"},
    "attraction_mcp_server": {"label": "Attraction MCP", "port": "8003", "kind": "mcp_attraction"},
    "hotel_mcp_server": {"label": "Hotel MCP", "port": "8004", "kind": "mcp_hotel"},
    "packing_mcp_server": {"label": "Packing MCP", "port": "8005", "kind": "mcp_packing"},
    "mcp_gateway": {"label": "MCP Gateway", "port": "8100", "kind": "gateway"},
    "weather_agent": {"label": "Weather", "port": "9010", "kind": "agent_weather"},
    "attraction_agent": {"label": "Attraction", "port": "9030", "kind": "agent_attraction"},
    "hotel_agent": {"label": "Hotel", "port": "9040", "kind": "agent_hotel"},
    "traffic_agent": {"label": "Traffic", "port": "9020", "kind": "agent_traffic"},
    "packing_agent": {"label": "Packing", "port": "9060", "kind": "agent_packing"},
    "coordinator": {"label": "Coordinator", "port": "9000 / 9001", "kind": "coordinator"},
    "user": {"label": "User", "port": "browser", "kind": "user"},
}

TOPOLOGY_LABELS = {
    "registry_center_primary": "Primary Reg.",
    "registry_center_backup": "Backup Reg.",
    "attraction_agent": "Attract",
    "mcp_gateway": "Gateway",
    "weather_mcp_server": "Weather",
    "traffic_mcp_server": "Traffic",
    "attraction_mcp_server": "Attract",
    "hotel_mcp_server": "Hotel",
    "packing_mcp_server": "Packing",
}

def start_service(name: str, env_vars: Dict[str, str], delay: float = 0.0):
    if is_service_running(name):
        return # Already running
    
    cmd = SERVICES[name]
    env = os.environ.copy()
    env.update(env_vars)
    
    # 强制 Python 无缓冲输出，保证日志实时打印到终端
    env["PYTHONUNBUFFERED"] = "1"
    
    # 支持给 MCP Server 添加 delay
    if name.endswith("_mcp_server") and delay > 0.0:
        cmd = cmd + ["--delay", str(delay)]
        
    proc = subprocess.Popen(
        cmd,
        env=env,
        stdout=sys.stdout,
        stderr=sys.stderr
    )
    _processes[name] = proc

def stop_service(name: str, *, include_port_processes: bool = True):
    proc = _processes.get(name)
    if proc is not None and proc.poll() is None:
        if os.name == 'nt':
            proc.terminate()
        else:
            proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
    _processes.pop(name, None)

    if include_port_processes:
        for pid in _listening_pids_for_service(name):
            if pid == os.getpid():
                continue
            _terminate_pid(pid)

def stop_all_services(*, include_port_processes: bool = True):
    service_names = SERVICES.keys() if include_port_processes else list(_processes.keys())
    for name in list(service_names):
        stop_service(name, include_port_processes=include_port_processes)

def is_service_running(name: str) -> bool:
    proc = _processes.get(name)
    if proc is not None and proc.poll() is None:
        return True
    if proc is not None and proc.poll() is not None:
        _processes.pop(name, None)
    return bool(_listening_pids_for_service(name))


def get_service_states() -> dict[str, bool]:
    all_ports = {port for ports in SERVICE_PORTS.values() for port in ports}
    listening_by_port = _listening_port_pids_for_ports_windows(all_ports) if os.name == "nt" else {}
    states: dict[str, bool] = {}
    for name in SERVICES:
        proc = _processes.get(name)
        if proc is not None and proc.poll() is None:
            states[name] = True
            continue
        if proc is not None and proc.poll() is not None:
            _processes.pop(name, None)
        states[name] = any(port in listening_by_port for port in SERVICE_PORTS.get(name, []))
    return states


def _listening_pids_for_service(name: str) -> set[int]:
    ports = set(SERVICE_PORTS.get(name, []))
    if not ports:
        return set()
    pids: set[int] = set()
    if os.name == "nt":
        return _listening_pids_for_ports_windows(ports)
    return pids


def _listening_pids_for_ports_windows(ports: set[int]) -> set[int]:
    return {pid for pids in _listening_port_pids_for_ports_windows(ports).values() for pid in pids}


def _listening_port_pids_for_ports_windows(ports: set[int]) -> dict[int, set[int]]:
    pids_by_port: dict[int, set[int]] = {}
    try:
        result = subprocess.run(
            ["netstat", "-ano", "-p", "tcp"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return pids_by_port
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) < 5 or parts[0].upper() != "TCP" or parts[3].upper() != "LISTENING":
            continue
        try:
            port = int(parts[1].rsplit(":", 1)[1])
            pid = int(parts[4])
        except (ValueError, IndexError):
            continue
        if port in ports:
            pids_by_port.setdefault(port, set()).add(pid)
    return pids_by_port


def _terminate_pid(pid: int) -> None:
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return


def toggle_service_node(node_name: str, env_vars: Dict[str, str]) -> None:
    if node_name not in SERVICES:
        return
    if is_service_running(node_name):
        stop_service(node_name)
    else:
        delay = float(st.session_state.get(f"delay_{node_name}", 0.0) or 0.0)
        start_service(node_name, env_vars, delay=delay)


def handle_topology_toggle(env_vars: Dict[str, str]) -> None:
    requested = st.query_params.get("toggle_node")
    if isinstance(requested, list):
        requested = requested[0] if requested else None
    if not requested:
        return

    node_name = str(requested)
    toggle_service_node(node_name, env_vars)

    st.query_params.clear()
    time.sleep(0.2)
    st.rerun()


def read_recent_network_activity(
    window_seconds: float = 8.0,
    *,
    show_activity: bool = True,
) -> tuple[set[str], set[str], set[str], list[dict[str, Any]]]:
    if not LOG_FILE.exists():
        return set(), set(), set(), []

    lines = _read_recent_log_lines()

    now = datetime.now(timezone.utc)
    active_edges: set[str] = set()
    active_nodes: set[str] = set()
    recent_events: list[dict[str, Any]] = []
    task_start = st.session_state.get("task_start_time") if show_activity else None
    task_start_time = None
    if isinstance(task_start, (int, float)):
        task_start_time = datetime.fromtimestamp(max(0.0, task_start - 1.0), timezone.utc)
    clear_marker = st.session_state.get("generated_content_cleared_at")
    clear_time = None
    if isinstance(clear_marker, (int, float)):
        clear_time = datetime.fromtimestamp(clear_marker, timezone.utc)

    for line in lines:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue

        event_time = _parse_event_time(event.get("ts"))
        if event_time is None or (now - event_time).total_seconds() > window_seconds:
            continue
        if clear_time is not None and event_time < clear_time:
            continue
        if task_start_time is not None and event_time < task_start_time:
            continue

        source = str(event.get("source", ""))
        target = str(event.get("target", ""))
        edge_id = _edge_id_for_event(source, target, str(event.get("event", "")))
        if edge_id:
            active_edges.add(edge_id)

        for endpoint in (source, target):
            if endpoint in SERVICES:
                active_nodes.add(endpoint)

        recent_events.append(event)

    if not show_activity:
        st.session_state.topology_edge_pulses = {}
        st.session_state.topology_failed_edge_pulses = {}
        st.session_state.topology_node_pulses = {}
        return set(), set(), set(), recent_events[-8:]

    active_edges, failed_edges, active_nodes = _pulse_recent_topology_activity(recent_events)
    return active_edges, failed_edges, active_nodes, recent_events[-8:]


def _read_recent_log_lines(max_lines: int = 240) -> list[str]:
    try:
        with LOG_FILE.open("r", encoding="utf-8", errors="replace") as handle:
            return list(deque(handle, maxlen=max_lines))
    except OSError:
        return []


def _pulse_recent_topology_activity(events: list[dict[str, Any]]) -> tuple[set[str], set[str], set[str]]:
    now = time.time()
    edge_pulses = dict(st.session_state.get("topology_edge_pulses", {}))
    failed_edge_pulses = dict(st.session_state.get("topology_failed_edge_pulses", {}))
    node_pulses = dict(st.session_state.get("topology_node_pulses", {}))
    seen_keys = list(st.session_state.get("topology_seen_event_keys", []))
    seen = set(seen_keys)

    for event in events:
        source = str(event.get("source", ""))
        target = str(event.get("target", ""))
        edge_id = _edge_id_for_event(source, target, str(event.get("event", "")))
        if not edge_id:
            continue

        event_key = _network_event_key(event)
        if event_key in seen:
            continue

        seen.add(event_key)
        seen_keys.append(event_key)
        until = now + TRANSFER_PULSE_SECONDS
        if _is_failed_network_event(event):
            failed_edge_pulses[edge_id] = until
        else:
            edge_pulses[edge_id] = until
        for endpoint in (source, target):
            if endpoint in SERVICES:
                node_pulses[endpoint] = until

    failed_edges = {edge_id for edge_id, until in failed_edge_pulses.items() if until > now}
    active_edges = {edge_id for edge_id, until in edge_pulses.items() if until > now} - failed_edges
    active_nodes = {node_id for node_id, until in node_pulses.items() if until > now}
    st.session_state.topology_edge_pulses = {
        edge_id: until for edge_id, until in edge_pulses.items() if until > now
    }
    st.session_state.topology_failed_edge_pulses = {
        edge_id: until for edge_id, until in failed_edge_pulses.items() if until > now
    }
    st.session_state.topology_node_pulses = {
        node_id: until for node_id, until in node_pulses.items() if until > now
    }
    st.session_state.topology_seen_event_keys = seen_keys[-800:]
    return active_edges, failed_edges, active_nodes


def _is_failed_network_event(event: dict[str, Any]) -> bool:
    event_name = str(event.get("event", "")).lower()
    status_code = event.get("status_code")
    try:
        failed_status = int(status_code) >= 400 if status_code not in (None, "") else False
    except (TypeError, ValueError):
        failed_status = False
    return bool(
        event.get("error")
        or event.get("error_type")
        or failed_status
        or "error" in event_name
        or "failed" in event_name
        or event_name.endswith("_fail")
    )


def _network_event_key(event: dict[str, Any]) -> str:
    return "|".join(
        str(event.get(field, ""))
        for field in ("ts", "event", "source", "target", "task_id", "method", "url")
    )


def _network_protocol_label(event: dict[str, Any]) -> str:
    method = str(event.get("method", "") or "").upper()
    event_name = str(event.get("event", ""))
    endpoints = {str(event.get("source", "")), str(event.get("target", ""))}
    if any(endpoint.startswith("registry_center") for endpoint in endpoints):
        return "HTTP/REST"
    if method == "TCP":
        return "A2A TCP"
    if method in {"GET", "POST", "PUT", "DELETE", "PATCH"}:
        if "jsonrpc" in event_name or "mcp" in event_name:
            return "HTTP/JSON-RPC"
        return "HTTP/JSON"
    return "internal"


def _network_operation_label(event: dict[str, Any]) -> str:
    method = str(event.get("method", "") or "").upper()
    url = str(event.get("url", "") or "")
    event_name = str(event.get("event", "") or "")
    path = url
    if "://" in url:
        parsed = urlparse(url)
        path = parsed.path or "/"
    if event_name.startswith("registry_discover"):
        return f"{method or 'GET'} /discover"
    if path:
        return f"{method} {path}".strip()
    return event_name or "internal"


def _packet_for_display(event: dict[str, Any]) -> dict[str, Any]:
    return {
        "summary": {
            "event": event.get("event", ""),
            "direction": event.get("direction", ""),
            "source": event.get("source", ""),
            "target": event.get("target", ""),
            "task_id": event.get("task_id", ""),
            "status_code": event.get("status_code", ""),
            "elapsed_ms": event.get("elapsed_ms", ""),
            "payload_size": event.get("payload_size", ""),
            "error_type": event.get("error_type", ""),
        },
        "protocol_view": {
            "protocol": _network_protocol_label(event),
            "operation": _network_operation_label(event),
            "transport": "TCP",
            "note": _network_protocol_note(event),
        },
        "layers": _protocol_layer_rows(event),
        "wire_message": _format_full_protocol_message(event),
        "payload": event.get("payload"),
        "raw_event": dict(event),
    }


def _network_protocol_note(event: dict[str, Any]) -> str:
    endpoints = {str(event.get("source", "")), str(event.get("target", ""))}
    if any(endpoint.startswith("registry_center") for endpoint in endpoints):
        return "Coordinator queries Registry over HTTP GET /discover; response body is JSON."
    return "Derived from method/url fields in the network event log."


def render_packet_inspector(events: list[dict[str, Any]]) -> None:
    st.caption("每一行对应一条网络事件。单击任意小条目查看完整协议内容。")
    selected = packet_component(
        html=build_packet_inspector_html(events),
        default=None,
        key="network_packet_inspector",
    )
    if isinstance(selected, dict):
        event_key = str(selected.get("event_key", ""))
        nonce = selected.get("nonce")
        try:
            event_index = int(selected.get("index", -1))
        except (TypeError, ValueError):
            event_index = -1
        if event_key and nonce != st.session_state.get("last_packet_click_nonce"):
            st.session_state.last_packet_click_nonce = nonce
            st.session_state.selected_packet_event_key = event_key
            st.session_state.selected_packet_event_index = event_index
            packet = _selected_packet_event(events)
            if packet:
                show_packet_dialog(packet)


@st.dialog("完整协议内容")
def show_packet_dialog(packet: dict[str, Any]) -> None:
    event = packet.get("event", "event")
    source = packet.get("source", "?")
    target = packet.get("target", "?")
    protocol = _network_protocol_label(packet)
    operation = _network_operation_label(packet)
    st.caption(f"{protocol} | {operation} | {source} -> {target} | {event}")
    packet_view = _packet_for_display(packet)
    if _is_failed_network_event(packet):
        error_text = packet.get("error") or packet.get("error_type") or "request failed"
        st.error(f"FAIL: {error_text}")

    st.markdown("#### 协议分层")
    st.table(packet_view["layers"])

    tab_protocol, tab_payload, tab_raw = st.tabs(["完整协议", "Payload", "原始事件"])
    with tab_protocol:
        st.code(str(packet_view["wire_message"]), language="http" if packet.get("method") != "TCP" else "text")
    with tab_payload:
        payload = packet_view["payload"]
        if payload is None:
            st.info("该事件日志中没有记录 payload。")
        else:
            st.json(payload, expanded=True)
    with tab_raw:
        st.json(packet_view["raw_event"], expanded=True)


def _protocol_layer_rows(event: dict[str, Any]) -> list[dict[str, str]]:
    protocol = _network_protocol_label(event)
    operation = _network_operation_label(event)
    return [
        {
            "层次": "应用层",
            "协议/内容": protocol,
            "关键字段": operation,
        },
        {
            "层次": "表示层",
            "协议/内容": _payload_encoding_label(event),
            "关键字段": _payload_shape_label(event),
        },
        {
            "层次": "传输层",
            "协议/内容": "TCP",
            "关键字段": _transport_detail_label(event),
        },
        {
            "层次": "端点",
            "协议/内容": f"{event.get('source', '?')} -> {event.get('target', '?')}",
            "关键字段": _endpoint_detail_label(event),
        },
    ]


def _payload_encoding_label(event: dict[str, Any]) -> str:
    if _network_protocol_label(event).startswith("A2A"):
        return "Length-Prefix JSON frame, UTF-8"
    if event.get("method") in {"GET", "POST", "PUT", "DELETE", "PATCH"}:
        return "HTTP message, JSON body"
    if event.get("payload") is not None:
        return "JSON event payload"
    return "no payload captured"


def _payload_shape_label(event: dict[str, Any]) -> str:
    payload = event.get("payload")
    if isinstance(payload, dict):
        if payload.get("jsonrpc") == "2.0":
            method = payload.get("method")
            request_id = payload.get("id")
            if method:
                return f"JSON-RPC 2.0 request, method={method}, id={request_id}"
            if "result" in payload:
                return f"JSON-RPC 2.0 response, id={request_id}, result"
            if "error" in payload:
                return f"JSON-RPC 2.0 error, id={request_id}"
        if payload.get("version") and payload.get("type"):
            return f"A2A envelope type={payload.get('type')}, version={payload.get('version')}"
        return f"JSON object, {len(payload)} top-level fields"
    if payload is None:
        return "none"
    return type(payload).__name__


def _transport_detail_label(event: dict[str, Any]) -> str:
    size = event.get("payload_size")
    elapsed = event.get("elapsed_ms")
    parts = []
    if size not in (None, ""):
        parts.append(f"payload={size} bytes")
    if elapsed not in (None, ""):
        parts.append(f"elapsed={elapsed} ms")
    if event.get("status_code") not in (None, ""):
        parts.append(f"status={event.get('status_code')}")
    if event.get("error_type"):
        parts.append(f"error_type={event.get('error_type')}")
    return ", ".join(parts) if parts else "application log event"


def _endpoint_detail_label(event: dict[str, Any]) -> str:
    url = str(event.get("url", "") or "")
    if not url:
        return "no URL captured"
    parsed = urlparse(url)
    if parsed.netloc:
        return f"host={parsed.netloc}, path={parsed.path or '/'}"
    return f"path={url}"


def _format_full_protocol_message(event: dict[str, Any]) -> str:
    method = str(event.get("method", "") or "").upper()
    if method == "TCP":
        return _format_a2a_tcp_message(event)
    if method in {"GET", "POST", "PUT", "DELETE", "PATCH"}:
        return _format_http_protocol_message(event)
    return _format_internal_protocol_message(event)


def _format_a2a_tcp_message(event: dict[str, Any]) -> str:
    payload = event.get("payload")
    payload_text = _format_json_block(payload) if payload is not None else "(payload not captured)"
    size = event.get("payload_size")
    size_text = str(size) if size not in (None, "") else "derived from JSON body"
    lines = [
        "A2A TCP over TCP",
        "Frame format: [4-byte big-endian length][UTF-8 JSON envelope]",
        f"Length: {size_text}",
        f"Direction: {event.get('source', '?')} -> {event.get('target', '?')}",
        f"URL: {event.get('url', '') or '(tcp endpoint not captured)'}",
        "",
        "JSON envelope:",
        payload_text,
    ]
    if event.get("error"):
        lines.extend(["", "Error:", str(event.get("error"))])
    return "\n".join(lines)


def _format_http_protocol_message(event: dict[str, Any]) -> str:
    event_name = str(event.get("event", ""))
    method = str(event.get("method", "") or "GET").upper()
    url = str(event.get("url", "") or "/")
    parsed = urlparse(url)
    path = parsed.path or url or "/"
    host = parsed.netloc or _endpoint_host_from_event(event)
    payload = event.get("payload")
    body = _format_json_block(payload) if payload is not None else ""
    is_response_event = _is_http_response_event(event)
    request_body = "" if is_response_event else body
    response_body = body if is_response_event else ""
    request_length = event.get("payload_size")
    if request_length in (None, "") and request_body:
        request_length = len(request_body.encode("utf-8"))
    response_length = event.get("payload_size")
    if response_length in (None, "") and response_body:
        response_length = len(response_body.encode("utf-8"))

    request_lines = [
        f"{method} {path} HTTP/1.1",
        f"Host: {host}",
        "Accept: application/json",
    ]
    if request_body:
        request_lines.extend(
            [
                "Content-Type: application/json; charset=utf-8",
                f"Content-Length: {request_length}",
            ]
        )
    request = "\n".join(request_lines) + "\n\n" + request_body

    response_parts: list[str] = []
    if event.get("status_code") not in (None, "") or is_response_event:
        status = event.get("status_code")
        if status in (None, ""):
            status_line = "HTTP response: not received"
        else:
            status_line = f"HTTP/1.1 {status} {_http_reason_phrase(status)}"
        response_parts = [
            status_line,
            "Content-Type: application/json; charset=utf-8",
        ]
        if event.get("elapsed_ms") not in (None, ""):
            response_parts.append(f"X-Demo-Elapsed-Ms: {event.get('elapsed_ms')}")
        if event.get("error"):
            response_parts.extend(["", str(event.get("error"))])
        elif response_body:
            response_parts.insert(2, f"Content-Length: {response_length}")
            response_parts.extend(["", response_body])

    blocks = ["HTTP request", request]
    if response_parts:
        blocks.extend(["", "HTTP response / outcome", "\n".join(response_parts)])
    return "\n".join(blocks)


def _is_http_response_event(event: dict[str, Any]) -> bool:
    event_name = str(event.get("event", "")).lower()
    if event_name.endswith("_request"):
        return False
    return any(token in event_name for token in ("response", "failed", "error"))


def _format_internal_protocol_message(event: dict[str, Any]) -> str:
    return "\n".join(
        [
            "Internal workflow event",
            "This row is not a wire packet. It records Coordinator workflow state.",
            "",
            _format_json_block(event),
        ]
    )


def _endpoint_host_from_event(event: dict[str, Any]) -> str:
    target = str(event.get("target", ""))
    ports = SERVICE_PORTS.get(target)
    if ports:
        return f"127.0.0.1:{ports[0]}"
    return target or "localhost"


def _http_reason_phrase(status: Any) -> str:
    try:
        status_int = int(status)
    except (TypeError, ValueError):
        return ""
    reasons = {
        200: "OK",
        201: "Created",
        202: "Accepted",
        204: "No Content",
        400: "Bad Request",
        404: "Not Found",
        408: "Request Timeout",
        429: "Too Many Requests",
        500: "Internal Server Error",
        502: "Bad Gateway",
        503: "Service Unavailable",
        504: "Gateway Timeout",
    }
    return reasons.get(status_int, "")


def _format_json_block(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, default=str)


def build_packet_inspector_html(events: list[dict[str, Any]]) -> str:
    rows: list[str] = []
    for index, event in enumerate(events):
        event_name = escape(str(event.get("event", "event")))
        source = escape(str(event.get("source", "?")))
        target = escape(str(event.get("target", "?")))
        protocol = escape(_network_protocol_label(event))
        operation = escape(_network_operation_label(event))
        status = event.get("status_code", "")
        status_text = f"status {status}" if status != "" else ""
        elapsed = event.get("elapsed_ms", "")
        elapsed_text = f"{elapsed} ms" if elapsed != "" else ""
        size = event.get("payload_size", "")
        size_text = f"{size} bytes" if size != "" else ""
        meta = " · ".join(part for part in [status_text, elapsed_text, size_text] if part)
        event_key = escape(_network_event_key(event), quote=True)
        rows.append(
            f'<button class="packet-row" type="button" data-packet-index="{index}" '
            f'data-packet-key="{event_key}" title="Click to inspect packet">'
            f'<span class="packet-main"><strong>{index + 1}. {event_name}</strong>'
            f'<span>{protocol} · {operation} · {source} -> {target}</span></span>'
            f'<span class="packet-meta">{escape(meta) if meta else "event"}</span>'
            f'</button>'
        )

    if not rows:
        rows.append('<div class="packet-empty">No recent packets.</div>')

    return dedent(f"""
    <style>
      .packet-box {{
        box-sizing: border-box;
        width: 100%;
        border: 1px solid #2f3846;
        border-radius: 8px;
        background: #0f141c;
        padding: 8px;
        font-family: "Inter", "Segoe UI", Arial, sans-serif;
      }}
      .packet-row {{
        display: grid;
        grid-template-columns: minmax(0, 1fr) auto;
        align-items: center;
        gap: 10px;
        width: 100%;
        min-height: 42px;
        margin: 0 0 6px;
        padding: 7px 9px;
        border: 1px solid #263140;
        border-radius: 7px;
        background: #151b24;
        color: #eef5ff;
        cursor: pointer;
        text-align: left;
        font: inherit;
      }}
      .packet-row:last-child {{
        margin-bottom: 0;
      }}
      .packet-row:hover,
      .packet-row.selected {{
        border-color: #38bdf8;
        background: #172334;
      }}
      .packet-main {{
        display: flex;
        min-width: 0;
        flex-direction: column;
        gap: 2px;
      }}
      .packet-main strong,
      .packet-main span {{
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
      }}
      .packet-main span {{
        color: #aebbd0;
        font-size: 12px;
      }}
      .packet-meta {{
        color: #93c5fd;
        font-size: 12px;
        white-space: nowrap;
      }}
      .packet-empty {{
        color: #aebbd0;
        padding: 10px;
      }}
    </style>
    <div class="packet-box">
      {''.join(rows)}
    </div>
    """).strip()


def _selected_packet_event(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    event_key = st.session_state.get("selected_packet_event_key")
    for event in events:
        if _network_event_key(event) == event_key:
            return event
    event_index = st.session_state.get("selected_packet_event_index")
    if isinstance(event_index, int) and 0 <= event_index < len(events):
        return events[event_index]
    return None


def _parse_event_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _edge_id_for_event(source: str, target: str, event_name: str) -> str | None:
    pair = {source, target}
    if pair == {"user", "coordinator"}:
        return "edge_user_coordinator"
    if "coordinator" in pair and any(endpoint.endswith("_agent") for endpoint in pair):
        return "edge_coordinator_agent_pool"
    if "mcp_gateway" in pair and any(endpoint.endswith("_agent") for endpoint in pair):
        return "edge_agent_pool_gateway"
    if "mcp_gateway" in pair:
        for service_name in MCP_SERVICE_LABELS:
            if service_name in pair:
                return f"edge_gateway_{service_name}"
    if pair == {"coordinator", "registry_center_primary"}:
        return "edge_coordinator_registry_primary"
    if pair == {"coordinator", "registry_center_backup"}:
        return "edge_coordinator_registry_backup"
    return None


@st.fragment(run_every=TOPOLOGY_REFRESH_SECONDS)
def render_topology_control(env_vars: Dict[str, str], *, show_activity: bool) -> None:
    active_edges, failed_edges, active_nodes, _ = read_recent_network_activity(
        window_seconds=TRANSFER_HISTORY_SECONDS,
        show_activity=show_activity,
    )
    node_states = get_service_states()
    html = build_topology_html(node_states, active_edges, failed_edges, active_nodes)
    click_event = topology_component(html=html, default=None, key="topology_control")
    if not isinstance(click_event, dict):
        return

    node_name = str(click_event.get("node", ""))
    nonce = click_event.get("nonce")
    if not node_name or nonce == st.session_state.get("last_topology_click_nonce"):
        return

    st.session_state.last_topology_click_nonce = nonce
    toggle_service_node(node_name, env_vars)
    try:
        st.rerun(scope="fragment")
    except Exception:
        st.rerun()

def build_topology_html(
    node_states: dict[str, bool],
    active_edges: set[str],
    failed_edges: set[str],
    active_nodes: set[str],
) -> str:
    node_specs = [
        ("user", 305, 18, 140, 62),
        ("coordinator", 305, 110, 140, 62),
        ("registry_center_primary", 42, 168, 150, 62),
        ("registry_center_backup", 558, 168, 150, 62),
        ("weather_agent", 32, 258, 118, 56),
        ("attraction_agent", 174, 258, 118, 56),
        ("hotel_agent", 316, 258, 118, 56),
        ("traffic_agent", 458, 258, 118, 56),
        ("packing_agent", 600, 258, 118, 56),
        ("mcp_gateway", 305, 378, 140, 62),
        ("weather_mcp_server", 32, 518, 118, 58),
        ("traffic_mcp_server", 174, 518, 118, 58),
        ("attraction_mcp_server", 316, 518, 118, 58),
        ("hotel_mcp_server", 458, 518, 118, 58),
        ("packing_mcp_server", 600, 518, 118, 58),
    ]
    node_lookup = {name: (x, y, w, h) for name, x, y, w, h in node_specs}

    def edge_path(edge_id: str, source: str, target: str, label: str = "", *, dashed: bool = False) -> str:
        sx, sy, sw, sh = node_lookup[source]
        tx, ty, tw, th = node_lookup[target]
        x1 = sx + sw / 2
        y1 = sy + sh
        x2 = tx + tw / 2
        y2 = ty
        if source.startswith("registry") or target.startswith("registry"):
            y1 = sy + sh / 2
            x1 = sx + sw if sx < tx else sx
            y2 = ty + th / 2
            x2 = tx if sx < tx else tx + tw
        if source == "mcp_gateway":
            y1 = sy + sh
            y2 = ty
        cls = "edge active" if edge_id in active_edges else "edge"
        if dashed:
            cls += " dashed"
        mid_x = (x1 + x2) / 2
        mid_y = (y1 + y2) / 2
        label_markup = ""
        if label:
            label_markup = f'<text class="edge-label" x="{mid_x:.1f}" y="{mid_y - 8:.1f}">{escape(label)}</text>'
        return (
            f'<path id="{edge_id}" class="{cls}" '
            f'd="M {x1:.1f} {y1:.1f} C {x1:.1f} {mid_y:.1f}, {x2:.1f} {mid_y:.1f}, {x2:.1f} {y2:.1f}" />'
            f'{label_markup}'
        )

    def edge_points(edge_id: str, x1: float, y1: float, x2: float, y2: float) -> str:
        cls = "edge active" if edge_id in active_edges else "edge"
        mid_y = (y1 + y2) / 2
        return (
            f'<path id="{edge_id}" class="{cls}" '
            f'd="M {x1:.1f} {y1:.1f} C {x1:.1f} {mid_y:.1f}, {x2:.1f} {mid_y:.1f}, {x2:.1f} {y2:.1f}" />'
        )

    edges = [
        edge_path("edge_user_coordinator", "user", "coordinator"),
        edge_points("edge_coordinator_agent_pool", 375, 172, 375, 232),
        edge_points("edge_agent_pool_gateway", 375, 332, 375, 378),
        edge_path("edge_coordinator_registry_primary", "coordinator", "registry_center_primary"),
        edge_path("edge_coordinator_registry_backup", "coordinator", "registry_center_backup"),
        edge_path("edge_gateway_weather_mcp_server", "mcp_gateway", "weather_mcp_server"),
        edge_path("edge_gateway_traffic_mcp_server", "mcp_gateway", "traffic_mcp_server"),
        edge_path("edge_gateway_attraction_mcp_server", "mcp_gateway", "attraction_mcp_server"),
        edge_path("edge_gateway_hotel_mcp_server", "mcp_gateway", "hotel_mcp_server"),
        edge_path("edge_gateway_packing_mcp_server", "mcp_gateway", "packing_mcp_server"),
    ]
    nodes = [
        _node_svg(name, x, y, w, h, node_states.get(name, True if name == "user" else False), name in active_nodes)
        for name, x, y, w, h in node_specs
    ]
    hitboxes = [
        _node_hitbox(name, x, y, w, h)
        for name, x, y, w, h in node_specs
        if name in SERVICES
    ]

    return dedent(f"""
    <style>
      .topology-control {{
        font-family: "Inter", "Segoe UI", Arial, sans-serif;
        background: transparent;
      }}
      .topology-control .map-shell {{
        position: relative;
        border: 0;
        border-radius: 8px;
        background: transparent;
        overflow: hidden;
        width: 100%;
        max-width: 750px;
      }}
      .topology-control .node-hitbox {{
        position: absolute;
        z-index: 20;
        display: block;
        border-radius: 8px;
        cursor: pointer;
        text-decoration: none;
      }}
      .topology-control .node-hitbox:hover {{
        outline: 2px solid rgba(56, 189, 248, 0.9);
        outline-offset: 2px;
      }}
      .topology-control .topology {{
        display: block;
        width: 100%;
        height: auto;
        overflow: visible;
      }}
      .topology-control .plane-label {{
        fill: #d8e2ef;
        font-size: 18px;
        font-weight: 700;
      }}
      .topology-control .pool-box {{
        fill: rgba(148, 163, 184, 0.08);
        stroke: #64748b;
        stroke-width: 2;
        stroke-dasharray: 8 7;
        rx: 8;
      }}
      .topology-control .edge {{
        fill: none;
        stroke: #607084;
        stroke-width: 3;
        marker-end: url(#arrow);
      }}
      .topology-control .edge.dashed {{
        stroke-dasharray: 7 8;
      }}
      .topology-control .edge.active {{
        stroke: url(#activeGradient);
        stroke-width: 5;
        stroke-dasharray: 18 12;
        animation: flow 0.85s linear infinite;
        filter: url(#lineGlow);
      }}
      .topology-control .edge-label {{
        fill: #d8e2ef;
        font-size: 13px;
        font-weight: 650;
        paint-order: stroke;
        stroke: #0e1117;
        stroke-width: 6px;
        stroke-linejoin: round;
      }}
      .topology-control .node-link {{
        cursor: pointer;
        text-decoration: none;
      }}
      .topology-control .node-rect {{
        fill: #151b24;
        stroke: #334155;
        stroke-width: 2;
        rx: 8;
        filter: url(#nodeShadow);
      }}
      .topology-control .node.running .node-rect {{
        stroke: #1f9d55;
      }}
      .topology-control .node.stopped .node-rect {{
        fill: #111720;
        stroke: #334155;
      }}
      .topology-control .node.active .node-rect {{
        stroke: #f59e0b;
        stroke-width: 4;
        animation: nodePulse 1.1s ease-in-out infinite;
      }}
      .topology-control .node-icon {{
        fill: none;
        stroke: #d8e2ef;
        stroke-width: 2.4;
        stroke-linecap: round;
        stroke-linejoin: round;
      }}
      .topology-control .node-title {{
        fill: #f8fafc;
        font-size: 15px;
        font-weight: 750;
      }}
      .topology-control .node-port {{
        fill: #aebbd0;
        font-size: 12px;
        font-weight: 650;
      }}
      .topology-control .protocol-chip {{
        fill: rgba(14, 165, 233, 0.14);
        stroke: #38bdf8;
        stroke-width: 1.5;
        rx: 14;
      }}
      .topology-control .protocol-text {{
        fill: #e0f2fe;
        font-size: 14px;
        font-weight: 800;
      }}
      .topology-control .status-dot {{
        stroke: #ffffff;
        stroke-width: 4;
      }}
      .topology-control .status-on {{
        fill: #16a34a;
      }}
      .topology-control .status-off {{
        fill: #dc2626;
      }}
      .topology-control .legend {{
        fill: #d8e2ef;
        font-size: 13px;
        font-weight: 650;
      }}
      @keyframes flow {{
        to {{ stroke-dashoffset: -60; }}
      }}
      @keyframes nodePulse {{
        0%, 100% {{ filter: url(#nodeShadow); }}
        50% {{ filter: url(#activeNodeGlow); }}
      }}
    </style>
    <div class="topology-control">
      <div class="map-shell">
        <svg class="topology" viewBox="0 0 750 610" width="750" height="610" role="img" aria-label="A2A MCP network topology">
          <defs>
            <linearGradient id="activeGradient" x1="0%" y1="0%" x2="100%" y2="0%">
              <stop offset="0%" stop-color="#0ea5e9" />
              <stop offset="45%" stop-color="#22c55e" />
              <stop offset="100%" stop-color="#f59e0b" />
            </linearGradient>
            <filter id="lineGlow" x="-30%" y="-30%" width="160%" height="160%">
              <feGaussianBlur stdDeviation="3" result="blur" />
              <feMerge>
                <feMergeNode in="blur" />
                <feMergeNode in="SourceGraphic" />
              </feMerge>
            </filter>
            <filter id="nodeShadow" x="-20%" y="-20%" width="140%" height="140%">
              <feDropShadow dx="0" dy="2" stdDeviation="2" flood-color="#94a3b8" flood-opacity="0.22" />
            </filter>
            <filter id="activeNodeGlow" x="-35%" y="-35%" width="170%" height="170%">
              <feDropShadow dx="0" dy="0" stdDeviation="6" flood-color="#f59e0b" flood-opacity="0.55" />
            </filter>
            <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
              <path d="M 0 0 L 10 5 L 0 10 z" fill="#9aa8b8" />
            </marker>
          </defs>
          <rect class="pool-box" x="18" y="232" width="714" height="100" rx="8" />
          {''.join(edges)}
          <rect class="protocol-chip" x="350" y="206" width="50" height="26" rx="13" />
          <text class="protocol-text" x="375" y="224" text-anchor="middle">TCP</text>
          <rect class="protocol-chip" x="329" y="474" width="92" height="26" rx="13" />
          <text class="protocol-text" x="375" y="492" text-anchor="middle">HTTP/JSON</text>
          {''.join(nodes)}
          <circle class="status-dot status-on" cx="32" cy="594" r="7" />
          <text class="legend" x="46" y="598">running</text>
          <circle class="status-dot status-off" cx="130" cy="594" r="7" />
          <text class="legend" x="144" y="598">stopped</text>
          <path class="edge active" d="M 226 594 L 306 594" />
          <text class="legend" x="322" y="598">recent transfer</text>
        </svg>
        {''.join(hitboxes)}
      </div>
    </div>
    """).strip()


def _node_svg(name: str, x: int, y: int, width: int, height: int, running: bool, active: bool) -> str:
    meta = SERVICE_META[name]
    state_class = "running" if running else "stopped"
    active_class = " active" if active else ""
    dot_class = "status-on" if running else "status-off"
    label = escape(str(TOPOLOGY_LABELS.get(name, meta["label"])))
    port = escape(str(meta["port"]))
    controlled = name in SERVICES
    tooltip_action = "stop" if running else "start"
    tooltip = escape(f"{label}: click to {tooltip_action}. Port: {port}") if controlled else escape(f"{label}: browser entry")
    icon = _icon_svg(str(meta["kind"]), x + 14, y + 16)
    title_x = x + 42
    return (
        f'<g class="node-link"><g class="node {state_class}{active_class}">'
        f'<title>{tooltip}</title>'
        f'<rect class="node-rect" x="{x}" y="{y}" width="{width}" height="{height}" rx="8" />'
        f'{icon}'
        f'<text class="node-title" x="{title_x}" y="{y + 26}">{label}</text>'
        f'<text class="node-port" x="{title_x}" y="{y + 45}">{port}</text>'
        f'<circle class="status-dot {dot_class}" cx="{x + width - 12}" cy="{y + height - 11}" r="8" />'
        f'</g></g>'
    )


def _node_hitbox(name: str, x: int, y: int, width: int, height: int) -> str:
    href = f"?toggle_node={escape(name, quote=True)}&toggle_ts={int(time.time() * 1000)}"
    left = x / 750 * 100
    top = y / 610 * 100
    box_width = width / 750 * 100
    box_height = height / 610 * 100
    label = escape(str(SERVICE_META[name]["label"]))
    return (
        f'<a class="node-hitbox" href="{href}" title="Toggle {label}" '
        f'style="left:{left:.3f}%;top:{top:.3f}%;width:{box_width:.3f}%;height:{box_height:.3f}%"></a>'
    )


def _icon_svg(kind: str, x: int, y: int) -> str:
    icons = {
        "user": f'<g class="node-icon"><circle cx="{x + 10}" cy="{y + 7}" r="5"/><path d="M{x + 2} {y + 23}c2-7 14-7 16 0"/></g>',
        "coordinator": f'<g class="node-icon"><circle cx="{x + 11}" cy="{y + 11}" r="5"/><path d="M{x + 11} {y + 1}v5M{x + 11} {y + 16}v6M{x + 1} {y + 11}h5M{x + 16} {y + 11}h6"/></g>',
        "registry": f'<g class="node-icon"><ellipse cx="{x + 11}" cy="{y + 6}" rx="9" ry="4"/><path d="M{x + 2} {y + 6}v14c0 2.2 18 2.2 18 0V{y + 6}"/><path d="M{x + 2} {y + 13}c0 2.2 18 2.2 18 0"/></g>',
        "gateway": f'<g class="node-icon"><rect x="{x + 1}" y="{y + 5}" width="21" height="13" rx="3"/><path d="M{x + 5} {y + 23}h13M{x + 11} {y + 18}v5M{x + 6} {y + 11}h.1M{x + 11} {y + 11}h.1M{x + 16} {y + 11}h.1"/></g>',
        "mcp_weather": f'<g class="node-icon"><path d="M{x + 7} {y + 17}h11a5 5 0 0 0-1-10 7 7 0 0 0-13 4 4 4 0 0 0 3 6z"/><path d="M{x + 7} {y + 23}v.1M{x + 14} {y + 23}v.1"/></g>',
        "mcp_traffic": f'<g class="node-icon"><path d="M{x + 4} {y + 4}h14l3 8v8H{x + 1}v-8z"/><circle cx="{x + 6}" cy="{y + 21}" r="2"/><circle cx="{x + 17}" cy="{y + 21}" r="2"/></g>',
        "mcp_attraction": f'<g class="node-icon"><path d="M{x + 3} {y + 22}h18M{x + 5} {y + 22}V{y + 10}M{x + 19} {y + 22}V{y + 10}"/><path d="M{x + 2} {y + 10}l10-7 10 7z"/></g>',
        "mcp_hotel": f'<g class="node-icon"><path d="M{x + 3} {y + 22}V{y + 4}h9v18M{x + 12} {y + 11}h9v11M{x + 6} {y + 8}h2M{x + 6} {y + 13}h2M{x + 15} {y + 15}h3"/></g>',
        "mcp_packing": f'<g class="node-icon"><rect x="{x + 4}" y="{y + 8}" width="16" height="13" rx="2"/><path d="M{x + 8} {y + 8}V{y + 5}h8v3M{x + 12} {y + 8}v13"/></g>',
    }
    if kind.startswith("agent"):
        return f'<g class="node-icon"><rect x="{x + 3}" y="{y + 4}" width="17" height="17" rx="3"/><path d="M{x + 8} {y + 9}h7v7h-7zM{x + 1} {y + 8}h2M{x + 1} {y + 17}h2M{x + 20} {y + 8}h2M{x + 20} {y + 17}h2M{x + 7} {y + 2}v2M{x + 16} {y + 2}v2M{x + 7} {y + 21}v2M{x + 16} {y + 21}v2"/></g>'
    return icons.get(kind, icons["gateway"])


# st.html sanitizes SVG in current Streamlit, so the visible topology is pure HTML/CSS.
def build_topology_html(
    node_states: dict[str, bool],
    active_edges: set[str],
    failed_edges: set[str],
    active_nodes: set[str],
) -> str:
    node_specs = [
        ("user", 300, 18, 150, 62),
        ("coordinator", 300, 110, 150, 62),
        ("registry_center_primary", 28, 168, 170, 62),
        ("registry_center_backup", 552, 168, 170, 62),
        ("weather_agent", 28, 258, 130, 56),
        ("attraction_agent", 168, 258, 130, 56),
        ("hotel_agent", 308, 258, 130, 56),
        ("traffic_agent", 448, 258, 130, 56),
        ("packing_agent", 588, 258, 130, 56),
        ("mcp_gateway", 300, 378, 150, 62),
        ("weather_mcp_server", 28, 518, 130, 58),
        ("traffic_mcp_server", 168, 518, 130, 58),
        ("attraction_mcp_server", 308, 518, 130, 58),
        ("hotel_mcp_server", 448, 518, 130, 58),
        ("packing_mcp_server", 588, 518, 130, 58),
    ]

    edges = [
        _edge_html("edge_user_coordinator", 375, 80, 375, 110, active_edges, failed_edges),
        _edge_html("edge_coordinator_agent_pool", 375, 172, 375, 232, active_edges, failed_edges),
        _edge_html("edge_agent_pool_gateway", 375, 332, 375, 378, active_edges, failed_edges),
        _edge_html("edge_coordinator_registry_primary", 300, 141, 198, 199, active_edges, failed_edges),
        _edge_html("edge_coordinator_registry_backup", 450, 141, 552, 199, active_edges, failed_edges),
        _edge_html("edge_gateway_weather_mcp_server", 375, 440, 93, 518, active_edges, failed_edges),
        _edge_html("edge_gateway_traffic_mcp_server", 375, 440, 233, 518, active_edges, failed_edges),
        _edge_html("edge_gateway_attraction_mcp_server", 375, 440, 373, 518, active_edges, failed_edges),
        _edge_html("edge_gateway_hotel_mcp_server", 375, 440, 513, 518, active_edges, failed_edges),
        _edge_html("edge_gateway_packing_mcp_server", 375, 440, 653, 518, active_edges, failed_edges),
    ]
    nodes = [
        _node_card_html(
            name,
            x,
            y,
            width,
            height,
            node_states.get(name, True if name == "user" else False),
            name in active_nodes,
        )
        for name, x, y, width, height in node_specs
    ]
    transfer_legend = (
        '<span class="legend-item"><span class="legend-flow"></span>recent transfer</span>'
        if active_edges
        else ""
    )
    failed_legend = (
        '<span class="legend-item"><span class="legend-flow failed"></span>failed transfer</span>'
        if failed_edges
        else ""
    )

    return dedent(f"""
    <style>
      .topology-control {{
        font-family: "Inter", "Segoe UI", Arial, sans-serif;
        background: transparent;
        width: 100%;
        max-width: 750px;
      }}
      .topology-control .map-shell {{
        position: relative;
        width: 100%;
        aspect-ratio: 750 / 610;
        border-radius: 8px;
        background: transparent;
        overflow: visible;
      }}
      .topology-control .pool-box {{
        position: absolute;
        left: 2.400%;
        top: 38.033%;
        width: 95.200%;
        height: 16.393%;
        border: 2px dashed #64748b;
        border-radius: 8px;
        background: rgba(148, 163, 184, 0.08);
        z-index: 0;
      }}
      .topology-control .edge {{
        position: absolute;
        height: 3px;
        border-radius: 999px;
        background: #607084;
        transform-origin: 0 50%;
        z-index: 1;
      }}
      .topology-control .edge::after {{
        content: "";
        position: absolute;
        right: -8px;
        top: 50%;
        width: 0;
        height: 0;
        border-top: 5px solid transparent;
        border-bottom: 5px solid transparent;
        border-left: 9px solid #9aa8b8;
        transform: translateY(-50%);
      }}
      .topology-control .edge.active {{
        height: 5px;
        background: linear-gradient(90deg, #0ea5e9, #22c55e, #f59e0b, #0ea5e9);
        background-size: 220% 100%;
        animation: flowBg 0.9s linear infinite;
        box-shadow: 0 0 12px rgba(34, 197, 94, 0.55);
      }}
      .topology-control .edge.active::after {{
        border-left-color: #f59e0b;
      }}
      .topology-control .edge.failed {{
        height: 5px;
        background: repeating-linear-gradient(
          90deg,
          #ef4444 0 12px,
          transparent 12px 18px
        );
        box-shadow: 0 0 13px rgba(239, 68, 68, 0.75);
        animation: failPulse 0.8s ease-in-out infinite alternate;
      }}
      .topology-control .edge.failed::after {{
        border-left-color: #ef4444;
      }}
      .topology-control .protocol-chip {{
        position: absolute;
        display: grid;
        place-items: center;
        border: 1.5px solid #38bdf8;
        border-radius: 999px;
        background: rgba(14, 165, 233, 0.14);
        color: #e0f2fe;
        font-size: 13px;
        font-weight: 800;
        z-index: 8;
        line-height: 1;
      }}
      .topology-control .edge-protocol-label {{
        position: absolute;
        z-index: 4;
        box-sizing: border-box;
        padding: 2px 7px;
        border: 1px solid rgba(56, 189, 248, 0.8);
        border-radius: 999px;
        background: rgba(15, 23, 42, 0.86);
        color: #e0f2fe;
        font-size: 11px;
        font-weight: 800;
        line-height: 1.1;
        white-space: nowrap;
        pointer-events: none;
      }}
      .topology-control .topology-node {{
        position: absolute;
        z-index: 5;
        display: grid;
        grid-template-columns: 22px minmax(0, 1fr);
        grid-template-rows: 1fr 1fr;
        column-gap: 5px;
        align-items: center;
        box-sizing: border-box;
        padding: 6px 19px 6px 8px;
        border: 2px solid #334155;
        border-radius: 8px;
        background: #151b24;
        color: #f8fafc;
        cursor: pointer;
        font-family: inherit;
        text-decoration: none;
        text-align: left;
        box-shadow: 0 2px 6px rgba(148, 163, 184, 0.22);
        appearance: none;
      }}
      .topology-control .topology-node:hover {{
        outline: 2px solid rgba(56, 189, 248, 0.9);
        outline-offset: 2px;
      }}
      .topology-control .topology-node.user {{
        pointer-events: none;
      }}
      .topology-control .topology-node.running {{
        border-color: #1f9d55;
      }}
      .topology-control .topology-node.stopped {{
        border-color: #334155;
        background: #111720;
      }}
      .topology-control .topology-node.active {{
        border-color: #f59e0b;
        box-shadow: 0 0 14px rgba(245, 158, 11, 0.45);
      }}
      .topology-control .node-icon-symbol {{
        grid-row: 1 / 3;
        display: grid;
        place-items: center;
        width: 19px;
        height: 19px;
        border: 1.5px solid #64748b;
        border-radius: 6px;
        color: #d8e2ef;
        font-size: 12px;
        font-weight: 800;
        line-height: 1;
      }}
      .topology-control .node-title {{
        min-width: 0;
        align-self: end;
        overflow: hidden;
        color: #f8fafc;
        font-size: 12px;
        font-weight: 750;
        line-height: 1.1;
        text-overflow: ellipsis;
        white-space: nowrap;
      }}
      .topology-control .node-port {{
        align-self: start;
        color: #aebbd0;
        font-size: 10px;
        font-weight: 650;
        line-height: 1.2;
      }}
      .topology-control .status-badge {{
        position: absolute;
        right: 5px;
        bottom: 5px;
        width: 12px;
        height: 12px;
        border: 3px solid #ffffff;
        border-radius: 999px;
        box-sizing: border-box;
      }}
      .topology-control .running .status-badge {{
        background: #16a34a;
      }}
      .topology-control .stopped .status-badge {{
        background: #dc2626;
      }}
      .topology-control .legend-row {{
        position: absolute;
        left: 3.200%;
        top: 95.902%;
        display: flex;
        align-items: center;
        gap: 14px;
        color: #d8e2ef;
        font-size: 13px;
        font-weight: 650;
        z-index: 8;
      }}
      .topology-control .legend-item {{
        display: inline-flex;
        align-items: center;
        gap: 7px;
      }}
      .topology-control .legend-dot {{
        width: 13px;
        height: 13px;
        border: 3px solid #ffffff;
        border-radius: 999px;
        box-sizing: border-box;
      }}
      .topology-control .legend-dot.on {{
        background: #16a34a;
      }}
      .topology-control .legend-dot.off {{
        background: #dc2626;
      }}
      .topology-control .legend-flow {{
        width: 80px;
        height: 5px;
        border-radius: 999px;
        background: linear-gradient(90deg, #0ea5e9, #22c55e, #f59e0b, #0ea5e9);
        background-size: 220% 100%;
        animation: flowBg 0.9s linear infinite;
      }}
      .topology-control .legend-flow.failed {{
        width: 54px;
        background: repeating-linear-gradient(90deg, #ef4444 0 12px, transparent 12px 18px);
        box-shadow: 0 0 8px rgba(239, 68, 68, 0.65);
        animation: failPulse 0.8s ease-in-out infinite alternate;
      }}
      @keyframes flowBg {{
        to {{ background-position: -220% 0; }}
      }}
      @keyframes failPulse {{
        from {{ opacity: 0.45; }}
        to {{ opacity: 1; }}
      }}
    </style>
    <div class="topology-control">
      <div class="map-shell">
        <div class="pool-box"></div>
        {''.join(edges)}
        <div class="edge-protocol-label" style="left:27.700%;top:25.900%;transform:rotate(-29.6deg);">HTTP/REST</div>
        <div class="edge-protocol-label" style="left:58.800%;top:25.900%;transform:rotate(29.6deg);">HTTP/REST</div>
        <div class="protocol-chip" style="left:46.667%;top:33.770%;width:6.667%;height:4.262%;">TCP</div>
        <div class="protocol-chip" style="left:43.867%;top:77.705%;width:12.267%;height:4.262%;">HTTP/JSON</div>
        {''.join(nodes)}
        <div class="legend-row">
          <span class="legend-item"><span class="legend-dot on"></span>running</span>
          <span class="legend-item"><span class="legend-dot off"></span>stopped</span>
          {transfer_legend}
          {failed_legend}
        </div>
      </div>
    </div>
    """).strip()


def _edge_html(
    edge_id: str,
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    active_edges: set[str],
    failed_edges: set[str],
) -> str:
    length = hypot(x2 - x1, y2 - y1) / 750 * 100
    angle = degrees(atan2(y2 - y1, x2 - x1))
    if edge_id in failed_edges:
        cls = "edge failed"
    elif edge_id in active_edges:
        cls = "edge active"
    else:
        cls = "edge"
    return (
        f'<div id="{edge_id}" class="{cls}" '
        f'style="left:{x1 / 750 * 100:.3f}%;top:{y1 / 610 * 100:.3f}%;'
        f'width:{length:.3f}%;transform:rotate({angle:.2f}deg);"></div>'
    )


def _node_card_html(name: str, x: int, y: int, width: int, height: int, running: bool, active: bool) -> str:
    meta = SERVICE_META[name]
    state_class = "running" if running else "stopped"
    active_class = " active" if active else ""
    kind = str(meta["kind"])
    label = escape(str(TOPOLOGY_LABELS.get(name, meta["label"])))
    port = escape(str(meta["port"]))
    tooltip_action = "stop" if running else "start"
    style = (
        f"left:{x / 750 * 100:.3f}%;top:{y / 610 * 100:.3f}%;"
        f"width:{width / 750 * 100:.3f}%;height:{height / 610 * 100:.3f}%;"
    )
    icon = _html_icon(kind)
    body = (
        f'<span class="node-icon-symbol">{icon}</span>'
        f'<span class="node-title">{label}</span>'
        f'<span class="node-port">{port}</span>'
        f'<span class="status-badge"></span>'
    )
    cls = f'topology-node {kind} {state_class}{active_class}'
    if name not in SERVICES:
        return f'<div class="{cls}" style="{style}" title="{label}: browser entry">{body}</div>'
    title = escape(f"{label}: click to {tooltip_action}. Port: {port}")
    return f'<button class="{cls}" data-node="{escape(name)}" type="button" style="{style}" title="{title}">{body}</button>'


def _html_icon(kind: str) -> str:
    if kind.startswith("agent"):
        return "&#9881;"
    return {
        "user": "&#9675;",
        "coordinator": "&#8857;",
        "registry": "DB",
        "gateway": "GW",
        "mcp_weather": "&#9729;",
        "mcp_traffic": "&#9654;",
        "mcp_attraction": "&#8962;",
        "mcp_hotel": "H",
        "mcp_packing": "&#9635;",
    }.get(kind, "&#9679;")

# 注册退出时的清理函数，防止 Streamlit 关闭时遗留僵尸进程
atexit.register(lambda: stop_all_services(include_port_processes=False))

# --- 页面布局 ---
st.title("✈️ A2A 旅行工作流 Agent")

col_sidebar, col_main = st.columns([1, 1])

with col_sidebar:
    st.header("⚙️ 节点管理与容错配置")
    
    st.markdown("### 全局超时参数")
    a2a_tcp_timeout = st.number_input("A2A TCP Timeout (秒)", value=3.0, step=1.0)
    mcp_http_timeout = st.number_input("MCP HTTP Timeout (秒)", value=10.0, step=1.0)
    task_timeout = st.number_input("Task 整体 Timeout (秒)", value=120.0, step=10.0)
    
    st.markdown("### 启停控制")
    col_btn1, col_btn2 = st.columns(2)
    
    env_config = {
        "A2A_TCP_TIMEOUT_SECONDS": str(a2a_tcp_timeout),
        "MCP_HTTP_TIMEOUT_SECONDS": str(mcp_http_timeout),
        "DEFAULT_TASK_TIMEOUT_SECONDS": str(task_timeout),
        "MAX_TASK_TIMEOUT_SECONDS": str(task_timeout),
        "PYTHONIOENCODING": "utf-8"
    }
    
    # 将系统环境变量中的 API_KEY 也传过去，防止丢失
    for k, v in os.environ.items():
        if k not in env_config:
            env_config[k] = v

    if col_btn1.button("▶️ 启动所有节点", use_container_width=True):
        with st.spinner("正在启动所有服务..."):
            for srv in SERVICES.keys():
                start_service(srv, env_config)
            time.sleep(3)
        st.rerun()
        
    if col_btn2.button("⏹️ 停止所有节点", use_container_width=True):
        stop_all_services()
        st.session_state.task_transfer_active = False
        st.rerun()

    st.markdown("### 节点拓扑控制")
    show_topology_activity = bool(st.session_state.get("task_transfer_active", False))
    _, _, _, recent_events = read_recent_network_activity(show_activity=show_topology_activity)
    render_topology_control(env_config, show_activity=show_topology_activity)
    st.caption("点击拓扑中的服务节点即可启停。Agent 池作为动态部署层展示，最近 8 秒有协议传输的链路会显示渐变流动。")

    with st.expander("MCP 延迟注入", expanded=False):
        st.caption("仅在对应 MCP 节点从停止状态启动时生效。")
        for srv_name, label in MCP_SERVICE_LABELS.items():
            st.number_input(
                f"{label} delay",
                min_value=0.0,
                value=float(st.session_state.get(f"delay_{srv_name}", 0.0) or 0.0),
                step=1.0,
                key=f"delay_{srv_name}",
            )

    if recent_events:
        st.session_state.last_recent_network_events = recent_events
    display_events = recent_events or st.session_state.get("last_recent_network_events", [])

    if display_events:
        with st.expander("最近网络事件", expanded=False):
            compact_events = [
                {
                    "event": item.get("event"),
                    "protocol": _network_protocol_label(item),
                    "operation": _network_operation_label(item),
                    "source": item.get("source"),
                    "target": item.get("target"),
                    "method": item.get("method", ""),
                    "url": item.get("url", ""),
                }
                for item in display_events
            ]
            st.dataframe(compact_events, hide_index=True, use_container_width=True)

        with st.expander("网络报文", expanded=False):
            render_packet_inspector(display_events)

with col_main:
    st.header("💬 旅行任务交互")
    st.markdown("输入您的旅行需求，Coordinator 将根据当前存活的 Agents 动态分析依赖，为您完成规划。")

    query = st.text_area(
        "请描述您的旅行需求：", 
        value="中秋节假期从上海去北京玩3天，要求穷游并且尽量乘坐地铁，必须去故宫看看。"
    )

    submit_col, clear_col = st.columns([2, 1])
    has_generated_content = bool(
        st.session_state.get("current_task_id")
        or st.session_state.get("last_recent_network_events")
        or st.session_state.get("selected_packet_event_key")
    )
    clear_requested = clear_col.button(
        "清空生成内容 / Clear",
        use_container_width=True,
        disabled=not has_generated_content,
    )
    if clear_requested:
        clear_generated_content_state()
        st.rerun()

    if submit_col.button("提交任务 / Submit", type="primary", use_container_width=True):
        is_coordinator_running = is_service_running("coordinator")
        
        if not is_coordinator_running:
            st.error("Coordinator 尚未启动！请先在左侧启动节点。")
        elif not query.strip():
            st.warning("请输入您的旅行需求。")
        else:
            try:
                st.session_state.pop("generated_content_cleared_at", None)
                st.session_state.task_start_time = time.time()
                st.session_state.task_transfer_active = True
                st.session_state.task_highlight_cleared_for = None
                st.session_state.topology_edge_pulses = {}
                st.session_state.topology_node_pulses = {}
                st.session_state.topology_seen_event_keys = []
                with st.status("正在提交任务，Coordinator 正在进行总体规划...", expanded=True, state="running") as submit_status:
                    st.write("已收到你的问题，正在发送给 Coordinator。")
                    st.write("正在生成总体规划与工作流 DAG，请稍等...")
                    response = requests.post(
                        COORDINATOR_URL,
                        json={"question": query, "timeout": task_timeout, "async": True},
                        timeout=task_timeout + 1
                    )
                    response.raise_for_status()
                    data = response.json()
                    st.session_state.current_task_id = data.get("task", {}).get("task_id")
                    st.session_state.task_transfer_active = bool(st.session_state.current_task_id)
                    submit_status.update(
                        label=f"任务已提交成功，耗时 {time.time() - st.session_state.task_start_time:.1f} 秒",
                        state="complete",
                        expanded=False,
                    )
                st.rerun() # 触发页面重载以脱离按钮作用域渲染任务状态
            except Exception as e:
                st.error(f"启动任务失败: {e}")

    task_id = st.session_state.get("current_task_id")
    if task_id:
        try:
            start_time = st.session_state.get("task_start_time", time.time())
            poll_resp = requests.get(f"http://127.0.0.1:9000/tasks?task_id={task_id}", timeout=5)
            poll_resp.raise_for_status()
            task = poll_resp.json().get("task", {})

            is_completed = task.get("status") in ["completed", "failed", "partial"]
            if is_completed and st.session_state.get("task_highlight_cleared_for") != task_id:
                st.session_state.task_transfer_active = False
                st.session_state.task_highlight_cleared_for = task_id
                st.rerun()
            elapsed = time.time() - start_time
            status_label = (
                f"整个工作流执行结束！总耗时: {elapsed:.1f} 秒"
                if is_completed
                else f"正在调度多 Agent 工作流，请稍等... 当前耗时: {elapsed:.1f} 秒"
            )
            status_state = "complete" if is_completed else "running"

            with st.status(status_label, expanded=True, state=status_state):
                if not is_completed:
                    st.write("已将您的描述发送到 Coordinator...")

                st.write("✅ Coordinator 已完成初始调度网络规划！")
                plan = task.get("plan", {})
                if plan:
                    with st.expander("📝 阶段 1：Coordinator 动态规划的工作流和分配情况 (DAG)", expanded=False):
                        st.json(plan, expanded=True)

                st.markdown("### 🤖 阶段 2：各个 Worker Agent 执行动态")
                results = task.get("results", {})
                errors = task.get("dispatch_errors", {})

                if not results and not errors:
                    st.info("当前还没有 Agent 返回结果。")

                for agent_name, result_data in results.items():
                    status_emoji = "✅" if result_data.get("status") == "success" else "❌"
                    with st.expander(f"{status_emoji} {agent_name} 的执行报告", expanded=False):
                        st.json(result_data, expanded=True)

                for agent_name, err_msg in errors.items():
                    with st.expander(f"💀 {agent_name} [DISPATCH_ERROR]", expanded=True):
                        st.error(err_msg)

            final_answer = task.get("final_answer")
            if final_answer:
                st.markdown("---")
                st.markdown(f"### 🌟 阶段 3：最终旅行方案 (状态: {task.get('status')})")
                st.success("多智能体系统已组合生成了这一份行程报告！", icon="🎉")
                st.markdown(final_answer)
            elif is_completed:
                st.warning("任务已结束，但未生成最终结果。")

            if not is_completed:
                time.sleep(0.8)
                st.rerun()

        except Exception as e:
            st.error(f"获取任务状态失败: {e}")
