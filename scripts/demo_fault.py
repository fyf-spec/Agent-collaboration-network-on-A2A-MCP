from __future__ import annotations

import argparse
import sys
import time
import os

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from scripts.demo_utils import run_task_demo
from scripts.demo_runtime import add_runtime_args, apply_runtime_args, runtime_summary
from scripts.start_all import run_services


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Weather MCP unavailable demo.")
    add_runtime_args(parser)
    parser.add_argument(
        "--question",
        default="帮我规划明天去广州的旅行方案，分别考虑天气情况和交通路线，并给出合理的出行建议。",
    )
    parser.add_argument("--timeout", type=float, default=240.0)
    parser.add_argument("--startup-delay", type=float, default=0.3)
    args = parser.parse_args()
    apply_runtime_args(args)

    # 不启动 Weather MCP Server
    print(f"Runtime: {runtime_summary(args)}")
    with run_services(
        exclude=["weather_mcp_server"],
        mode=args.mode,
        startup_delay_seconds=args.startup_delay,
    ):
        time.sleep(1) # 等待端口释放和系统稳定

        run_task_demo(
            args.question,
            timeout=args.timeout,
        )


if __name__ == "__main__":
    main()


