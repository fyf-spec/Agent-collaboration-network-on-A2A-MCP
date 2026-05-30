import json
import sys
import os

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from common.config import COORDINATOR_HOST, COORDINATOR_PORT
from common.http_client import HttpJsonClientError, post_json
from scripts.start_all import run_services
import time


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")


def main() -> None:
    with run_services():
        time.sleep(2.0)  # 等待所有服务完全启动就绪
        
        url = f"http://{COORDINATOR_HOST}:{COORDINATOR_PORT}/submit_task"
        payload = {
            "question": "请帮我规划从上海去北京的五天低预算旅行计划，尽量公共交通，故宫和天安门一定要去。",
            "timeout": 600.0
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


if __name__ == "__main__":
    main()
