from __future__ import annotations

import atexit
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
from typing import Any

import requests
import streamlit as st


# 项目根目录与后端观测入口。
PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOG_FILE = PROJECT_ROOT / "logs" / "demo_log.jsonl"
COORDINATOR_URL = "http://127.0.0.1:9000"
GATEWAY_URL = "http://127.0.0.1:8100"
TERMINAL_STATES = {"completed", "partial", "failed"}

# 默认任务用于所有非缓存类消融实验，保证不同实验之间输入一致。
DEFAULT_QUESTION = "中秋节假期从上海去北京玩3天，要求穷游并且尽量乘坐地铁，必须去故宫看看。"


@dataclass(frozen=True)
class ServiceSpec:
    """本地服务启动配置。"""

    name: str
    label: str
    script: str
    ports: tuple[int, ...]
    args: tuple[str, ...] = ()


@dataclass(frozen=True)
class Scenario:
    """一个消融实验场景。

    excluded_services 表示启动时不拉起的节点；
    stop_after_start 表示先启动再停掉，用来模拟运行期宕机；
    cache_probe 表示不走 Coordinator，而是直接打 MCP Gateway。
    """

    key: str
    label: str
    category: str
    goal: str
    expected: str
    excluded_services: tuple[str, ...] = ()
    extra_args: dict[str, tuple[str, ...]] = field(default_factory=dict)
    env: dict[str, str] = field(default_factory=dict)
    stop_after_start: tuple[str, ...] = ()
    cache_probe: bool = False


# 服务启动顺序尽量贴合系统依赖：
# 先注册中心和 MCP，再 Gateway，再 Agent，最后 Coordinator。
SERVICE_ORDER = [
    "registry_center_primary",
    "registry_center_backup",
    "weather_mcp_server",
    "traffic_mcp_server",
    "attraction_mcp_server",
    "hotel_mcp_server",
    "packing_mcp_server",
    "mcp_gateway",
    "weather_agent",
    "attraction_agent",
    "hotel_agent",
    "traffic_agent",
    "packing_agent",
    "coordinator",
]

# UI 自己维护一份服务清单，避免改动 common.config 或 start_all.py。
SERVICES: dict[str, ServiceSpec] = {
    "registry_center_primary": ServiceSpec("registry_center_primary", "主注册中心", "registry_center.py", (7000,)),
    "registry_center_backup": ServiceSpec(
        "registry_center_backup",
        "备注册中心",
        "registry_center.py",
        (7001,),
        ("--port", "7001"),
    ),
    "weather_mcp_server": ServiceSpec("weather_mcp_server", "天气 MCP", "mcp_servers/weather_mcp_server.py", (8001,)),
    "traffic_mcp_server": ServiceSpec("traffic_mcp_server", "交通 MCP", "mcp_servers/traffic_mcp_server.py", (8002,)),
    "attraction_mcp_server": ServiceSpec(
        "attraction_mcp_server",
        "景点 MCP",
        "mcp_servers/attraction_mcp_server.py",
        (8003,),
    ),
    "hotel_mcp_server": ServiceSpec("hotel_mcp_server", "酒店 MCP", "mcp_servers/hotel_mcp_server.py", (8004,)),
    "packing_mcp_server": ServiceSpec("packing_mcp_server", "行李 MCP", "mcp_servers/packing_mcp_server.py", (8005,)),
    "mcp_gateway": ServiceSpec("mcp_gateway", "MCP 网关", "mcp_gateway.py", (8100,)),
    "weather_agent": ServiceSpec("weather_agent", "天气 Agent", "agents/weather_agent.py", (9010,)),
    "attraction_agent": ServiceSpec("attraction_agent", "景点 Agent", "agents/attraction_agent.py", (9030,)),
    "hotel_agent": ServiceSpec("hotel_agent", "酒店 Agent", "agents/hotel_agent.py", (9040,)),
    "traffic_agent": ServiceSpec("traffic_agent", "交通 Agent", "agents/traffic_agent.py", (9020,)),
    "packing_agent": ServiceSpec("packing_agent", "行李 Agent", "agents/packing_agent.py", (9060,)),
    "coordinator": ServiceSpec("coordinator", "Coordinator", "coordinator.py", (9000, 9001)),
}

