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
from common.schemas import RESULT_SUCCESS, build_error_result_payload, build_result_payload
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
            inputs = context.get("inputs") or {}
            upstream_results = inputs.get("upstream_results", {})
            daily_plan = _extract_daily_plan(inputs)
            hotel_plan = _extract_hotel_plan(inputs)
            city = str(travel_task.get("destination_city") or travel_task.get("city") or self.build_mcp_params(task_payload).get("city") or "北京")
            traffic_constraints = _constraint_section(travel_task, "traffic")
            general_constraints = _constraint_section(travel_task, "general")
            preference = str(traffic_constraints.get("preference") or travel_task.get("transport_preference") or "public_transport")
            intercity_transport = self.call_intercity_transport_mcp(task_id, travel_task=travel_task)

            route_segments = _build_route_segments(daily_plan, hotel_plan)
            route_results = self.call_routes_mcp(task_id, city=city, segments=route_segments, preference=preference)
            segments_for_llm = _build_segments_for_llm(route_results)
            llm_selected_route_ids: dict[str, str] = {}
            selected_route_ids: dict[str, str] = {}
            quality_source = "traffic_agent_rule_fallback"

            try:
                llm_json = llm.chat_json(
                    _traffic_route_selector_prompt(
                        city=city,
                        days=int(travel_task.get("days") or max(1, len(daily_plan) or 1)),
                        general_constraints=general_constraints,
                        traffic_constraints=traffic_constraints,
                        segments=segments_for_llm,
                        upstream_results=upstream_results,
                    ),
                    max_tokens=300,
                    temperature=0.2,
                    timeout_seconds=45.0,
                )
                raw_selected = llm_json.get("selected_route_ids")
                if not isinstance(raw_selected, dict):
                    raise ValueError("selected_route_ids must be a JSON object")
                llm_selected_route_ids = {str(key): str(value) for key, value in raw_selected.items()}
                selected_route_ids, fallback_errors = _normalize_selected_route_ids(
                    llm_selected_route_ids,
                    segments=segments_for_llm,
                    travel_task=travel_task,
                )
                traffic_plan = _expand_traffic_plan(selected_route_ids, segments_for_llm)
                structured_result = {
                    "traffic_plan": traffic_plan,
                    "traffic_summary": _estimate_traffic_summary(traffic_plan),
                    "intercity_transport": intercity_transport,
                    "selected_route_ids": selected_route_ids,
                    "llm_selected_route_ids": llm_selected_route_ids,
                }
                llm_used = True
                if fallback_errors:
                    llm_error = "; ".join(fallback_errors)
                    quality_source = "traffic_agent_llm_route_selector_with_partial_fallback"
                else:
                    quality_source = "traffic_agent_llm_route_selector"
            except Exception as exc:
                llm_error = str(exc)
                selected_route_ids = _fallback_selected_route_ids(segments_for_llm, travel_task)
                traffic_plan = _expand_traffic_plan(selected_route_ids, segments_for_llm)
                structured_result = {
                    "traffic_plan": traffic_plan,
                    "traffic_summary": _estimate_traffic_summary(traffic_plan),
                    "intercity_transport": intercity_transport,
                    "selected_route_ids": selected_route_ids,
                    "llm_selected_route_ids": llm_selected_route_ids,
                }
                quality_source = "traffic_agent_rule_fallback"

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
                    "mcp_method": "get_routes",
                    "mcp_result": {"intercity_transport": intercity_transport, "route_queries": route_results},
                    "travel_task": travel_task,
                    "upstream_results": upstream_results,
                    "traffic_constraints": traffic_constraints,
                    "general_constraints": general_constraints,
                    "daily_plan_skeleton": daily_plan,
                    "hotel_plan": hotel_plan,
                    "route_queries": route_results,
                    "segments_for_llm": segments_for_llm,
                    "llm_selected_route_ids": llm_selected_route_ids,
                    "selected_route_ids": selected_route_ids,
                    "structured_result": structured_result,
                    "intercity_transport": intercity_transport,
                    "traffic_plan": structured_result.get("traffic_plan", {}),
                    "traffic_summary": structured_result.get("traffic_summary", {}),
                    "quality": {
                        "llm_used": llm_used,
                        "llm_error": llm_error,
                        "source": quality_source,
                        "confidence": 0.88 if llm_error is None else 0.72,
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


    def call_routes_mcp(self, task_id: str, *, city: str, segments: list[dict[str, Any]], preference: str) -> list[dict[str, Any]]:
        url = f"http://{MCP_GATEWAY['host']}:{MCP_GATEWAY['port']}{MCP_GATEWAY.get('path', '/')}"
        network_target = str(MCP_GATEWAY["name"])
        rpc_payload = {
            "jsonrpc": "2.0",
            "id": f"{task_id}:routes",
            "method": "get_routes",
            "params": {
                "city": city,
                "preference": preference,
                "segments": [
                    _route_segment_payload(segment)
                    for segment in segments
                ],
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
            raise RuntimeError(f"Traffic MCP get_routes returned invalid response: {response.status_code} {response.raw_body}")
        if response.data.get("error"):
            raise RuntimeError(f"Traffic MCP get_routes error: {response.data['error']}")
        result = response.data.get("result")
        if not isinstance(result, dict):
            raise RuntimeError("Traffic MCP get_routes result missing")
        routes = result.get("routes")
        if not isinstance(routes, list):
            raise RuntimeError("Traffic MCP get_routes result missing routes")

        route_results: list[dict[str, Any]] = []
        for index, segment in enumerate(segments):
            route = routes[index] if index < len(routes) and isinstance(routes[index], dict) else {}
            route_results.append({"day": segment.get("day"), "index": segment.get("index"), **route})
        return route_results

    def call_intercity_transport_mcp(self, task_id: str, *, travel_task: dict[str, Any]) -> dict[str, Any]:
        url = f"http://{MCP_GATEWAY['host']}:{MCP_GATEWAY['port']}{MCP_GATEWAY.get('path', '/')}"
        network_target = str(MCP_GATEWAY["name"])
        origin_city = str(travel_task.get("origin_city") or "上海")
        destination_city = str(travel_task.get("destination_city") or travel_task.get("city") or "北京")
        traffic_constraints = _constraint_section(travel_task, "traffic")
        general_constraints = _constraint_section(travel_task, "general")
        rpc_payload = {
            "jsonrpc": "2.0",
            "id": f"{task_id}:intercity",
            "method": "get_intercity_transport",
            "params": {
                "origin_city": origin_city,
                "destination_city": destination_city,
                "budget_level": general_constraints.get("budget_level", travel_task.get("budget_level", "normal")),
                "transport_preference": traffic_constraints.get("preference", travel_task.get("transport_preference", "public_transport")),
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


def _extract_daily_plan(inputs: dict[str, Any]) -> dict[str, Any]:

    upstream = inputs.get("upstream_results", {})
    attr_res = upstream.get("attraction_agent", {}).get("structured", {})
    return attr_res.get("daily_plan") or attr_res.get("daily_plan_skeleton") or {}


def _extract_hotel_plan(inputs: dict[str, Any]) -> dict[str, Any]:
    upstream = inputs.get("upstream_results", {})
    hotel_res = upstream.get("hotel_agent", {}).get("structured", {})
    return hotel_res.get("hotel_plan") or {}





def _build_route_segments(daily_plan: dict[str, Any], hotel_plan: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    segments: list[dict[str, Any]] = []
    for day_key, plan in daily_plan.items():
        if not isinstance(plan, dict):
            continue
        spots = plan.get("spots")
        if not isinstance(spots, list):
            continue
        clean_spots = [str(x) for x in spots if str(x).strip()]
        spot_details = _spot_details_by_name(plan)
        hotel_point = _hotel_origin_point(hotel_plan)
        hotel_name = str(hotel_point.get("name") or "")
        next_index = 1
        if hotel_name and clean_spots:
            destination_point = spot_details.get(clean_spots[0], {"name": clean_spots[0]})
            segments.append(
                {
                    "day": str(day_key),
                    "index": next_index,
                    "origin": hotel_name,
                    "destination": clean_spots[0],
                    **_endpoint_fields("origin", hotel_point),
                    **_endpoint_fields("destination", destination_point),
                }
            )
            next_index += 1
        for index in range(len(clean_spots) - 1):
            origin_point = spot_details.get(clean_spots[index], {"name": clean_spots[index]})
            destination_point = spot_details.get(clean_spots[index + 1], {"name": clean_spots[index + 1]})
            segments.append(
                {
                    "day": str(day_key),
                    "index": next_index,
                    "origin": clean_spots[index],
                    "destination": clean_spots[index + 1],
                    **_endpoint_fields("origin", origin_point),
                    **_endpoint_fields("destination", destination_point),
                }
            )
            next_index += 1
        if hotel_name and clean_spots:
            origin_point = spot_details.get(clean_spots[-1], {"name": clean_spots[-1]})
            segments.append(
                {
                    "day": str(day_key),
                    "index": next_index,
                    "origin": clean_spots[-1],
                    "destination": hotel_name,
                    **_endpoint_fields("origin", origin_point),
                    **_endpoint_fields("destination", hotel_point),
                }
            )
    if not segments:
        # Keep demo usable even if attraction plan is empty.
        segments.append({"day": "day1", "index": 1, "origin": "住宿地", "destination": "核心景区"})
    return segments[:18]


def _route_segment_payload(segment: dict[str, Any]) -> dict[str, Any]:
    return {
        "origin": segment.get("origin"),
        "destination": segment.get("destination"),
        "origin_name": segment.get("origin_name") or segment.get("origin"),
        "origin_location": segment.get("origin_location"),
        "destination_name": segment.get("destination_name") or segment.get("destination"),
        "destination_location": segment.get("destination_location"),
        "origin_id": segment.get("origin_id"),
        "destination_id": segment.get("destination_id"),
        "origin_address": segment.get("origin_address"),
        "destination_address": segment.get("destination_address"),
        "mode": segment.get("mode"),
    }


def _spot_details_by_name(plan: dict[str, Any]) -> dict[str, dict[str, Any]]:
    details = plan.get("spot_details")
    if not isinstance(details, list):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for item in details:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if name:
            result[name] = item
    return result


def _hotel_origin_point(hotel_plan: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(hotel_plan, dict):
        return {}
    selected = hotel_plan.get("selected_hotel")
    if isinstance(selected, dict):
        name = str(selected.get("name") or "").strip()
        if name:
            return {
                "id": selected.get("hotel_id"),
                "name": name,
                "location": selected.get("location"),
                "address": selected.get("address"),
            }
    area = str(hotel_plan.get("recommended_area") or "").strip()
    return {"name": f"住宿地（{area}）" if area else ""}


def _endpoint_fields(prefix: str, point: dict[str, Any]) -> dict[str, Any]:
    return {
        f"{prefix}_name": point.get("name"),
        f"{prefix}_location": point.get("location"),
        f"{prefix}_id": point.get("spot_id") or point.get("hotel_id") or point.get("id"),
        f"{prefix}_address": point.get("address"),
    }


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


def _build_segments_for_llm(route_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    segments: list[dict[str, Any]] = []
    route_counter = 1
    for route in route_results:
        if not isinstance(route, dict):
            continue
        day = str(route.get("day") or "day1")
        index = _safe_int(route.get("index"), default=len(segments) + 1)
        segment_id = f"{day}_seg{index}"
        same_area = bool(route.get("same_area"))
        options = []
        candidates = route.get("candidates", [])
        if isinstance(candidates, list):
            for candidate in candidates:
                if not isinstance(candidate, dict):
                    continue
                option = {
                    "route_id": f"r{route_counter}",
                    "mode": candidate.get("mode"),
                    "route": candidate.get("route"),
                    "duration_minutes": candidate.get("duration_minutes"),
                    "cost_yuan": candidate.get("cost_yuan"),
                    "walk_minutes": candidate.get("walk_minutes"),
                    "transfers": candidate.get("transfers"),
                    "same_area": same_area,
                }
                route_counter += 1
                options.append(option)
        segments.append(
            {
                "segment_id": segment_id,
                "day": day,
                "from": route.get("origin"),
                "to": route.get("destination"),
                "same_area": same_area,
                "options": options,
            }
        )
    return segments


def _traffic_route_selector_prompt(
    *,
    city: str,
    days: int,
    general_constraints: dict[str, Any],
    traffic_constraints: dict[str, Any],
    upstream_results: dict[str, Any],
    segments: list[dict[str, Any]],
) -> str:
    payload = {
        "city": city,
        "days": days,
        "general_constraints": general_constraints,
        "traffic_constraints": traffic_constraints,
        "upstream_results": upstream_results,
        "segments": segments,
        "output_schema": {
            "selected_route_ids": {
                "day1_seg1": "r1",
                "day1_seg2": "r3",
            },
            "reason": "不超过20个中文字符",
        },
    }
    return "\n".join(
        [
            "你是路线选择器。每个 segment 必须从 options 中选择一个已有 route_id。",
            "请仔细参考 upstream_results 内的前置依赖 agents 给出的结果，并据此作出合理调整。",
            "根据用户预算、交通偏好、费用、耗时、换乘次数、步行时间、是否同区域自行判断。",
            "低预算通常优先低费用；公共交通偏好通常优先 subway/bus/walk；同一区域可考虑 walk；taxi 只有明显更合理时才选。",
            "只能选择已有 route_id；不要编造路线；不要改写路线内容；不要输出完整 traffic_plan。",
            "不要输出 route、duration、cost 等字段；不要 Markdown；不要解释；不要推理过程；只输出合法 JSON。",
            json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str),
        ]
    )


def _normalize_selected_route_ids(
    value: dict[str, str],
    *,
    segments: list[dict[str, Any]],
    travel_task: dict[str, Any],
) -> tuple[dict[str, str], list[str]]:
    selected: dict[str, str] = {}
    fallback_errors: list[str] = []
    for segment in segments:
        segment_id = str(segment.get("segment_id") or "")
        valid_route_ids = {
            str(option.get("route_id"))
            for option in segment.get("options", [])
            if isinstance(option, dict) and option.get("route_id")
        }
        route_id = str(value.get(segment_id) or "").strip()
        if route_id in valid_route_ids:
            selected[segment_id] = route_id
            continue
        selected[segment_id] = _fallback_route_id(segment, travel_task)
        fallback_errors.append(f"{segment_id}: invalid_or_missing_route_id")
    return selected, fallback_errors


def _fallback_selected_route_ids(
    segments: list[dict[str, Any]],
    travel_task: dict[str, Any],
) -> dict[str, str]:
    return {
        str(segment.get("segment_id")): _fallback_route_id(segment, travel_task)
        for segment in segments
        if segment.get("segment_id")
    }


def _fallback_route_id(segment: dict[str, Any], travel_task: dict[str, Any]) -> str:
    options = [option for option in segment.get("options", []) if isinstance(option, dict)]
    if not options:
        return ""
    if segment.get("same_area"):
        walk_options = [option for option in options if option.get("mode") == "walk"]
        if walk_options:
            return str(walk_options[0].get("route_id") or "")

    general_constraints = _constraint_section(travel_task, "general")
    traffic_constraints = _constraint_section(travel_task, "traffic")
    budget_level = str(general_constraints.get("budget_level") or travel_task.get("budget_level") or "")
    preference = str(traffic_constraints.get("preference") or travel_task.get("transport_preference") or "")
    if budget_level == "low":
        return str(min(options, key=lambda option: _safe_int(option.get("cost_yuan"), default=9999)).get("route_id") or "")
    if preference == "public_transport":
        public_options = [option for option in options if option.get("mode") in {"subway", "bus", "walk"}]
        if public_options:
            return str(
                min(
                    public_options,
                    key=lambda option: (
                        _safe_int(option.get("cost_yuan"), default=9999),
                        _safe_int(option.get("duration_minutes"), default=9999),
                    ),
                ).get("route_id")
                or ""
            )
    return str(options[0].get("route_id") or "")


def _constraint_section(travel_task: dict[str, Any], section: str) -> dict[str, Any]:
    constraints = travel_task.get("constraints")
    if isinstance(constraints, dict) and isinstance(constraints.get(section), dict):
        return dict(constraints[section])
    if section == "traffic":
        return {
            "preference": travel_task.get("transport_preference", "public_transport"),
            "avoid": [],
            "max_transfer": None,
            "walking_tolerance": "normal",
        }
    if section == "general":
        return {
            "budget_level": travel_task.get("budget_level", "normal"),
            "travel_style": "budget" if travel_task.get("budget_level") == "low" else "balanced",
            "special_needs": [],
        }
    return {}


def _expand_traffic_plan(
    selected_route_ids: dict[str, str],
    segments: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    traffic_plan: dict[str, list[dict[str, Any]]] = {}
    for segment in segments:
        segment_id = str(segment.get("segment_id") or "")
        route_id = selected_route_ids.get(segment_id)
        option = _find_option(segment, route_id)
        if option is None:
            continue
        day = str(segment.get("day") or "day1")
        traffic_plan.setdefault(day, []).append(
            {
                "from": segment.get("from"),
                "to": segment.get("to"),
                "selected_mode": option.get("mode"),
                "route": option.get("route"),
                "reason": _display_route_reason(option, same_area=bool(segment.get("same_area"))),
                "estimated_cost_yuan": option.get("cost_yuan"),
                "estimated_duration_minutes": option.get("duration_minutes"),
            }
        )
    return traffic_plan


def _find_option(segment: dict[str, Any], route_id: str | None) -> dict[str, Any] | None:
    for option in segment.get("options", []):
        if isinstance(option, dict) and option.get("route_id") == route_id:
            return option
    return None


def _display_route_reason(option: dict[str, Any], *, same_area: bool) -> str:
    mode = str(option.get("mode") or "")
    if mode == "walk" and same_area:
        return "同一区域，选择步行"
    if mode == "walk":
        return "选择步行路线"
    if mode == "subway":
        return "选择地铁路线"
    if mode == "bus":
        return "选择公交路线"
    if mode == "taxi":
        return "选择打车路线"
    return "根据候选路线选择"


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default




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
                "reason": _display_route_reason(selected, same_area=bool(route.get("same_area"))),
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
