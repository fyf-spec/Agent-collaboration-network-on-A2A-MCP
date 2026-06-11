from __future__ import annotations

import argparse
import contextlib
from dataclasses import dataclass, field
import logging
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from collections.abc import Iterator

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import common.logger
from common.runtime import configure_runtime_env, env_bool, runtime_mode_name


logger = logging.getLogger("start_all")


@dataclass(frozen=True)
class ServiceSpec:
    name: str
    script: str
    args: tuple[str, ...] = field(default_factory=tuple)


SERVICES: tuple[ServiceSpec, ...] = (
    ServiceSpec("registry_center_primary", "registry_center.py"),
    ServiceSpec("registry_center_backup", "registry_center.py", ("--port", "7001")),
    ServiceSpec("weather_mcp_server", "mcp_servers/weather_mcp_server.py"),
    ServiceSpec("traffic_mcp_server", "mcp_servers/traffic_mcp_server.py"),
    ServiceSpec("attraction_mcp_server", "mcp_servers/attraction_mcp_server.py"),
    ServiceSpec("hotel_mcp_server", "mcp_servers/hotel_mcp_server.py"),
    ServiceSpec("packing_mcp_server", "mcp_servers/packing_mcp_server.py"),
    ServiceSpec("mcp_gateway", "mcp_gateway.py"),
    ServiceSpec("weather_agent", "agents/weather_agent.py"),
    ServiceSpec("attraction_agent", "agents/attraction_agent.py"),
    ServiceSpec("hotel_agent", "agents/hotel_agent.py"),
    ServiceSpec("traffic_agent", "agents/traffic_agent.py"),
    ServiceSpec("packing_agent", "agents/packing_agent.py"),
    ServiceSpec("coordinator", "coordinator.py"),
)


def service_names() -> list[str]:
    return [service.name for service in SERVICES]


@contextlib.contextmanager
def run_services(
    exclude: list[str] | None = None,
    extra_args: dict[str, list[str]] | None = None,
    *,
    mode: str | None = None,
    startup_delay_seconds: float = 0.3,
    stdout=None,
    stderr=None,
) -> Iterator[list[tuple[str, subprocess.Popen[str]]]]:
    mode_name = _normalize_mode(mode) if mode else runtime_mode_name()
    env = _build_child_env(mode_name)
    processes: list[tuple[str, subprocess.Popen[str]]] = []

    exclude_set = set(exclude or [])
    extra_args_dict = {name: list(args) for name, args in (extra_args or {}).items()}
    if not env_bool("MCP_GATEWAY_ENABLED", True):
        exclude_set.add("mcp_gateway")

    logger.info("Runtime mode: %s", mode_name)
    if mode_name == "no-llm":
        logger.info("LLM calls are disabled; services will use deterministic rule fallbacks.")
    else:
        logger.info("LLM calls are enabled; configure A2A_LLM_* and MODELSCOPE_API_KEY in .env.")

    try:
        for service in SERVICES:
            if service.name in exclude_set:
                logger.info("skipped %s", service.name)
                continue

            script_path = PROJECT_ROOT / service.script
            cmd = [sys.executable, str(script_path), *service.args, *extra_args_dict.get(service.name, [])]
            process = subprocess.Popen(
                cmd,
                cwd=PROJECT_ROOT,
                env=env,
                stdout=stdout,
                stderr=stderr,
            )
            processes.append((service.name, process))
            logger.info("started %s pid=%s", service.name, process.pid)
            time.sleep(max(0.0, startup_delay_seconds))

        logger.info("Local services started. Coordinator: http://127.0.0.1:9000")
        yield processes
    finally:
        stop_processes(processes)


def main() -> None:
    args = _parse_args()
    try:
        with run_services(
            exclude=args.exclude,
            mode=args.mode,
            startup_delay_seconds=args.startup_delay,
        ) as processes:
            logger.info("Press Ctrl+C to stop all local services.")
            while True:
                _report_exited_processes(processes)
                time.sleep(1.0)
    except KeyboardInterrupt:
        logger.critical("Stopping local services...")


def stop_processes(processes: list[tuple[str, subprocess.Popen[str]]]) -> None:
    for name, process in reversed(processes):
        if process.poll() is not None:
            continue
        logger.critical("stopping %s pid=%s", name, process.pid)
        if os.name == "nt":
            process.terminate()
        else:
            process.send_signal(signal.SIGTERM)

    deadline = time.monotonic() + 5
    for name, process in reversed(processes):
        if process.poll() is not None:
            continue
        remaining = max(0.1, deadline - time.monotonic())
        try:
            process.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            logger.critical("killing %s pid=%s", name, process.pid)
            process.kill()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Start all local A2A/MCP demo services.")
    parser.add_argument(
        "--mode",
        choices=("llm", "no-llm"),
        default=runtime_mode_name(),
        help="llm uses external model calls; no-llm skips them and uses deterministic rules.",
    )
    parser.add_argument(
        "--exclude",
        action="append",
        choices=service_names(),
        default=[],
        help="Skip a service by name. Can be provided multiple times.",
    )
    parser.add_argument(
        "--startup-delay",
        type=float,
        default=0.3,
        help="Seconds to wait after starting each service.",
    )
    return parser.parse_args()


def _build_child_env(mode: str) -> dict[str, str]:
    env = os.environ.copy()
    configure_runtime_env(env, use_llm=(mode == "llm"))
    return env


def _normalize_mode(mode: str) -> str:
    normalized = mode.strip().lower()
    if normalized in {"llm", "use-llm", "with-llm"}:
        return "llm"
    if normalized in {"no-llm", "nollm", "no_llm", "fast", "rule", "rules"}:
        return "no-llm"
    raise ValueError(f"unsupported runtime mode: {mode}")


def _report_exited_processes(processes: list[tuple[str, subprocess.Popen[str]]]) -> None:
    for name, process in processes:
        if process.poll() is None or getattr(process, "_already_reported", False):
            continue
        logger.error("%s exited with code %s", name, process.returncode)
        process._already_reported = True


if __name__ == "__main__":
    main()