# 预置消融实验。每个场景都只通过“排除服务 / 注入环境变量 / 附加启动参数”来控制，
# 不修改后端代码，便于保持实验可复现。
SCENARIOS: dict[str, Scenario] = {
    "baseline": Scenario(
        key="baseline",
        label="完整系统对照组",
        category="对照组",
        goal="启动全部节点，验证端到端旅行规划链路。",
        expected="应得到 completed，关键 Agent 均成功。",
    ),
    "remove_weather_mcp": Scenario(
        key="remove_weather_mcp",
        label="去掉天气 MCP",
        category="MCP 消融",
        goal="不启动 weather_mcp_server，观察天气数据源缺失的影响。",
        expected="weather_agent 应返回 error，最终任务通常为 partial。",
        excluded_services=("weather_mcp_server",),
    ),
    "remove_traffic_mcp": Scenario(
        key="remove_traffic_mcp",
        label="去掉交通 MCP",
        category="MCP 消融",
        goal="不启动 traffic_mcp_server，观察交通规划能力缺失的影响。",
        expected="traffic_agent 应返回 error，最终交通方案不完整。",
        excluded_services=("traffic_mcp_server",),
    ),
    "remove_packing_agent": Scenario(
        key="remove_packing_agent",
        label="去掉行李 Agent",
        category="Agent 消融",
        goal="不启动 packing_agent，观察非核心 Agent 缺失时的系统降级。",
        expected="Coordinator 应记录 packing_agent 的 dispatch_error。",
        excluded_services=("packing_agent",),
    ),
    "primary_registry_down": Scenario(
        key="primary_registry_down",
        label="主注册中心宕机",
        category="服务发现",
        goal="启动后停止主注册中心，验证 Coordinator 是否切换到备注册中心。",
        expected="日志中应出现主注册中心 discover 失败，并继续从备注册中心发现 Agent。",
        stop_after_start=("registry_center_primary",),
    ),
    "weather_mcp_delay": Scenario(
        key="weather_mcp_delay",
        label="天气 MCP 延迟",
        category="超时控制",
        goal="给天气 MCP 注入 5 秒延迟，观察超时与降级。",
        expected="天气链路应在 timeout 内失败，系统不应长时间卡死。",
        extra_args={"weather_mcp_server": ("--delay", "5.0")},
    ),
    "a2a_ack_delay": Scenario(
        key="a2a_ack_delay",
        label="A2A ACK 延迟",
        category="A2A TCP",
        goal="让 weather_agent 延迟 TCP ACK，验证 Coordinator dispatch 超时。",
        expected="Coordinator 应记录 weather_agent 的 TCP dispatch_error。",
        env={"A2A_DELAY_ACK": "weather_agent"},
    ),
    "gateway_cache": Scenario(
        key="gateway_cache",
        label="网关缓存",
        category="网关优化",
        goal="连续发送两次相同 JSON-RPC 请求，观察缓存命中。",
        expected="cache_hits 增加，upstream_calls 应少于请求数。",
        excluded_services=(
            "registry_center_primary",
            "registry_center_backup",
            "traffic_mcp_server",
            "attraction_mcp_server",
            "hotel_mcp_server",
            "packing_mcp_server",
            "weather_agent",
            "attraction_agent",
            "hotel_agent",
            "traffic_agent",
            "packing_agent",
            "coordinator",
        ),
        cache_probe=True,
    ),
}


st.set_page_config(page_title="A2A 消融实验对比", layout="wide")


@st.cache_resource
def process_store() -> dict[str, subprocess.Popen[str]]:
    # Streamlit 每次交互都会重跑脚本，用 cache_resource 保留进程句柄。
    return {}


PROCESSES = process_store()


@st.cache_resource
def register_cleanup() -> bool:
    # 浏览器关闭或 Streamlit 退出时，尽量回收由本 UI 启动的子进程。
    atexit.register(stop_ui_managed_services)
    return True


def main() -> None:
    # 主渲染流程：侧边栏收集配置，按钮触发实验，然后刷新对比视图。
    register_cleanup()
    init_state()
    inject_style()

    st.title("A2A 消融实验对比")
    st.caption("独立于原 demo_ui 的简化实验台：一键运行消融场景，只展示结果对比。")

    config = render_sidebar()

    if config["clear"]:
        st.session_state["runs"] = []
        st.rerun()

    if config["stop"]:
        stop_ui_managed_services()
        st.success("已停止本 UI 启动的服务。")

    if config["run"]:
        run_selected_scenarios(config)
        st.rerun()

    render_summary()
    render_gateway_role()
    render_comparison_table()
    render_details()


def init_state() -> None:
    # runs 保存所有已完成实验，用来生成跨实验对比表。
    st.session_state.setdefault("runs", [])


