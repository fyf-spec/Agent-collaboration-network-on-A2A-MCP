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
from common.config import COORDINATOR_NAME, MCP_SERVERS, MCP_GATEWAY, MCP_HTTP_TIMEOUT_SECONDS
from common.http_client import HttpJsonClientError, post_json
from common.logger import log_network_event
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
        task_id = str(task_payload["task_id"])
        started = time.perf_counter()
        
        try:
            context = task_payload.get("context") or {}
            travel_task = _extract_travel_task(context)
            inputs = context.get("inputs") or {}
            upstream_results = inputs.get("upstream_results", {})
            
            # Extract weather condition for MCP
            weather_data = upstream_results.get("weather_agent", {}).get("structured", {})
            temperature = str(weather_data.get("temp", ""))
            condition = str(weather_data.get("condition", ""))
            city = str(travel_task.get("destination_city") or travel_task.get("city") or "北京")
            days = travel_task.get("days", 3)

            # Step 1: Call MCP
            mcp_result = self.call_packing_mcp(
                task_id,
                city=city,
                days=days,
                temperature=temperature,
                condition=condition
            )
            
            prompt = build_packaging_prompt(task_payload, upstream_results, mcp_result)
            
            try:
                llm_json = llm.chat_json(
                    prompt,
                    max_tokens=600,
                    temperature=0.7,
                    timeout_seconds=20.0,
                )
                packing_list = llm_json.get("packing_list", mcp_result.get("packing_list", []))
                summary = llm_json.get("summary", "行李准备清单已生成。")
                llm_used = True
                llm_error = None
            except Exception as exc:
                llm_error = str(exc)
                packing_list = mcp_result.get("packing_list", [])
                summary = "由于网络原因，为您直接返回了基础版行李准备清单。"
                llm_used = False

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
                        "confidence": 0.9 if llm_used else 0.7,
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

def _extract_travel_task(context: dict[str, Any]) -> dict[str, Any]:
    if isinstance(context.get("travel_task"), dict):
        return dict(context["travel_task"])
    inputs = context.get("inputs") or {}
    if isinstance(inputs, dict) and isinstance(inputs.get("travel_task"), dict):
        return dict(inputs["travel_task"])
    return {}

def build_packaging_prompt(task_payload: dict[str, Any], upstream_results: dict[str, Any], mcp_result: dict[str, Any]) -> str:
    context = task_payload.get("context") or {}
    travel_task = context.get("travel_task") or {}
    
    payload = {
        "travel_task": travel_task,
        "upstream_results": upstream_results,
        "mcp_packing_list": mcp_result.get("packing_list", []),
        "output_schema": {
            "packing_list": [
                {"category": "类别名(如证件/衣物)", "items": ["物品1", "物品2"], "reason": "为什么带(基于天气或行程)"}
            ],
            "summary": "一句简短的总结"
        }
    }
    
    return "\n".join([
        "你是 Packing Agent，负责根据目的地、天数、天气情况，以及 MCP 返回的基础行李清单，生成贴心的最终行李准备清单。",
        "请仔细参考上下文中 mcp_packing_list 内的基础物品，可以对其进行补充、精简和个性化调整。",
        "必须输出严格的 JSON 格式，不要输出 Markdown 或其他解释文字。",
        json.dumps(payload, ensure_ascii=False, default=str)
    ])

def main() -> None:
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