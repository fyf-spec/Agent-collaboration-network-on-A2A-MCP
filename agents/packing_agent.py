from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agents.base_agent import BaseAgent
from agents.request_parser import extract_travel_task_from_payload
from common.config import COORDINATOR_NAME, MCP_SERVERS, MCP_GATEWAY, MCP_HTTP_TIMEOUT_SECONDS
from common.http_client import HttpJsonClientError, post_json
from common.logger import log_network_event
from common.runtime import no_llm_mode_enabled
from common.schemas import (
    RESULT_ERROR,
    RESULT_SUCCESS,
    PayloadValidationError,
    build_result_payload,
    validate_task_payload,
)
from llm_client import llm_small as llm

AGENT_NAME = "packing_agent"
CAPABILITY = "packing"

class PackingAgent(BaseAgent):
    agent_name = "packing_agent"
    capability = "packing"
    mcp_server_key = "packing"

    def process_task(self, task_payload: dict[str, Any]) -> None:
        # 处理行李准备任务
        task_id = str(task_payload["task_id"])
        started = time.perf_counter()
        
        try:
            context = task_payload.get("context") or {}
            travel_task = _extract_travel_task(task_payload)
            inputs = context.get("inputs") or {}
            upstream_results = inputs.get("upstream_results", {})
            
            # Extract multi-day weather from upstream
            weather_struct = upstream_results.get("weather_agent", {}).get("structured", {})
            weather_constraints = weather_struct.get("weather_constraints", weather_struct)
            weather_by_day = weather_constraints.get("weather_by_day", [])
            city = str(travel_task.get("destination_city") or travel_task.get("city") or "未指定")
            days = travel_task.get("days", 3)

            # 汇总多日天气：温度范围、天气状况、雨天标记
            all_conditions: list[str] = []
            all_temps_min: list[float] = []
            all_temps_max: list[float] = []
            rainy_days = weather_constraints.get("rainy_days", [])
            for wd in (weather_by_day or []):
                if isinstance(wd, dict):
                    cond = str(wd.get("condition") or "").strip()
                    if cond and cond != "待确认" and "待确认" not in cond:
                        all_conditions.append(cond)
                    for key in ("temp_min", "temp_max", "temp"):
                        val = wd.get(key)
                        if val is not None:
                            try:
                                num = float(str(val).replace("C", "").replace("°", "").strip())
                                all_temps_max.append(num) if "max" in key or key == "temp" else all_temps_min.append(num)
                            except (ValueError, TypeError):
                                pass
            temp_range = f"{min(all_temps_min):.0f}-{max(all_temps_max):.0f}°C" if all_temps_min and all_temps_max else str(weather_struct.get("temp", ""))
            condition_summary = "、".join(dict.fromkeys(all_conditions[:5])) or str(weather_struct.get("condition", ""))
            has_rain = bool(rainy_days) or any("雨" in c or "雪" in c for c in all_conditions)

            # Step 1: Call MCP with enriched weather
            mcp_result = self.call_packing_mcp(
                task_id,
                city=city,
                days=days,
                temperature=temp_range,
                condition=condition_summary
            )
            
            prompt = build_packaging_prompt(task_payload, upstream_results, mcp_result)
            packing_list = mcp_result.get("packing_list", [])
            llm_used = False
            llm_error = None
            quality_source = "packing_agent_mcp_rule_summary"

            if no_llm_mode_enabled():
                summary = _packing_summary_from_list(packing_list)
                quality_source = "packing_agent_mcp_rule_summary_no_llm"
            else:
                try:
                    llm_json = llm.chat_json(
                        prompt,
                        max_tokens=600,
                        temperature=0.7,
                        timeout_seconds=20.0,
                    )
                    packing_list = llm_json.get("packing_list", packing_list)
                    summary = llm_json.get("summary", "行李准备清单已生成。")
                    llm_used = True
                    quality_source = "packing_agent_llm_summary"
                except Exception as exc:
                    llm_error = str(exc)
                    summary = _packing_summary_from_list(packing_list)

            elapsed_ms = (time.perf_counter() - started) * 1000
            
            structured_result = {
                "packing_list": packing_list,
                "summary": summary
            }

            result_payload = build_result_payload(
                source=self.agent_name,
                target=COORDINATOR_NAME,
                task_id=task_id,
                status=RESULT_SUCCESS,
                result=summary,
                metadata={
                    "agent": self.agent_name,
                    "capability": self.capability,
                    "workflow": "packing_mcp_then_llm",
                    "mcp_server": MCP_SERVERS[self.mcp_server_key]["name"],
                    "mcp_method": "get_packing_list",
                    "mcp_result": mcp_result,
                    "travel_task": travel_task,
                    "upstream_results": upstream_results,
                    "structured_result": structured_result,
                    "quality": {
                        "llm_used": llm_used,
                        "llm_error": llm_error,
                        "source": quality_source,
                        "confidence": 0.9 if llm_error is None else 0.7,
                    },
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

    def call_packing_mcp(
        self,
        task_id: str,
        *,
        city: str,
        days: int,
        temperature: str,
        condition: str,
    ) -> dict[str, Any]:
        # 调用行李准备 MCP 获取基础清单
        server = MCP_SERVERS[self.mcp_server_key]
        url = f"http://{MCP_GATEWAY['host']}:{MCP_GATEWAY['port']}{MCP_GATEWAY.get('path', '/')}"
        network_target = str(MCP_GATEWAY["name"])
        rpc_payload = {
            "jsonrpc": "2.0",
            "id": task_id,
            "method": "get_packing_list",
            "params": {
                "city": city,
                "days": days,
                "temperature": temperature,
                "condition": condition,
            },
        }
        log_network_event(
            event="agent_call_mcp",
            direction="outbound",
            source=self.agent_name,
            target=network_target,
            method="POST",
            url=url,
            task_id=task_id,
            payload=rpc_payload,
        )
        try:
            response = post_json(url, rpc_payload, timeout=MCP_HTTP_TIMEOUT_SECONDS)
        except HttpJsonClientError as exc:
            log_network_event(
                event="agent_mcp_failed",
                direction="inbound",
                source=network_target,
                target=self.agent_name,
                method="POST",
                url=exc.url,
                task_id=task_id,
                error=str(exc),
                elapsed_ms=exc.elapsed_ms,
                error_type=type(exc).__name__,
            )
            raise
        log_network_event(
            event="agent_mcp_response",
            direction="inbound",
            source=network_target,
            target=self.agent_name,
            method="POST",
            url=url,
            task_id=task_id,
            status_code=response.status_code,
            elapsed_ms=response.elapsed_ms,
            payload=response.data,
        )
        if not response.ok or not isinstance(response.data, dict):
            raise RuntimeError(f"Packing MCP returned invalid response: {response.status_code} {response.raw_body}")
        if response.data.get("error"):
            raise RuntimeError(f"Packing MCP error: {response.data['error']}")
        result = response.data.get("result")
        if not isinstance(result, dict):
            raise RuntimeError("Packing MCP result missing")
        return result

def _extract_travel_task(task_payload: dict[str, Any]) -> dict[str, Any]:
    return extract_travel_task_from_payload(task_payload, capability="packing")

def build_packaging_prompt(task_payload: dict[str, Any], upstream_results: dict[str, Any], mcp_result: dict[str, Any]) -> str:
    travel_task = _extract_travel_task(task_payload)
    # 构造行李准备 LLM 提示词
    # 提取天气摘要让 LLM 看到
    weather_struct = upstream_results.get("weather_agent", {}).get("structured", {})
    wc = weather_struct.get("weather_constraints", weather_struct)
    weather_by_day = wc.get("weather_by_day", [])
    city = travel_task.get("destination_city", "未知")
    days = travel_task.get("days", 3)

    weather_summary = "未获取到天气信息"
    if weather_by_day:
        lines = []
        for wd in weather_by_day:
            if isinstance(wd, dict):
                d = wd.get("day", "?")
                c = wd.get("condition", "?")
                tmin = wd.get("temp_min", "")
                tmax = wd.get("temp_max", "")
                t = f"{tmin}~{tmax}" if tmin and tmax else wd.get("temp", "?")
                lines.append(f"{d}: {c} {t}")
        if lines:
            weather_summary = f"{city}{days}天天气: " + "; ".join(lines)

    payload = {
        "destination": city,
        "days": days,
        "weather_summary": weather_summary,
        "travel_task": travel_task,
        "mcp_packing_list": mcp_result.get("packing_list", []),
        "output_schema": {
            "packing_list": [
                {"category": "类别名(如证件/衣物)", "items": ["物品1", "物品2"], "reason": "为什么带(基于天气或行程)"}
            ],
            "summary": "一句简短的总结"
        }
    }

    return "\n".join([
        "你是 Packing Agent，负责根据目的地、天数、天气情况，生成贴心的行李准备清单。",
        f"请根据 weather_summary 中的每天天气和温度，给出具体的衣物建议（如温度>30°C建议短袖、<10°C建议羽绒服等）。",
        "可以参考 mcp_packing_list 中的基础物品，根据实际天气进行个性化调整。",
        "必须输出严格的 JSON 格式，不要输出 Markdown 或其他解释文字。",
        json.dumps(payload, ensure_ascii=False, default=str)
    ])


def _packing_summary_from_list(packing_list: Any) -> str:
    if not isinstance(packing_list, list) or not packing_list:
        return "已根据目的地、天数和天气生成基础行李准备清单。"
    categories = []
    for item in packing_list:
        if isinstance(item, dict):
            category = str(item.get("category") or "").strip()
            if category:
                categories.append(category)
    if categories:
        shown = "、".join(dict.fromkeys(categories[:5]))
        return f"已生成行李准备清单，覆盖{shown}等类别。"
    return "已根据 MCP 返回数据生成行李准备清单。"

def main() -> None:
    # 启动行李准备 Agent
    from common.config import AGENTS
    default_host = AGENTS[AGENT_NAME]["host"]
    default_port = AGENTS[AGENT_NAME]["port"]

    parser = argparse.ArgumentParser(description="Run Packing Agent.")
    parser.add_argument("--host", default=default_host)
    parser.add_argument("--port", type=int, default=default_port)
    args = parser.parse_args()

    agent = PackingAgent(host=args.host, port=args.port)
    agent.run()

if __name__ == "__main__":
    main()
