from __future__ import annotations

import json
import os
import signal
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
    print("🚀 启动 [双注册中心高可用 Demo]")
    print("目标: 验证主注册中心宕机时，系统是否能自动切换到备用注册中心并完成任务。")
    print("================================================================\n")
    
    old_demo_fast = os.environ.get("A2A_DEMO_FAST")
    os.environ["A2A_DEMO_FAST"] = "1"

    try:
        # 拉起所有服务（包括 primary 和 backup 两个注册中心）
        with run_services() as processes:
            print("⏳ 等待 3 秒，让所有 Agent 向两个注册中心完成初始注册和心跳...")
            time.sleep(3)
            
            # 1. 找到并杀掉主注册中心 (registry_center_primary)
            primary_process = None
            for name, proc in processes:
                if name == "registry_center_primary":
                    primary_process = proc
                    break
            
            if primary_process:
                print(f"\n💥 [模拟灾难] 正在强行终止主注册中心 (pid={primary_process.pid})...")
                if os.name == "nt":
                    primary_process.terminate()
                else:
                    primary_process.send_signal(signal.SIGTERM)
                time.sleep(1) # 给点时间让它死透
                print("💀 主注册中心已宕机！")
            else:
                print("❌ 找不到主注册中心进程！")
                return

            print("\n✈️  开始向 Coordinator 提交旅行任务...")
            print("预期表现：Coordinator 请求主节点会超时/拒绝连接，然后自动 fallback 到备用节点，任务正常执行。")
            
            url = f"http://{COORDINATOR_HOST}:{COORDINATOR_PORT}/submit_task"
            payload = {
                "question": "请帮我规划从上海去北京的五天低预算旅行计划，尽量公共交通，故宫和天安门一定要去。",
                "timeout": 600.0,
            }

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