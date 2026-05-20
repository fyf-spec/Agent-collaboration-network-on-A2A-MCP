from __future__ import annotations

import json
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from common.config import COORDINATOR_HOST, COORDINATOR_PORT
from common.http_client import HttpJsonClientError, post_json
from scripts.start_all import run_services


def main() -> None:
    # Agent 调用 MCP 的超时时间默认是 3 秒，5秒触发超时错误
    extra_args = {
        "weather_mcp_server": ["--delay", "5.0"]
    }
    
    with run_services(extra_args=extra_args):
        time.sleep(1)  # 等待服务完全启动

        url = f"http://{COORDINATOR_HOST}:{COORDINATOR_PORT}/submit_task"
        payload = {
            "question": "帮我规划明天去广州的旅行方案，分别考虑天气情况和交通路线，并给出合理的出行建议。",
            "timeout": 60.0,
        }

        try:
            response = post_json(url, payload, timeout=70.0)
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



if __name__ == "__main__":
    main()
