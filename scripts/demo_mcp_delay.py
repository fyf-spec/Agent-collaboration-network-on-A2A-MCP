from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from common.config import COORDINATOR_HOST, COORDINATOR_PORT
from common.http_client import HttpJsonClientError, post_json
from scripts.start_all import run_services


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")


def main() -> None:
    print("================================================================")
    print("🐌 启动 [MCP 超时故障 Demo]")
    print("目标: 验证当 MCP Server 响应极慢时，Agent 内部的 3 秒短超时能否被触发，")
    print("      并正常向 Coordinator 返回 status: error，且不影响下游工作流。")
    print("================================================================\n")
    
    # 利用 extra_args 让 weather_mcp_server 强行延迟 5 秒返回数据
    # 因为 Agent 调用 MCP 的 HTTP 超时配置（MCP_HTTP_TIMEOUT_SECONDS）是 3 秒，这必然会引发 Agent 内部报错
    extra_args = {
        "weather_mcp_server": ["--delay", "5.0"]
    }
    
    old_demo_fast = os.environ.get("A2A_DEMO_FAST")
    os.environ["A2A_DEMO_FAST"] = "1"

    try:
        with run_services(extra_args=extra_args):
            time.sleep(2)  # 等待服务完全启动

            url = f"http://{COORDINATOR_HOST}:{COORDINATOR_PORT}/submit_task"
            payload = {
                "question": "请帮我规划从上海去北京的五天低预算旅行计划，尽量公共交通，故宫和天安门一定要去。",
                "timeout": 600.0,
            }

            print("\n✈️  开始向 Coordinator 提交旅行任务...")
            print("预期表现：weather_agent 会在约 3 秒后因 MCP 超时报错，返回 error。")
            print("          但 Coordinator 不会崩溃，后续节点将带着这个 error 继续规划。")

            try:
                response = post_json(url, payload, timeout=660.0)
                print(f"\n====== Get Response (Time elapsed: {response.elapsed_ms:.2f}ms) ======")
                print(f"HTTP Status Code: {response.status_code}")
                
                if response.ok and response.data:
                    task = response.data.get("task", {})
                    print(f"\nTask Status: {task.get('status')}")
                    print(f"Final Answer:\n")
                    print(task.get("final_answer", ""))
                    
                    print("\nAnswers of Agents:")
                    results = task.get("results", {})
                    errors = task.get("dispatch_errors", {})
                    
                    for agent, result in results.items():
                        print(f"- {agent}: {result.get('status')}\n{result.get('error') or result.get('result')[:50] + '...'}")
                    
                    for agent, err in errors.items():
                        print(f"- {agent} [DISPATCH_ERROR]: {err}")
                else:
                    print(f"Request Failed:\n{json.dumps(response.data, indent=2, ensure_ascii=False)}")

            except HttpJsonClientError as exc:
                print(f"HTTP Request Error: {exc}")
            except Exception as e:
                print(f"Unknown Error: {str(e)}")
    finally:
        if old_demo_fast is None:
            os.environ.pop("A2A_DEMO_FAST", None)
        else:
            os.environ["A2A_DEMO_FAST"] = old_demo_fast


if __name__ == "__main__":
    main()