def render_sidebar() -> dict[str, Any]:
    # 左侧只保留实验相关的最小控制项，避免和旧 demo_ui 的节点控制台重复。
    with st.sidebar:
        st.header("实验设置")
        selected = st.multiselect(
            "选择要运行的实验",
            options=list(SCENARIOS),
            default=["baseline", "remove_weather_mcp", "primary_registry_down"],
            format_func=lambda key: f"{SCENARIOS[key].category}｜{SCENARIOS[key].label}",
        )

        st.header("任务输入")
        question = st.text_area("旅行问题", value=DEFAULT_QUESTION, height=120)

        st.header("超时参数")
        task_timeout = st.number_input("任务总 timeout / 秒", min_value=10.0, max_value=900.0, value=120.0, step=10.0)
        a2a_timeout = st.number_input("A2A TCP timeout / 秒", min_value=1.0, max_value=30.0, value=3.0, step=1.0)
        mcp_timeout = st.number_input("MCP HTTP timeout / 秒", min_value=1.0, max_value=60.0, value=3.0, step=1.0)

        st.header("运行方式")
        demo_fast = st.toggle("启用快速演示模式", value=True)
        reuse_external = st.toggle("允许复用已运行服务", value=True)

        st.header("操作")
        run = st.button("运行所选实验", type="primary", use_container_width=True, disabled=not selected)
        stop = st.button("停止本 UI 启动的服务", use_container_width=True)
        clear = st.button("清空对比结果", use_container_width=True)

    return {
        "selected": selected,
        "question": question,
        "task_timeout": float(task_timeout),
        "a2a_timeout": float(a2a_timeout),
        "mcp_timeout": float(mcp_timeout),
        "demo_fast": bool(demo_fast),
        "reuse_external": bool(reuse_external),
        "run": bool(run),
        "stop": bool(stop),
        "clear": bool(clear),
    }


def run_selected_scenarios(config: dict[str, Any]) -> None:
    # 按用户选择顺序串行运行实验，避免多个场景同时争抢固定端口。
    selected = list(config["selected"])
    progress = st.progress(0, text="准备运行实验...")
    status_box = st.empty()

    new_runs: list[dict[str, Any]] = []
    for index, key in enumerate(selected, start=1):
        scenario = SCENARIOS[key]
        progress.progress((index - 1) / len(selected), text=f"正在运行：{scenario.label}")
        status_box.info(f"第 {index}/{len(selected)} 个实验：{scenario.label}")
        result = run_one_scenario(scenario, config)
        new_runs.append(result)

    progress.progress(1.0, text="实验完成")
    status_box.success("所选实验已完成。")
    st.session_state["runs"] = list(st.session_state.get("runs", [])) + new_runs


def run_one_scenario(scenario: Scenario, config: dict[str, Any]) -> dict[str, Any]:
    # 单个实验的生命周期：
    # 清理旧进程 -> 启动场景服务 -> 注入故障 -> 发任务或缓存探测 -> 采集指标与日志。
    started_at = datetime.now(timezone.utc)
    started = time.perf_counter()
    warnings: list[str] = []
    task: dict[str, Any] = {}
    task_id = ""
    submit_error = ""
    cache_responses: list[dict[str, Any]] = []

    stop_ui_managed_services()
    time.sleep(0.4)

    try:
        env = build_env(scenario, config)
        included_services = [name for name in SERVICE_ORDER if name not in set(scenario.excluded_services)]

        # 如果“被消融”的服务已经由外部进程占住端口，实验结果会不纯，需要提示用户。
        conflicts = excluded_services_still_running(scenario)
        if conflicts:
            warnings.append("以下本应被消融的服务仍在监听端口，结果可能被污染：" + "、".join(conflicts))

        for name in included_services:
            warning = start_service(
                name,
                env=env,
                extra_args=scenario.extra_args.get(name, ()),
                allow_external=bool(config["reuse_external"]),
            )
            if warning:
                warnings.append(warning)
            time.sleep(0.12)

        # 等待服务端口可连接，防止 Coordinator/Agent 尚未启动就开始提交任务。
        _, not_ready = wait_for_services(included_services, timeout=18.0)
        if not_ready:
            warnings.append("启动等待超时：" + "、".join(not_ready))

        # 主注册中心宕机等场景需要“先启动再停止”，这样备注册中心保留注册信息。
        for name in scenario.stop_after_start:
            if not stop_managed_service(name):
                warnings.append(f"无法停止 {SERVICES[name].label}；它可能不是由本 UI 启动。")
            time.sleep(0.8)

        if scenario.cache_probe:
            # 网关缓存实验直接请求 MCP Gateway，隔离掉 Coordinator/Agent 的影响。
            cache_responses = run_cache_probe()
        else:
            task_id, submit_error = submit_task(config)
            if task_id:
                task = poll_task(task_id, timeout_seconds=float(config["task_timeout"]) + 10.0)

        # Gateway metrics 与 JSONL 日志是 UI 展示消融效果的主要数据来源。
        metrics = get_gateway_metrics()
        events = read_events_since(started_at, task_id=task_id or None)
    finally:
        stop_ui_managed_services()

    elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
    return build_result(
        scenario=scenario,
        started_at=started_at,
        elapsed_ms=elapsed_ms,
        task_id=task_id,
        task=task,
        submit_error=submit_error,
        cache_responses=cache_responses,
        metrics=metrics,
        events=events,
        warnings=warnings,
    )


