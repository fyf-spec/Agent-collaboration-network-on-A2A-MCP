import sys
import os

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from scripts.demo_utils import run_task_demo
from scripts.start_all import run_services
import time


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")


def main() -> None:
    with run_services(mode="no-llm"):
        time.sleep(2.0)  # 等待所有服务完全启动就绪

        run_task_demo(
            "请帮我规划从上海去北京的五天低预算旅行计划，尽量公共交通，故宫和天安门一定要去。",
            timeout=600.0,
        )


if __name__ == "__main__":
    main()
