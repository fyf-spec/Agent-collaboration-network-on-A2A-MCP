from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


from agents.base_agent import BaseAgent
from common.config import AGENTS


class TrafficAgent(BaseAgent):
    agent_name = "traffic_agent"
    capability = "traffic"
    mcp_server_key = "traffic"

    def build_prompt(self, task_payload: dict[str, Any], mcp_result: dict[str, Any]) -> str:
        instruction = str(task_payload.get("instruction", ""))

        return "\n".join(
            [
                "A2A_TRAFFIC_AGENT",
                "你是一个交通规划顾问。",
                "你只能根据 Traffic MCP Server 返回的交通数据给出建议，不能自行编造路线、价格、车次、航班或耗时。",
                "你的任务是根据用户出行需求和交通数据，推荐合适的交通方式或市内路线。",
                "",
                "请严格按照以下结构回答：",
                "1. 交通概况：说明城市、路线、路况、预计耗时。",
                "2. 推荐方案：给出最合适的交通选择。",
                "3. 推荐理由：从时间、稳定性、便利性角度解释。",
                "4. 注意事项：如高峰期、天气影响、提前出发等。",
                "",
                "不要回答天气、酒店、餐饮问题。",
                "不要虚构 MCP 数据中不存在的车次、航班、价格或路线。",
                "",
                f"用户原始需求：{instruction}",
                f"Traffic MCP 数据：{json.dumps(mcp_result, ensure_ascii=False)}",
            ]
        )

    def build_fallback_answer(
        self,
        task_payload: dict[str, Any],
        mcp_result: dict[str, Any],
        llm_error: str,
    ) -> str:
        city = mcp_result.get("city", "目标城市")
        route = mcp_result.get("route", "未知路线")
        status = mcp_result.get("status", "未知路况")
        duration = mcp_result.get("duration", "未知耗时")

        return (
            f"交通概况：{city}推荐路线为{route}，当前路况为{status}，预计耗时{duration}。\n"
            f"推荐方案：建议优先选择上述路线，并预留一定机动时间。\n"
            f"注意事项：当前为备用回答，LLM 调用失败，错误为：{llm_error}"
        )


def main() -> None:
    default_host = AGENTS["traffic_agent"]["host"]
    default_port = AGENTS["traffic_agent"]["port"]

    parser = argparse.ArgumentParser(description="Run Traffic Agent.")
    parser.add_argument("--host", default=default_host)
    parser.add_argument("--port", type=int, default=default_port)
    args = parser.parse_args()

    agent = TrafficAgent(host=args.host, port=args.port)
    agent.run()


if __name__ == "__main__":
    main()
