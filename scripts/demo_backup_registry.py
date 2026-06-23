from __future__ import annotations

import argparse
import os
import signal
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
    parser = argparse.ArgumentParser(description="Run the backup registry failover demo.")
    add_runtime_args(parser)
    parser.add_argument(
        "--question",
        default="请帮我规划从上海去杭州的三天旅行计划，预算适中，想去西湖和灵隐寺，尽量使用地铁和步行。",
    )
    parser.add_argument("--timeout", type=float, default=600.0)
    parser.add_argument("--startup-delay", type=float, default=0.3)
    args = parser.parse_args()
    apply_runtime_args(args)

    print("================================================================")
    print("🚀 启动 [双注册中心高可用 Demo]")
    print("目标: 验证主注册中心宕机时，系统是否能自动切换到备用注册中心并完成任务。")
    print("================================================================\n")
    
    # 拉起所有服务（包括 primary 和 backup 两个注册中心）
    print(f"Runtime: {runtime_summary(args)}")
    with run_services(mode=args.mode, startup_delay_seconds=args.startup_delay) as processes:
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
        print("预期表现：Coordinator 在主节点不可用时切换到备用节点，任务正常执行。")
        
        run_task_demo(
            args.question,
            timeout=args.timeout,
        )


if __name__ == "__main__":
    main()
