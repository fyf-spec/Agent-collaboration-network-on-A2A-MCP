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
    parser = argparse.ArgumentParser(description="Run the MCP timeout demo.")
    add_runtime_args(parser)
    parser.add_argument(
        "--question",
        default="请帮我规划从上海去杭州的三天旅行计划，预算适中，想去西湖和灵隐寺，尽量使用地铁和步行。",
    )
    parser.add_argument("--timeout", type=float, default=600.0)
    parser.add_argument("--startup-delay", type=float, default=0.3)
    args = parser.parse_args()
    apply_runtime_args(args)

    demo_timeout = float(os.environ.get("A2A_DEMO_MCP_TIMEOUT_SECONDS", "6.0"))
    demo_delay = float(os.environ.get("A2A_DEMO_MCP_DELAY_SECONDS", str(demo_timeout + 2.0)))
    print("================================================================")
    print("🐌 启动 [MCP 超时故障 Demo]")
    print(f"目标: 验证当 MCP Server 响应极慢时，{demo_timeout:g} 秒 MCP HTTP 超时能否被触发，")
    print("      并正常向 Coordinator 返回 status: error，且不影响下游工作流。")
    print("================================================================\n")
    
    old_mcp_timeout = os.environ.get("MCP_HTTP_TIMEOUT_SECONDS")
    old_gateway_timeout = os.environ.get("MCP_GATEWAY_UPSTREAM_TIMEOUT_SECONDS")
    # 同时设置 Agent -> Gateway 与 Gateway -> MCP 两段 HTTP 超时。
    os.environ["MCP_HTTP_TIMEOUT_SECONDS"] = str(demo_timeout)
    os.environ["MCP_GATEWAY_UPSTREAM_TIMEOUT_SECONDS"] = str(demo_timeout)
    extra_args = {
        "weather_mcp_server": ["--delay", str(demo_delay)]
    }
    
    try:
        print(f"Runtime: {runtime_summary(args)}")
        with run_services(
            extra_args=extra_args,
            mode=args.mode,
            startup_delay_seconds=args.startup_delay,
        ):
            time.sleep(2)  # 等待服务完全启动

            print("\n✈️  开始向 Coordinator 提交旅行任务...")
            print(f"预期表现：weather_agent 会在约 {demo_timeout:g} 秒后因 MCP 超时报错，返回 error。")
            print("          Coordinator 记录该错误后继续执行后续节点。")

            run_task_demo(
                args.question,
                timeout=args.timeout,
            )
    finally:
        if old_mcp_timeout is None:
            os.environ.pop("MCP_HTTP_TIMEOUT_SECONDS", None)
        else:
            os.environ["MCP_HTTP_TIMEOUT_SECONDS"] = old_mcp_timeout
        if old_gateway_timeout is None:
            os.environ.pop("MCP_GATEWAY_UPSTREAM_TIMEOUT_SECONDS", None)
        else:
            os.environ["MCP_GATEWAY_UPSTREAM_TIMEOUT_SECONDS"] = old_gateway_timeout


if __name__ == "__main__":
    main()