def build_env(scenario: Scenario, config: dict[str, Any]) -> dict[str, str]:
    # 给本次实验启动的所有服务注入统一超时和演示模式配置。
    env = os.environ.copy()
    env.update(
        {
            "PYTHONUTF8": "1",
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUNBUFFERED": "1",
            "A2A_TCP_TIMEOUT_SECONDS": str(config["a2a_timeout"]),
            "MCP_HTTP_TIMEOUT_SECONDS": str(config["mcp_timeout"]),
            "MCP_GATEWAY_UPSTREAM_TIMEOUT_SECONDS": str(config["mcp_timeout"]),
            "DEFAULT_TASK_TIMEOUT_SECONDS": str(config["task_timeout"]),
            "MAX_TASK_TIMEOUT_SECONDS": str(config["task_timeout"]),
        }
    )
    if config["demo_fast"]:
        env["A2A_DEMO_FAST"] = "1"
    else:
        env.pop("A2A_DEMO_FAST", None)
    env.update(scenario.env)
    return env


def start_service(
    name: str,
    *,
    env: dict[str, str],
    extra_args: tuple[str, ...],
    allow_external: bool,
) -> str:
    # 只启动本场景需要的服务；如果端口已有外部进程，可按配置选择复用或报警。
    spec = SERVICES[name]
    if managed_service_running(name):
        return ""
    if any(port_open(port) for port in spec.ports):
        if allow_external:
            return f"{spec.label} 已在端口监听，复用现有进程。"
        return f"{spec.label} 端口被占用，未启动。"

    cmd = [sys.executable, spec.script, *spec.args, *extra_args]
    try:
        process = subprocess.Popen(
            cmd,
            cwd=PROJECT_ROOT,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
        )
    except OSError as exc:
        return f"{spec.label} 启动失败：{exc}"

    PROCESSES[name] = process
    return ""


def stop_managed_service(name: str) -> bool:
    # 只停止本 UI 启动并记录在 PROCESSES 中的进程，不主动杀外部进程。
    process = PROCESSES.get(name)
    if process is None:
        return False
    if process.poll() is None:
        terminate_process(process)
    PROCESSES.pop(name, None)
    return True


def stop_ui_managed_services() -> None:
    # 按启动顺序的反向停止服务，先停 Coordinator/Agent，再停 Gateway/MCP/Registry。
    for name in reversed(SERVICE_ORDER):
        stop_managed_service(name)


def terminate_process(process: subprocess.Popen[str]) -> None:
    try:
        if os.name == "nt":
            process.terminate()
        else:
            process.send_signal(signal.SIGTERM)
        process.wait(timeout=2.5)
    except subprocess.TimeoutExpired:
        process.kill()
    except OSError:
        return


def managed_service_running(name: str) -> bool:
    # 顺手清理已经自然退出的 Popen 句柄。
    process = PROCESSES.get(name)
    if process is None:
        return False
    if process.poll() is None:
        return True
    PROCESSES.pop(name, None)
    return False


def port_open(port: int, *, host: str = "127.0.0.1", timeout: float = 0.15) -> bool:
    # 用 TCP 连接判断端口是否已监听，比依赖 ps/netstat 更轻量。
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def wait_for_services(service_names: list[str], *, timeout: float) -> tuple[list[str], list[str]]:
    deadline = time.monotonic() + timeout
    pending = set(service_names)
    ready: set[str] = set()
    while pending and time.monotonic() < deadline:
        for name in list(pending):
            if all(port_open(port) for port in SERVICES[name].ports):
                pending.remove(name)
                ready.add(name)
        time.sleep(0.25)
    return sorted(ready), sorted(pending)


def excluded_services_still_running(scenario: Scenario) -> list[str]:
    result: list[str] = []
    for name in scenario.excluded_services:
        if any(port_open(port) for port in SERVICES[name].ports):
            result.append(SERVICES[name].label)
    return result


