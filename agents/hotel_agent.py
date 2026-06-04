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

from agents.base_agent import BaseAgent, _demo_fast_mode_enabled
from agents.request_parser import extract_travel_task_from_payload
from common.config import AGENTS, COORDINATOR_NAME, MCP_GATEWAY, MCP_HTTP_TIMEOUT_SECONDS, MCP_SERVERS
from common.http_client import HttpJsonClientError, post_json
from common.logger import log_network_event
from common.schemas import RESULT_SUCCESS, build_error_result_payload, build_result_payload
from llm_client import llm_small as llm


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
            travel_task = _extract_travel_task(task_payload)
            # 为什么一定要weather_constraints呢？景点那个确实有道理，但是这个酒店选择并没有和天气有直接关系，不应该写死的我觉得，有种提前知道参考答案的感觉
            inputs = context.get("inputs") or {}
            upstream_results = inputs.get("upstream_results", {})
            daily_plan = _extract_daily_plan(inputs)
            city = str(travel_task.get("destination_city") or travel_task.get("city") or "未指定")

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

            if _demo_fast_mode_enabled():
                selection = _fallback_hotel_selection(
                    area_options=area_options,
                    hotel_options=hotel_options_for_llm,
                    travel_task=travel_task,
                )
                quality_source = "hotel_agent_rule_fallback_demo_fast"
            else:
                try:
                    llm_hotel_selection = llm.chat_json(
                        _hotel_selector_prompt(
                            travel_task=travel_task,
                            upstream_results=upstream_results,
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
                # 我怎么知道我会对traffic产生影响呢？不应该有constraints_for_traffic
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
                    "upstream_results": upstream_results,
                    "hotel_constraints": _constraint_section(travel_task, "hotel"),
                    "general_constraints": _constraint_section(travel_task, "general"),
                    "daily_plan_skeleton": daily_plan,
                    "structured_result": structured_result,
                    "hotel_plan": hotel_plan,
                    "selected_hotel": hotel_plan.get("selected_hotel") if isinstance(hotel_plan, dict) else {},
                    "hotel_area": hotel_plan.get("recommended_area") if isinstance(hotel_plan, dict) else None,
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


def _extract_travel_task(task_payload: dict[str, Any]) -> dict[str, Any]:
    return extract_travel_task_from_payload(task_payload, capability="hotel")





def _extract_daily_plan(inputs: dict[str, Any]) -> dict[str, Any]:
    upstream = inputs.get("upstream_results", {})
    attr_res = upstream.get("attraction_agent", {}).get("structured", {})
    return attr_res.get("daily_plan") or attr_res.get("daily_plan_skeleton") or {}



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
    '''
    规范化候选酒店的信息给llm
    '''
    options: list[dict[str, Any]] = []
    hotels = hotel_candidates.get("hotels", []) if isinstance(hotel_candidates, dict) else []
    ranked_hotels = _rank_hotel_candidates([item for item in hotels if isinstance(item, dict)])
    for index, hotel in enumerate(ranked_hotels, start=1):
        options.append(
            {
                "hotel_id": f"h{index}",
                "name": hotel.get("name"),
                "area": hotel.get("area"),
                "price_per_night": hotel.get("price_per_night"),
                "type": hotel.get("type"),
                "nearest_subway": hotel.get("nearest_subway"),
                "tags": hotel.get("tags") if isinstance(hotel.get("tags"), list) else [],
                "provider_hotel_id": hotel.get("hotel_id") or hotel.get("id"),
                "location": hotel.get("location"),
                "address": hotel.get("address"),
            }
        )
    return options


def _rank_hotel_candidates(hotels: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(hotels, key=_hotel_quality_key)


def _hotel_quality_key(hotel: dict[str, Any]) -> tuple[int, int, int, int, int, str]:
    name = str(hotel.get("name") or "")
    hotel_type = str(hotel.get("type") or "")
    price = _safe_int(hotel.get("price_per_night"), default=9999)
    has_price = 0 if hotel.get("price_per_night") not in (None, "", "待确认") else 1
    has_subway = 0 if str(hotel.get("nearest_subway") or "").strip() not in {"", "待确认"} else 1
    good_type = 0 if any(word in hotel_type for word in ["酒店", "宾馆", "经济型连锁酒店", "舒适型酒店"]) else 1
    weak_type = 1 if "住宿服务相关" in hotel_type else 0
    weak_name = 1 if any(word in name for word in ["家庭旅店", "招待所"]) else 0
    return (has_price + has_subway + good_type + weak_type + weak_name, has_subway, has_price, good_type, price, name)


def _constraint_section(travel_task: dict[str, Any], section: str) -> dict[str, Any]:
    '''
    从task中总结各种约束，section可以是hotel/traffic/general/attractions等，返回对应的约束字典
    '''
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
        budget_level = travel_task.get("budget_level", "normal")
        return {
            "budget_level": budget_level,
            "travel_style": "budget" if budget_level == "low" else ("comfort" if budget_level in {"high", "luxury"} else "balanced"),
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
    upstream_results: dict[str, Any],
    hotel_options: list[dict[str, Any]],
) -> str:
    budget_style = _hotel_style_rule(travel_task)
    raw_constraints = travel_task.get("raw_constraints") or ""
    payload = {
        "city": travel_task.get("destination_city") or travel_task.get("city") or "未指定",
        "days": travel_task.get("days", 3),
        "user_intent": raw_constraints,
        "selection_rule": budget_style,
        "hotel_constraints": _constraint_section(travel_task, "hotel"),
        "traffic_constraints": _constraint_section(travel_task, "traffic"),
        "general_constraints": _constraint_section(travel_task, "general"),
        "attraction_constraints": _constraint_section(travel_task, "attractions"),
        "area_options": area_options,
        "hotel_options": hotel_options,
        "upstream_results": upstream_results,
        "output_schema": {
            "recommended_area_id": "a1",
            "selected_hotel_id": "h1",
            "reason": "不超过20个中文字符",
        },
    }
    budget_guidance = {
        "high": "预算充裕、追求舒适：优先选择价格高、评分高、环境好的酒店，"
                "不要因价格低而选择经济型酒店。用户宁愿多花钱也要住得舒服。",
        "normal": "预算适中、追求性价比：在价格和品质之间取得平衡。",
        "low": "预算有限：优先选择价格低、交通便利的酒店。",
    }.get(
        str(_constraint_section(travel_task, "general").get("budget_level")
            or travel_task.get("budget_level", "")).strip().lower(),
        "预算水平未知：优先选择评分高、交通便利的酒店。",
    )
    return "\n".join(
        [
            "你是住宿区域与酒店选择器。",
            "必须从 area_options 中选择一个 area_id；必须从 hotel_options 中选择一个 hotel_id。",
            "hotel_id 必须来自已有候选；推荐区域最好与酒店 area 一致；不要编造酒店；不要改写酒店信息。",
            "仔细参考 upstream_results 内的前置依赖节点 (比如 weather、attraction) 等智能体给出的结果。",
            "",
            f"【用户意图】{raw_constraints}" if raw_constraints else "",
            f"【选择策略】{budget_style}",
            f"【预算指导】{budget_guidance}",
            "",
            "不要输出完整 hotel_plan；不要 Markdown；不要推理过程；不要大段解释；只输出合法 JSON。",
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
        "hotel_id": hotel.get("provider_hotel_id") or hotel.get("hotel_id"),
        "name": hotel.get("name") or "待确认住宿",
        "area": hotel.get("area") or area.get("area") or "市中心地铁沿线",
        "price_per_night": hotel.get("price_per_night"),
        "nearest_subway": hotel.get("nearest_subway"),
        "type": hotel.get("type") or "经济型酒店",
    }
    reason_prefix = ""
    selected_hotel["location"] = hotel.get("location")
    selected_hotel["address"] = hotel.get("address")
    return {
        "recommended_area": area.get("area") or selected_hotel["area"],
        "area_reason": "该区域覆盖较多行程景点，适合减少通勤。",
        "selected_hotel": selected_hotel,
        "hotel_reason": f"{reason_prefix}{_hotel_reason_from_facts(selected_hotel)}",
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
        quality = _hotel_quality_key(hotel)[0]
        return quality, area_match, has_subway, price

    selected = min(hotel_options, key=key)
    return str(selected.get("hotel_id") or "")


def _fallback_area_id(area_options: list[dict[str, Any]]) -> str:
    return str((area_options[0] if area_options else {}).get("area_id") or "")


def _hotel_reason_from_facts(selected_hotel: dict[str, Any]) -> str:
    parts: list[str] = []
    price = selected_hotel.get("price_per_night")
    subway = selected_hotel.get("nearest_subway")
    hotel_type = str(selected_hotel.get("type") or "")
    if price not in (None, "", "待确认"):
        parts.append(f"参考价格约{price}元/晚")
    if subway not in (None, "", "待确认"):
        parts.append(f"靠近{subway}")
    if any(word in hotel_type for word in ["酒店", "宾馆", "经济型连锁酒店", "舒适型酒店"]):
        parts.append("住宿类型明确")
    if not parts:
        return "实时数据不足，优先作为区域住宿参考，具体房价和交通以订房平台及地图为准。"
    return "，".join(parts) + "，适合作为本次住宿方案。"


def _hotel_style_rule(travel_task: dict[str, Any]) -> str:
    general = _constraint_section(travel_task, "general")
    traffic = _constraint_section(travel_task, "traffic")
    budget_level = str(general.get("budget_level") or travel_task.get("budget_level") or "normal")
    travel_style = str(general.get("travel_style") or "")
    preference = str(traffic.get("preference") or travel_task.get("transport_preference") or "")
    if budget_level in {"high", "luxury"} or travel_style == "comfort":
        return "舒适优先：优先高品质酒店、环境好、少换乘、少步行和通勤更均衡"
    if budget_level == "low":
        return "预算有限：优先价格可控、公共交通便利和通勤成本低"
    if preference == "taxi":
        return "交通偏好打车：优先靠近主要景点区域，减少跨城通勤"
    return "普通预算：优先交通便利、体验均衡、性价比稳定"


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
    }




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
