from __future__ import annotations

import json
import sys
import time
import os

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from common.config import COORDINATOR_HOST, COORDINATOR_PORT
from common.http_client import HttpJsonClientError, post_json
from scripts.start_all import run_services


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")


def main() -> None:
    # 不启动 Weather MCP Server
    old_demo_fast = os.environ.get("A2A_DEMO_FAST")
    os.environ["A2A_DEMO_FAST"] = "1"

    try:
        with run_services(exclude=["weather_mcp_server"]):
            time.sleep(1) # 等待端口释放和系统稳定

            url = f"http://{COORDINATOR_HOST}:{COORDINATOR_PORT}/submit_task"
            payload = {
                "question": "帮我规划明天去广州的旅行方案，分别考虑天气情况和交通路线，并给出合理的出行建议。",
                "timeout": 20.0,
            }

            try:
                response = post_json(url, payload, timeout=45.0)
                print(f"====== Get Response (Time elapsed: {response.elapsed_ms:.2f}ms) ======")
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

    print("Sending request to Coordinator")

# 预期行为:
# - Weather Agent调用MCP失败，返回包含错误信息的RESULT_ERROR给 Coordinator
# - Coordinator继续结合Traffic Agent的成功结果输出降级方案