def submit_task(config: dict[str, Any]) -> tuple[str, str]:
    # Coordinator 的 /submit_task 是异步入口，返回 task_id 后再轮询 /tasks。
    try:
        response = requests.post(
            f"{COORDINATOR_URL}/submit_task",
            json={"question": config["question"], "timeout": config["task_timeout"], "async": True},
            timeout=min(float(config["task_timeout"]) + 2.0, 60.0),
        )
        response.raise_for_status()
        body = response.json()
    except Exception as exc:
        return "", str(exc)

    task = body.get("task") if isinstance(body, dict) else {}
    task_id = str((task or {}).get("task_id") or "")
    return task_id, ""


def poll_task(task_id: str, *, timeout_seconds: float) -> dict[str, Any]:
    # 等待任务进入 completed/partial/failed，超时则返回最后一次快照。
    deadline = time.monotonic() + timeout_seconds
    latest: dict[str, Any] = {}
    while time.monotonic() < deadline:
        try:
            response = requests.get(f"{COORDINATOR_URL}/tasks", params={"task_id": task_id}, timeout=4.0)
            if response.ok:
                body = response.json()
                task = body.get("task") if isinstance(body, dict) else {}
                if isinstance(task, dict):
                    latest = task
                    if task.get("status") in TERMINAL_STATES:
                        return task
        except requests.RequestException:
            pass
        time.sleep(0.8)
    return latest


def run_cache_probe() -> list[dict[str, Any]]:
    # 两次请求只改变 JSON-RPC id，业务参数相同，用来观察 Gateway cache key 是否复用。
    payloads = [
        {"jsonrpc": "2.0", "id": "cache-a", "method": "get_weather", "params": {"city": "北京"}},
        {"jsonrpc": "2.0", "id": "cache-b", "method": "get_weather", "params": {"city": "北京"}},
    ]
    responses: list[dict[str, Any]] = []
    for payload in payloads:
        try:
            response = requests.post(f"{GATEWAY_URL}/", json=payload, timeout=8.0)
            body: Any
            try:
                body = response.json()
            except ValueError:
                body = response.text
            responses.append({"status_code": response.status_code, "body": body})
        except Exception as exc:
            responses.append({"status_code": "error", "body": str(exc)})
    return responses


def get_gateway_metrics() -> dict[str, Any]:
    # MCP Gateway 暴露 /metrics，是展示缓存、上游调用、错误和熔断的关键接口。
    try:
        response = requests.get(f"{GATEWAY_URL}/metrics", timeout=2.0)
        if response.ok:
            body = response.json()
            metrics = body.get("metrics") if isinstance(body, dict) else {}
            return metrics if isinstance(metrics, dict) else {}
    except Exception:
        return {}
    return {}


def read_recent_log_lines(max_lines: int = 1000) -> list[str]:
    # 从尾部读取日志，避免 UI 每次刷新都加载完整历史文件。
    if not LOG_FILE.exists():
        return []
    try:
        with LOG_FILE.open("r", encoding="utf-8", errors="replace") as handle:
            return list(deque(handle, maxlen=max_lines))
    except OSError:
        return []


def read_events_since(started_at: datetime, *, task_id: str | None = None) -> list[dict[str, Any]]:
    # 截取本轮实验开始之后的事件；普通任务进一步按 task_id 过滤。
    events: list[dict[str, Any]] = []
    for line in read_recent_log_lines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        event_time = parse_event_time(event.get("ts"))
        if event_time is None or event_time < started_at:
            continue
        if task_id and not event_matches_task(event, task_id):
            continue
        events.append(event)
    return events


