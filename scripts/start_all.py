from __future__ import annotations

import os
from pathlib import Path
import signal
import subprocess
import sys
import time


PROJECT_ROOT = Path(__file__).resolve().parent.parent

SERVICES = [
    ("weather_mcp_server", "mcp_servers/weather_mcp_server.py"),
    ("traffic_mcp_server", "mcp_servers/traffic_mcp_server.py"),
    ("weather_agent", "agents/weather_agent.py"),
    ("traffic_agent", "agents/traffic_agent.py"),
    ("coordinator", "coordinator.py"),
]


def main() -> None:
    env = os.environ.copy()
    env.setdefault("PYTHONUTF8", "1")
    processes: list[tuple[str, subprocess.Popen[str]]] = []

    try:
        for name, relative_script in SERVICES:
            script_path = PROJECT_ROOT / relative_script
            process = subprocess.Popen(
                [sys.executable, str(script_path)],
                cwd=PROJECT_ROOT,
                env=env,
                text=True,
            )
            processes.append((name, process))
            print(f"started {name} pid={process.pid}", flush=True)
            time.sleep(0.3)

        print("All local services started. Press Ctrl+C to stop.", flush=True)
        while True:
            failed = [(name, process.returncode) for name, process in processes if process.poll() is not None]
            if failed:
                for name, returncode in failed:
                    print(f"{name} exited with code {returncode}", flush=True)
                break
            time.sleep(1.0)
    except KeyboardInterrupt:
        print("\nStopping local services...", flush=True)
    finally:
        stop_processes(processes)


def stop_processes(processes: list[tuple[str, subprocess.Popen[str]]]) -> None:
    for name, process in reversed(processes):
        if process.poll() is not None:
            continue
        print(f"stopping {name} pid={process.pid}", flush=True)
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
            print(f"killing {name} pid={process.pid}", flush=True)
            process.kill()


if __name__ == "__main__":
    main()
