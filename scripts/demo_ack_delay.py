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
    # 让 weather_agent 在 TCP A2A 握手时强行睡眠 5 秒，触发 Coordinator 的 3 秒派发超时
    # 从而产生真实的 DISPATCH_ERROR
    os.environ["A2A_DELAY_ACK"] = "weather_agent"
    
    old_demo_fast = os.environ.get("A2A_DEMO_FAST")
    os.environ["A2A_DEMO_FAST"] = "1"

    try:
        with run_services():
            time.sleep(2)  # 等待服务完全启动

            url = f"http://{COORDINATOR_HOST}:{COORDINATOR_PORT}/submit_task"
            payload = {
                "question": "帮我规划明天去广州的旅行方案，分别考虑天气情况和交通路线，并给出合理的出行建议。",
                "timeout": 600.0,
            }

            try:
                response = post_json(url, payload, timeout=660.0)
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
        os.environ.pop("A2A_DELAY_ACK", None)
        if old_demo_fast is None:
            os.environ.pop("A2A_DEMO_FAST", None)
        else:
            os.environ["A2A_DEMO_FAST"] = old_demo_fast



if __name__ == "__main__":
    main()
