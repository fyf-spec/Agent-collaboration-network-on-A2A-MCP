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


class WeatherAgent(BaseAgent):
    agent_name = "weather_agent"
    capability = "weather"
    mcp_server_key = "weather"

    def build_prompt(self, task_payload: dict[str, Any], mcp_result: dict[str, Any]) -> str:
        instruction = str(task_payload.get("instruction", ""))

        return "\n".join(
            [
                "A2A_WEATHER_AGENT",
                "你是一个旅行天气顾问。",
                "你只能根据 Weather MCP Server 返回的天气数据给出建议，不能自行编造天气信息。",
                "你的任务是根据用户出行需求和天气数据，输出简洁、可执行的旅行天气建议。",
                "",
                "请严格按照以下结构回答：",
                "1. 天气概况：说明城市、日期、温度、天气、风力。",
                "2. 出行影响：说明是否适合户外活动。",
                "3. 穿衣建议：给出具体穿衣建议。",
                "4. 风险提醒：如降雨、温差、强风等；如果数据中没有明显风险，就说明暂无明显天气风险。",
                "",
                "不要回答交通、酒店、餐饮问题。",
                "不要虚构 MCP 数据中不存在的天气指标。",
                "",
                f"用户原始需求：{instruction}",
                f"Weather MCP 数据：{json.dumps(mcp_result, ensure_ascii=False)}",
            ]
        )

    def build_fallback_answer(
        self,
        task_payload: dict[str, Any],
        mcp_result: dict[str, Any],
        llm_error: str,
    ) -> str:
        city = mcp_result.get("city", "目标城市")
        date = mcp_result.get("date", "目标日期")
        temp = mcp_result.get("temp", "未知温度")
        condition = mcp_result.get("condition", "未知天气")
        wind = mcp_result.get("wind", "未知风力")

        return (
            f"天气概况：{city}{date}天气为{condition}，气温{temp}，风力{wind}。\n"
            f"出行影响：请根据天气情况合理安排户外活动。\n"
            f"穿衣建议：建议根据{temp}准备合适衣物。\n"
            f"风险提醒：当前为备用回答，LLM 调用失败，错误为：{llm_error}"
        )


def main() -> None:
    default_host = AGENTS["weather_agent"]["host"]
    default_port = AGENTS["weather_agent"]["port"]

    parser = argparse.ArgumentParser(description="Run Weather Agent.")
    parser.add_argument("--host", default=default_host)
    parser.add_argument("--port", type=int, default=default_port)
    args = parser.parse_args()

    agent = WeatherAgent(host=args.host, port=args.port)
    agent.run()


if __name__ == "__main__":
    main()