import streamlit as st
import streamlit.components.v1 as components
import requests
import time
import subprocess
import os
import signal
import json
import shutil
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
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
MCP_GATEWAY_URL = "http://127.0.0.1:8100"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from common.config import (
    A2A_REALTIME_MCP_ENABLED as DEFAULT_REALTIME_MCP_ENABLED,
    A2A_TCP_TIMEOUT_SECONDS as DEFAULT_A2A_TCP_TIMEOUT_SECONDS,
    AMAP_API_BASE_URL as DEFAULT_AMAP_API_BASE_URL,
    AMAP_WEB_KEY as DEFAULT_AMAP_WEB_KEY,
    DEFAULT_TASK_TIMEOUT_SECONDS as DEFAULT_TASK_TIMEOUT_SECONDS_CONFIG,
    MCP_GATEWAY as DEFAULT_MCP_GATEWAY_CONFIG,
    MCP_HTTP_TIMEOUT_SECONDS as DEFAULT_MCP_HTTP_TIMEOUT_SECONDS,
    MCP_REALTIME_FALLBACK_TO_MOCK as DEFAULT_MCP_REALTIME_FALLBACK_TO_MOCK,
    MCP_REALTIME_TIMEOUT_SECONDS as DEFAULT_MCP_REALTIME_TIMEOUT_SECONDS,
)

LOG_FILE = PROJECT_ROOT / "logs" / "demo_log.jsonl"
TOPOLOGY_COMPONENT_DIR = PROJECT_ROOT / "scripts" / "topology_component"
topology_component = components.declare_component("topology_control", path=str(TOPOLOGY_COMPONENT_DIR))
PACKET_COMPONENT_DIR = PROJECT_ROOT / "scripts" / "packet_component"
packet_component = components.declare_component("packet_inspector", path=str(PACKET_COMPONENT_DIR))
CAPTURE_DIR = PROJECT_ROOT / "logs" / "captures"
TOPOLOGY_REFRESH_SECONDS = 0.35
TRANSFER_HISTORY_SECONDS = 30.0
TRANSFER_PULSE_SECONDS = 1.6

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

REALTIME_MCP_SOURCE_ROWS = [
    {
        "MCP": "Weather",
        "实时来源": "高德天气 / Open-Meteo 预报",
        "UI 可观察字段": "provider, forecast_days",
    },
    {
        "MCP": "Attraction",
        "实时来源": "高德 POI + 本地画像补齐",
        "UI 可观察字段": "provider, missing_fields, field_sources",
    },
    {
        "MCP": "Hotel",
        "实时来源": "高德 POI，按预算档搜索并围绕景点重心",
        "UI 可观察字段": "provider, field_sources",
    },
    {
        "MCP": "Traffic",
        "实时来源": "高德路线；城际交通按驾车距离推算高铁/飞机",
        "UI 可观察字段": "provider, preference",
    },
    {
        "MCP": "Packing",
        "实时来源": "规则引擎，依赖天气结果",
        "UI 可观察字段": "provider",
    },
]

MCP_GATEWAY_CACHE_ROWS = [
    {"方法": "get_weather", "缓存 TTL": "1 天", "缓存 key": "业务参数"},
    {"方法": "get_packing_list", "缓存 TTL": "1 天", "缓存 key": "业务参数"},
    {"方法": "get_routes", "缓存 TTL": "30 天", "缓存 key": "业务参数"},
    {"方法": "search_hotels", "缓存 TTL": "30 天", "缓存 key": "业务参数"},
    {"方法": "search_attractions", "缓存 TTL": "30 天", "缓存 key": "业务参数"},
    {"方法": "get_intercity_transport", "缓存 TTL": "30 天", "缓存 key": "业务参数"},
]

DATA_PROVIDER_LABELS = {
    "amap": "高德地图",
    "amap+local_profile": "高德地图 + 本地画像",
    "amap+mock": "高德地图 + Mock",
    "open-meteo": "Open-Meteo",
    "mock": "本地 Mock",
}

DEMO_PARAMETER_PRESETS = {
    "正常链路对照": {
        "a2a_timeout": 3.0,
        "mcp_timeout": 10.0,
        "realtime_timeout": 5.0,
        "task_timeout": 120.0,
        "delays": {},
        "purpose": "所有节点正常响应，用作成功路径基线。",
        "steps": "应用后启动所有节点，直接提交旅行任务。",
    },
    "MCP HTTP 超时": {
        "a2a_timeout": 3.0,
        "mcp_timeout": 1.0,
        "realtime_timeout": 1.0,
        "task_timeout": 60.0,
        "delays": {"weather_mcp_server": 2.0},
        "purpose": "Weather MCP 人为慢于 MCP HTTP Timeout，展示 HTTP 请求超时、错误传播和 partial 回答。",
        "steps": "应用后启动或重启节点，让 1s timeout 和 Weather MCP delay 生效，然后提交任务。",
    },
    "A2A 派发容错": {
        "a2a_timeout": 1.0,
        "mcp_timeout": 3.0,
        "realtime_timeout": 5.0,
        "task_timeout": 60.0,
        "delays": {},
        "purpose": "缩短 A2A 等待窗口，用于配合关闭某个 Agent 展示 Coordinator 派发失败。",
        "steps": "应用后关闭一个 Agent 节点，例如 Packing Agent，再提交任务。",
    },
}
CUSTOM_DEMO_PARAMETER_OPTION = "自定义参数"

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


def _display_value(value: Any) -> str:
    if value is None or value == "":
        return "-"
    if isinstance(value, list):
        return ", ".join(str(item) for item in value) or "-"
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _provider_label(provider: Any) -> str:
    provider_text = str(provider or "-")
    return DATA_PROVIDER_LABELS.get(provider_text, provider_text)


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
        start_service(node_name, env_vars, delay=_service_delay(node_name))


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
        },
        "layers": _protocol_layer_rows(event),
        "wire_message": _format_full_protocol_message(event),
        "payload": event.get("payload"),
        "raw_event": dict(event),
    }

def render_packet_inspector(events: list[dict[str, Any]]) -> None:
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
            st.code("null", language="json")
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
    size_text = str(size) if size not in (None, "") else "not recorded"
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
            f"Event: {event.get('event', 'event')}",
            f"Source: {event.get('source', '?')}",
            f"Target: {event.get('target', '?')}",
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


def _creationflags_no_window(*, new_process_group: bool = False) -> dict[str, int]:
    if os.name != "nt":
        return {}
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    if new_process_group:
        flags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    return {"creationflags": flags}


def _run_cli_no_window(args: list[str], *, timeout: int = 10) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        **_creationflags_no_window(),
    )


