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
from common.schemas import RESULT_ERROR, RESULT_SUCCESS, build_result_payload
from llm_client import LLMClientError
from llm_client import llm_small as llm


class WeatherAgent(BaseAgent):
    agent_name = "weather_agent"
    capability = "weather"
    mcp_server_key = "weather"

    def process_task(self, task_payload: dict[str, Any]) -> None:
        """Use LLM only for a short JSON weather-constraint decision."""
        task_id = str(task_payload["task_id"])
        started = time.perf_counter()
        llm_used = False
        llm_error: str | None = None

        try:
            mcp_result = self.call_mcp_server(task_payload)
            travel_task = _extract_travel_task(task_payload)
            days = int(travel_task.get("days") or 3)

            try:
                llm_json = llm.chat_json(
                    _weather_constraint_prompt(travel_task, mcp_result),
                    max_tokens=350,
                    temperature=0.0,
                    timeout_seconds=12.0,
                )
                weather_constraints = _normalize_weather_constraints(
                    llm_json.get("weather_constraints") or llm_json,
                    days=days,
                    mcp_result=mcp_result,
                )
                llm_used = True
            except Exception as exc:
                llm_error = str(exc)
                weather_constraints = _fallback_weather_constraints(days, mcp_result)

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
                        "confidence": 0.9 if llm_error is None else 0.72,
                    },
                    "llm_error": llm_error,
                    "elapsed_ms": round(elapsed_ms, 2),
                },
            )
        except Exception as exc:
            elapsed_ms = (time.perf_counter() - started) * 1000
            result_payload = build_result_payload(
                source=self.agent_name,
                target=COORDINATOR_NAME,
                task_id=task_id,
                status=RESULT_ERROR,
                result=None,
                error=str(exc),
                metadata={
                    "agent": self.agent_name,
                    "capability": self.capability,
                    "elapsed_ms": round(elapsed_ms, 2),
                },
            )

        self.send_result_to_coordinator(task_payload, result_payload)

    def build_prompt(self, task_payload: dict[str, Any], mcp_result: dict[str, Any]) -> str:
        # Kept for compatibility with BaseAgent, but process_task is overridden.
        return _weather_constraint_prompt(_extract_travel_task(task_payload), mcp_result)

    def build_fallback_answer(
        self,
        task_payload: dict[str, Any],
        mcp_result: dict[str, Any],
        llm_error: str,
    ) -> str:
        constraints = _fallback_weather_constraints(
            int(_extract_travel_task(task_payload).get("days") or 3),
            mcp_result,
        )
        return _short_weather_summary(mcp_result, constraints)


def _extract_travel_task(task_payload: dict[str, Any]) -> dict[str, Any]:
    context = task_payload.get("context") or {}
    if isinstance(context.get("travel_task"), dict):
        return dict(context["travel_task"])
    inputs = context.get("inputs") or {}
    if isinstance(inputs, dict) and isinstance(inputs.get("travel_task"), dict):
        return dict(inputs["travel_task"])
    return {}


def _weather_constraint_prompt(travel_task: dict[str, Any], mcp_result: dict[str, Any]) -> str:
    payload = {
        "travel_task": travel_task,
        "weather_mcp_result": mcp_result,
        "output_schema": {
            "weather_constraints": {
                "risk_level": "low|medium|high",
                "outdoor_good_days": ["day1"],
                "indoor_preferred_days": [],
                "rainy_days": [],
                "schedule_advice": "不超过25字",
                "clothing_advice": "不超过25字",
            }
        },
    }
    return "\n".join(
        [
            "你是 Weather Agent，只做天气约束判断。",
            "根据 travel_task 和 Weather MCP 数据输出严格 JSON。",
            "不要 Markdown，不要解释，不要生成完整旅行方案。",
            "如果只有一天的天气数据，可将判断扩展到全部 travel days，并在 source 中说明。",
            json.dumps(payload, ensure_ascii=False, default=str),
        ]
    )


def _normalize_weather_constraints(value: Any, *, days: int, mcp_result: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict):
        return _fallback_weather_constraints(days, mcp_result)
    all_days = [f"day{i}" for i in range(1, max(1, days) + 1)]
    constraints = _fallback_weather_constraints(days, mcp_result)
    for key in ["risk_level", "schedule_advice", "clothing_advice"]:
        if isinstance(value.get(key), str) and value[key].strip():
            constraints[key] = value[key].strip()
    for key in ["outdoor_good_days", "indoor_preferred_days", "rainy_days"]:
        if isinstance(value.get(key), list):
            days_value = [str(item) for item in value[key] if str(item).startswith("day")]
            constraints[key] = [item for item in days_value if item in all_days]
    constraints["source"] = "weather_agent_llm_constraints"
    constraints["raw_condition"] = mcp_result.get("condition")
    constraints["city"] = mcp_result.get("city")
    constraints["date"] = mcp_result.get("date")
    return constraints


def _fallback_weather_constraints(days: int, mcp_result: dict[str, Any]) -> dict[str, Any]:
    all_days = [f"day{i}" for i in range(1, max(1, days) + 1)]
    condition = str(mcp_result.get("condition", ""))
    rainy = any(word in condition for word in ["雨", "雪", "雷", "大风"])
    rainy_days = ["day1"] if rainy else []
    indoor_days = ["day1"] if rainy else []
    outdoor_days = [day for day in all_days if day not in indoor_days]
    return {
        "risk_level": "medium" if rainy else "low",
        "outdoor_good_days": outdoor_days or all_days,
        "indoor_preferred_days": indoor_days,
        "rainy_days": rainy_days,
        "schedule_advice": "雨天优先室内景点" if rainy else "适合安排户外景点",
        "clothing_advice": _fallback_clothing_advice(str(mcp_result.get("temp", ""))),
        "source": "weather_agent_rule_fallback",
        "raw_condition": condition,
        "city": mcp_result.get("city"),
        "date": mcp_result.get("date"),
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
    risk = constraints.get("risk_level", "unknown")
    advice = constraints.get("schedule_advice", "已生成天气约束")
    return f"已生成天气约束：{city}{date}{condition}，气温{temp}，风险等级{risk}，{advice}。"

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
