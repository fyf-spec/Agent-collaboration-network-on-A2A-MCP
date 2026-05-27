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


class HotelAgent(BaseAgent):
    agent_name = "hotel_agent"
    capability = "hotel"
    mcp_server_key = "hotel"

    def process_task(self, task_payload: dict[str, Any]) -> None:
        """Two-step hotel workflow: LLM selects area -> MCP returns hotels -> LLM selects hotel."""
        task_id = str(task_payload["task_id"])
        started = time.perf_counter()
        area_llm_used = False
        hotel_llm_used = False
        area_llm_error: str | None = None
        hotel_llm_error: str | None = None

        try:
            context = task_payload.get("context") or {}
            travel_task = _extract_travel_task(context)
            weather_constraints = _extract_weather_constraints(context)
            daily_plan = _extract_daily_plan(context)
            city = str(travel_task.get("destination_city") or travel_task.get("city") or "北京")

            area_candidates = _build_area_candidates(daily_plan)

            # Step 1: LLM chooses the best accommodation area from the itinerary.
            try:
                area_json = llm.chat_json(
                    _hotel_area_selection_prompt(
                        travel_task=travel_task,
                        weather_constraints=weather_constraints,
                        daily_plan=daily_plan,
                        area_candidates=area_candidates,
                    ),
                    max_tokens=500,
                    temperature=0.0,
                    timeout_seconds=25.0,
                )
                area_selection = _normalize_area_selection(area_json, area_candidates)
                area_llm_used = True
            except Exception as exc:
                area_llm_error = str(exc)
                area_selection = _fallback_area_selection(area_candidates)

            selected_area = str(area_selection.get("recommended_area") or "").strip()
            if not selected_area:
                selected_area = area_candidates[0]["area"] if area_candidates else "市中心地铁沿线"
                area_selection["recommended_area"] = selected_area

            # Step 2: MCP searches hotels only for the selected area.
            hotel_candidates = self.call_hotel_mcp(
                task_id,
                city=city,
                travel_task=travel_task,
                daily_plan=daily_plan,
                selected_area=selected_area,
                area_selection=area_selection,
            )

            # Step 3: LLM chooses the specific hotel from MCP candidates.
            try:
                hotel_json = llm.chat_json(
                    _hotel_choice_prompt(
                        travel_task=travel_task,
                        weather_constraints=weather_constraints,
                        daily_plan=daily_plan,
                        area_selection=area_selection,
                        hotel_candidates=hotel_candidates,
                    ),
                    max_tokens=700,
                    temperature=0.0,
                    timeout_seconds=25.0,
                )
                structured_result = _normalize_hotel_plan(
                    hotel_json,
                    hotel_candidates=hotel_candidates,
                    area_selection=area_selection,
                    travel_task=travel_task,
                )
                hotel_llm_used = True
            except Exception as exc:
                hotel_llm_error = str(exc)
                structured_result = _fallback_hotel_plan(hotel_candidates, area_selection, travel_task)

            elapsed_ms = (time.perf_counter() - started) * 1000
            hotel_plan = structured_result.get("hotel_plan", {})
            result_payload = build_result_payload(
                source=self.agent_name,
                target=COORDINATOR_NAME,
                task_id=task_id,
                status=RESULT_SUCCESS,
                result=_short_hotel_summary(structured_result),
                metadata={
                    "agent": self.agent_name,
                    "capability": self.capability,
                    "workflow": "area_llm_then_hotel_mcp_then_hotel_llm",
                    "mcp_server": MCP_SERVERS[self.mcp_server_key]["name"],
                    "mcp_method": "search_hotels",
                    "area_candidates": area_candidates,
                    "area_selection": area_selection,
                    "mcp_result": hotel_candidates,
                    "hotel_candidates": hotel_candidates.get("hotels", []) if isinstance(hotel_candidates, dict) else [],
                    "travel_task": travel_task,
                    "weather_constraints": weather_constraints,
                    "daily_plan_skeleton": daily_plan,
                    "structured_result": structured_result,
                    "hotel_plan": hotel_plan,
                    "selected_hotel": hotel_plan.get("selected_hotel") if isinstance(hotel_plan, dict) else {},
                    "hotel_area": hotel_plan.get("recommended_area") if isinstance(hotel_plan, dict) else None,
                    "constraints_for_traffic": structured_result.get("constraints_for_traffic", []),
                    "quality": {
                        "area_llm_used": area_llm_used,
                        "hotel_llm_used": hotel_llm_used,
                        "llm_used": area_llm_used or hotel_llm_used,
                        "area_llm_error": area_llm_error,
                        "hotel_llm_error": hotel_llm_error,
                        "confidence": 0.9 if (area_llm_error is None and hotel_llm_error is None) else 0.76,
                    },
                    "llm_error": area_llm_error or hotel_llm_error,
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
                "budget_level": travel_task.get("budget_level", "normal"),
                "preferences": travel_task.get("preferences", []),
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
