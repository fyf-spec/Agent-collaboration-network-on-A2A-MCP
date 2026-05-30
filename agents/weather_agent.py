from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


from agents.base_agent import BaseAgent
from common.config import AGENTS, COORDINATOR_NAME, MCP_SERVERS
from common.schemas import RESULT_SUCCESS, build_error_result_payload, build_result_payload
from llm_client import LLMClientError
from llm_client import llm_small as llm


class WeatherAgent(BaseAgent):
    agent_name = "weather_agent"
    capability = "weather"
    mcp_server_key = "weather"

    def build_mcp_params(self, task_payload: dict[str, Any]) -> dict[str, Any]:
        travel_task = _extract_travel_task(task_payload)
        city = str(
            travel_task.get("destination_city")
            or travel_task.get("city")
            or super().build_mcp_params(task_payload).get("city")
            or "北京"
        ).strip()
        date = str(travel_task.get("start_date") or "明天").strip()
        return {"city": city, "date": date}

    def process_task(self, task_payload: dict[str, Any]) -> None:
        """Call Weather MCP and derive weather constraints with deterministic rules."""
        task_id = str(task_payload["task_id"])
        started = time.perf_counter()
        llm_used = False
        llm_error: str | None = None

        try:
            mcp_result = self.call_mcp_server(task_payload)
            travel_task = _extract_travel_task(task_payload)
            days = int(travel_task.get("days") or 3)
            weather_constraints = _rule_weather_constraints(days, mcp_result)

            elapsed_ms = (time.perf_counter() - started) * 1000
            result_payload = build_result_payload(
                source=self.agent_name,
                target=COORDINATOR_NAME,
                task_id=task_id,
                status=RESULT_SUCCESS,
                result=_short_weather_summary(mcp_result, weather_constraints),
                metadata={
                    "agent": self.agent_name,
                    "capability": self.capability,
                    "mcp_server": MCP_SERVERS[self.mcp_server_key]["name"],
                    "mcp_method": MCP_SERVERS[self.mcp_server_key]["method"],
                    "mcp_result": mcp_result,
                    "travel_task": travel_task,
                    "weather_constraints": weather_constraints,
                    "structured_result": {"weather_constraints": weather_constraints},
                    "quality": {
                        "llm_used": llm_used,
                        "llm_error": llm_error,
                        "source": "weather_agent_rule_activity_fit",
                        "confidence": 0.9,
                    },
                    "llm_error": llm_error,
                    "elapsed_ms": round(elapsed_ms, 2),
                },
            )
        except Exception as exc:
            elapsed_ms = (time.perf_counter() - started) * 1000
            result_payload = build_error_result_payload(
                source=self.agent_name,
                target=COORDINATOR_NAME,
                task_id=task_id,
                message=str(exc),
                error_code="agent_execution_failed",
                http_status=500,
                metadata={
                    "agent": self.agent_name,
                    "capability": self.capability,
                    "elapsed_ms": round(elapsed_ms, 2),
                },
            )

        self.send_result_to_coordinator(task_payload, result_payload)

    def build_prompt(self, task_payload: dict[str, Any], mcp_result: dict[str, Any]) -> str:
        # Kept for compatibility with BaseAgent, but process_task is overridden.
        return "WeatherAgent uses process_task override for rule-based weather constraints."

    def build_fallback_answer(
        self,
        task_payload: dict[str, Any],
        mcp_result: dict[str, Any],
        llm_error: str,
    ) -> str:
        constraints = _rule_weather_constraints(
            int(_extract_travel_task(task_payload).get("days") or 3),
            mcp_result,
        )
        return _short_weather_summary(mcp_result, constraints)


def _extract_travel_task(task_payload: dict[str, Any]) -> dict[str, Any]:
    '''返回payload里面的task'''
    context = task_payload.get("context") or {}
    if isinstance(context.get("travel_task"), dict):
        return dict(context["travel_task"])
    inputs = context.get("inputs") or {}
    if isinstance(inputs, dict) and isinstance(inputs.get("travel_task"), dict):
        return dict(inputs["travel_task"])
    return {}


def _rule_weather_constraints(days: int, mcp_result: dict[str, Any]) -> dict[str, Any]:
    all_days = [f"day{i}" for i in range(1, max(1, days) + 1)]
    condition = str(mcp_result.get("condition", ""))
    wet_or_snowy = any(word in condition for word in ["雨", "雷", "雪"])
    rainy_days = ["day1"] if wet_or_snowy else []
    indoor_days = ["day1"] if wet_or_snowy else []
    outdoor_days = [day for day in all_days if day not in indoor_days]
    return {
        "outdoor_good_days": outdoor_days or all_days,
        "outdoor_suitable_days": outdoor_days or all_days,
        "indoor_preferred_days": indoor_days,
        "rainy_days": rainy_days,
        "weather_by_day": [
            {
                "day": day,
                "condition": condition,
                "outdoor_suitable": day not in indoor_days,
                "indoor_preferred": day in indoor_days,
            }
            for day in all_days
        ],
        "source": "weather_agent_rule_constraints",
        "raw_condition": condition,
        "city": mcp_result.get("city"),
        "date": mcp_result.get("date"),
        "temp": mcp_result.get("temp"),
        "wind": mcp_result.get("wind"),
    }


def _fallback_clothing_advice(temp: str) -> str:
    if any(x in temp for x in ["24", "25", "26", "27", "28", "29", "30"]):
        return "轻薄衣物，备雨具或防晒"
    if any(x in temp for x in ["10", "11", "12", "13", "14", "15", "16", "17", "18"]):
        return "长袖加薄外套"
    return "按实时温度准备衣物"


def _short_weather_summary(mcp_result: dict[str, Any], constraints: dict[str, Any]) -> str:
    city = mcp_result.get("city", "目的地")
    date = mcp_result.get("date", "目标日期")
    condition = mcp_result.get("condition", "未知天气")
    temp = mcp_result.get("temp", "未知温度")
    indoor_days = constraints.get("indoor_preferred_days", [])
    outdoor_days = constraints.get("outdoor_suitable_days") or constraints.get("outdoor_good_days") or []
    return f"已生成天气活动适配：{city}{date}{condition}，气温{temp}，适合户外{outdoor_days}，优先室内{indoor_days}。"

    def build_demo_answer(self, task_payload: dict[str, Any], mcp_result: dict[str, Any]) -> str:
        city = mcp_result.get("city", "目标城市")
        date = mcp_result.get("date", "目标日期")
        temp = mcp_result.get("temp", "未知温度")
        condition = mcp_result.get("condition", "未知天气")
        wind = mcp_result.get("wind", "未知风力")

        return (
            f"天气概况：{city}{date}天气为{condition}，气温{temp}，风力{wind}。\n"
            f"出行影响：请根据天气情况合理安排户外活动。\n"
            f"穿衣建议：建议根据{temp}准备合适衣物。\n"
            f"风险提醒：当前为演示快速模式，已跳过外部 LLM 调用。"
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
