from __future__ import annotations

import sys
import time
import os

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from scripts.demo_utils import run_task_demo
from scripts.start_all import run_services


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")


def main() -> None:
    # 不启动 Weather MCP Server
    with run_services(exclude=["weather_mcp_server"], mode="no-llm"):
        time.sleep(1) # 等待端口释放和系统稳定

        run_task_demo(
            "帮我规划明天去广州的旅行方案，分别考虑天气情况和交通路线，并给出合理的出行建议。",
            timeout=20.0,
        )


if __name__ == "__main__":
    main()

    print("Sending request to Coordinator")

# 预期行为:
# - Weather Agent调用MCP失败，返回包含错误信息的RESULT_ERROR给 Coordinator
# - Coordinator继续结合Traffic Agent的成功结果输出降级方案