def parse_event_time(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        result = datetime.fromisoformat(text)
        if result.tzinfo is None:
            result = result.replace(tzinfo=timezone.utc)
        return result.astimezone(timezone.utc)
    except ValueError:
        return None


def event_matches_task(event: dict[str, Any], task_id: str) -> bool:
    # 有些内部事件没有顶层 task_id，可能只在 payload 里携带。
    if event.get("task_id") in {None, "", task_id}:
        return True
    payload = event.get("payload")
    if isinstance(payload, dict) and payload.get("task_id") == task_id:
        return True
    return False


def build_result(
    *,
    scenario: Scenario,
    started_at: datetime,
    elapsed_ms: float,
    task_id: str,
    task: dict[str, Any],
    submit_error: str,
    cache_responses: list[dict[str, Any]],
    metrics: dict[str, Any],
    events: list[dict[str, Any]],
    warnings: list[str],
) -> dict[str, Any]:
    # 把 task 快照、Gateway metrics、JSONL 事件压成一行对比表数据，
    # 同时保留以下划线开头的原始字段用于详情展开。
    task_results = task.get("results") if isinstance(task.get("results"), dict) else {}
    dispatch_errors = task.get("dispatch_errors") if isinstance(task.get("dispatch_errors"), dict) else {}
    failed_agents = count_failed_agents(task_results, dispatch_errors)
    error_events = [event for event in events if event_failed(event)]
    status = task.get("status") or ("完成" if scenario.cache_probe and not submit_error else "失败" if submit_error else "未知")

    return {
        "时间": started_at.astimezone().strftime("%H:%M:%S"),
        "实验": scenario.label,
        "类别": scenario.category,
        "目标": scenario.goal,
        "预期": scenario.expected,
        "状态": status,
        "耗时(s)": round(elapsed_ms / 1000, 2),
        "成功Agent": task.get("success_count", ""),
        "失败Agent": failed_agents,
        "错误事件": len(error_events),
        "Gateway请求": metrics.get("total_requests", ""),
        "上游调用": metrics.get("upstream_calls", ""),
        "缓存命中": metrics.get("cache_hits", ""),
        "Gateway错误": metrics.get("error_count", ""),
        "平均延迟(ms)": metrics.get("avg_latency_ms", ""),
        "限流次数": metrics.get("rate_limited", ""),
        "熔断次数": metrics.get("circuit_open", ""),
        "结论": conclusion_for(scenario, status, failed_agents, metrics, error_events),
        "_scenario_key": scenario.key,
        "_task_id": task_id,
        "_task": task,
        "_submit_error": submit_error,
        "_cache_responses": cache_responses,
        "_metrics": metrics,
        "_events": events,
        "_warnings": warnings,
    }


def count_failed_agents(task_results: dict[str, Any], dispatch_errors: dict[str, Any]) -> int:
    # dispatch_errors 代表任务没有被 Agent 正常接收；results 中的非 success 代表 Agent 返回错误结果。
    failed = len(dispatch_errors)
    for payload in task_results.values():
        if isinstance(payload, dict) and payload.get("status") != "success":
            failed += 1
    return failed


def event_failed(event: dict[str, Any]) -> bool:
    # 统一判断日志事件是否体现失败，供错误事件计数和详情表复用。
    name = str(event.get("event", "")).lower()
    status_code = event.get("status_code")
    try:
        bad_status = int(status_code) >= 400 if status_code not in (None, "") else False
    except (TypeError, ValueError):
        bad_status = False
    return bool(event.get("error") or event.get("error_type") or bad_status or "failed" in name or "error" in name)


def conclusion_for(
    scenario: Scenario,
    status: Any,
    failed_agents: int,
    metrics: dict[str, Any],
    error_events: list[dict[str, Any]],
) -> str:
    # 给每个实验生成一句面向报告/展示的结论，避免用户从原始指标里手动推断。
    status_text = str(status)
    if scenario.key == "baseline":
        return "对照组正常" if status_text == "completed" else "对照组未完全完成，需检查环境"
    if scenario.key == "gateway_cache":
        hits = int(metrics.get("cache_hits") or 0)
        upstream = int(metrics.get("upstream_calls") or 0)
        return "缓存生效" if hits >= 1 and upstream <= 1 else "缓存效果不明显"
    if scenario.key == "primary_registry_down":
        has_registry_error = any("registry_discover_error" in str(e.get("event", "")) for e in error_events)
        return "主备切换可观测" if has_registry_error else "未观察到主注册中心失败事件"
    if status_text in {"partial", "failed"} or failed_agents > 0:
        return "消融影响已体现，系统进入降级/失败路径"
    return "未明显体现消融影响"


def render_summary() -> None:
    # 顶部四个指标给出本轮实验批次的整体完成情况。
    runs = list(st.session_state.get("runs", []))
    if not runs:
        st.info("还没有实验结果。请在左侧选择实验并点击“运行所选实验”。")
        return

    completed = sum(1 for item in runs if item.get("状态") == "completed")
    partial = sum(1 for item in runs if item.get("状态") == "partial")
    failed = sum(1 for item in runs if item.get("状态") in {"failed", "失败"})
    latest = runs[-1]

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("实验次数", len(runs))
    col2.metric("completed", completed)
    col3.metric("partial", partial)
    col4.metric("failed", failed)
    st.caption(f"最近一次：{latest.get('实验')}｜状态：{latest.get('状态')}｜结论：{latest.get('结论')}")


def render_gateway_role() -> None:
    # 单独强调 MCP Gateway 的作用：统一入口、缓存、故障隔离、metrics。
    runs = list(st.session_state.get("runs", []))
    st.subheader("MCP Gateway 作用")

    st.code(
        "Agent -> MCP Gateway -> MCP Server\n"
        "统一 JSON-RPC 入口 / 路由转发 / 缓存复用 / 限流 / 熔断 / metrics 可观测",
        language="text",
    )

    if not runs:
        st.caption("运行实验后，这里会展示 MCP Gateway 在本轮实验中的请求数、上游调用数、缓存命中和故障隔离指标。")
        return

    latest = runs[-1]
    metrics = latest.get("_metrics") if isinstance(latest.get("_metrics"), dict) else {}
    total_requests = safe_int(metrics.get("total_requests"))
    upstream_calls = safe_int(metrics.get("upstream_calls"))
    cache_hits = safe_int(metrics.get("cache_hits"))
    error_count = safe_int(metrics.get("error_count"))
    circuit_open = safe_int(metrics.get("circuit_open"))
    saved_calls = max(0, total_requests - upstream_calls)

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("统一入口请求", total_requests)
    col2.metric("真实上游调用", upstream_calls, delta=f"-{saved_calls} 次复用" if saved_calls else None)
    col3.metric("缓存命中", cache_hits)
    col4.metric("错误 / 熔断", f"{error_count} / {circuit_open}")

    if latest.get("_scenario_key") == "gateway_cache":
        st.success("当前最近一次是网关缓存实验：重点观察 `缓存命中` 是否增加，以及 `真实上游调用` 是否少于请求数。")
    elif error_count:
        st.warning("当前最近一次实验中 Gateway 记录了上游错误，说明它承担了故障隔离和错误上报的角色。")
    else:
        st.info("Gateway 在正常链路中作为所有 Agent 调用 MCP Server 的统一入口，并沉淀 metrics 供实验对比。")


def render_comparison_table() -> None:
    # 主对比表，只展示最适合写入实验报告的聚合指标。
    runs = list(st.session_state.get("runs", []))
    if not runs:
        return
    columns = [
        "时间",
        "类别",
        "实验",
        "状态",
        "耗时(s)",
        "成功Agent",
        "失败Agent",
        "错误事件",
        "Gateway请求",
        "上游调用",
        "缓存命中",
        "Gateway错误",
        "平均延迟(ms)",
        "结论",
    ]
    st.subheader("消融实验对比")
    st.dataframe([{key: run.get(key, "") for key in columns} for run in runs], hide_index=True, use_container_width=True)


def render_details() -> None:
    # 详情区保留每次实验的 Agent 结果、Gateway 指标和关键错误事件。
    runs = list(st.session_state.get("runs", []))
    if not runs:
        return

    st.subheader("实验详情")
    for index, run in enumerate(reversed(runs), start=1):
        title = f"{index}. {run.get('实验')}｜{run.get('状态')}｜{run.get('结论')}"
        with st.expander(title, expanded=index == 1):
            if run.get("_warnings"):
                st.warning("\n".join(str(item) for item in run["_warnings"]))
            if run.get("_submit_error"):
                st.error(str(run["_submit_error"]))

            st.markdown(f"**目标：** {run.get('目标')}")
            st.markdown(f"**预期：** {run.get('预期')}")
            if run.get("_task_id"):
                st.code(str(run["_task_id"]), language="text")

            agent_rows = agent_result_rows(run.get("_task") or {})
            if agent_rows:
                st.markdown("**Agent 结果**")
                st.dataframe(agent_rows, hide_index=True, use_container_width=True)

            if run.get("_scenario_key") == "gateway_cache":
                st.markdown("**缓存请求结果**")
                st.json(run.get("_cache_responses") or [], expanded=False)

            gateway_metrics = gateway_metric_rows(run.get("_metrics") or {})
            if gateway_metrics:
                st.markdown("**MCP Gateway 指标**")
                st.dataframe(gateway_metrics, hide_index=True, use_container_width=True)

            gateway_events = gateway_event_rows(run.get("_events") or [])
            if gateway_events:
                st.markdown("**MCP Gateway 关键事件**")
                st.dataframe(gateway_events, hide_index=True, use_container_width=True)

            error_rows = error_event_rows(run.get("_events") or [])
            if error_rows:
                st.markdown("**关键错误事件**")
                st.dataframe(error_rows, hide_index=True, use_container_width=True)


def agent_result_rows(task: dict[str, Any]) -> list[dict[str, Any]]:
    # 将 Coordinator task 快照中的 results/dispatch_errors 转成表格行。
    rows: list[dict[str, Any]] = []
    results = task.get("results") if isinstance(task.get("results"), dict) else {}
    errors = task.get("dispatch_errors") if isinstance(task.get("dispatch_errors"), dict) else {}
    for agent, payload in results.items():
        metadata = payload.get("metadata") if isinstance(payload, dict) else {}
        rows.append(
            {
                "Agent": agent,
                "状态": payload.get("status") if isinstance(payload, dict) else "unknown",
                "耗时(ms)": metadata.get("elapsed_ms") if isinstance(metadata, dict) else "",
                "错误": short_text(payload.get("error", "") if isinstance(payload, dict) else ""),
            }
        )
    for agent, message in errors.items():
        rows.append({"Agent": agent, "状态": "dispatch_error", "耗时(ms)": "", "错误": short_text(message)})
    return rows


def error_event_rows(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # 只挑出失败相关日志，方便在实验详情中快速定位降级原因。
    rows: list[dict[str, Any]] = []
    for event in events:
        if not event_failed(event):
            continue
        rows.append(
            {
                "事件": event.get("event", ""),
                "源": event.get("source", ""),
                "目标": event.get("target", ""),
                "错误": short_text(event.get("error") or event.get("error_type") or ""),
            }
        )
    return rows[:12]


def gateway_metric_rows(metrics: dict[str, Any]) -> list[dict[str, Any]]:
    # 给 Gateway 原始 metrics 字段补上中文解释，突出网关作用。
    if not metrics:
        return []
    labels = {
        "total_requests": "进入 Gateway 的 JSON-RPC 请求数",
        "upstream_calls": "Gateway 实际转发到 MCP Server 的次数",
        "cache_hits": "缓存命中次数",
        "cache_misses": "缓存未命中次数",
        "rate_limited": "限流次数",
        "circuit_open": "熔断拒绝次数",
        "error_count": "Gateway 观察到的错误次数",
        "avg_latency_ms": "平均处理延迟 / ms",
        "cache_size": "当前缓存项数量",
    }
    return [
        {"指标": label, "值": metrics.get(key, "")}
        for key, label in labels.items()
        if key in metrics
    ]


def gateway_event_rows(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # 过滤出 Gateway 相关链路事件，展示“统一入口 -> 转发 -> 缓存/失败”的过程。
    rows: list[dict[str, Any]] = []
    for event in events:
        event_name = str(event.get("event", ""))
        if not (
            event_name.startswith("gateway_")
            or event.get("source") == "mcp_gateway"
            or event.get("target") == "mcp_gateway"
        ):
            continue
        rows.append(
            {
                "事件": event_name,
                "含义": gateway_event_meaning(event_name),
                "源": event.get("source", ""),
                "目标": event.get("target", ""),
                "耗时(ms)": event.get("elapsed_ms", ""),
                "错误": short_text(event.get("error") or event.get("error_type") or ""),
            }
        )
    return rows[:16]


def gateway_event_meaning(event_name: str) -> str:
    # 把日志事件名翻译成更适合课堂展示的中文说明。
    return {
        "gateway_jsonrpc_request": "收到 Agent 的统一 JSON-RPC 请求",
        "gateway_call_mcp": "路由并转发到具体 MCP Server",
        "gateway_mcp_response": "收到上游 MCP Server 响应",
        "gateway_mcp_failed": "上游 MCP 调用失败并被 Gateway 捕获",
        "gateway_cache_hit": "缓存命中，避免重复上游调用",
        "gateway_coalesced_result": "复用并发中的相同请求结果",
        "gateway_circuit_open": "熔断打开，快速拒绝不健康上游",
    }.get(event_name, "Gateway 相关网络事件")


def safe_int(value: Any) -> int:
    # Streamlit metric 需要稳定数值，缺失或非法时统一按 0 处理。
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def short_text(value: Any, max_chars: int = 80) -> str:
    # 表格中错误信息只保留摘要，完整内容仍在日志文件中。
    text = " ".join(str(value or "").split())
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1] + "..."


def inject_style() -> None:
    # 轻量样式：只让指标卡更像实验仪表盘，不引入复杂前端组件。
    st.markdown(
        """
        <style>
        .block-container {
            max-width: 1320px;
            padding-top: 1.4rem;
        }
        div[data-testid="stMetric"] {
            background: #fafafa;
            border: 1px solid #e5e7eb;
            border-radius: 8px;
            padding: 12px 14px;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


if __name__ == "__main__":
    main()
