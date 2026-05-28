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
from llm_client import llm


class HotelAgent(BaseAgent):
    agent_name = "hotel_agent"
    capability = "hotel"
    mcp_server_key = "hotel"

    def process_task(self, task_payload: dict[str, Any]) -> None:
        """Use one LLM call to choose an area_id and hotel_id from compact candidates."""
        task_id = str(task_payload["task_id"])
        started = time.perf_counter()
        llm_used = False
        llm_error: str | None = None
        quality_source = "hotel_agent_rule_fallback"

        try:
            context = task_payload.get("context") or {}
            travel_task = _extract_travel_task(context)
            weather_constraints = _extract_weather_constraints(context)
            daily_plan = _extract_daily_plan(context)
            city = str(travel_task.get("destination_city") or travel_task.get("city") or "北京")

            area_options = _build_area_options(daily_plan)
            area_candidates = _area_options_to_legacy_candidates(area_options)
            hotel_candidates = self.call_hotel_candidates_mcp(
                task_id=task_id,
                city=city,
                travel_task=travel_task,
                daily_plan=daily_plan,
                area_options=area_options,
            )
            hotel_options_for_llm = _build_hotel_options_for_llm(hotel_candidates)
            llm_hotel_selection: dict[str, Any] = {}

            try:
                llm_hotel_selection = llm.chat_json(
                    _hotel_selector_prompt(
                        travel_task=travel_task,
                        area_options=area_options,
                        hotel_options=hotel_options_for_llm,
                    ),
                    max_tokens=300,
                    temperature=0.2,
                    timeout_seconds=45.0,
                )
                selection, selection_errors = _normalize_hotel_selection(
                    llm_hotel_selection,
                    area_options=area_options,
                    hotel_options=hotel_options_for_llm,
                    travel_task=travel_task,
                )
                llm_used = True
                if selection_errors:
                    llm_error = "; ".join(selection_errors)
                    quality_source = "hotel_agent_llm_area_hotel_selector_with_partial_fallback"
                else:
                    quality_source = "hotel_agent_llm_area_hotel_selector"
            except Exception as exc:
                llm_error = str(exc)
                selection = _fallback_hotel_selection(
                    area_options=area_options,
                    hotel_options=hotel_options_for_llm,
                    travel_task=travel_task,
                )
                quality_source = "hotel_agent_rule_fallback"

            hotel_plan = _expand_hotel_plan(
                selection,
                area_options=area_options,
                hotel_options=hotel_options_for_llm,
                travel_task=travel_task,
                llm_reason=str(llm_hotel_selection.get("reason") or ""),
            )
            structured_result = {
                "hotel_plan": hotel_plan,
                "constraints_for_traffic": _default_constraints_for_traffic(),
            }

            elapsed_ms = (time.perf_counter() - started) * 1000
            result_payload = build_result_payload(
                source=self.agent_name,
                target=COORDINATOR_NAME,
                task_id=task_id,
                status=RESULT_SUCCESS,
                result=_short_hotel_summary(structured_result),
                metadata={
                    "agent": self.agent_name,
                    "capability": self.capability,
                    "workflow": "area_options_hotel_mcp_then_single_llm_selector",
                    "mcp_server": MCP_SERVERS[self.mcp_server_key]["name"],
                    "mcp_method": "search_hotels",
                    "area_options_for_llm": area_options,
                    "hotel_options_for_llm": hotel_options_for_llm,
                    "llm_hotel_selection": llm_hotel_selection,
                    "recommended_area_id": selection.get("recommended_area_id"),
                    "selected_hotel_id": selection.get("selected_hotel_id"),
                    "area_candidates": area_candidates,
                    "area_selection": {"recommended_area": hotel_plan.get("recommended_area")},
                    "mcp_result": hotel_candidates,
                    "hotel_candidates": hotel_candidates.get("hotels", []) if isinstance(hotel_candidates, dict) else [],
                    "travel_task": travel_task,
                    "hotel_constraints": _constraint_section(travel_task, "hotel"),
                    "general_constraints": _constraint_section(travel_task, "general"),
                    "weather_constraints": weather_constraints,
                    "daily_plan_skeleton": daily_plan,
                    "structured_result": structured_result,
                    "hotel_plan": hotel_plan,
                    "selected_hotel": hotel_plan.get("selected_hotel") if isinstance(hotel_plan, dict) else {},
                    "hotel_area": hotel_plan.get("recommended_area") if isinstance(hotel_plan, dict) else None,
                    "constraints_for_traffic": structured_result.get("constraints_for_traffic", []),
                    "quality": {
                        "llm_used": llm_used,
                        "llm_error": llm_error,
                        "source": quality_source,
                        "confidence": 0.9 if llm_error is None else 0.76,
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

    def call_hotel_mcp(
        self,
        task_id: str,
        *,
        city: str,
        travel_task: dict[str, Any],
        daily_plan: dict[str, Any],
        selected_area: str,
        area_selection: dict[str, Any],
    ) -> dict[str, Any]:
        server = MCP_SERVERS[self.mcp_server_key]
        url = f"http://{MCP_GATEWAY['host']}:{MCP_GATEWAY['port']}{MCP_GATEWAY.get('path', '/')}"
        network_target = str(MCP_GATEWAY["name"])
        rpc_payload = {
            "jsonrpc": "2.0",
            "id": task_id,
            "method": "search_hotels",
            "params": {
                "city": city,
                "days": travel_task.get("days", 3),
                "budget_level": _constraint_section(travel_task, "general").get("budget_level", travel_task.get("budget_level", "normal")),
                "preferences": _constraint_section(travel_task, "hotel").get("preferred_features", []),
                "target_area": selected_area,
                "preferred_areas": [selected_area],
                "area_selection": area_selection,
                "daily_plan": daily_plan,
                "requested_fields": [
                    "name",
                    "area",
                    "price_per_night",
                    "type",
                    "nearest_subway",
                    "tags",
                    "pros",
                    "cons",
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
            raise RuntimeError(f"Hotel MCP returned invalid response: {response.status_code} {response.raw_body}")
        if response.data.get("error"):
            raise RuntimeError(f"Hotel MCP error: {response.data['error']}")
        result = response.data.get("result")
        if not isinstance(result, dict):
            raise RuntimeError("Hotel MCP result missing")
        return result

    def call_hotel_candidates_mcp(
        self,
        *,
        task_id: str,
        city: str,
        travel_task: dict[str, Any],
        daily_plan: dict[str, Any],
        area_options: list[dict[str, Any]],
    ) -> dict[str, Any]:
        selected_areas = [str(item.get("area")) for item in area_options[:3] if item.get("area")]
        if not selected_areas:
            selected_areas = ["市中心地铁沿线"]

        merged_hotels: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        raw_results: list[dict[str, Any]] = []
        for area in selected_areas:
            area_selection = {"recommended_area": area}
            result = self.call_hotel_mcp(
                task_id,
                city=city,
                travel_task=travel_task,
                daily_plan=daily_plan,
                selected_area=area,
                area_selection=area_selection,
            )
            raw_results.append(result)
            for hotel in result.get("hotels", []) if isinstance(result, dict) else []:
                if not isinstance(hotel, dict):
                    continue
                key = (str(hotel.get("name") or ""), str(hotel.get("area") or ""))
                if key in seen:
                    continue
                seen.add(key)
                merged_hotels.append(dict(hotel))

        return {
            "city": city,
            "requested_city": city,
            "searched_areas": selected_areas,
            "raw_results": raw_results,
            "hotels": merged_hotels,
        }

    def build_prompt(self, task_payload: dict[str, Any], mcp_result: dict[str, Any]) -> str:
        return "HotelAgent uses process_task override for structured area and hotel selection."

    def build_fallback_answer(self, task_payload: dict[str, Any], mcp_result: dict[str, Any], llm_error: str) -> str:
        return "酒店 Agent 已获得候选酒店数据，并使用规则 fallback 完成住宿选择。"


def _extract_travel_task(context: dict[str, Any]) -> dict[str, Any]:
    if isinstance(context.get("travel_task"), dict):
        return dict(context["travel_task"])
    inputs = context.get("inputs") or {}
    if isinstance(inputs, dict) and isinstance(inputs.get("travel_task"), dict):
        return dict(inputs["travel_task"])
    return {}


def _extract_weather_constraints(context: dict[str, Any]) -> dict[str, Any]:
    inputs = context.get("inputs") or {}
    if isinstance(inputs.get("weather_constraints"), dict):
        return dict(inputs["weather_constraints"])
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


def _build_area_candidates(daily_plan: dict[str, Any]) -> list[dict[str, Any]]:
    """Summarize itinerary areas for the first LLM area decision."""
    area_info: dict[str, dict[str, Any]] = {}
    for day_key, day in daily_plan.items():
        if not isinstance(day, dict):
            continue
        raw_area = str(day.get("area") or "").strip()
        if not raw_area:
            continue
        spots = day.get("spots") if isinstance(day.get("spots"), list) else []
        for area in _split_area_phrase(raw_area):
            item = area_info.setdefault(area, {"area": area, "days": [], "spots": [], "score_hint": 0})
            item["days"].append(str(day_key))
            item["spots"].extend([str(x) for x in spots])
            item["score_hint"] += 2 if area == raw_area else 1

    candidates = list(area_info.values())
    for item in candidates:
        # remove duplicate spots and days while preserving order
        item["days"] = list(dict.fromkeys(item.get("days", [])))
        item["spots"] = list(dict.fromkeys(item.get("spots", [])))
        item["visit_day_count"] = len(item["days"])
        item["spot_count"] = len(item["spots"])

    candidates.sort(key=lambda x: (x.get("visit_day_count", 0), x.get("spot_count", 0), x.get("score_hint", 0)), reverse=True)
    return candidates[:8]


def _build_area_options(daily_plan: dict[str, Any]) -> list[dict[str, Any]]:
    area_info: dict[str, dict[str, Any]] = {}
    for day_key, day in daily_plan.items():
        if not isinstance(day, dict):
            continue
        raw_area = str(day.get("area") or "").strip()
        if not raw_area:
            continue
        spots = [str(item) for item in day.get("spots", []) if str(item).strip()] if isinstance(day.get("spots"), list) else []
        for area in _split_area_phrase(raw_area):
            item = area_info.setdefault(area, {"area": area, "days": [], "nearby_spots": []})
            item["days"].append(str(day_key))
            item["nearby_spots"].extend(spots)

    options: list[dict[str, Any]] = []
    for index, item in enumerate(
        sorted(
            area_info.values(),
            key=lambda x: (len(set(x.get("days", []))), len(set(x.get("nearby_spots", [])))),
            reverse=True,
        ),
        start=1,
    ):
        days = list(dict.fromkeys(str(day) for day in item.get("days", [])))
        nearby_spots = list(dict.fromkeys(str(spot) for spot in item.get("nearby_spots", [])))
        options.append(
            {
                "area_id": f"a{index}",
                "area": item.get("area"),
                "visit_day_count": len(days),
                "spot_count": len(nearby_spots),
                "days": days,
                "nearby_spots": nearby_spots,
            }
        )
    if not options:
        options.append(
            {
                "area_id": "a1",
                "area": "市中心地铁沿线",
                "visit_day_count": 0,
                "spot_count": 0,
                "days": [],
                "nearby_spots": [],
            }
        )
    return options[:8]


def _area_options_to_legacy_candidates(area_options: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "area": option.get("area"),
            "days": option.get("days", []),
            "spots": option.get("nearby_spots", []),
            "visit_day_count": option.get("visit_day_count", 0),
            "spot_count": option.get("spot_count", 0),
        }
        for option in area_options
    ]


def _build_hotel_options_for_llm(hotel_candidates: dict[str, Any]) -> list[dict[str, Any]]:
    options: list[dict[str, Any]] = []
    hotels = hotel_candidates.get("hotels", []) if isinstance(hotel_candidates, dict) else []
    for index, hotel in enumerate([item for item in hotels if isinstance(item, dict)], start=1):
        options.append(
            {
                "hotel_id": f"h{index}",
                "name": hotel.get("name"),
                "area": hotel.get("area"),
                "price_per_night": hotel.get("price_per_night"),
                "type": hotel.get("type"),
                "nearest_subway": hotel.get("nearest_subway"),
                "tags": hotel.get("tags") if isinstance(hotel.get("tags"), list) else [],
            }
        )
    return options


def _constraint_section(travel_task: dict[str, Any], section: str) -> dict[str, Any]:
    constraints = travel_task.get("constraints")
    if isinstance(constraints, dict) and isinstance(constraints.get(section), dict):
        return dict(constraints[section])
    if section == "hotel":
        return {
            "preferred_features": _as_str_list(travel_task.get("preferences"), default=[]),
            "preferred_area": None,
            "hotel_type": None,
        }
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
    if section == "attractions":
        return {
            "must_visit": _as_str_list(travel_task.get("must_visit"), default=[]),
            "preferred_types": _as_str_list(travel_task.get("preferences"), default=[]),
            "avoid": _as_str_list(travel_task.get("avoid"), default=[]),
            "pace": "normal",
        }
    return {}


def _hotel_selector_prompt(
    *,
    travel_task: dict[str, Any],
    area_options: list[dict[str, Any]],
    hotel_options: list[dict[str, Any]],
) -> str:
    payload = {
        "city": travel_task.get("destination_city") or travel_task.get("city") or "北京",
        "days": travel_task.get("days", 3),
        "hotel_constraints": _constraint_section(travel_task, "hotel"),
        "traffic_constraints": _constraint_section(travel_task, "traffic"),
        "general_constraints": _constraint_section(travel_task, "general"),
        "attraction_constraints": _constraint_section(travel_task, "attractions"),
        "area_options": area_options,
        "hotel_options": hotel_options,
        "output_schema": {
            "recommended_area_id": "a1",
            "selected_hotel_id": "h1",
            "reason": "不超过20个中文字符",
        },
    }
    return "\n".join(
        [
            "你是住宿区域与酒店选择器。",
            "必须从 area_options 中选择一个 area_id；必须从 hotel_options 中选择一个 hotel_id。",
            "hotel_id 必须来自已有候选；推荐区域最好与酒店 area 一致；不要编造酒店；不要改写酒店信息。",
            "不要输出完整 hotel_plan；不要 Markdown；不要推理过程；不要大段解释；只输出合法 JSON。",
            "低预算通常优先低价格；公共交通偏好通常优先地铁方便；多天行程可优先覆盖景点较多、通勤更均衡的区域。",
            json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str),
        ]
    )


def _normalize_hotel_selection(
    value: dict[str, Any],
    *,
    area_options: list[dict[str, Any]],
    hotel_options: list[dict[str, Any]],
    travel_task: dict[str, Any],
) -> tuple[dict[str, str], list[str]]:
    if not isinstance(value, dict):
        raise ValueError("hotel selection must be a JSON object")

    errors: list[str] = []
    area_by_id = {str(item.get("area_id")): item for item in area_options if item.get("area_id")}
    hotel_by_id = {str(item.get("hotel_id")): item for item in hotel_options if item.get("hotel_id")}
    area_id = str(value.get("recommended_area_id") or "").strip()
    hotel_id = str(value.get("selected_hotel_id") or "").strip()

    if hotel_id not in hotel_by_id:
        hotel_id = _fallback_hotel_id(hotel_options, area_options=area_options, travel_task=travel_task)
        errors.append("selected_hotel_id invalid_or_missing")

    if area_id not in area_by_id:
        hotel_area = str(hotel_by_id.get(hotel_id, {}).get("area") or "")
        area_id = _find_area_id_by_name(area_options, hotel_area) or _fallback_area_id(area_options)
        errors.append("recommended_area_id invalid_or_missing")

    return {"recommended_area_id": area_id, "selected_hotel_id": hotel_id}, errors


def _fallback_hotel_selection(
    *,
    area_options: list[dict[str, Any]],
    hotel_options: list[dict[str, Any]],
    travel_task: dict[str, Any],
) -> dict[str, str]:
    hotel_id = _fallback_hotel_id(hotel_options, area_options=area_options, travel_task=travel_task)
    hotel = _find_hotel_option(hotel_options, hotel_id)
    area_id = _find_area_id_by_name(area_options, str(hotel.get("area") if hotel else "")) or _fallback_area_id(area_options)
    return {"recommended_area_id": area_id, "selected_hotel_id": hotel_id}


def _expand_hotel_plan(
    selection: dict[str, str],
    *,
    area_options: list[dict[str, Any]],
    hotel_options: list[dict[str, Any]],
    travel_task: dict[str, Any],
    llm_reason: str,
) -> dict[str, Any]:
    area = _find_area_option(area_options, selection.get("recommended_area_id")) or (area_options[0] if area_options else {})
    hotel = _find_hotel_option(hotel_options, selection.get("selected_hotel_id")) or (hotel_options[0] if hotel_options else {})
    selected_hotel = {
        "name": hotel.get("name") or "待确认住宿",
        "area": hotel.get("area") or area.get("area") or "市中心地铁沿线",
        "price_per_night": hotel.get("price_per_night") or "待确认",
        "nearest_subway": hotel.get("nearest_subway") or "待确认",
        "type": hotel.get("type") or "经济型酒店",
    }
    reason_prefix = f"LLM选择理由：{_truncate_text(llm_reason, 20)}；" if llm_reason else ""
    return {
        "recommended_area": area.get("area") or selected_hotel["area"],
        "area_reason": "该区域覆盖较多行程景点，适合减少通勤。",
        "selected_hotel": selected_hotel,
        "hotel_reason": (
            f"{reason_prefix}该酒店价格为{selected_hotel['price_per_night']}，"
            f"附近地铁为{selected_hotel['nearest_subway']}。"
        ),
        "estimated_total_hotel_cost": _estimate_total_cost(selected_hotel, travel_task),
    }


def _fallback_hotel_id(
    hotel_options: list[dict[str, Any]],
    *,
    area_options: list[dict[str, Any]],
    travel_task: dict[str, Any],
) -> str:
    if not hotel_options:
        return ""
    top_area = str((area_options[0] if area_options else {}).get("area") or "")
    budget_level = str(_constraint_section(travel_task, "general").get("budget_level") or travel_task.get("budget_level") or "")

    def key(hotel: dict[str, Any]) -> tuple[int, int, int]:
        price = _safe_int(hotel.get("price_per_night"), default=9999)
        has_subway = 0 if str(hotel.get("nearest_subway") or "").strip() else 1
        area_match = 0 if top_area and str(hotel.get("area") or "") == top_area else 1
        if budget_level == "low":
            return price, has_subway, area_match
        return area_match, has_subway, price

    selected = min(hotel_options, key=key)
    return str(selected.get("hotel_id") or "")


def _fallback_area_id(area_options: list[dict[str, Any]]) -> str:
    return str((area_options[0] if area_options else {}).get("area_id") or "")


def _find_area_id_by_name(area_options: list[dict[str, Any]], area_name: str) -> str:
    for option in area_options:
        area = str(option.get("area") or "")
        if area_name and (area_name == area or area_name in area or area in area_name):
            return str(option.get("area_id") or "")
    return ""


def _find_area_option(area_options: list[dict[str, Any]], area_id: str | None) -> dict[str, Any] | None:
    for option in area_options:
        if option.get("area_id") == area_id:
            return option
    return None


def _find_hotel_option(hotel_options: list[dict[str, Any]], hotel_id: str | None) -> dict[str, Any] | None:
    for option in hotel_options:
        if option.get("hotel_id") == hotel_id:
            return option
    return None


def _default_constraints_for_traffic() -> list[str]:
    return ["住宿地到每日首个景点要纳入交通规划", "每日最后一个景点回住宿地要纳入交通规划", "优先地铁/步行"]


def _truncate_text(value: Any, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _split_area_phrase(raw_area: str) -> list[str]:
    text = raw_area.replace("和", "及").replace("、", "及").replace("/", "及")
    parts = [part.strip() for part in text.split("及") if part.strip()]
    result = [raw_area]
    for part in parts:
        if part not in result:
            result.append(part)
    return result


def _hotel_area_selection_prompt(
    *,
    travel_task: dict[str, Any],
    weather_constraints: dict[str, Any],
    daily_plan: dict[str, Any],
    area_candidates: list[dict[str, Any]],
) -> str:
    payload = {
        "travel_task": travel_task,
        "weather_constraints": weather_constraints,
        "daily_plan": daily_plan,
        "area_candidates": area_candidates,
        "decision_goal": "先选择住宿区域。住宿区域应尽量位于多日行程的中心位置，或者靠近地铁枢纽，使到各天景点都方便；低预算用户优先公共交通便利、减少通勤成本。",
        "selection_rules": [
            "必须从 area_candidates 中选择一个 recommended_area；如果候选里没有完美中心，则选综合通勤最方便的区域。",
            "不要因为某一天景点很多就机械选择该区域，要综合五天景点分布。",
            "如果核心景点主要集中在老城中心，通常优先天安门-故宫、前门、王府井等中心区域。",
            "输出严格 JSON，不要 Markdown。",
        ],
        "output_schema": {
            "recommended_area": "区域名称，必须来自 area_candidates.area",
            "area_reason": "不超过60字，说明为什么到各天景点都方便",
            "area_tradeoff": "不超过50字，说明低预算/通勤/景点覆盖的取舍",
            "traffic_considerations": ["给后续交通规划的约束，例如住处到每日首个景点要纳入路线"],
        },
    }
    return "\n".join(
        [
            "你是 Hotel Agent 的住宿区域选择模块。",
            "你现在只选择住宿区域，不选择具体酒店，也不要调用外部信息。",
            "根据景点日程选择一个到各天景点都方便的住宿区域。",
            json.dumps(payload, ensure_ascii=False, default=str),
        ]
    )


def _hotel_choice_prompt(
    *,
    travel_task: dict[str, Any],
    weather_constraints: dict[str, Any],
    daily_plan: dict[str, Any],
    area_selection: dict[str, Any],
    hotel_candidates: dict[str, Any],
) -> str:
    days = _safe_int(travel_task.get("days"), default=3)
    nights = max(1, days - 1)
    payload = {
        "travel_task": travel_task,
        "weather_constraints": weather_constraints,
        "daily_plan": daily_plan,
        "area_selection": area_selection,
        "hotel_candidates_from_mcp": hotel_candidates.get("hotels", []),
        "nights": nights,
        "decision_goal": "在 MCP 返回的该区域酒店候选中，结合低预算、公共交通、离景点方便程度选择最合适酒店。",
        "selection_rules": [
            "必须从 hotel_candidates_from_mcp 中选择，不得编造酒店。",
            "低预算优先价格低；如果价格差距不大，优先地铁方便和到景点方便。",
            "如果是青旅/床位，要说明它低预算但舒适度和私密性一般。",
            "输出严格 JSON，不要 Markdown。",
        ],
        "output_schema": {
            "hotel_plan": {
                "recommended_area": area_selection.get("recommended_area"),
                "area_reason": area_selection.get("area_reason"),
                "selected_hotel": {
                    "name": "候选酒店名",
                    "area": "区域",
                    "price_per_night": 200,
                    "nearest_subway": "地铁站",
                    "type": "酒店类型",
                },
                "hotel_reason": "不超过60字，说明为什么适合本任务",
                "estimated_total_hotel_cost": f"约{nights}晚总价",
            },
            "constraints_for_traffic": ["住宿地到每日首个景点要纳入交通规划", "每日最后一个景点回住宿地要纳入交通规划", "优先地铁/步行"],
        },
    }
    return "\n".join(
        [
            "你是 Hotel Agent 的具体酒店选择模块。",
            "住宿区域已经选定，MCP 已返回该区域候选酒店。",
            "你只从候选酒店中选择一个最合适酒店，并输出严格 JSON。",
            json.dumps(payload, ensure_ascii=False, default=str),
        ]
    )


def _normalize_area_selection(value: Any, area_candidates: list[dict[str, Any]]) -> dict[str, Any]:
    if not isinstance(value, dict):
        return _fallback_area_selection(area_candidates)
    candidate_names = [str(item.get("area")) for item in area_candidates if item.get("area")]
    chosen = str(value.get("recommended_area") or "").strip()
    if not chosen or chosen not in candidate_names:
        # Try fuzzy match before fallback.
        for name in candidate_names:
            if chosen and (chosen in name or name in chosen):
                chosen = name
                break
        else:
            return _fallback_area_selection(area_candidates)
    return {
        "recommended_area": chosen,
        "area_reason": str(value.get("area_reason") or "综合景点分布和公共交通便利性较好"),
        "area_tradeoff": str(value.get("area_tradeoff") or "兼顾低预算、通勤时间和景点覆盖"),
        "traffic_considerations": _as_str_list(
            value.get("traffic_considerations"),
            default=["住宿地到每日首个景点要纳入交通规划", "优先公共交通和步行"],
        ),
    }


def _fallback_area_selection(area_candidates: list[dict[str, Any]]) -> dict[str, Any]:
    if area_candidates:
        area = str(area_candidates[0].get("area") or "市中心地铁沿线")
        days = area_candidates[0].get("days") or []
        reason = f"覆盖{len(days)}天行程，且位于主要景点区域"
    else:
        area = "市中心地铁沿线"
        reason = "缺少区域统计时优先选择市中心地铁沿线"
    return {
        "recommended_area": area,
        "area_reason": reason,
        "area_tradeoff": "规则 fallback：优先覆盖天数多且公共交通方便的区域",
        "traffic_considerations": ["住宿地到每日首个景点要纳入交通规划", "优先公共交通和步行"],
    }


def _normalize_hotel_plan(
    value: Any,
    *,
    hotel_candidates: dict[str, Any],
    area_selection: dict[str, Any],
    travel_task: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(value, dict):
        return _fallback_hotel_plan(hotel_candidates, area_selection, travel_task)
    hotel_plan = value.get("hotel_plan")
    if not isinstance(hotel_plan, dict):
        return _fallback_hotel_plan(hotel_candidates, area_selection, travel_task)

    selected = hotel_plan.get("selected_hotel")
    if not isinstance(selected, dict) or not selected.get("name"):
        return _fallback_hotel_plan(hotel_candidates, area_selection, travel_task)

    candidates = [h for h in (hotel_candidates.get("hotels", []) if isinstance(hotel_candidates, dict) else []) if isinstance(h, dict)]
    matched = _find_candidate_by_name(str(selected.get("name")), candidates)
    if matched is None:
        return _fallback_hotel_plan(hotel_candidates, area_selection, travel_task)

    # Use MCP candidate facts as source of truth; do not trust LLM-generated price/subway fields.
    selected_hotel = {
        "name": matched.get("name"),
        "area": matched.get("area"),
        "price_per_night": matched.get("price_per_night"),
        "nearest_subway": matched.get("nearest_subway"),
        "type": matched.get("type"),
    }
    hotel_plan["recommended_area"] = area_selection.get("recommended_area") or matched.get("area")
    hotel_plan["area_reason"] = area_selection.get("area_reason") or hotel_plan.get("area_reason") or "到多日景点较方便"
    hotel_plan["selected_hotel"] = selected_hotel
    hotel_plan["hotel_reason"] = str(hotel_plan.get("hotel_reason") or "兼顾低预算和公共交通便利")
    if not hotel_plan.get("estimated_total_hotel_cost"):
        hotel_plan["estimated_total_hotel_cost"] = _estimate_total_cost(selected_hotel, travel_task)

    constraints = _as_str_list(
        value.get("constraints_for_traffic"),
        default=["住宿地到每日首个景点要纳入交通规划", "每日最后一个景点回住宿地要纳入交通规划", "优先公共交通和步行"],
    )
    return {"hotel_plan": hotel_plan, "constraints_for_traffic": constraints}


def _fallback_hotel_plan(hotel_candidates: dict[str, Any], area_selection: dict[str, Any], travel_task: dict[str, Any]) -> dict[str, Any]:
    hotels = [h for h in (hotel_candidates.get("hotels", []) if isinstance(hotel_candidates, dict) else []) if isinstance(h, dict)]
    if not hotels:
        selected = {
            "name": "待确认住宿",
            "area": area_selection.get("recommended_area") or "市中心地铁沿线",
            "price_per_night": "待确认",
            "nearest_subway": "待确认",
            "type": "经济型酒店",
        }
    else:
        # Low-budget fallback: cheapest in selected-area candidate set.
        selected = min(hotels, key=lambda x: _safe_int(x.get("price_per_night"), default=9999))
        selected = {
            "name": selected.get("name"),
            "area": selected.get("area"),
            "price_per_night": selected.get("price_per_night"),
            "nearest_subway": selected.get("nearest_subway"),
            "type": selected.get("type"),
        }
    return {
        "hotel_plan": {
            "recommended_area": area_selection.get("recommended_area") or selected.get("area") or "市中心地铁沿线",
            "area_reason": area_selection.get("area_reason") or "根据景点分布和公共交通便利性选择",
            "selected_hotel": selected,
            "hotel_reason": "在所选区域候选酒店中兼顾低预算和交通便利",
            "estimated_total_hotel_cost": _estimate_total_cost(selected, travel_task),
        },
        "constraints_for_traffic": ["住宿地到每日首个景点要纳入交通规划", "每日最后一个景点回住宿地要纳入交通规划", "优先公共交通和步行"],
    }


def _find_candidate_by_name(name: str, candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    for hotel in candidates:
        candidate_name = str(hotel.get("name") or "")
        if name == candidate_name or name in candidate_name or candidate_name in name:
            return hotel
    return None


def _estimate_total_cost(selected_hotel: dict[str, Any], travel_task: dict[str, Any]) -> str:
    days = _safe_int(travel_task.get("days"), default=3)
    nights = max(1, days - 1)
    price = selected_hotel.get("price_per_night")
    try:
        return f"约{int(price) * nights}元"
    except (TypeError, ValueError):
        return "待确认"


def _as_str_list(value: Any, default: list[str]) -> list[str]:
    if not isinstance(value, list):
        return list(default)
    result = [str(item) for item in value if str(item).strip()]
    return result or list(default)


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _short_hotel_summary(structured_result: dict[str, Any]) -> str:
    plan = structured_result.get("hotel_plan", {}) if isinstance(structured_result, dict) else {}
    selected = plan.get("selected_hotel", {}) if isinstance(plan, dict) else {}
    return (
        f"已先根据景点分布选择住宿区域：{plan.get('recommended_area', '待确认')}，"
        f"再从该区域酒店中选择：{selected.get('name', '待确认')}。"
    )


def main() -> None:
    default_host = AGENTS["hotel_agent"]["host"]
    default_port = AGENTS["hotel_agent"]["port"]

    parser = argparse.ArgumentParser(description="Run Hotel Agent.")
    parser.add_argument("--host", default=default_host)
    parser.add_argument("--port", type=int, default=default_port)
    args = parser.parse_args()

    agent = HotelAgent(host=args.host, port=args.port)
    agent.run()


if __name__ == "__main__":
    main()
