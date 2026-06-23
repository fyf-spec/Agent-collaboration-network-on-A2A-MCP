import argparse
import sys
import os

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from scripts.demo_utils import run_task_demo
from scripts.demo_runtime import add_runtime_args, apply_runtime_args, runtime_summary
from scripts.start_all import run_services
import time


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the normal end-to-end travel demo.")
    add_runtime_args(parser)
    parser.add_argument(
        "--question",
        default="请帮我规划从上海去杭州的三天旅行计划，预算适中，想去西湖和灵隐寺，尽量使用地铁和步行。",
        help="Travel planning request to submit.",
    )
    parser.add_argument("--timeout", type=float, default=600.0)
    parser.add_argument("--startup-delay", type=float, default=0.3)
    args = parser.parse_args()
    apply_runtime_args(args)

    print(f"Runtime: {runtime_summary(args)}")
    with run_services(mode=args.mode, startup_delay_seconds=args.startup_delay):
        time.sleep(2.0)  # 等待所有服务完全启动就绪

        run_task_demo(
            args.question,
            timeout=args.timeout,
        )


if __name__ == "__main__":
    main()