@st.cache_data(ttl=300)
def _find_wireshark_tool(tool_name: str) -> str:
    found = shutil.which(tool_name)
    if found:
        return found

    candidates = [
        Path("D:/Wireshark") / tool_name,
        Path("C:/Program Files/Wireshark") / tool_name,
        Path("C:/Program Files (x86)/Wireshark") / tool_name,
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return ""


@st.cache_data(ttl=120)
def _dumpcap_interfaces(dumpcap_path: str) -> dict[str, Any]:
    if not dumpcap_path:
        return {"ok": False, "interfaces": [], "error": "dumpcap.exe not found"}
    try:
        result = _run_cli_no_window([dumpcap_path, "-D"], timeout=10)
    except subprocess.TimeoutExpired:
        return {"ok": False, "interfaces": [], "error": "dumpcap -D timed out"}
    except OSError as exc:
        return {"ok": False, "interfaces": [], "error": _capture_os_error_message(exc)}

    output = (result.stdout or "") + "\n" + (result.stderr or "")
    interfaces: list[dict[str, str]] = []
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line or "." not in line:
            continue
        number, rest = line.split(".", 1)
        if not number.isdigit():
            continue
        rest = rest.strip()
        name = rest
        description = rest
        if " (" in rest and rest.endswith(")"):
            name, description = rest.rsplit(" (", 1)
            description = description[:-1]
        interfaces.append({"name": name.strip(), "description": description.strip(), "label": rest})

    return {
        "ok": result.returncode == 0 and bool(interfaces),
        "interfaces": interfaces,
        "error": output.strip() if result.returncode != 0 else "",
    }


def _capture_os_error_message(exc: OSError) -> str:
    winerror = getattr(exc, "winerror", None)
    if winerror == 740:
        return "当前 Wireshark/Npcap 配置要求管理员权限。UI 已阻止弹出 UAC，请改用普通用户可抓包的 Npcap 配置或以管理员启动 UI。"
    return str(exc)


def _default_capture_interface(interfaces: list[dict[str, str]]) -> str:
    for item in interfaces:
        if item.get("name") == r"\Device\NPF_Loopback":
            return item["name"]
    return interfaces[0]["name"] if interfaces else ""


def _format_capture_interface(name: str, interfaces: list[dict[str, str]]) -> str:
    for item in interfaces:
        if item.get("name") == name:
            return item.get("label") or name
    return name


def _project_capture_ports() -> list[int]:
    ports = {port for service_ports in SERVICE_PORTS.values() for port in service_ports}
    return sorted(ports)


def _pcap_capture_filter() -> str:
    return " or ".join(f"tcp port {port}" for port in _project_capture_ports())


def _packet_capture_capability() -> dict[str, Any]:
    dumpcap = _find_wireshark_tool("dumpcap.exe")
    tshark = _find_wireshark_tool("tshark.exe")
    capinfos = _find_wireshark_tool("capinfos.exe")
    interfaces_result = _dumpcap_interfaces(dumpcap) if dumpcap else {"ok": False, "interfaces": [], "error": ""}
    interfaces = interfaces_result.get("interfaces", [])
    return {
        "ok": bool(dumpcap and interfaces_result.get("ok")),
        "dumpcap": dumpcap,
        "tshark": tshark,
        "capinfos": capinfos,
        "interfaces": interfaces,
        "default_interface": _default_capture_interface(interfaces),
        "error": interfaces_result.get("error") or ("dumpcap.exe not found" if not dumpcap else ""),
    }


def start_task_packet_capture(*, max_seconds: int, interface_name: str | None = None) -> dict[str, Any]:
    capability = _packet_capture_capability()
    if not capability["ok"]:
        return {"ok": False, "status": "error", "error": capability.get("error") or "packet capture unavailable"}

    interface = interface_name or capability["default_interface"]
    if not interface:
        return {"ok": False, "status": "error", "error": "no capture interface available"}

    CAPTURE_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    pcap_path = CAPTURE_DIR / f"a2a_task_{timestamp}.pcapng"
    capture_filter = _pcap_capture_filter()
    args = [
        capability["dumpcap"],
        "-q",
        "-i",
        interface,
        "-a",
        f"duration:{max(3, int(max_seconds))}",
        "-f",
        capture_filter,
        "-w",
        str(pcap_path),
    ]

    try:
        proc = subprocess.Popen(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=False,
            **_creationflags_no_window(new_process_group=True),
        )
    except OSError as exc:
        return {"ok": False, "status": "error", "error": _capture_os_error_message(exc), "command": args}

    time.sleep(0.6)
    if proc.poll() is not None:
        stdout, stderr = _communicate_process(proc)
        return {
            "ok": False,
            "status": "error",
            "error": (stderr or stdout or f"dumpcap exited with code {proc.returncode}").strip(),
            "command": args,
            "stdout": stdout,
            "stderr": stderr,
            "returncode": proc.returncode,
        }

    return {
        "ok": True,
        "status": "capturing",
        "process": proc,
        "pcap_path": str(pcap_path),
        "interface": interface,
        "interface_label": _format_capture_interface(interface, capability["interfaces"]),
        "filter": capture_filter,
        "command": args,
        "started_at": time.time(),
        "max_seconds": max_seconds,
        "tshark": capability.get("tshark", ""),
        "capinfos": capability.get("capinfos", ""),
    }


def _communicate_process(proc: subprocess.Popen[str], *, timeout: float = 2.0) -> tuple[str, str]:
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        return "", ""
    return stdout or "", stderr or ""


def refresh_task_packet_capture() -> None:
    capture = st.session_state.get("active_pcap_capture")
    proc = capture.get("process") if isinstance(capture, dict) else None
    if proc is not None and proc.poll() is not None:
        finalize_task_packet_capture(reason="capture_timeout_or_exit")


def finalize_task_packet_capture(*, reason: str) -> dict[str, Any] | None:
    capture = st.session_state.get("active_pcap_capture")
    if not isinstance(capture, dict):
        return None

    proc = capture.get("process")
    if proc is not None and proc.poll() is None:
        try:
            if os.name == "nt":
                proc.send_signal(signal.CTRL_BREAK_EVENT)
            else:
                proc.send_signal(signal.SIGINT)
            proc.wait(timeout=5)
        except Exception:
            proc.kill()
            try:
                proc.wait(timeout=2)
            except Exception:
                pass

    stdout, stderr = _communicate_process(proc) if proc is not None else ("", "")
    capture["stdout"] = stdout
    capture["stderr"] = stderr
    capture["returncode"] = proc.returncode if proc is not None else capture.get("returncode")
    capture["status"] = "complete"
    capture["reason"] = reason
    capture["stopped_at"] = time.time()
    capture.update(_parse_pcap_capture(capture))

    finished = dict(capture)
    finished.pop("process", None)
    st.session_state.last_pcap_capture = finished
    st.session_state.active_pcap_capture = None
    return finished


def _parse_pcap_capture(capture: dict[str, Any]) -> dict[str, Any]:
    pcap_path = Path(str(capture.get("pcap_path", "")))
    if not pcap_path.exists():
        return {"ok": False, "packet_rows": [], "packet_count": 0, "error": "pcap file was not created"}

    parsed: dict[str, Any] = {"ok": True, "pcap_size": pcap_path.stat().st_size}
    capinfos = capture.get("capinfos") or _find_wireshark_tool("capinfos.exe")
    if capinfos:
        try:
            info_result = _run_cli_no_window([capinfos, str(pcap_path)], timeout=10)
            parsed["capinfos"] = ((info_result.stdout or "") + (info_result.stderr or "")).strip()
        except Exception as exc:
            parsed["capinfos_error"] = str(exc)

    tshark = capture.get("tshark") or _find_wireshark_tool("tshark.exe")
    if not tshark:
        parsed.update({"packet_rows": [], "packet_count": 0, "parse_error": "tshark.exe not found"})
        return parsed

    fields = [
        ("frame", "frame.number"),
        ("stream", "tcp.stream"),
        ("time_s", "frame.time_relative"),
        ("frame_len", "frame.len"),
        ("src_ip", "ip.src"),
        ("src_port", "tcp.srcport"),
        ("dst_ip", "ip.dst"),
        ("dst_port", "tcp.dstport"),
        ("flags_hex", "tcp.flags"),
        ("flag_syn", "tcp.flags.syn"),
        ("flag_ack", "tcp.flags.ack"),
        ("flag_psh", "tcp.flags.push"),
        ("flag_fin", "tcp.flags.fin"),
        ("flag_rst", "tcp.flags.reset"),
        ("seq", "tcp.seq"),
        ("ack", "tcp.ack"),
        ("window", "tcp.window_size_value"),
        ("tcp_len", "tcp.len"),
        ("segment_count", "tcp.segment.count"),
        ("reassembled_len", "tcp.reassembled.length"),
        ("retransmission", "tcp.analysis.retransmission"),
        ("lost_segment", "tcp.analysis.lost_segment"),
        ("http_method", "http.request.method"),
        ("http_uri", "http.request.uri"),
        ("http_status", "http.response.code"),
        ("payload_hex", "tcp.payload"),
    ]
    args = [
        tshark,
        "-r",
        str(pcap_path),
        "-o",
        "tcp.desegment_tcp_streams:TRUE",
        "-Y",
        "tcp",
        "-T",
        "fields",
        "-E",
        "separator=\t",
        "-E",
        "occurrence=f",
    ]
    for _, field_name in fields:
        args.extend(["-e", field_name])

    try:
        result = _run_cli_no_window(args, timeout=20)
    except Exception as exc:
        parsed.update({"packet_rows": [], "packet_count": 0, "parse_error": str(exc)})
        return parsed

    rows: list[dict[str, Any]] = []
    keys = [key for key, _ in fields]
    for line in (result.stdout or "").splitlines():
        values = line.split("\t")
        values.extend([""] * (len(keys) - len(values)))
        raw = dict(zip(keys, values[: len(keys)]))
        rows.append(_pcap_row_for_display(raw))

    parsed["packet_rows"] = rows
    parsed["packet_count"] = len(rows)
    parsed["tshark_stderr"] = (result.stderr or "").strip()
    if result.returncode != 0 and not rows:
        parsed["parse_error"] = parsed["tshark_stderr"] or f"tshark exited with code {result.returncode}"
    return parsed


@st.cache_data(ttl=15)
def _latest_capture_path_from_disk() -> str:
    if not CAPTURE_DIR.exists():
        return ""
    pcaps = sorted(
        [item for item in CAPTURE_DIR.glob("*.pcapng") if "_filtered_protocols" not in item.stem],
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )
    return str(pcaps[0]) if pcaps else ""


@st.cache_data(ttl=15)
def _latest_finished_capture_from_disk() -> dict[str, Any] | None:
    pcap_path = _latest_capture_path_from_disk()
    if not pcap_path:
        return None
    capture = {
        "ok": True,
        "status": "complete",
        "reason": "latest_pcap",
        "pcap_path": pcap_path,
        "tshark": _find_wireshark_tool("tshark.exe"),
        "capinfos": _find_wireshark_tool("capinfos.exe"),
    }
    capture.update(_parse_pcap_capture(capture))
    return capture


def _pcap_row_for_display(raw: dict[str, str]) -> dict[str, Any]:
    payload_hex = raw.get("payload_hex", "")
    http = ""
    if raw.get("http_method"):
        http = f"{raw.get('http_method')} {raw.get('http_uri', '')}".strip()
    elif raw.get("http_status"):
        http = f"HTTP {raw.get('http_status')}"

    analysis = []
    if raw.get("retransmission"):
        analysis.append("retransmission")
    if raw.get("lost_segment"):
        analysis.append("lost_segment")
    if raw.get("segment_count"):
        analysis.append(f"segments={raw.get('segment_count')}")
    if raw.get("reassembled_len"):
        analysis.append(f"reassembled={raw.get('reassembled_len')}")

    return {
        "frame": raw.get("frame"),
        "stream": raw.get("stream"),
        "time_s": _short_decimal(raw.get("time_s")),
        "src_port": raw.get("src_port"),
        "dst_port": raw.get("dst_port"),
        "src": _pcap_endpoint_label(raw.get("src_ip", ""), raw.get("src_port", "")),
        "dst": _pcap_endpoint_label(raw.get("dst_ip", ""), raw.get("dst_port", "")),
        "direction": _coordinator_agent_direction(raw.get("src_port", ""), raw.get("dst_port", "")),
        "flags": _tcp_flags_label(raw),
        "seq": raw.get("seq"),
        "ack": raw.get("ack"),
        "win": raw.get("window"),
        "tcp_len": raw.get("tcp_len"),
        "frame_len": raw.get("frame_len"),
        "http": http,
        "analysis": ", ".join(analysis),
        "payload_hex": _hex_preview(payload_hex),
        "payload_hex_full": payload_hex,
        "payload_text": _payload_text_preview(payload_hex),
    }


AGENT_TCP_PORTS = {9010, 9020, 9030, 9040, 9060}
COORDINATOR_CALLBACK_PORT = 9001
COORDINATOR_HTTP_PORT = 9000
REGISTRY_PORTS = {7000, 7001}
GATEWAY_PORT = 8100
MCP_SERVER_PORTS = {8001, 8002, 8003, 8004, 8005}


def _port_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _is_coordinator_agent_tcp_row(row: dict[str, Any]) -> bool:
    src_port = _port_int(row.get("src_port"))
    dst_port = _port_int(row.get("dst_port"))
    if src_port is None or dst_port is None:
        return False
    return (
        src_port in AGENT_TCP_PORTS
        or dst_port in AGENT_TCP_PORTS
        or src_port == COORDINATOR_CALLBACK_PORT
        or dst_port == COORDINATOR_CALLBACK_PORT
    )


def _row_ports(row: dict[str, Any]) -> set[int]:
    return {
        port
        for port in (_port_int(row.get("src_port")), _port_int(row.get("dst_port")))
        if port is not None
    }


def _row_has_any_port(row: dict[str, Any], ports: set[int]) -> bool:
    return bool(_row_ports(row) & ports)


def _row_tcp_len(row: dict[str, Any]) -> int:
    try:
        return int(str(row.get("tcp_len") or "0"))
    except ValueError:
        return 0


def _row_payload_text(row: dict[str, Any]) -> str:
    return str(row.get("payload_text", "") or "")


def _row_contains(row: dict[str, Any], *needles: str) -> bool:
    haystack = " ".join(
        [
            str(row.get("http", "")),
            str(row.get("analysis", "")),
            _row_payload_text(row),
        ]
    ).lower()
    return any(needle.lower() in haystack for needle in needles)


def _is_user_coordinator_http_row(row: dict[str, Any]) -> bool:
    if not _row_has_any_port(row, {COORDINATOR_HTTP_PORT}):
        return False
    http = str(row.get("http", ""))
    return (
        "submit_task" in http
        or _row_contains(row, "submit_task", "task_id", '"question"', "question")
    )


def _is_registry_discover_request_row(row: dict[str, Any]) -> bool:
    if not _row_has_any_port(row, REGISTRY_PORTS):
        return False
    return _row_contains(row, "discover")


def _registry_discover_rows(rows: list[dict[str, Any]], *, limit: int = 16) -> list[dict[str, Any]]:
    discover_streams: set[int] = set()
    discover_frames: set[str] = set()
    for row in rows:
        if not _is_registry_discover_request_row(row):
            continue
        stream = row.get("stream")
        try:
            discover_streams.add(int(str(stream)))
        except (TypeError, ValueError):
            frame = str(row.get("frame") or "")
            if frame:
                discover_frames.add(frame)

    selected: list[dict[str, Any]] = []
    for row in rows:
        if not _row_has_any_port(row, REGISTRY_PORTS):
            continue
        stream = row.get("stream")
        frame = str(row.get("frame") or "")
        try:
            stream_match = int(str(stream)) in discover_streams
        except (TypeError, ValueError):
            stream_match = False
        if stream_match or frame in discover_frames:
            selected.append(row)
            if len(selected) >= limit:
                break
    return selected


def _is_agent_gateway_jsonrpc_row(row: dict[str, Any]) -> bool:
    if not _row_has_any_port(row, {GATEWAY_PORT}):
        return False
    return bool(row.get("http")) or _row_contains(row, "jsonrpc", "method", "params", "result", "error", "circuit_open", "-32001")


def _is_gateway_mcp_jsonrpc_row(row: dict[str, Any]) -> bool:
    if not _row_has_any_port(row, MCP_SERVER_PORTS):
        return False
    return bool(row.get("http")) or _row_contains(row, "jsonrpc", "method", "params", "result", "error")


def _is_failure_pcap_row(row: dict[str, Any]) -> bool:
    flags = {part.strip().upper() for part in str(row.get("flags", "")).split(",") if part.strip()}
    if "RST" in flags:
        return True
    if _row_contains(row, "retransmission", "lost_segment", "circuit_open", "-32001", '"error"', "error"):
        return True
    http = str(row.get("http", ""))
    if http.startswith("HTTP "):
        try:
            return int(http.split(maxsplit=1)[1]) >= 400
        except (IndexError, ValueError):
            return False
    return False


def _a2a_message_type(row: dict[str, Any]) -> str:
    for message_type in ("TASK_REQUEST", "TASK_ACK", "TASK_RESULT", "RESULT_ACK"):
        if _row_contains(row, message_type):
            return message_type
    return ""


def _pcap_group_rows(rows: list[dict[str, Any]], predicate, *, limit: int = 16) -> list[dict[str, Any]]:
    return [row for row in rows if predicate(row)][:limit]


def _coordinator_agent_direction(src_port_value: Any, dst_port_value: Any) -> str:
    src_port = _port_int(src_port_value)
    dst_port = _port_int(dst_port_value)
    if dst_port in AGENT_TCP_PORTS:
        return f"Coordinator -> {_service_label_for_port(str(dst_port)).strip('()')}"
    if src_port in AGENT_TCP_PORTS:
        return f"{_service_label_for_port(str(src_port)).strip('()')} -> Coordinator"
    if dst_port == COORDINATOR_CALLBACK_PORT:
        return "Agent callback -> Coordinator"
    if src_port == COORDINATOR_CALLBACK_PORT:
        return "Coordinator -> Agent ACK"
    return ""


def _pcap_endpoint_label(ip: str, port: str) -> str:
    label = _service_label_for_port(port)
    endpoint = f"{ip}:{port}" if ip or port else ""
    return f"{endpoint} {label}".strip()


def _tcp_flags_label(raw: dict[str, str]) -> str:
    names = []
    flag_fields = [
        ("SYN", "flag_syn"),
        ("ACK", "flag_ack"),
        ("PSH", "flag_psh"),
        ("FIN", "flag_fin"),
        ("RST", "flag_rst"),
    ]
    for label, key in flag_fields:
        value = str(raw.get(key, "")).strip()
        if value in {"1", "True", "true"}:
            names.append(label)
    if names:
        return ",".join(names)
    return raw.get("flags_hex", "")


def _service_label_for_port(port: str) -> str:
    try:
        port_int = int(port)
    except (TypeError, ValueError):
        return ""
    for service_name, ports in SERVICE_PORTS.items():
        if port_int in ports:
            meta = SERVICE_META.get(service_name, {})
            return f"({meta.get('label', service_name)})"
    return ""


def _hex_preview(value: str, max_chars: int = 96) -> str:
    if not value:
        return ""
    return value if len(value) <= max_chars else value[:max_chars] + "..."


def _payload_text_preview(value: str, max_chars: int = 120) -> str:
    payload = str(value or "").replace(":", "").strip()
    if not payload:
        return ""
    try:
        text = bytes.fromhex(payload).decode("utf-8", errors="replace")
    except ValueError:
        return ""
    text = "".join(ch if ch in "\r\n\t" or ord(ch) >= 32 else "." for ch in text)
    text = " ".join(text.split())
    return text if len(text) <= max_chars else text[:max_chars] + "..."


def _short_decimal(value: str) -> str:
    if not value:
        return ""
    try:
        return f"{float(value):.6f}"
    except (TypeError, ValueError):
        return value


def _payload_hex_clean(row: dict[str, Any]) -> str:
    return str(row.get("payload_hex_full") or row.get("payload_hex") or "").replace(":", "").strip()


def _payload_byte_len(row: dict[str, Any]) -> int:
    payload = _payload_hex_clean(row)
    return len(payload) // 2 if payload else 0


def _a2a_frame_total_bytes(row: dict[str, Any]) -> int | None:
    payload = _payload_hex_clean(row)
    if len(payload) < 10:
        return None
    try:
        body_len = int(payload[:8], 16)
    except ValueError:
        return None
    if body_len <= 0 or body_len > 2_000_000:
        return None
    if payload[8:10].lower() != "7b":
        return None
    return body_len + 4


def _direction_flow_key(row: dict[str, Any]) -> tuple[str, str]:
    return (str(row.get("src", "")), str(row.get("dst", "")))


def _find_fragmented_a2a_example(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    for index, row in enumerate(rows):
        expected_total = _a2a_frame_total_bytes(row)
        first_payload_len = _payload_byte_len(row)
        if expected_total is None or first_payload_len <= 0 or first_payload_len >= expected_total:
            continue

        flow_key = _direction_flow_key(row)
        parts = [row]
        captured_total = first_payload_len
        for next_row in rows[index + 1 :]:
            if _direction_flow_key(next_row) != flow_key:
                continue
            next_payload_len = _payload_byte_len(next_row)
            if next_payload_len <= 0:
                continue
            parts.append(next_row)
            captured_total += next_payload_len
            if captured_total >= expected_total:
                return {
                    "expected_total": expected_total,
                    "captured_total": captured_total,
                    "rows": parts[:6],
                }
        if len(parts) > 1:
            return {"expected_total": expected_total, "captured_total": captured_total, "rows": parts[:6]}
    return None


def _find_single_frame_a2a_example(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    for row in rows:
        expected_total = _a2a_frame_total_bytes(row)
        payload_len = _payload_byte_len(row)
        if expected_total is not None and payload_len == expected_total:
            return {"expected_total": expected_total, "captured_total": payload_len, "rows": [row]}
    return None


def _has_tcp_flag(row: dict[str, Any], flag: str) -> bool:
    flags = {part.strip().upper() for part in str(row.get("flags", "")).split(",") if part.strip()}
    return flag.upper() in flags


def _is_zero_payload_ack(row: dict[str, Any]) -> bool:
    try:
        tcp_len = int(str(row.get("tcp_len") or "0"))
    except ValueError:
        return False
    return tcp_len == 0 and _has_tcp_flag(row, "ACK") and not _has_tcp_flag(row, "SYN")


def _find_three_way_handshake_example(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    for index, syn in enumerate(rows):
        if not _has_tcp_flag(syn, "SYN") or _has_tcp_flag(syn, "ACK"):
            continue

        client = str(syn.get("src", ""))
        server = str(syn.get("dst", ""))
        syn_ack = None
        final_ack = None

        for candidate in rows[index + 1 : index + 12]:
            if str(candidate.get("src", "")) == server and str(candidate.get("dst", "")) == client:
                if _has_tcp_flag(candidate, "SYN") and _has_tcp_flag(candidate, "ACK"):
                    syn_ack = candidate
                    break
        if syn_ack is None:
            continue

        for candidate in rows[index + 1 : index + 16]:
            if str(candidate.get("src", "")) == client and str(candidate.get("dst", "")) == server:
                if _is_zero_payload_ack(candidate):
                    final_ack = candidate
                    break
        if final_ack is None:
            continue

        return {"rows": [syn, syn_ack, final_ack]}
    return None


def _brief_pcap_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    brief_rows = []
    for row in rows:
        brief_rows.append(
            {
                "frame": row.get("frame"),
                "stream": row.get("stream"),
                "time_s": row.get("time_s"),
                "direction": row.get("direction"),
                "src": row.get("src"),
                "dst": row.get("dst"),
                "flags": row.get("flags"),
                "seq": row.get("seq"),
                "ack": row.get("ack"),
                "tcp_len": row.get("tcp_len"),
                "http": row.get("http"),
                "analysis": row.get("analysis"),
                "message": _a2a_message_type(row),
                "payload_text": row.get("payload_text"),
                "payload_head": row.get("payload_hex"),
            }
        )
    return brief_rows


PCAP_TABLE_COLUMNS = [
    ("frame", "frame"),
    ("stream", "stream"),
    ("time_s", "time_s"),
    ("direction", "direction"),
    ("src", "src"),
    ("dst", "dst"),
    ("flags", "flags"),
    ("seq", "seq"),
    ("ack", "ack"),
    ("tcp_len", "tcp_len"),
    ("http", "http"),
    ("analysis", "analysis"),
    ("message", "message"),
    ("payload_text", "payload_text"),
    ("payload_head", "payload_head"),
]


def _render_pcap_rows_table(rows: list[dict[str, Any]]) -> None:
    brief_rows = _brief_pcap_rows(rows)
    headers = "".join(f"<th>{escape(label)}</th>" for _, label in PCAP_TABLE_COLUMNS)
    body_rows = []
    for row in brief_rows:
        cells = []
        for key, _ in PCAP_TABLE_COLUMNS:
            value = "" if row.get(key) is None else str(row.get(key))
            css_class = "pcap-wide-cell" if key in {"src", "dst", "analysis", "payload_text", "payload_head"} else ""
            cells.append(f'<td class="{css_class}" title="{escape(value, quote=True)}">{escape(value)}</td>')
        body_rows.append(f"<tr>{''.join(cells)}</tr>")

    st.markdown(
        f"""
        <style>
        .pcap-table-wrap {{
            width: 100%;
            margin: 8px 0 18px;
            overflow-x: auto;
            overflow-y: visible;
            border: 1px solid rgba(148, 163, 184, .28);
            border-radius: 8px;
        }}
        .pcap-table {{
            width: max-content;
            min-width: 100%;
            border-collapse: collapse;
            font-size: 12px;
            line-height: 1.35;
        }}
        .pcap-table th,
        .pcap-table td {{
            max-width: 180px;
            padding: 7px 9px;
            border-bottom: 1px solid rgba(148, 163, 184, .18);
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
            vertical-align: top;
        }}
        .pcap-table th {{
            position: sticky;
            top: 0;
            z-index: 1;
            background: #161b24;
            color: #e5edf8;
            font-weight: 700;
            text-align: left;
        }}
        .pcap-table td {{
            color: rgba(226, 232, 240, .94);
            background: rgba(15, 23, 42, .18);
        }}
        .pcap-table tr:last-child td {{
            border-bottom: 0;
        }}
        .pcap-table .pcap-wide-cell {{
            max-width: 280px;
        }}
        </style>
        <div class="pcap-table-wrap">
          <table class="pcap-table">
            <thead><tr>{headers}</tr></thead>
            <tbody>{''.join(body_rows)}</tbody>
          </table>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _example_caption(example: dict[str, Any]) -> str:
    rows = example.get("rows", [])
    frame_ids = ", ".join(str(row.get("frame")) for row in rows)
    return (
        f"frames: {frame_ids}; "
        f"A2A frame bytes: {example.get('captured_total')} / {example.get('expected_total')}"
    )


def _pcap_demo_groups(all_rows: list[dict[str, Any]], a2a_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    handshake_example = _find_three_way_handshake_example(a2a_rows)
    fragment_example = _find_fragmented_a2a_example(a2a_rows)
    single_frame_example = _find_single_frame_a2a_example(a2a_rows)

    groups: list[dict[str, Any]] = [
        {
            "title": "User -> Coordinator: HTTP POST /submit_task",
            "rows": _pcap_group_rows(all_rows, _is_user_coordinator_http_row),
            "empty": "本次 pcap 没有抓到 User -> Coordinator 的 HTTP submit_task。",
        },
        {
            "title": "Coordinator -> Registry: HTTP/REST GET /discover",
            "rows": _registry_discover_rows(all_rows),
            "empty": "本次 pcap 没有抓到注册中心 discover 报文。",
        },
        {
            "title": "Coordinator -> Agent: TCP 三次握手",
            "rows": handshake_example["rows"] if handshake_example else [],
            "caption": "SYN -> SYN/ACK -> ACK",
            "empty": "本次 pcap 没有发现 Coordinator 与 Agent 之间完整的三次握手。",
        },
        {
            "title": "Coordinator -> Agent: A2A TCP 拆分/重组 frame",
            "rows": fragment_example["rows"] if fragment_example else [],
            "caption": _example_caption(fragment_example) if fragment_example else "",
            "empty": "本次 pcap 没有发现一个 A2A frame 被拆成多个 TCP frame 的例子。",
        },
        {
            "title": "Coordinator -> Agent: A2A TCP 独立完整 frame",
            "rows": single_frame_example["rows"] if single_frame_example else [],
            "caption": _example_caption(single_frame_example) if single_frame_example else "",
            "empty": "本次 pcap 没有发现完整 A2A frame 单独落在一个 TCP frame 内的例子。",
        },
        {
            "title": "Agent -> Gateway: HTTP/JSON-RPC",
            "rows": _pcap_group_rows(all_rows, _is_agent_gateway_jsonrpc_row),
            "empty": "本次 pcap 没有抓到 Agent -> Gateway 的 JSON-RPC 报文。",
        },
        {
            "title": "Gateway -> MCP: HTTP/JSON-RPC",
            "rows": _pcap_group_rows(all_rows, _is_gateway_mcp_jsonrpc_row),
            "empty": "本次 pcap 没有抓到 Gateway -> MCP 的 JSON-RPC 报文。",
        },
    ]
    return groups


def _group_frame_numbers(groups: list[dict[str, Any]]) -> list[int]:
    frame_numbers: set[int] = set()
    for group in groups:
        for row in group.get("rows", []):
            try:
                frame_numbers.add(int(str(row.get("frame"))))
            except (TypeError, ValueError):
                continue
    return sorted(frame_numbers)


def _group_tcp_streams(groups: list[dict[str, Any]]) -> list[int]:
    streams: set[int] = set()
    for group in groups:
        for row in group.get("rows", []):
            try:
                streams.add(int(str(row.get("stream"))))
            except (TypeError, ValueError):
                continue
    return sorted(streams)


def _pcap_demo_filter(streams: list[int], frame_numbers: list[int]) -> str:
    parts = []
    if streams:
        parts.append("tcp.stream in {" + ",".join(str(stream) for stream in streams) + "}")
    if frame_numbers:
        parts.append("frame.number in {" + ",".join(str(number) for number in frame_numbers) + "}")
    return " || ".join(parts)


def _filtered_pcap_download(capture: dict[str, Any], groups: list[dict[str, Any]]) -> dict[str, Any]:
    pcap_path = Path(str(capture.get("pcap_path", "")))
    if not pcap_path.exists():
        return {"ok": False, "error": "pcap file not found"}
    streams = _group_tcp_streams(groups)
    frame_numbers = _group_frame_numbers(groups)
    if not streams and not frame_numbers:
        return {"ok": False, "error": "没有可筛选的真实抓包 frame"}

    tshark = capture.get("tshark") or _find_wireshark_tool("tshark.exe")
    if not tshark:
        return {"ok": False, "error": "tshark.exe not found"}

    filtered_path = pcap_path.with_name(f"{pcap_path.stem}_filtered_protocols.pcapng")
    source_mtime = pcap_path.stat().st_mtime
    if filtered_path.exists() and filtered_path.stat().st_mtime >= source_mtime:
        return {
            "ok": True,
            "path": filtered_path,
            "frame_count": len(frame_numbers),
            "stream_count": len(streams),
            "filter": _pcap_demo_filter(streams, frame_numbers),
        }

    filter_expr = _pcap_demo_filter(streams, frame_numbers)
    try:
        result = _run_cli_no_window(
            [tshark, "-r", str(pcap_path), "-Y", filter_expr, "-w", str(filtered_path)],
            timeout=30,
        )
    except Exception as exc:
        return {"ok": False, "error": str(exc), "filter": filter_expr}

    if result.returncode != 0 or not filtered_path.exists():
        return {
            "ok": False,
            "error": ((result.stderr or "") + (result.stdout or "")).strip() or f"tshark exited with code {result.returncode}",
            "filter": filter_expr,
        }
    return {
        "ok": True,
        "path": filtered_path,
        "frame_count": len(frame_numbers),
        "stream_count": len(streams),
        "filter": filter_expr,
    }


def _render_pcap_group(group: dict[str, Any]) -> None:
    st.markdown(f"#### {group['title']}")
    caption = group.get("caption")
    if caption:
        st.caption(str(caption))
    rows = group.get("rows", [])
    if rows:
        _render_pcap_rows_table(rows)
    else:
        st.warning(str(group.get("empty", "本次 pcap 没有该分组报文。")))


def render_packet_capture_settings() -> None:
    capability = _packet_capture_capability()
    interfaces = capability.get("interfaces", [])
    st.checkbox(
        "提交任务时同步真实抓包",
        value=bool(capability.get("ok")),
        key="pcap_capture_enabled",
        disabled=not capability.get("ok"),
    )
    if not capability.get("ok"):
        st.warning(capability.get("error") or "未检测到可用抓包工具。")
        return

    interface_names = [item["name"] for item in interfaces]
    default_interface = capability.get("default_interface") or (interface_names[0] if interface_names else "")
    default_index = interface_names.index(default_interface) if default_interface in interface_names else 0
    st.selectbox(
        "抓包接口",
        interface_names,
        index=default_index,
        key="pcap_capture_interface",
        format_func=lambda value: _format_capture_interface(value, interfaces),
    )
    st.text_input("BPF Filter", value=_pcap_capture_filter(), disabled=True)
    st.caption(f"dumpcap: {capability.get('dumpcap')}")


def render_task_packet_capture_panel() -> None:
    refresh_task_packet_capture()
    active = st.session_state.get("active_pcap_capture")
    capture = active if isinstance(active, dict) else st.session_state.get("last_pcap_capture")
    if not isinstance(capture, dict):
        capture = _latest_finished_capture_from_disk()

    if not isinstance(capture, dict):
        st.info("提交任务后会显示本次任务同步抓到的 pcap frame。")
        return

    if capture.get("status") == "capturing":
        elapsed = time.time() - float(capture.get("started_at", time.time()))
        st.info(f"正在抓包：{elapsed:.1f}s，接口 {capture.get('interface_label', capture.get('interface', ''))}")
        st.code(str(capture.get("pcap_path", "")), language="text")
        return

    if not capture.get("ok", True):
        st.error(capture.get("error") or "抓包失败")
        return

    all_rows = list(capture.get("packet_rows", []))
    rows = [row for row in all_rows if _is_coordinator_agent_tcp_row(row)]
    groups = _pcap_demo_groups(all_rows, rows)
    filtered_download = _filtered_pcap_download(capture, groups)

    metric_cols = st.columns(3)
    metric_cols[0].metric("Coordinator-Agent TCP", str(len(rows)))
    metric_cols[1].metric("pcap Size", f"{int(capture.get('pcap_size', 0))} B")
    metric_cols[2].metric("Reason", str(capture.get("reason", "")))

    for group in groups:
        _render_pcap_group(group)

    if not rows:
        st.warning("pcap 中没有解析到 Coordinator 与 Agent 之间的 TCP frame。")

    if filtered_download.get("ok"):
        filtered_path = Path(str(filtered_download["path"]))
        st.caption(
            f"下载文件已按上面分组筛选：{filtered_download.get('stream_count', 0)} TCP streams，"
            f"{filtered_download.get('frame_count', 0)} seed frames"
        )
        st.download_button(
            "下载筛选后的 pcapng",
            data=filtered_path.read_bytes(),
            file_name=filtered_path.name,
            mime="application/vnd.tcpdump.pcap",
            width="stretch",
        )
    else:
        st.warning(f"筛选 pcapng 失败：{filtered_download.get('error', 'unknown error')}")
        pcap_path = Path(str(capture.get("pcap_path", "")))
        if pcap_path.exists():
            st.download_button(
                "下载原始 pcapng",
                data=pcap_path.read_bytes(),
                file_name=pcap_path.name,
                mime="application/vnd.tcpdump.pcap",
                width="stretch",
            )

    if capture.get("parse_error"):
        st.error(capture["parse_error"])


def has_task_packet_capture() -> bool:
    refresh_task_packet_capture()
    return isinstance(st.session_state.get("active_pcap_capture"), dict) or isinstance(
        st.session_state.get("last_pcap_capture"), dict
    ) or bool(_latest_capture_path_from_disk())


def clear_task_display_state() -> None:
    if isinstance(st.session_state.get("active_pcap_capture"), dict):
        finalize_task_packet_capture(reason="new_task_submit")
    for key in (
        "current_task_id",
        "task_start_time",
        "last_recent_network_events",
        "selected_packet_event_key",
        "selected_packet_event_index",
        "active_pcap_capture",
        "last_pcap_capture",
    ):
        st.session_state.pop(key, None)
    st.session_state.task_transfer_active = False
    st.session_state.task_highlight_cleared_for = None
    st.session_state.topology_edge_pulses = {}
    st.session_state.topology_failed_edge_pulses = {}
    st.session_state.topology_node_pulses = {}
    st.session_state.topology_seen_event_keys = []


def _gateway_demo_cases() -> dict[str, dict[str, Any]]:
    return {
        "Weather MCP / get_weather": {
            "method": "get_weather",
            "params": {"city": "北京", "date": "未指定", "days": 5},
        },
        "Traffic MCP / get_route": {
            "method": "get_route",
            "params": {
                "city": "北京",
                "origin": "王府井青年旅舍",
                "destination": "故宫",
                "preference": "public_transport",
            },
        },
        "Traffic MCP / get_intercity_transport": {
            "method": "get_intercity_transport",
            "params": {
                "origin_city": "北京",
                "destination_city": "广州",
                "budget_level": "high",
                "transport_preference": "taxi",
            },
        },
        "Attraction MCP / search_attractions": {
            "method": "search_attractions",
            "params": {"city": "北京", "days": 3, "budget_level": "low", "must_visit": ["故宫"]},
        },
        "Hotel MCP / search_hotels": {
            "method": "search_hotels",
            "params": {"city": "北京", "target_area": "天安门-故宫区域", "budget_level": "low"},
        },
        "Packing MCP / get_packing_list": {
            "method": "get_packing_list",
            "params": {"city": "北京", "days": 3, "temperature": "15°C", "condition": "晴"},
        },
    }


GATEWAY_DEMO_METHOD_SERVICE = {
    "get_weather": "weather_mcp_server",
    "get_route": "traffic_mcp_server",
    "get_routes": "traffic_mcp_server",
    "get_intercity_transport": "traffic_mcp_server",
    "search_attractions": "attraction_mcp_server",
    "search_hotels": "hotel_mcp_server",
    "get_packing_list": "packing_mcp_server",
}


def _gateway_response_result(response: Any) -> dict[str, Any] | None:
    if not isinstance(response, dict):
        return None
    result = response.get("result")
    return result if isinstance(result, dict) else None


def _data_source_row(path: str, source: dict[str, Any]) -> dict[str, str]:
    return {
        "位置": path,
        "Provider": _provider_label(source.get("provider")),
        "实时": "是" if source.get("realtime") else "否",
        "数据状态": "实时数据" if source.get("realtime") else "本地数据",
        "说明": _display_value(source.get("fallback_reason") or source.get("forecast_unavailable_reason")),
        "缺失/补齐字段": _display_value(source.get("missing_fields") or source.get("field_sources")),
    }


def _legacy_source_row(path: str, payload: dict[str, Any]) -> dict[str, str]:
    realtime_used = bool(payload.get("fallback_used"))
    provider = "高德距离推算" if realtime_used else "本地距离表"
    return {
        "位置": path,
        "Provider": provider,
        "实时": "是" if realtime_used else "否",
        "数据状态": "实时数据" if realtime_used else "本地数据",
        "说明": _display_value(payload.get("fallback_reason") or payload.get("cost_note")),
        "缺失/补齐字段": "-",
    }


def _collect_data_source_rows(payload: Any, path: str = "result", rows: list[dict[str, str]] | None = None) -> list[dict[str, str]]:
    if rows is None:
        rows = []
    if len(rows) >= 16:
        return rows

    if isinstance(payload, dict):
        source = payload.get("data_source")
        if isinstance(source, dict):
            rows.append(_data_source_row(path, source))
        elif "fallback_used" in payload and any(key in payload for key in ("recommended_option", "alternatives", "cost_note")):
            rows.append(_legacy_source_row(path, payload))

        for key, value in payload.items():
            if key == "data_source" or not isinstance(value, (dict, list)):
                continue
            _collect_data_source_rows(value, f"{path}.{key}", rows)
            if len(rows) >= 16:
                break
    elif isinstance(payload, list):
        for index, item in enumerate(payload[:8]):
            _collect_data_source_rows(item, f"{path}[{index}]", rows)
            if len(rows) >= 16:
                break
    return rows


def _gateway_data_source_rows(response: Any) -> list[dict[str, str]]:
    result = _gateway_response_result(response)
    if result is None:
        return []
    return _collect_data_source_rows(result)


def run_gateway_demo(method: str, params: dict[str, Any]) -> dict[str, Any]:
    request_payload = {
        "jsonrpc": "2.0",
        "method": method,
        "params": params,
        "id": f"ui-gateway-demo-{int(time.time() * 1000)}",
    }
    started = time.time()
    result: dict[str, Any] = {
        "gateway": MCP_GATEWAY_URL,
        "request": request_payload,
        "ok": False,
    }
    try:
        health_resp = requests.get(f"{MCP_GATEWAY_URL}/health", timeout=3)
        result["health_status"] = health_resp.status_code
        result["health"] = health_resp.json()

        methods_resp = requests.get(f"{MCP_GATEWAY_URL}/methods", timeout=3)
        result["methods_status"] = methods_resp.status_code
        result["methods"] = methods_resp.json()

        request_timeout = max(12.0, _gateway_upstream_timeout_seconds() + 2.0)
        response = requests.post(MCP_GATEWAY_URL, json=request_payload, timeout=request_timeout)
        result["response_status"] = response.status_code
        result["response"] = response.json()

        metrics_resp = requests.get(f"{MCP_GATEWAY_URL}/metrics", timeout=3)
        result["metrics_status"] = metrics_resp.status_code
        result["metrics"] = metrics_resp.json()

        result["ok"] = response.ok and "result" in result["response"]
    except Exception as exc:
        result["error"] = str(exc)
    result["elapsed_ms"] = round((time.time() - started) * 1000, 2)
    return result


def _gateway_metrics_snapshot() -> dict[str, Any]:
    response = requests.get(f"{MCP_GATEWAY_URL}/metrics", timeout=4)
    response.raise_for_status()
    data = response.json()
    return data.get("metrics", data)


def _gateway_cache_snapshot() -> dict[str, Any]:
    response = requests.get(f"{MCP_GATEWAY_URL}/cache", timeout=4)
    response.raise_for_status()
    data = response.json()
    return data.get("cache", data)


def _gateway_cache_clear(method: str | None = None, key: str | None = None) -> dict[str, Any]:
    payload = {}
    if method:
        payload["method"] = method
    if key:
        payload["key"] = key
    response = requests.post(f"{MCP_GATEWAY_URL}/cache/clear", json=payload, timeout=4)
    response.raise_for_status()
    return response.json()


def _gateway_cache_policy_rows() -> list[dict[str, Any]]:
    per_method_ttl = DEFAULT_MCP_GATEWAY_CONFIG.get("per_method_ttl_seconds", {})
    rows = [
        {
            "method": method,
            "ttl": _format_seconds(float(ttl)),
            "说明": "按方法覆盖 TTL",
        }
        for method, ttl in sorted(per_method_ttl.items())
    ]
    rows.append(
        {
            "method": "default",
            "ttl": _format_seconds(float(DEFAULT_MCP_GATEWAY_CONFIG.get("cache_ttl_seconds", 0))),
            "说明": "未配置方法的默认 TTL",
        }
    )
    return rows


def _gateway_cache_method_rows(cache: dict[str, Any]) -> list[dict[str, Any]]:
    method_counts = cache.get("method_counts", {})
    if not isinstance(method_counts, dict):
        method_counts = {}
    per_method_ttl = DEFAULT_MCP_GATEWAY_CONFIG.get("per_method_ttl_seconds", {})
    methods = sorted(set(method_counts) | set(per_method_ttl))
    return [
        {
            "method": method,
            "entries": int(method_counts.get(method, 0) or 0),
            "ttl": _format_seconds(float(per_method_ttl.get(method, DEFAULT_MCP_GATEWAY_CONFIG.get("cache_ttl_seconds", 0)))),
        }
        for method in methods
    ]


def _gateway_cache_entry_rows(cache: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    entries = cache.get("entries", [])
    if not isinstance(entries, list):
        return rows
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        params = entry.get("semantic_params", {})
        try:
            params_preview = json.dumps(params, ensure_ascii=False, sort_keys=True)
        except (TypeError, ValueError):
            params_preview = str(params)
        if len(params_preview) > 140:
            params_preview = params_preview[:137] + "..."
        rows.append(
            {
                "method": entry.get("method"),
                "key_hash": entry.get("key_hash"),
                "ttl_left_s": round(float(entry.get("ttl_remaining_ms", 0) or 0) / 1000, 2),
                "age_s": round(float(entry.get("age_ms", 0) or 0) / 1000, 2),
                "hits": int(entry.get("hit_count", 0) or 0),
                "bytes": int(entry.get("payload_size_bytes", 0) or 0),
                "params": params_preview,
                "key": entry.get("key"),
            }
        )
    return rows


def _format_seconds(value: float) -> str:
    if value >= 86400 and value % 86400 == 0:
        return f"{int(value / 86400)}d"
    if value >= 3600 and value % 3600 == 0:
        return f"{int(value / 3600)}h"
    if value >= 60 and value % 60 == 0:
        return f"{int(value / 60)}min"
    return f"{value:g}s"


def _numeric_metric(metrics: dict[str, Any], key: str) -> float:
    value = metrics.get(key, 0)
    return float(value) if isinstance(value, (int, float)) else 0.0


def _method_metric(metrics: dict[str, Any], method: str, key: str) -> float:
    method_stats = metrics.get("method_stats", {})
    if not isinstance(method_stats, dict):
        return 0.0
    stats = method_stats.get(method, {})
    if not isinstance(stats, dict):
        return 0.0
    value = stats.get(key, 0)
    return float(value) if isinstance(value, (int, float)) else 0.0


def _gateway_metric_delta_rows(before: dict[str, Any], after: dict[str, Any], method: str) -> list[dict[str, Any]]:
    metric_pairs = [
        ("requests", "total_requests", "requests"),
        ("upstream_calls", "upstream_calls", "upstream_calls"),
        ("cache_hits", "cache_hits", "cache_hits"),
        ("cache_misses", "cache_misses", "cache_misses"),
        ("coalesced_requests", "coalesced_requests", "coalesced_requests"),
        ("rate_limited", "rate_limited", "rate_limited"),
        ("circuit_open", "circuit_open", "circuit_open"),
        ("errors", "error_count", "error_count"),
    ]
    rows = []
    for label, global_key, method_key in metric_pairs:
        global_before = _numeric_metric(before, global_key)
        global_after = _numeric_metric(after, global_key)
        method_before = _method_metric(before, method, method_key)
        method_after = _method_metric(after, method, method_key)
        rows.append(
            {
                "metric": label,
                "global_delta": int(global_after - global_before),
                "method_delta": int(method_after - method_before),
                "method_after": int(method_after),
            }
        )
    rows.append(
        {
            "metric": "cache_size",
            "global_delta": int(_numeric_metric(after, "cache_size") - _numeric_metric(before, "cache_size")),
            "method_delta": 0,
            "method_after": 0,
        }
    )
    return rows


def _wait_for_gateway_breaker_retry_window(method: str, *, max_wait_seconds: float = 12.0) -> None:
    try:
        metrics = _gateway_metrics_snapshot()
    except Exception:
        return
    breakers = metrics.get("circuit_breakers", {})
    breaker = breakers.get(method, {}) if isinstance(breakers, dict) else {}
    if not isinstance(breaker, dict) or breaker.get("state") != "open":
        return
    retry_after_ms = breaker.get("retry_after_ms", 0.0)
    try:
        retry_after_seconds = float(retry_after_ms) / 1000.0
    except (TypeError, ValueError):
        retry_after_seconds = 0.0
    if retry_after_seconds > 0:
        time.sleep(min(max_wait_seconds, retry_after_seconds + 0.25))


def _gateway_jsonrpc_call(method: str, params: dict[str, Any], request_id: str, *, timeout: float | None = None) -> dict[str, Any]:
    payload = {"jsonrpc": "2.0", "method": method, "params": params, "id": request_id}
    started = time.perf_counter()
    request_timeout = timeout if timeout is not None else max(12.0, _gateway_upstream_timeout_seconds() + 2.0)
    try:
        response = requests.post(MCP_GATEWAY_URL, json=payload, timeout=request_timeout)
        elapsed_ms = (time.perf_counter() - started) * 1000
        try:
            body = response.json()
        except ValueError:
            body = {"raw": response.text}
        error_body = body.get("error") if isinstance(body, dict) else None
        return {
            "ok": response.ok and isinstance(body, dict) and "result" in body,
            "status_code": response.status_code,
            "elapsed_ms": round(elapsed_ms, 2),
            "error_code": error_body.get("code") if isinstance(error_body, dict) else "",
            "error_message": error_body.get("message") if isinstance(error_body, dict) else "",
            "response": body,
        }
    except Exception as exc:
        elapsed_ms = (time.perf_counter() - started) * 1000
        return {
            "ok": False,
            "status_code": "",
            "elapsed_ms": round(elapsed_ms, 2),
            "error_code": "client_error",
            "error_message": str(exc),
            "response": {},
        }


def _run_gateway_single_scenario(
    *,
    label: str,
    method: str,
    params: dict[str, Any],
    request_id: str,
) -> dict[str, Any]:
    before = _gateway_metrics_snapshot()
    started = time.perf_counter()
    response = _gateway_jsonrpc_call(method, params, request_id)
    after = _gateway_metrics_snapshot()
    elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
    return {
        "label": label,
        "request_count": 1,
        "ok_count": 1 if response.get("ok") else 0,
        "error_count": 0 if response.get("ok") else 1,
        "elapsed_ms": elapsed_ms,
        "avg_ms": response.get("elapsed_ms", elapsed_ms),
        "p95_ms": response.get("elapsed_ms", elapsed_ms),
        "max_ms": response.get("elapsed_ms", elapsed_ms),
        "metric_delta": _gateway_metric_delta_rows(before, after, method),
        "sample_errors": [
            {
                "status_code": response.get("status_code"),
                "error_code": response.get("error_code"),
                "error_message": response.get("error_message"),
            }
        ]
        if not response.get("ok")
        else [],
    }


def _latency_summary(responses: list[dict[str, Any]]) -> dict[str, Any]:
    latencies = sorted(float(item.get("elapsed_ms", 0.0) or 0.0) for item in responses)
    if not latencies:
        return {"avg_ms": 0.0, "p95_ms": 0.0, "max_ms": 0.0}
    p95_index = min(len(latencies) - 1, int(len(latencies) * 0.95))
    return {
        "avg_ms": round(sum(latencies) / len(latencies), 2),
        "p95_ms": round(latencies[p95_index], 2),
        "max_ms": round(latencies[-1], 2),
    }


def _run_gateway_concurrent_scenario(
    *,
    label: str,
    method: str,
    params_factory,
    request_count: int,
    max_workers: int,
    request_id_prefix: str,
) -> dict[str, Any]:
    before = _gateway_metrics_snapshot()
    started = time.perf_counter()
    responses: list[dict[str, Any]] = []
    workers = max(1, min(max_workers, request_count))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [
            executor.submit(
                _gateway_jsonrpc_call,
                method,
                params_factory(index),
                f"{request_id_prefix}-{index}",
            )
            for index in range(request_count)
        ]
        for future in as_completed(futures):
            responses.append(future.result())
    after = _gateway_metrics_snapshot()
    elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
    latencies = _latency_summary(responses)
    return {
        "label": label,
        "request_count": request_count,
        "ok_count": sum(1 for item in responses if item.get("ok")),
        "error_count": sum(1 for item in responses if not item.get("ok")),
        "elapsed_ms": elapsed_ms,
        **latencies,
        "metric_delta": _gateway_metric_delta_rows(before, after, method),
        "sample_errors": [
            {
                "status_code": item.get("status_code"),
                "error_code": item.get("error_code"),
                "error_message": item.get("error_message"),
            }
            for item in responses
            if not item.get("ok")
        ][:3],
    }


def _run_gateway_cache_scenario(method: str, base_params: dict[str, Any], request_count: int, max_workers: int, nonce: str) -> dict[str, Any]:
    params = {**base_params, "_demo_nonce": f"cache-{nonce}"}
    before = _gateway_metrics_snapshot()
    warm = _gateway_jsonrpc_call(method, params, f"cache-warm-{nonce}")
    started = time.perf_counter()
    responses: list[dict[str, Any]] = []
    workers = max(1, min(max_workers, request_count))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [
            executor.submit(_gateway_jsonrpc_call, method, params, f"cache-hot-{nonce}-{index}")
            for index in range(request_count)
        ]
        for future in as_completed(futures):
            responses.append(future.result())
    after = _gateway_metrics_snapshot()
    elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
    latencies = _latency_summary(responses)
    return {
        "label": "Hot key 高并发",
        "request_count": request_count + 1,
        "ok_count": (1 if warm.get("ok") else 0) + sum(1 for item in responses if item.get("ok")),
        "error_count": (0 if warm.get("ok") else 1) + sum(1 for item in responses if not item.get("ok")),
        "elapsed_ms": elapsed_ms,
        **latencies,
        "metric_delta": _gateway_metric_delta_rows(before, after, method),
        "sample_errors": [
            {
                "status_code": item.get("status_code"),
                "error_code": item.get("error_code"),
                "error_message": item.get("error_message"),
            }
            for item in [warm, *responses]
            if not item.get("ok")
        ][:3],
    }


def run_gateway_ablation_experiment(
    *,
    request_count: int,
    max_workers: int,
    include_circuit: bool,
    include_rate_limit: bool,
    env_config: dict[str, str],
) -> dict[str, Any]:
    method = "get_weather"
    service_name = "weather_mcp_server"
    base_params = {"city": "北京", "date": "未指定"}
    nonce = str(int(time.time() * 1000))
    results: list[dict[str, Any]] = []

    if not is_service_running("mcp_gateway"):
        return {"ok": False, "error": "MCP Gateway 未启动。"}
    if not is_service_running(service_name):
        return {"ok": False, "error": "Weather MCP 未启动，无法运行 cache/coalescing ablation。"}

    _wait_for_gateway_breaker_retry_window(method)
    try:
        cache_reset = _gateway_cache_clear()
    except Exception as exc:
        cache_reset = {"ok": False, "error": str(exc)}
    try:
        cache_before = _gateway_cache_snapshot()
    except Exception:
        cache_before = {}

    lifecycle_params = {**base_params, "_demo_nonce": f"key-lifecycle-{nonce}"}
    results.append(
        _run_gateway_single_scenario(
            label="Cold key 首次请求",
            method=method,
            params=lifecycle_params,
            request_id=f"cold-key-{nonce}",
        )
    )
    results.append(
        _run_gateway_single_scenario(
            label="Hot key 二次请求",
            method=method,
            params=lifecycle_params,
            request_id=f"hot-key-{nonce}",
        )
    )

    results.append(
        _run_gateway_concurrent_scenario(
            label="Cold key churn / 无缓存复用",
            method=method,
            params_factory=lambda index: {**base_params, "_demo_nonce": f"baseline-{nonce}-{index}"},
            request_count=request_count,
            max_workers=max_workers,
            request_id_prefix=f"baseline-{nonce}",
        )
    )
    results.append(_run_gateway_cache_scenario(method, base_params, request_count, max_workers, nonce))
    results.append(
        _run_gateway_concurrent_scenario(
            label="Coalescing 冷 key",
            method=method,
            params_factory=lambda _index: {**base_params, "_demo_nonce": f"coalesce-{nonce}"},
            request_count=request_count,
            max_workers=max_workers,
            request_id_prefix=f"coalesce-{nonce}",
        )
    )

    if include_rate_limit:
        was_running = is_service_running(service_name)
        try:
            if was_running:
                stop_service(service_name)
                time.sleep(0.8)
            start_service(service_name, env_config, delay=2.0)
            time.sleep(1.0)
            _wait_for_gateway_breaker_retry_window(method)
            results.append(
                _run_gateway_concurrent_scenario(
                    label="Rate Limit 压力（慢上游 + 冷 key）",
                    method=method,
                    params_factory=lambda index: {**base_params, "_demo_nonce": f"rate-limit-{nonce}-{index}"},
                    request_count=max(request_count, max_workers),
                    max_workers=max_workers,
                    request_id_prefix=f"rate-limit-{nonce}",
                )
            )
        finally:
            stop_service(service_name)
            if was_running:
                start_service(service_name, env_config, delay=_service_delay(service_name))
                time.sleep(1.0)

    if include_circuit:
        was_running = is_service_running(service_name)
        circuit_before = _gateway_metrics_snapshot()
        try:
            stop_service(service_name)
            time.sleep(0.8)
            for index in range(4):
                _gateway_jsonrpc_call(
                    method,
                    {**base_params, "_demo_nonce": f"circuit-prime-{nonce}-{index}"},
                    f"circuit-prime-{nonce}-{index}",
                    timeout=4,
                )
            circuit_scenario = _run_gateway_concurrent_scenario(
                label="Circuit Breaker 打开后",
                method=method,
                params_factory=lambda index: {**base_params, "_demo_nonce": f"circuit-open-{nonce}-{index}"},
                request_count=request_count,
                max_workers=max_workers,
                request_id_prefix=f"circuit-open-{nonce}",
            )
            circuit_after = _gateway_metrics_snapshot()
            circuit_scenario["metric_delta"] = _gateway_metric_delta_rows(circuit_before, circuit_after, method)
            results.append(circuit_scenario)
        finally:
            if was_running:
                start_service(service_name, env_config, delay=_service_delay(service_name))
                time.sleep(1.0)
        if was_running:
            _wait_for_gateway_breaker_retry_window(method)
            results.append(
                _run_gateway_single_scenario(
                    label="Circuit Breaker 恢复探测",
                    method=method,
                    params={**base_params, "_demo_nonce": f"circuit-recovery-{nonce}"},
                    request_id=f"circuit-recovery-{nonce}",
                )
            )

    try:
        cache_after = _gateway_cache_snapshot()
    except Exception:
        cache_after = {}

    return {
        "ok": True,
        "method": method,
        "request_count": request_count,
        "max_workers": max_workers,
        "cache_reset": cache_reset,
        "cache_before": cache_before,
        "cache_after": cache_after,
        "results": results,
        "final_metrics": _gateway_metrics_snapshot(),
    }


def _gateway_ablation_summary_rows(result: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for scenario in result.get("results", []):
        deltas = {
            item["metric"]: item.get("method_delta")
            for item in scenario.get("metric_delta", [])
        }
        rows.append(
            {
                "scenario": scenario.get("label"),
                "requests": scenario.get("request_count"),
                "ok": scenario.get("ok_count"),
                "errors": scenario.get("error_count"),
                "elapsed_ms": scenario.get("elapsed_ms"),
                "avg_ms": scenario.get("avg_ms"),
                "p95_ms": scenario.get("p95_ms"),
                "upstream": deltas.get("upstream_calls"),
                "cache_hits": deltas.get("cache_hits"),
                "cache_hit_rate": _gateway_percent(
                    deltas.get("cache_hits", 0),
                    int(deltas.get("cache_hits", 0) or 0) + int(deltas.get("cache_misses", 0) or 0),
                ),
                "coalesced": deltas.get("coalesced_requests"),
                "upstream_saved": max(
                    0,
                    int(scenario.get("request_count") or 0) - int(deltas.get("upstream_calls", 0) or 0),
                ),
                "circuit_open": deltas.get("circuit_open"),
                "rate_limited": deltas.get("rate_limited"),
            }
        )
    return rows


def _gateway_percent(numerator: Any, denominator: Any) -> str:
    try:
        denominator_value = float(denominator)
        if denominator_value <= 0:
            return "0%"
        return f"{float(numerator) / denominator_value * 100:.1f}%"
    except (TypeError, ValueError, ZeroDivisionError):
        return "0%"


def _gateway_metric_value(row: dict[str, Any], key: str) -> float:
    value = row.get(key, 0)
    return float(value) if isinstance(value, (int, float)) else 0.0


def _gateway_metric_bars(rows: list[dict[str, Any]], metric: str, title: str, *, color: str) -> str:
    max_value = max((_gateway_metric_value(row, metric) for row in rows), default=0.0)
    max_value = max(max_value, 1.0)
    parts = [
        '<div class="gateway-bars">',
        f'<div class="gateway-bars-title">{escape(title)}</div>',
    ]
    for row in rows:
        label = escape(str(row.get("scenario", "")))
        value = _gateway_metric_value(row, metric)
        width = min(100.0, max(3.0 if value > 0 else 0.0, value / max_value * 100.0))
        parts.append(
            '<div class="gateway-bar-row">'
            f'<div class="gateway-bar-label">{label}</div>'
            '<div class="gateway-bar-track">'
            f'<div class="gateway-bar-fill" style="width:{width:.1f}%;background:{color};"></div>'
            '</div>'
            f'<div class="gateway-bar-value">{value:g}</div>'
            '</div>'
        )
    parts.append("</div>")
    return "\n".join(parts)


def _render_gateway_ablation_visuals(rows: list[dict[str, Any]]) -> None:
    st.markdown(
        """
        <style>
        .gateway-bars {
            border: 1px solid rgba(148, 163, 184, .28);
            border-radius: 8px;
            padding: 12px;
            margin: 8px 0 14px;
            background: rgba(15, 23, 42, .18);
        }
        .gateway-bars-title {
            font-weight: 700;
            margin-bottom: 10px;
        }
        .gateway-bar-row {
            display: grid;
            grid-template-columns: minmax(150px, 230px) 1fr 64px;
            align-items: center;
            gap: 10px;
            margin: 7px 0;
            font-size: 13px;
        }
        .gateway-bar-label {
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
            color: rgba(226, 232, 240, .94);
        }
        .gateway-bar-track {
            height: 12px;
            border-radius: 999px;
            overflow: hidden;
            background: rgba(148, 163, 184, .18);
        }
        .gateway-bar-fill {
            height: 100%;
            border-radius: 999px;
        }
        .gateway-bar-value {
            text-align: right;
            color: rgba(226, 232, 240, .9);
            font-variant-numeric: tabular-nums;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
    cols = st.columns(3)
    total_requests = sum(int(row.get("requests") or 0) for row in rows)
    total_upstream = sum(int(row.get("upstream") or 0) for row in rows)
    total_saved = sum(int(row.get("upstream_saved") or 0) for row in rows)
    total_guard = sum(int(row.get("circuit_open") or 0) + int(row.get("rate_limited") or 0) for row in rows)
    cols[0].metric("总请求", total_requests)
    cols[1].metric("上游调用", total_upstream, delta=f"节省 {total_saved}")
    cols[2].metric("保护事件", total_guard)
    st.markdown(_gateway_metric_bars(rows, "upstream", "上游调用对比", color="#38bdf8"), unsafe_allow_html=True)
    st.markdown(_gateway_metric_bars(rows, "cache_hits", "Cache hit 对比", color="#22c55e"), unsafe_allow_html=True)
    st.markdown(_gateway_metric_bars(rows, "coalesced", "Coalescing 合并请求对比", color="#f59e0b"), unsafe_allow_html=True)
    st.markdown(_gateway_metric_bars(rows, "rate_limited", "Rate limit 拒绝对比", color="#f97316"), unsafe_allow_html=True)
    st.markdown(_gateway_metric_bars(rows, "circuit_open", "Circuit open 拒绝对比", color="#ef4444"), unsafe_allow_html=True)


def render_gateway_cache_panel() -> None:
    st.markdown("#### Gateway Cache 管理")
    policy_tab, live_tab = st.tabs(["TTL 策略", "实时缓存"])
    with policy_tab:
        st.dataframe(_gateway_cache_policy_rows(), hide_index=True, width="stretch")
        st.caption(
            f"容量上限：{int(DEFAULT_MCP_GATEWAY_CONFIG.get('cache_max_entries', 512))} entries；"
            "缓存 key 只使用业务语义参数，不使用 id、task_id、instruction 等一次性字段。"
        )

    with live_tab:
        if not is_service_running("mcp_gateway"):
            st.info("MCP Gateway 未启动，无法读取实时缓存。")
            return
        try:
            cache = _gateway_cache_snapshot()
        except Exception as exc:
            st.warning(f"无法读取 /cache 接口：{exc}。请重启 MCP Gateway 以启用新的缓存管理接口。")
            return

        stats_cols = st.columns(4)
        stats_cols[0].metric("Cache entries", int(cache.get("size", 0) or 0))
        stats_cols[1].metric("Capacity", int(cache.get("max_entries", 0) or 0))
        stats_cols[2].metric("Evictions", int(cache.get("evictions", 0) or 0))
        stats_cols[3].metric("Expired", int(cache.get("expired_removals", 0) or 0))

        method_rows = _gateway_cache_method_rows(cache)
        if method_rows:
            st.dataframe(method_rows, hide_index=True, width="stretch")

        entry_rows = _gateway_cache_entry_rows(cache)
        clear_cols = st.columns([1, 1, 2])
        if clear_cols[0].button("清空全部缓存", width="stretch", key="gateway_cache_clear_all"):
            _gateway_cache_clear()
            st.rerun()

        method_options = [""] + sorted({str(row.get("method")) for row in entry_rows if row.get("method")})
        selected_method = clear_cols[1].selectbox("按 method 清理", method_options, key="gateway_cache_clear_method")
        if clear_cols[2].button("清理选中 method", width="stretch", key="gateway_cache_clear_method_btn"):
            if selected_method:
                _gateway_cache_clear(method=selected_method)
                st.rerun()

        if not entry_rows:
            st.info("当前没有缓存条目。先运行一次 Gateway Demo 或 ablation 后再查看。")
            return

        display_rows = [
            {key: value for key, value in row.items() if key != "key"}
            for row in entry_rows
        ]
        st.dataframe(display_rows, hide_index=True, width="stretch")

        key_options = {
            f"{row.get('method')} / {row.get('key_hash')}": row.get("key")
            for row in entry_rows
            if row.get("key")
        }
        if key_options:
            selected_key_label = st.selectbox("按 key 清理", list(key_options), key="gateway_cache_clear_key")
            if st.button("清理选中 key", width="stretch", key="gateway_cache_clear_key_btn"):
                _gateway_cache_clear(key=key_options[selected_key_label])
                st.rerun()


def render_gateway_demo_panel(env_config: dict[str, str]) -> None:
    cases = _gateway_demo_cases()
    case_label = st.selectbox("Gateway JSON-RPC 方法", list(cases), key="gateway_demo_case")
    case = cases[case_label]
    st.code(_format_json_block({"method": case["method"], "params": case["params"]}), language="json")

    if st.button("运行 Gateway Demo", width="stretch", key="run_gateway_demo"):
        if not is_service_running("mcp_gateway"):
            st.session_state.gateway_demo_result = {
                "ok": False,
                "error": "MCP Gateway 未启动，请先启动所有节点或单独启动 Gateway。",
            }
        else:
            with st.spinner("正在请求 MCP Gateway..."):
                st.session_state.gateway_demo_result = run_gateway_demo(case["method"], case["params"])

    result = st.session_state.get("gateway_demo_result")
    if isinstance(result, dict):
        if result.get("ok"):
            st.success(f"Gateway Demo 成功，耗时 {result.get('elapsed_ms')} ms")
        else:
            st.error(result.get("error") or "Gateway Demo 失败")

        summary = {
            "gateway": result.get("gateway", MCP_GATEWAY_URL),
            "health_status": result.get("health_status"),
            "methods_status": result.get("methods_status"),
            "response_status": result.get("response_status"),
            "elapsed_ms": result.get("elapsed_ms"),
        }
        st.table([summary])

        data_source_rows = _gateway_data_source_rows(result.get("response"))
        if data_source_rows:
            st.markdown("##### MCP 数据源")
            st.dataframe(data_source_rows, hide_index=True, width="stretch")

        tab_request, tab_response, tab_metrics = st.tabs(["Request", "Response", "Metrics"])
        with tab_request:
            st.json(result.get("request", {}), expanded=True)
        with tab_response:
            st.json(result.get("response", result.get("health", {})), expanded=True)
        with tab_metrics:
            st.json(result.get("metrics", {}), expanded=True)

    render_gateway_cache_panel()

    st.divider()
    st.markdown("#### 高并发 Ablation")
    st.caption(
        "覆盖冷/热 key、无复用基线、热 key 高并发、冷 key coalescing、rate limit、"
        "circuit breaker open 与恢复探测。"
    )
    request_count = int(
        st.number_input(
            "并发请求数",
            min_value=4,
            max_value=80,
            value=24,
            step=4,
            key="gateway_ablation_request_count",
        )
    )
    max_workers = int(
        st.number_input(
            "客户端并发 worker",
            min_value=2,
            max_value=80,
            value=24,
            step=2,
            key="gateway_ablation_workers",
        )
    )
    include_circuit = st.checkbox(
        "包含 Circuit Breaker 实验（会临时停止 Weather MCP 后自动重启）",
        value=True,
        key="gateway_ablation_include_circuit",
    )
    include_rate_limit = st.checkbox(
        "包含 Rate Limit 压力实验（会临时以 2s delay 重启 Weather MCP 后恢复）",
        value=True,
        key="gateway_ablation_include_rate_limit",
    )

    if st.button("运行高并发 Gateway Ablation", width="stretch", key="run_gateway_ablation"):
        with st.spinner("正在运行 Gateway 高并发 ablation..."):
            st.session_state.gateway_ablation_result = run_gateway_ablation_experiment(
                request_count=request_count,
                max_workers=max_workers,
                include_circuit=include_circuit,
                include_rate_limit=include_rate_limit,
                env_config=env_config,
            )

    ablation = st.session_state.get("gateway_ablation_result")
    if not isinstance(ablation, dict):
        return
    if not ablation.get("ok"):
        st.error(ablation.get("error") or "Gateway ablation 失败")
        return

    st.success("Gateway ablation 完成")
    summary_rows = _gateway_ablation_summary_rows(ablation)
    tab_overview, tab_table, tab_cache, tab_delta = st.tabs(["可视化总览", "结果表", "Cache after", "Metric delta"])
    with tab_overview:
        _render_gateway_ablation_visuals(summary_rows)
    with tab_table:
        st.dataframe(summary_rows, hide_index=True, width="stretch")
    with tab_cache:
        cache_after = ablation.get("cache_after", {})
        if isinstance(cache_after, dict) and cache_after:
            st.dataframe(_gateway_cache_method_rows(cache_after), hide_index=True, width="stretch")
            st.dataframe(
                [{key: value for key, value in row.items() if key != "key"} for row in _gateway_cache_entry_rows(cache_after)],
                hide_index=True,
                width="stretch",
            )
        else:
            st.info("本次没有读取到 Gateway cache 快照。")

    with tab_delta:
        for scenario in ablation.get("results", []):
            st.markdown(f"##### {scenario.get('label')}")
            st.dataframe(scenario.get("metric_delta", []), hide_index=True, width="stretch")
            if scenario.get("sample_errors"):
                st.json(scenario["sample_errors"], expanded=False)


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
            f'data-packet-key="{event_key}">'
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


def _preset_delay_text(delays: dict[str, float]) -> str:
    if not delays:
        return "全部 0s"
    return ", ".join(f"{MCP_SERVICE_LABELS.get(name, name)}={delay:g}s" for name, delay in delays.items())


def _service_delay(name: str) -> float:
    if not name.endswith("_mcp_server"):
        return 0.0
    return float(st.session_state.get(f"delay_{name}", 0.0) or 0.0)


def _gateway_upstream_timeout_seconds() -> float:
    return float(st.session_state.get("mcp_http_timeout_seconds", DEFAULT_MCP_HTTP_TIMEOUT_SECONDS))


def _running_demo_services() -> list[str]:
    return [name for name in SERVICES if is_service_running(name)]


def _apply_demo_parameter_preset(name: str) -> None:
    preset = DEMO_PARAMETER_PRESETS[name]
    st.session_state["a2a_tcp_timeout_seconds"] = float(preset["a2a_timeout"])
    st.session_state["mcp_http_timeout_seconds"] = float(preset["mcp_timeout"])
    st.session_state["mcp_realtime_timeout_seconds"] = float(preset.get("realtime_timeout", DEFAULT_MCP_REALTIME_TIMEOUT_SECONDS))
    st.session_state["task_timeout_seconds"] = float(preset["task_timeout"])
    preset_delays = preset.get("delays", {})
    for srv_name in MCP_SERVICE_LABELS:
        st.session_state[f"delay_{srv_name}"] = float(preset_delays.get(srv_name, 0.0))
    st.session_state["applied_demo_parameter_preset"] = name
    if _running_demo_services():
        st.session_state["restart_services_after_preset_apply"] = name


def render_demo_parameter_presets() -> None:
    with st.expander("Demo 参数组合", expanded=False):
        preset_name = st.selectbox(
            "演示目标",
            list(DEMO_PARAMETER_PRESETS) + [CUSTOM_DEMO_PARAMETER_OPTION],
            key="demo_parameter_preset",
        )
        if preset_name == CUSTOM_DEMO_PARAMETER_OPTION:
            timeout_cols = st.columns(4)
            with timeout_cols[0]:
                st.number_input(
                    "A2A TCP Timeout (秒)",
                    min_value=0.5,
                    step=1.0,
                    key="a2a_tcp_timeout_seconds",
                )
            with timeout_cols[1]:
                st.number_input(
                    "MCP HTTP Timeout (秒)",
                    min_value=1.0,
                    step=1.0,
                    key="mcp_http_timeout_seconds",
                )
            with timeout_cols[2]:
                st.number_input(
                    "Realtime API Timeout (秒)",
                    min_value=1.0,
                    step=1.0,
                    key="mcp_realtime_timeout_seconds",
                )
            with timeout_cols[3]:
                st.number_input(
                    "Task Timeout (秒)",
                    min_value=10.0,
                    step=10.0,
                    key="task_timeout_seconds",
                )
            st.caption("MCP HTTP Timeout 同时作用于 Agent -> Gateway 和 Gateway -> MCP。自定义参数会用于后续节点启动和任务提交。")
            return

        preset = DEMO_PARAMETER_PRESETS[preset_name]
        st.table(
            [
                {
                    "A2A TCP Timeout": f"{preset['a2a_timeout']:g}s",
                    "MCP HTTP Timeout": f"{preset['mcp_timeout']:g}s",
                    "Gateway Upstream Timeout": f"{preset['mcp_timeout']:g}s",
                    "Realtime API Timeout": f"{preset.get('realtime_timeout', DEFAULT_MCP_REALTIME_TIMEOUT_SECONDS):g}s",
                    "Task Timeout": f"{preset['task_timeout']:g}s",
                    "MCP delay": _preset_delay_text(preset.get("delays", {})),
                }
            ]
        )
        st.caption(str(preset["purpose"]))
        st.caption(str(preset["steps"]))
        if st.button("应用该参数组合", width="stretch", key="apply_demo_parameter_preset"):
            _apply_demo_parameter_preset(preset_name)
            st.rerun()


def initialize_runtime_control_defaults() -> None:
    defaults = {
        "a2a_tcp_timeout_seconds": float(DEFAULT_A2A_TCP_TIMEOUT_SECONDS),
        "mcp_http_timeout_seconds": float(DEFAULT_MCP_HTTP_TIMEOUT_SECONDS),
        "mcp_realtime_timeout_seconds": float(DEFAULT_MCP_REALTIME_TIMEOUT_SECONDS),
        "task_timeout_seconds": float(DEFAULT_TASK_TIMEOUT_SECONDS_CONFIG),
        "a2a_realtime_mcp_enabled": bool(DEFAULT_REALTIME_MCP_ENABLED),
        "mcp_realtime_fallback_to_mock": bool(DEFAULT_MCP_REALTIME_FALLBACK_TO_MOCK),
        "llm_enable_thinking": str(os.getenv("A2A_LLM_ENABLE_THINKING", "0")).strip().lower() in {"1", "true", "yes", "on"},
    }
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)


def render_realtime_mcp_panel() -> None:
    with st.expander("实时 MCP 数据源", expanded=True):
        control_cols = st.columns(3)
        with control_cols[0]:
            st.toggle("实时 MCP", key="a2a_realtime_mcp_enabled")
        with control_cols[1]:
            st.toggle("本地数据备选", key="mcp_realtime_fallback_to_mock")
        with control_cols[2]:
            st.number_input(
                "Realtime API Timeout (秒)",
                min_value=1.0,
                step=1.0,
                key="mcp_realtime_timeout_seconds",
            )

        realtime_enabled = bool(st.session_state.get("a2a_realtime_mcp_enabled", DEFAULT_REALTIME_MCP_ENABLED))
        fallback_enabled = bool(st.session_state.get("mcp_realtime_fallback_to_mock", DEFAULT_MCP_REALTIME_FALLBACK_TO_MOCK))
        realtime_timeout = float(st.session_state.get("mcp_realtime_timeout_seconds", DEFAULT_MCP_REALTIME_TIMEOUT_SECONDS))
        gateway_upstream_timeout = _gateway_upstream_timeout_seconds()
        status_rows = [
            {
                "配置": "实时 MCP",
                "当前值": "开启" if realtime_enabled else "关闭",
                "来源": "UI -> A2A_REALTIME_MCP_ENABLED",
            },
            {
                "配置": "高德 Key",
                "当前值": "已配置" if DEFAULT_AMAP_WEB_KEY else "未配置",
                "来源": "AMAP_WEB_KEY",
            },
            {
                "配置": "高德 API",
                "当前值": DEFAULT_AMAP_API_BASE_URL,
                "来源": "AMAP_API_BASE_URL",
            },
            {
                "配置": "本地数据备选",
                "当前值": "开启" if fallback_enabled else "关闭",
                "来源": "UI -> MCP_REALTIME_FALLBACK_TO_MOCK",
            },
            {
                "配置": "Realtime API 超时",
                "当前值": f"{realtime_timeout:g}s",
                "来源": "UI -> MCP_REALTIME_TIMEOUT_SECONDS",
            },
            {
                "配置": "Gateway 上游超时",
                "当前值": f"{gateway_upstream_timeout:g}s",
                "来源": "UI MCP HTTP Timeout -> MCP_GATEWAY_UPSTREAM_TIMEOUT_SECONDS",
            },
        ]
        st.dataframe(status_rows, hide_index=True, width="stretch")

        source_tab, cache_tab = st.tabs(["数据源", "Gateway 缓存"])
        with source_tab:
            st.dataframe(REALTIME_MCP_SOURCE_ROWS, hide_index=True, width="stretch")
        with cache_tab:
            st.dataframe(MCP_GATEWAY_CACHE_ROWS, hide_index=True, width="stretch")

        if realtime_enabled and not DEFAULT_AMAP_WEB_KEY:
            st.warning("实时 MCP 已开启，但 AMAP_WEB_KEY 未配置；请配置后再运行实时数据演示。")
        st.caption("这些参数会进入后续启动的节点进程；已经运行的节点需要重启后才会读取新值。")


# 注册退出时的清理函数，防止 Streamlit 关闭时遗留僵尸进程
atexit.register(lambda: stop_all_services(include_port_processes=False))

# --- 页面布局 ---
st.title("✈️ A2A 旅行工作流 Agent")

col_sidebar, col_main = st.columns([1, 1])

with col_sidebar:
    st.header("⚙️ 节点管理与容错配置")
    initialize_runtime_control_defaults()

    render_demo_parameter_presets()

    a2a_tcp_timeout = float(st.session_state["a2a_tcp_timeout_seconds"])
    mcp_http_timeout = float(st.session_state["mcp_http_timeout_seconds"])
    mcp_realtime_timeout = float(st.session_state["mcp_realtime_timeout_seconds"])
    task_timeout = float(st.session_state["task_timeout_seconds"])
    render_realtime_mcp_panel()
    
    st.markdown("### 启停控制")
    col_btn1, col_btn2 = st.columns(2)
    
    env_config = {
        "A2A_TCP_TIMEOUT_SECONDS": str(a2a_tcp_timeout),
        "MCP_HTTP_TIMEOUT_SECONDS": str(mcp_http_timeout),
        "MCP_GATEWAY_UPSTREAM_TIMEOUT_SECONDS": str(_gateway_upstream_timeout_seconds()),
        "MCP_REALTIME_TIMEOUT_SECONDS": str(mcp_realtime_timeout),
        "A2A_REALTIME_MCP_ENABLED": "1" if bool(st.session_state.get("a2a_realtime_mcp_enabled")) else "0",
        "MCP_REALTIME_FALLBACK_TO_MOCK": "1" if bool(st.session_state.get("mcp_realtime_fallback_to_mock")) else "0",
        "A2A_LLM_ENABLE_THINKING": "1" if bool(st.session_state.get("llm_enable_thinking")) else "0",
        "DEFAULT_TASK_TIMEOUT_SECONDS": str(task_timeout),
        "MAX_TASK_TIMEOUT_SECONDS": str(task_timeout),
        "PYTHONIOENCODING": "utf-8"
    }
    # 将系统环境变量中的 API_KEY 也传过去，防止丢失
    for k, v in os.environ.items():
        if k not in env_config:
            env_config[k] = v

    pending_preset_restart = st.session_state.pop("restart_services_after_preset_apply", None)
    if pending_preset_restart:
        with st.spinner(f"正在按「{pending_preset_restart}」重启所有服务..."):
            stop_all_services()
            for srv in SERVICES.keys():
                start_service(srv, env_config, delay=_service_delay(srv))
            time.sleep(3)
        st.success(f"已按「{pending_preset_restart}」重启所有服务，运行进程已读取最新参数。")

    if col_btn1.button("▶️ 启动所有节点", width="stretch"):
        with st.spinner("正在启动所有服务..."):
            for srv in SERVICES.keys():
                start_service(srv, env_config, delay=_service_delay(srv))
            time.sleep(3)
        st.rerun()
        
    if col_btn2.button("⏹️ 停止所有节点", width="stretch"):
        stop_all_services()
        st.session_state.task_transfer_active = False
        st.rerun()

    st.markdown("### 节点拓扑控制")
    show_topology_activity = bool(st.session_state.get("task_transfer_active", False))
    _, _, _, recent_events = read_recent_network_activity(show_activity=show_topology_activity)
    render_topology_control(env_config, show_activity=show_topology_activity)
    st.caption("点击拓扑中的服务节点即可启停。Agent 池作为动态部署层展示，最近 8 秒有协议传输的链路会显示渐变流动。")

    if recent_events:
        st.session_state.last_recent_network_events = recent_events
    display_events = recent_events or st.session_state.get("last_recent_network_events", [])

    if display_events:
        with st.expander("网络报文", expanded=False):
            render_packet_inspector(display_events)

    if has_task_packet_capture():
        with st.expander("真实抓包 Frame", expanded=False):
            render_task_packet_capture_panel()

with col_main:
    st.header("💬 旅行任务交互")
    st.markdown("输入您的旅行需求，Coordinator 将根据当前存活的 Agents 动态分析依赖，为您完成规划。")

    query = st.text_area(
        "请描述您的旅行需求：", 
        value="明天打算从上海去广州玩3天，要求穷游并且尽量乘坐地铁，必须去越秀公园看看。"
    )
    st.toggle(
        "深度思考",
        key="llm_enable_thinking",
        help="开启后本轮任务会允许模型进行更深的思考，但可能会更慢。",
    )

    with st.expander("MCP Gateway Demo 测试", expanded=False):
        render_gateway_demo_panel(env_config)

    submit_clicked = st.button("提交任务 / Submit", type="primary", on_click=clear_task_display_state)
    task_output_slot = st.empty()

    if submit_clicked:

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
                capture = None
                if _packet_capture_capability().get("ok"):
                    capture = start_task_packet_capture(
                        max_seconds=int(task_timeout) + 20,
                        interface_name=st.session_state.get("pcap_capture_interface"),
                    )
                    if capture.get("ok"):
                        st.session_state.active_pcap_capture = capture
                    else:
                        st.session_state.active_pcap_capture = None
                        st.session_state.last_pcap_capture = capture
                with task_output_slot.container():
                    st.info("已清空上一轮回答，正在提交新任务。")
                    with st.status("正在提交任务，Coordinator 正在进行总体规划...", expanded=True, state="running") as submit_status:
                        st.write("已收到你的问题，正在发送给 Coordinator。")
                        if capture:
                            if capture.get("ok"):
                                st.write(f"真实抓包已启动：{capture.get('interface_label', capture.get('interface', ''))}")
                            else:
                                st.warning(f"真实抓包未启动：{capture.get('error', 'unknown error')}")
                        st.write(f"深度思考模式：{'开启' if bool(st.session_state.get('llm_enable_thinking')) else '关闭'}")
                        st.write("正在生成总体规划与工作流 DAG，请稍等...")
                        response = requests.post(
                            COORDINATOR_URL,
                            json={
                                "question": query,
                                "timeout": task_timeout,
                                "async": True,
                                "enable_thinking": bool(st.session_state.get("llm_enable_thinking")),
                            },
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
                finalize_task_packet_capture(reason="submit_failed")
                st.error(f"启动任务失败: {e}")

    task_id = st.session_state.get("current_task_id")
    if task_id:
        with task_output_slot.container():
            try:
                start_time = st.session_state.get("task_start_time", time.time())
                poll_resp = requests.get(f"http://127.0.0.1:9000/tasks?task_id={task_id}", timeout=5)
                poll_resp.raise_for_status()
                task = poll_resp.json().get("task", {})

                is_completed = task.get("status") in ["completed", "failed", "partial"]
                if is_completed and st.session_state.get("task_highlight_cleared_for") != task_id:
                    finalize_task_packet_capture(reason=f"task_{task.get('status')}")
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
