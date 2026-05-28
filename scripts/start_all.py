from __future__ import annotations

import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import logging
import contextlib
from typing import Iterator

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import common.logger

logger = logging.getLogger("start_all")

SERVICES = [
    ("registry_center", "registry_center.py"),
    ("weather_mcp_server", "mcp_servers/weather_mcp_server.py"),
    ("traffic_mcp_server", "mcp_servers/traffic_mcp_server.py"),
    ("attraction_mcp_server", "mcp_servers/attraction_mcp_server.py"),
    ("hotel_mcp_server", "mcp_servers/hotel_mcp_server.py"),
    ("packing_mcp_server", "mcp_servers/packing_mcp_server.py"),
    ("mcp_gateway", "mcp_gateway.py"),
    ("weather_agent", "agents/weather_agent.py"),
    ("attraction_agent", "agents/attraction_agent.py"),
    ("hotel_agent", "agents/hotel_agent.py"),
    ("traffic_agent", "agents/traffic_agent.py"),
    ("packing_agent", "agents/packing_agent.py"),
    ("coordinator", "coordinator.py"),
]


@contextlib.contextmanager
def run_services(
    exclude: list[str] | None = None,
    extra_args: dict[str, list[str]] | None = None,
) -> Iterator[list[tuple[str, subprocess.Popen[str]]]]:
    env = os.environ.copy()
    env.setdefault("PYTHONUTF8", "1")
    processes: list[tuple[str, subprocess.Popen[str]]] = []

    exclude_set = set(exclude or [])
    extra_args_dict = extra_args or {}

    try:
        for name, relative_script in SERVICES:
            if name in exclude_set:
                continue
            script_path = PROJECT_ROOT / relative_script
            cmd = [sys.executable, str(script_path)]
            if name in extra_args_dict:
                cmd.extend(extra_args_dict[name])

            process = subprocess.Popen(
                cmd,
                cwd=PROJECT_ROOT,
                env=env,
            )
            processes.append((name, process))
            logger.info(f"started {name} pid={process.pid}")
            time.sleep(0.3)

        logger.info("Local services started.")
        yield processes
    finally:
        stop_processes(processes)


def main() -> None:
    try:
        with run_services() as processes:
            logger.info("Press Ctrl+C to stop.")
            while True:
                for name, process in processes:
                    if process.poll() is not None and getattr(process, "_already_reported", False) is False:
                        logger.error(f"{name} exited with code {process.returncode}")
                        process._already_reported = True
                time.sleep(1.0)
    except KeyboardInterrupt:
        logger.critical("Stopping local services...")


def stop_processes(processes: list[tuple[str, subprocess.Popen[str]]]) -> None:
    for name, process in reversed(processes):
        if process.poll() is not None:
            continue
        logger.critical(f"stopping {name} pid={process.pid}")
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
            logger.critical(f"killing {name} pid={process.pid}")
            process.kill()


if __name__ == "__main__":
    main()
