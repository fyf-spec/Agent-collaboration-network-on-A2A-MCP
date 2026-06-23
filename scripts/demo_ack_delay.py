from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.demo_utils import run_task_demo
from scripts.demo_runtime import add_runtime_args, apply_runtime_args, runtime_summary
from scripts.start_all import run_services


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the A2A ACK delay demo.")
    add_runtime_args(parser)
    parser.add_argument(
        "--question",
        default="帮我规划明天去广州的旅行方案，分别考虑天气情况和交通路线，并给出合理的出行建议。",
    )
    parser.add_argument("--timeout", type=float, default=600.0)
    parser.add_argument("--startup-delay", type=float, default=0.3)
    args = parser.parse_args()
    apply_runtime_args(args)

    demo_a2a_timeout = float(os.environ.get("A2A_DEMO_TCP_TIMEOUT_SECONDS", "3.0"))
    demo_ack_delay = float(os.environ.get("A2A_DEMO_ACK_DELAY_SECONDS", "5.0"))
    # 让 weather_agent 在 TCP A2A 握手时强行睡眠，触发 Coordinator 派发超时
    # 从而产生真实的 DISPATCH_ERROR
    old_a2a_timeout = os.environ.get("A2A_TCP_TIMEOUT_SECONDS")
    old_ack_delay = os.environ.get("A2A_DELAY_ACK_SECONDS")
    os.environ["A2A_TCP_TIMEOUT_SECONDS"] = str(demo_a2a_timeout)
    os.environ["A2A_DELAY_ACK"] = "weather_agent"
    os.environ["A2A_DELAY_ACK_SECONDS"] = str(demo_ack_delay)
    
    try:
        print(f"Runtime: {runtime_summary(args)}")
        with run_services(mode=args.mode, startup_delay_seconds=args.startup_delay):
            time.sleep(2)  # 等待服务完全启动

            run_task_demo(
                args.question,
                timeout=args.timeout,
            )
    finally:
        os.environ.pop("A2A_DELAY_ACK", None)
        if old_a2a_timeout is None:
            os.environ.pop("A2A_TCP_TIMEOUT_SECONDS", None)
        else:
            os.environ["A2A_TCP_TIMEOUT_SECONDS"] = old_a2a_timeout
        if old_ack_delay is None:
            os.environ.pop("A2A_DELAY_ACK_SECONDS", None)
        else:
            os.environ["A2A_DELAY_ACK_SECONDS"] = old_ack_delay



if __name__ == "__main__":
    main()
