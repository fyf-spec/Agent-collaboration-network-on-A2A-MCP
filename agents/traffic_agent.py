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
from common.config import AGENTS, COORDINATOR_NAME, MCP_GATEWAY, MCP_HTTP_TIMEOUT_SECONDS, MCP_SERVERS
from common.http_client import HttpJsonClientError, post_json
from common.logger import log_network_event
from common.schemas import RESULT_ERROR, RESULT_SUCCESS, build_result_payload
from llm_client import llm_small as llm


class TrafficAgent(BaseAgent):
    agent_name = "traffic_agent"
    capability = "traffic"
    mcp_server_key = "traffic"

    def process_task(self, task_payload: dict[str, Any]) -> None:
        """Use MCP to get route candidates and LLM only for short JSON selection."""
        task_id = str(task_payload["task_id"])
        started = time.perf_counter()
        llm_used = False
        llm_error: str | None = None

        try:
            context = task_payload.get("context") or {}
            travel_task = _extract_travel_task(context)
            daily_plan = _extract_daily_plan(context)
            hotel_plan = _extract_hotel_plan(context)
            constraints_for_traffic = _extract_constraints_for_traffic(context)
            city = str(travel_task.get("destination_city") or travel_task.get("city") or self.build_mcp_params(task_payload).get("city") or "北京")
            preference = str(travel_task.get("transport_preference") or "public_transport")
            intercity_transport = self.call_intercity_transport_mcp(task_id, travel_task=travel_task)

            route_segments = _build_route_segments(daily_plan, hotel_plan)
            route_results = [
                self.call_route_mcp(task_id, city=city, segment=segment, preference=preference)
                for segment in route_segments
            ]

            try:
                llm_json = llm.chat_json(
                    _traffic_selection_prompt(travel_task, daily_plan, hotel_plan, constraints_for_traffic, route_results),
                    max_tokens=1100,
                    temperature=0.0,
                    timeout_seconds=18.0,
                )
                structured_result = _normalize_traffic_plan(
                    llm_json,
                    route_results=route_results,
                    travel_task=travel_task,
                )
                structured_result["intercity_transport"] = intercity_transport
                llm_used = True
            except Exception as exc:
                llm_error = str(exc)
                structured_result = _fallback_traffic_plan(route_results, travel_task)
                structured_result["intercity_transport"] = intercity_transport

            elapsed_ms = (time.perf_counter() - started) * 1000
            result_payload = build_result_payload(
                source=self.agent_name,
                target=COORDINATOR_NAME,
                task_id=task_id,
                status=RESULT_SUCCESS,
                result=_short_traffic_summary(structured_result),
                metadata={
                    "agent": self.agent_name,
                    "capability": self.capability,
                    "mcp_server": MCP_SERVERS[self.mcp_server_key]["name"],
                    "mcp_method": "get_route",
                    "mcp_result": {"intercity_transport": intercity_transport, "route_queries": route_results},
                    "travel_task": travel_task,
                    "daily_plan_skeleton": daily_plan,
                    "hotel_plan": hotel_plan,
                    "constraints_for_traffic": constraints_for_traffic,
                    "structured_result": structured_result,
                    "intercity_transport": intercity_transport,
                    "traffic_plan": structured_result.get("traffic_plan", {}),
                    "traffic_summary": structured_result.get("traffic_summary", {}),
                    "quality": {
                        "llm_used": llm_used,
                        "llm_error": llm_error,
                        "confidence": 0.88 if llm_error is None else 0.72,
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

    def call_route_mcp(self, task_id: str, *, city: str, segment: dict[str, Any], preference: str) -> dict[str, Any]:
        server = MCP_SERVERS[self.mcp_server_key]
        url = f"http://{MCP_GATEWAY['host']}:{MCP_GATEWAY['port']}{MCP_GATEWAY.get('path', '/')}"
        network_target = str(MCP_GATEWAY["name"])
        rpc_payload = {
            "jsonrpc": "2.0",
            "id": f"{task_id}:{segment.get('day')}:{segment.get('index')}",
            "method": "get_route",
            "params": {
                "city": city,
                "origin": segment.get("origin"),
                "destination": segment.get("destination"),
                "preference": preference,
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
            raise RuntimeError(f"Traffic MCP returned invalid response: {response.status_code} {response.raw_body}")
        if response.data.get("error"):
            raise RuntimeError(f"Traffic MCP error: {response.data['error']}")
        result = response.data.get("result")
        if not isinstance(result, dict):
            raise RuntimeError("Traffic MCP result missing")
        return {"day": segment.get("day"), "index": segment.get("index"), **result}

    def call_intercity_transport_mcp(self, task_id: str, *, travel_task: dict[str, Any]) -> dict[str, Any]:
        url = f"http://{MCP_GATEWAY['host']}:{MCP_GATEWAY['port']}{MCP_GATEWAY.get('path', '/')}"
        network_target = str(MCP_GATEWAY["name"])
        origin_city = str(travel_task.get("origin_city") or "上海")
        destination_city = str(travel_task.get("destination_city") or travel_task.get("city") or "北京")
        rpc_payload = {
            "jsonrpc": "2.0",
            "id": f"{task_id}:intercity",
            "method": "get_intercity_transport",
            "params": {
                "origin_city": origin_city,
                "destination_city": destination_city,
                "budget_level": travel_task.get("budget_level", "normal"),
                "transport_preference": travel_task.get("transport_preference", "public_transport"),
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
            raise RuntimeError(f"Intercity transport MCP returned invalid response: {response.status_code} {response.raw_body}")
        if response.data.get("error"):
            raise RuntimeError(f"Intercity transport MCP error: {response.data['error']}")
        result = response.data.get("result")
        if not isinstance(result, dict):
            raise RuntimeError("Intercity transport MCP result missing")
        return _normalize_intercity_transport(result)

    def build_prompt(self, task_payload: dict[str, Any], mcp_result: dict[str, Any]) -> str:
        return "TrafficAgent uses process_task override for structured route selection."

    def build_fallback_answer(self, task_payload: dict[str, Any], mcp_result: dict[str, Any], llm_error: str) -> str:
        return "交通 Agent 已获得候选交通数据，并使用规则 fallback 完成选择。"


def _extract_travel_task(context: dict[str, Any]) -> dict[str, Any]:
    if isinstance(context.get("travel_task"), dict):
        return dict(context["travel_task"])
    inputs = context.get("inputs") or {}
    if isinstance(inputs, dict) and isinstance(inputs.get("travel_task"), dict):
        return dict(inputs["travel_task"])
    return {}


def _extract_daily_plan(context: dict[str, Any]) -> dict[str, Any]:
    inputs = context.get("inputs") or {}
    value = inputs.get("daily_plan_skeleton") or inputs.get("daily_plan")
    if isinstance(value, dict):
        return value
    attraction_result = inputs.get("attraction_result")
    if isinstance(attraction_result, dict):
        metadata = attraction_result.get("metadata") or {}
        if isinstance(metadata, dict):
            structured = metadata.get("structured_result")
            if isinstance(structured, dict):
                value = structured.get("daily_plan") or structured.get("daily_plan_skeleton")
                if isinstance(value, dict):
                    return value
            value = metadata.get("daily_plan_skeleton")
            if isinstance(value, dict):
                return value
    return {}


def _extract_hotel_plan(context: dict[str, Any]) -> dict[str, Any]:
    inputs = context.get("inputs") or {}
    value = inputs.get("hotel_plan")
    if isinstance(value, dict):
        return value
    hotel_result = inputs.get("hotel_result")
    if isinstance(hotel_result, dict):
        metadata = hotel_result.get("metadata") or {}
        if isinstance(metadata, dict):
            structured = metadata.get("structured_result")
            if isinstance(structured, dict) and isinstance(structured.get("hotel_plan"), dict):
                return structured["hotel_plan"]
            value = metadata.get("hotel_plan")
            if isinstance(value, dict):
                return value
    return {}


def _extract_constraints_for_traffic(context: dict[str, Any]) -> list[str]:
    inputs = context.get("inputs") or {}
    result: list[str] = []
    value = inputs.get("constraints_for_traffic")
    if isinstance(value, list):
        result.extend(str(x) for x in value)
    value = inputs.get("hotel_constraints_for_traffic")
    if isinstance(value, list):
        result.extend(str(x) for x in value)
    return result


def _build_route_segments(daily_plan: dict[str, Any], hotel_plan: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    segments: list[dict[str, Any]] = []
    for day_key, plan in daily_plan.items():
        if not isinstance(plan, dict):
            continue
        spots = plan.get("spots")
        if not isinstance(spots, list):
            continue
        clean_spots = [str(x) for x in spots if str(x).strip()]
        hotel_name = _hotel_origin_name(hotel_plan)
        next_index = 1
        if hotel_name and clean_spots:
            segments.append(
                {
                    "day": str(day_key),
                    "index": next_index,
                    "origin": hotel_name,
                    "destination": clean_spots[0],
                }
            )
            next_index += 1
        for index in range(len(clean_spots) - 1):
            segments.append(
                {
                    "day": str(day_key),
                    "index": next_index,
                    "origin": clean_spots[index],
                    "destination": clean_spots[index + 1],
                }
            )
            next_index += 1
        if hotel_name and clean_spots:
            segments.append(
                {
                    "day": str(day_key),
                    "index": next_index,
                    "origin": clean_spots[-1],
                    "destination": hotel_name,
                }
            )
    if not segments:
        # Keep demo usable even if attraction plan is empty.
        segments.append({"day": "day1", "index": 1, "origin": "住宿地", "destination": "核心景区"})
    return segments[:18]


def _hotel_origin_name(hotel_plan: dict[str, Any] | None) -> str:
    if not isinstance(hotel_plan, dict):
        return ""
    selected = hotel_plan.get("selected_hotel")
    if isinstance(selected, dict):
        name = str(selected.get("name") or "").strip()
        if name:
            return name
    area = str(hotel_plan.get("recommended_area") or "").strip()
    return f"住宿地（{area}）" if area else ""


def _traffic_selection_prompt(
    travel_task: dict[str, Any],
    daily_plan: dict[str, Any],
    hotel_plan: dict[str, Any],
    constraints_for_traffic: list[str],
    route_results: list[dict[str, Any]],
) -> str:
    payload = {
        "travel_task": travel_task,
        "daily_plan": daily_plan,
        "hotel_plan": hotel_plan,
        "constraints_for_traffic": constraints_for_traffic,
        "route_candidates": route_results,
        "selection_rule": "根据用户偏好选择。低预算/公共交通优先时优先 walk/subway/bus；最快优先时比较 duration_minutes；减少步行时比较 walk_minutes。",
        "output_schema": {
            "traffic_plan": {
                "day1": [
                    {
                        "from": "景点A",
                        "to": "景点B",
                        "selected_mode": "walk|subway|bus|taxi",
                        "route": "路线名",
                        "reason": "不超过30字",
                        "estimated_cost_yuan": 0,
                        "estimated_duration_minutes": 10,
                    }
                ]
            },
            "traffic_summary": {
                "main_strategy": "不超过30字",
                "total_estimated_local_transport_cost": "如 40-60元",
            },
        },
    }
    return "\n".join(
        [
            "你是 Traffic Agent，只做候选交通方式选择。",
            "根据 travel_task、daily_plan、hotel_plan 和 route_candidates 输出严格 JSON。",
            "不要 Markdown，不要解释，不要编造候选中不存在的路线、费用或耗时。",
            json.dumps(payload, ensure_ascii=False, default=str),
        ]
    )


def _normalize_traffic_plan(value: Any, *, route_results: list[dict[str, Any]], travel_task: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict):
        return _fallback_traffic_plan(route_results, travel_task)
    traffic_plan = value.get("traffic_plan")
    traffic_summary = value.get("traffic_summary")
    if not isinstance(traffic_plan, dict):
        return _fallback_traffic_plan(route_results, travel_task)
    if not isinstance(traffic_summary, dict):
        traffic_summary = _estimate_traffic_summary(traffic_plan)
    return {"traffic_plan": traffic_plan, "traffic_summary": traffic_summary}


def _fallback_traffic_plan(route_results: list[dict[str, Any]], travel_task: dict[str, Any]) -> dict[str, Any]:
    preference = str(travel_task.get("transport_preference") or "public_transport")
    low_budget = travel_task.get("budget_level") == "low" or preference == "public_transport"
    traffic_plan: dict[str, list[dict[str, Any]]] = {}
    for route in route_results:
        candidates = route.get("candidates", [])
        if not isinstance(candidates, list) or not candidates:
            continue
        selected = _select_candidate(candidates, low_budget=low_budget, preference=preference)
        day = str(route.get("day") or "day1")
        traffic_plan.setdefault(day, []).append(
            {
                "from": route.get("origin"),
                "to": route.get("destination"),
                "selected_mode": selected.get("mode"),
                "route": selected.get("route"),
                "reason": selected.get("note") or "根据用户约束选择",
                "estimated_cost_yuan": selected.get("cost_yuan"),
                "estimated_duration_minutes": selected.get("duration_minutes"),
            }
        )
    return {"traffic_plan": traffic_plan, "traffic_summary": _estimate_traffic_summary(traffic_plan)}


def _select_candidate(candidates: list[dict[str, Any]], *, low_budget: bool, preference: str) -> dict[str, Any]:
    if "最快" in preference or preference == "fastest":
        return min(candidates, key=lambda x: int(x.get("duration_minutes") or 999))
    if low_budget:
        public_modes = [c for c in candidates if c.get("mode") in {"walk", "subway", "bus"}]
        pool = public_modes or candidates
        return min(pool, key=lambda x: (int(x.get("cost_yuan") or 999), int(x.get("duration_minutes") or 999)))
    return min(candidates, key=lambda x: (int(x.get("duration_minutes") or 999), int(x.get("cost_yuan") or 999)))


def _estimate_traffic_summary(traffic_plan: dict[str, Any]) -> dict[str, Any]:
    total_cost = 0
    has_unknown = False
    modes: set[str] = set()
    for day_routes in traffic_plan.values():
        if not isinstance(day_routes, list):
            continue
        for route in day_routes:
            if not isinstance(route, dict):
                continue
            modes.add(str(route.get("selected_mode")))
            try:
                total_cost += int(route.get("estimated_cost_yuan") or 0)
            except (TypeError, ValueError):
                has_unknown = True
    strategy = "地铁/公交/步行为主，低预算优先" if modes & {"walk", "subway", "bus"} else "按候选路线选择"
    return {
        "main_strategy": strategy,
        "total_estimated_local_transport_cost": f"约{total_cost}元" if not has_unknown else "待确认",
    }


def _normalize_intercity_transport(value: dict[str, Any]) -> dict[str, Any]:
    result = dict(value)
    option = result.get("recommended_option")
    if isinstance(option, dict):
        cost_range = option.get("cost_yuan_range")
        if (
            isinstance(cost_range, list)
            and len(cost_range) >= 2
            and isinstance(cost_range[0], (int, float))
            and isinstance(cost_range[1], (int, float))
        ):
            one_way_low = int(cost_range[0])
            one_way_high = int(cost_range[1])
            result["round_trip_assumption"] = "按往返估算"
            result["estimated_intercity_cost"] = f"约{one_way_low * 2}-{one_way_high * 2}元"
            result["one_way_cost"] = f"约{one_way_low}-{one_way_high}元"
    return result


def _short_traffic_summary(structured_result: dict[str, Any]) -> str:
    summary = structured_result.get("traffic_summary", {}) if isinstance(structured_result, dict) else {}
    strategy = summary.get("main_strategy", "已生成交通方案")
    cost = summary.get("total_estimated_local_transport_cost", "待确认")
    return f"已根据每日景点和用户约束生成交通方案：{strategy}，市内交通费用{cost}。"

    def build_demo_answer(self, task_payload: dict[str, Any], mcp_result: dict[str, Any]) -> str:
        city = mcp_result.get("city", "目标城市")
        route = mcp_result.get("route", "未知路线")
        status = mcp_result.get("status", "未知路况")
        duration = mcp_result.get("duration", "未知耗时")

        return (
            f"交通概况：{city}推荐路线为{route}，当前路况为{status}，预计耗时{duration}。\n"
            f"推荐方案：建议优先选择上述路线，并预留一定机动时间。\n"
            f"注意事项：当前为演示快速模式，已跳过外部 LLM 调用。"
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
