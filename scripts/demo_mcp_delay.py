from __future__ import annotations

import os
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.demo_utils import run_task_demo
from scripts.start_all import run_services


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")


def main() -> None:
    demo_timeout = float(os.environ.get("A2A_DEMO_MCP_TIMEOUT_SECONDS", "3.0"))
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
        with run_services(extra_args=extra_args, mode="no-llm"):
            time.sleep(2)  # 等待服务完全启动

            print("\n✈️  开始向 Coordinator 提交旅行任务...")
            print(f"预期表现：weather_agent 会在约 {demo_timeout:g} 秒后因 MCP 超时报错，返回 error。")
            print("          但 Coordinator 不会崩溃，后续节点将带着这个 error 继续规划。")

            run_task_demo(
                "请帮我规划从上海去北京的五天低预算旅行计划，尽量公共交通，故宫和天安门一定要去。",
                timeout=600.0,
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
