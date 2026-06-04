from __future__ import annotations

from collections import defaultdict
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import argparse
import json
from pathlib import Path
import re
import sys
import threading
import time
from typing import Any
from urllib import request


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


from agents.base_agent import BaseAgent, _demo_fast_mode_enabled
from agents.request_parser import extract_travel_task_from_context
from common.config import (
    AGENTS,
    COORDINATOR_NAME,
    MCP_GATEWAY,
    MCP_HTTP_TIMEOUT_SECONDS,
    MCP_SERVERS,
    REGISTRY_HOST,
    REGISTRY_PORT,
)
from common.http_client import HttpJsonClientError, post_json
from common.logger import log_network_event
from common.schemas import (
    PayloadValidationError,
    RESULT_SUCCESS,
    build_error_result_payload,
    build_result_payload,
    validate_task_payload,
)
from llm_client import LLMClientError
from llm_client import llm_small as llm


AGENT_NAME = "attraction_agent"
MCP_SERVER_KEY = "attraction"
CAPABILITY = "attraction"


class AttractionAgent(BaseAgent):
    agent_name = AGENT_NAME
    capability = CAPABILITY
    mcp_server_key = MCP_SERVER_KEY

    def process_task(self, task_payload: dict[str, Any]) -> None:
        result_payload = handle_task(task_payload, callback=False)
        self.send_result_to_coordinator(task_payload, result_payload)

    def build_prompt(self, task_payload: dict[str, Any], mcp_result: dict[str, Any]) -> str:
        return "AttractionAgent uses process_task override for structured attraction planning."


class AttractionAgentServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, server_address: tuple[str, int]) -> None:
        super().__init__(server_address, AttractionAgentHandler)


class AttractionAgentHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/health":
            self._send_json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "role": AGENT_NAME,
                    "status": "ok",
                    "capability": CAPABILITY,
                    "mcp_server_key": MCP_SERVER_KEY,
                },
            )
            return
        self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": f"unknown path: {self.path}"})

    def do_POST(self) -> None:
        if self.path != "/execute_task":
            self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": f"unknown path: {self.path}"})
            return

        try:
            payload = self._read_json()
            validate_task_payload(payload)
            task_id = str(payload["task_id"])

            log_network_event(
                event="agent_receive_task",
                direction="inbound",
                source=str(payload.get("source", "coordinator")),
                target=AGENT_NAME,
                method="POST",
                url=self.path,
                task_id=task_id,
                payload=payload,
            )

            worker = threading.Thread(
                target=handle_task,
                args=(payload,),
                name=f"{AGENT_NAME}-{task_id[:8]}",
                daemon=True,
            )
            worker.start()

            self._send_json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "accepted": True,
                    "agent": AGENT_NAME,
                    "task_id": task_id,
                },
            )
        except PayloadValidationError as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})
        except ValueError as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": f"invalid json: {exc}"})
        except Exception as exc:
            log_network_event(
                event="agent_error",
                direction="internal",
                source=AGENT_NAME,
                target=AGENT_NAME,
                payload=locals().get("payload"),
                error=str(exc),
                error_type=type(exc).__name__,
            )
            self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": str(exc)})

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        raw_body = self.rfile.read(length).decode("utf-8") if length else ""
        payload = json.loads(raw_body or "{}")
        if not isinstance(payload, dict):
            raise ValueError("body must be a JSON object")
        return payload

    def _send_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(int(status))
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def handle_task(task_payload: dict[str, Any], *, callback: bool = True) -> dict[str, Any]:
    started = time.perf_counter()
    task_id = str(task_payload["task_id"])
    instruction = str(task_payload.get("instruction", ""))
    context = task_payload.get("context") or {}
    reply_to = str(task_payload["reply_to"])
    llm_used = False
    llm_error: str | None = None

    try:
        travel_task = _extract_travel_task(instruction, context)
        # 天气约束影响景点推荐（雨天优先室内）
        inputs = context.get("inputs", {})
        weather_constraints = _extract_weather_constraints(inputs)
        upstream_results = inputs.get("upstream_results", {})
        mcp_result = call_attraction_mcp(task_id, travel_task)
        spots = mcp_result.get("spots", [])
        days = int(travel_task.get("days") or mcp_result.get("days") or 3)
        compact_spots = build_compact_spots(spots)
        grouped_spots = build_grouped_spots(compact_spots)
        spot_relations = build_spot_relations(grouped_spots)
        llm_daily_spot_ids: dict[str, list[str]] = {}
        quality_source = "attraction_agent_rule_fallback"

        if _demo_fast_mode_enabled():
            llm_daily_spot_ids = build_rule_daily_spot_ids(
                compact_spots=compact_spots,
                days=days,
                must_visit=_attraction_must_visit(travel_task),
                weather_constraints=weather_constraints,
            )
            quality_source = "attraction_agent_rule_fallback_demo_fast"
        else:
            try:
                llm_json = llm.chat_json(
                    _attraction_selection_prompt(
                        travel_task=travel_task,
                        weather_constraints=weather_constraints,
                        grouped_spots=grouped_spots,
                        spot_relations=spot_relations,
                    ),
                    max_tokens=400,
                    temperature=0.2,
                    timeout_seconds=45.0,
                )
                llm_daily_spot_ids = normalize_daily_spot_ids(
                    llm_json.get("daily_spot_ids"),
                    compact_spots=compact_spots,
                    days=days,
                    must_visit=_attraction_must_visit(travel_task),
                    weather_constraints=weather_constraints,
                )
                if not any(llm_daily_spot_ids.values()):
                    raise ValueError("LLM daily_spot_ids is empty after validation")
                llm_used = True
                quality_source = "attraction_agent_llm_spot_selector"
            except Exception as exc:
                llm_error = str(exc)
                llm_daily_spot_ids = build_rule_daily_spot_ids(
                    compact_spots=compact_spots,
                    days=days,
                    must_visit=_attraction_must_visit(travel_task),
                    weather_constraints=weather_constraints,
                )
                quality_source = "attraction_agent_rule_fallback"

        daily_plan_skeleton = expand_daily_plan_skeleton(
            llm_daily_spot_ids,
            compact_spots=compact_spots,
            days=days,
            weather_constraints=weather_constraints,
            travel_task=travel_task,
        )
        if not any(day.get("spots") for day in daily_plan_skeleton.values() if isinstance(day, dict)):
            llm_error = llm_error or "daily_plan_skeleton is empty after expansion"
            llm_used = False
            quality_source = "attraction_agent_rule_fallback"
            llm_daily_spot_ids = build_rule_daily_spot_ids(
                compact_spots=compact_spots,
                days=days,
                must_visit=_attraction_must_visit(travel_task),
                weather_constraints=weather_constraints,
            )
            daily_plan_skeleton = expand_daily_plan_skeleton(
                llm_daily_spot_ids,
                compact_spots=compact_spots,
                days=days,
                weather_constraints=weather_constraints,
                travel_task=travel_task,
            )


        rejected_spots = []

        ticket_total = estimate_ticket_total(daily_plan_skeleton)
        summary = build_summary(travel_task, daily_plan_skeleton)
        structured_result = {
            "daily_plan_skeleton": daily_plan_skeleton,
            "estimated_cost": {"ticket_total": ticket_total},
            "rejected_spots": rejected_spots,
        }
        metadata = {
            "agent": AGENT_NAME,
            "capability": CAPABILITY,
            "mcp_server": MCP_SERVERS[MCP_SERVER_KEY]["name"],
            "mcp_method": MCP_SERVERS[MCP_SERVER_KEY]["method"],
            "mcp_result": mcp_result,
            "travel_task": travel_task,
            "upstream_results": upstream_results,
            "attraction_constraints": _constraint_section(travel_task, "attractions"),
            "general_constraints": _constraint_section(travel_task, "general"),
            "weather_constraints": weather_constraints,
            "compact_spots": compact_spots,
            "grouped_spots": grouped_spots,
            "spot_relations": spot_relations,
            "llm_daily_spot_ids": llm_daily_spot_ids,
            "daily_plan_skeleton": daily_plan_skeleton,
            "estimated_cost": {"ticket_total": ticket_total},
            "structured_result": structured_result,
            "quality": {
                "llm_used": llm_used,
                "llm_error": llm_error,
                "source": quality_source,
                "missing_fields": [],
                "confidence": 0.9 if llm_error is None else 0.78,
            },
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
        }
        result_payload = build_result_payload(
            source=AGENT_NAME,
            target=COORDINATOR_NAME,
            task_id=task_id,
            status=RESULT_SUCCESS,
            result=summary,
            metadata=metadata,
        )
    except Exception as exc:
        result_payload = build_error_result_payload(
            source=AGENT_NAME,
            target=COORDINATOR_NAME,
            task_id=task_id,
            message=str(exc),
            error_code="agent_execution_failed",
            http_status=500,
            metadata={
                "agent": AGENT_NAME,
                "capability": CAPABILITY,
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
            },
        )

    if callback:
        _callback_result(task_id, reply_to, result_payload)
    return result_payload


def _callback_result(task_id: str, reply_to: str, result_payload: dict[str, Any]) -> None:
    callback_started = time.perf_counter()
    log_network_event(
        event="agent_callback_result",
        direction="outbound",
        source=AGENT_NAME,
        target=COORDINATOR_NAME,
        method="POST",
        url=reply_to,
        task_id=task_id,
        payload=result_payload,
    )
    try:
        callback_response = post_json(reply_to, result_payload, timeout=MCP_HTTP_TIMEOUT_SECONDS)
        log_network_event(
            event="agent_callback_response",
            direction="inbound",
            source=COORDINATOR_NAME,
            target=AGENT_NAME,
            method="POST",
            url=reply_to,
            task_id=task_id,
            payload=callback_response.data,
            status_code=callback_response.status_code,
            elapsed_ms=callback_response.elapsed_ms,
            error=None if callback_response.ok else callback_response.raw_body,
        )
    except HttpJsonClientError as exc:
        log_network_event(
            event="agent_callback_response",
            direction="inbound",
            source=COORDINATOR_NAME,
            target=AGENT_NAME,
            method="POST",
            url=reply_to,
            task_id=task_id,
            status_code=0,
            elapsed_ms=(time.perf_counter() - callback_started) * 1000,
            error=str(exc),
            error_type=type(exc).__name__,
        )


def call_attraction_mcp(task_id: str, travel_task: dict[str, Any]) -> dict[str, Any]:
    config = MCP_SERVERS[MCP_SERVER_KEY]
    url = f"http://{MCP_GATEWAY['host']}:{MCP_GATEWAY['port']}{MCP_GATEWAY.get('path', '/')}"
    network_target = str(MCP_GATEWAY["name"])
    params = {
        "city": travel_task.get("destination_city") or travel_task.get("city") or "北京",
        "days": travel_task.get("days", 3),
        "budget_level": _constraint_section(travel_task, "general").get("budget_level", travel_task.get("budget_level", "normal")),
        "must_visit": _attraction_must_visit(travel_task),
        "preferences": _constraint_section(travel_task, "attractions").get("preferred_types", travel_task.get("preferences", [])),
        "requested_fields": [
            "name",
            "area",
            "ticket",
            "duration",
            "open_time",
            "reservation_required",
            "indoor_or_outdoor",
            "nearest_subway",
            "tags",
        ],
    }
    rpc_payload = {"jsonrpc": "2.0", "id": task_id, "method": config["method"], "params": params}

    log_network_event(
        event="agent_call_mcp",
        direction="outbound",
        source=AGENT_NAME,
        target=network_target,
        method="POST",
        url=url,
        task_id=task_id,
        payload=rpc_payload,
    )
    response = post_json(url, rpc_payload, timeout=MCP_HTTP_TIMEOUT_SECONDS)
    log_network_event(
        event="agent_mcp_response",
        direction="inbound",
        source=network_target,
        target=AGENT_NAME,
        method="POST",
        url=url,
        task_id=task_id,
        payload=response.data,
        status_code=response.status_code,
        elapsed_ms=response.elapsed_ms,
        error=None if response.ok else response.raw_body,
    )

    if not response.ok:
        raise RuntimeError(f"Attraction MCP HTTP error {response.status_code}: {response.raw_body}")
    if not isinstance(response.data, dict):
        raise RuntimeError("Attraction MCP returned non-object JSON")
    if "error" in response.data:
        raise RuntimeError(f"Attraction MCP error: {response.data['error']}")
    result = response.data.get("result")
    if not isinstance(result, dict):
        raise RuntimeError("Attraction MCP result is missing or invalid")
    return result


def _attraction_selection_prompt(
    *,
    travel_task: dict[str, Any],
    weather_constraints: dict[str, Any],
    grouped_spots: list[dict[str, Any]],
    spot_relations: list[dict[str, Any]],
) -> str:
    must_visit_ids = _must_visit_spot_ids(grouped_spots, _attraction_must_visit(travel_task))
    payload = {
        "city": travel_task.get("destination_city") or travel_task.get("city") or "北京",
        "days": travel_task.get("days", 3),
        "attraction_constraints": _constraint_section(travel_task, "attractions"),
        "general_constraints": _constraint_section(travel_task, "general"),
        "weather_summary": _weather_summary(weather_constraints),
        "grouped_spots": grouped_spots,
        "spot_relations": spot_relations,
        "must_visit_spot_ids": must_visit_ids,
        "output_schema": {
            "daily_spot_ids": {
                "day1": ["s1", "s2"],
                "day2": ["s3"],
            },
            "reason": "不超过20个中文字符",
        },
    }
    must_line = ""
    if must_visit_ids:
        must_line = f"【硬性要求】must_visit_spot_ids={must_visit_ids} 这些景点必须全部出现在行程中，一个都不能少。"
    return "\n".join(
        [
            "你是景点选择器，只从给定 spot_id 中选择每天安排哪些景点。",
            "景点已按地理区域分组；spot_relations 已给出远近关系，不要根据地址自行猜测距离。",
            "优先把同一区域/very_close 景点放在同一天；不同区域除非必要不要强行同一天。",
            "每天安排 1-3 个景点；general_constraints 预算有限时优先免费或低价；雨天优先 indoor 或 mixed。",
            must_line,
            "不要编造新景点；不要 Markdown；不要解释；不要推理过程；只输出合法 JSON。",
            "输出格式只能是 {\"daily_spot_ids\":{\"day1\":[\"s1\"]},\"reason\":\"不超过20个中文字符\"}。",
            json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str),
        ]
    )


def build_compact_spots(spots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    '''
    总之就是把spots再压缩一下
    '''
    compact: list[dict[str, Any]] = []
    quality_spots = _quality_filter_spots(spots)
    for index, spot in enumerate(quality_spots, start=1):
        if not isinstance(spot, dict):
            continue
        area = spot.get("area") or spot.get("area_cluster") or "未知区域"
        compact.append(
            {
                "id": f"s{index}",
                "name": spot.get("name"),
                "area": area,
                "ticket": spot.get("ticket"),
                "duration": spot.get("duration"),
                "indoor_or_outdoor": spot.get("indoor_or_outdoor"),
                "reservation_required": bool(spot.get("reservation_required")),
                "tags": spot.get("tags") if isinstance(spot.get("tags"), list) else [],
                "spot_id": spot.get("spot_id") or spot.get("id"),
                "location": spot.get("location"),
                "address": spot.get("address"),
            }
        )
    return compact


def _quality_filter_spots(spots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    candidates = [spot for spot in spots if isinstance(spot, dict) and not _is_bad_attraction_poi(spot)]
    candidates.sort(key=_attraction_quality_key)
    selected: list[dict[str, Any]] = []
    for spot in candidates:
        name = str(spot.get("name") or "")
        if not name:
            continue
        if any(_is_parent_child_duplicate(name, str(item.get("name") or "")) for item in selected):
            continue
        selected.append(spot)
    return selected or [spot for spot in spots if isinstance(spot, dict)]


def _is_bad_attraction_poi(spot: dict[str, Any]) -> bool:
    name = str(spot.get("name") or "")
    poi_type = str(spot.get("type") or "")
    tags = " ".join(str(item) for item in spot.get("tags", []) if item) if isinstance(spot.get("tags"), list) else ""
    text = f"{name} {poi_type} {tags}"
    bad_words = [
        "停车场",
        "售票处",
        "服务中心",
        "游客中心",
        "旅行社",
        "观光巴士",
        "商店",
        "酒店",
        "宾馆",
        "公交站",
        "地铁站",
        "火车站",
        "码头",
        "出入口",
        "入口",
    ]
    if any(word in text for word in bad_words):
        return True
    representative_words = [
        "风景名胜",
        "公园",
        "博物馆",
        "纪念馆",
        "纪念堂",
        "寺",
        "塔",
        "步行街",
        "古迹",
        "故宫",
        "西湖",
        "沙面",
        "永庆坊",
        "陈家祠",
        "越秀",
        "广州塔",
        "北京路",
        "大学",
        "高等院校",
        "学校",
    ]
    has_local_facts = any(spot.get(field) not in (None, "", "待确认") for field in ["ticket", "duration", "nearest_subway"])
    if not has_local_facts and not any(word in text for word in representative_words):
        return True
    return False


def _attraction_quality_key(spot: dict[str, Any]) -> tuple[int, int, int, str]:
    name = str(spot.get("name") or "")
    tags = [str(item) for item in spot.get("tags", [])] if isinstance(spot.get("tags"), list) else []
    penalty = 0
    if any(word in name for word in ["-", "门", "广场内部点位", "站", "码头"]):
        penalty += 3
    if spot.get("ticket") is None:
        penalty += 1
    if spot.get("duration") is None:
        penalty += 1
    if "经典景点" in tags or "热门" in tags:
        penalty -= 2
    return (penalty, len(name), 0 if spot.get("location") else 1, name)


def _is_parent_child_duplicate(left: str, right: str) -> bool:
    if not left or not right or left == right:
        return left == right
    left_base = left.split("-")[0]
    right_base = right.split("-")[0]
    if left_base == right_base and (left_base in left or right_base in right):
        return True
    return left in right or right in left


def build_grouped_spots(compact_spots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for spot in compact_spots:
        groups[str(spot.get("area") or "未知区域")].append(spot)
    result: list[dict[str, Any]] = []
    for area, area_spots in groups.items():
        result.append(
            {
                "area": area,
                "spot_ids": [str(spot.get("id")) for spot in area_spots],
                "spots": area_spots,
                "internal_travel_hint": "同一区域，优先安排在同一天",
            }
        )
    result.sort(key=lambda item: len(item["spot_ids"]), reverse=True)
    return result


def build_spot_relations(grouped_spots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    relations: list[dict[str, Any]] = []
    for group in grouped_spots:
        spot_ids = [str(spot_id) for spot_id in group.get("spot_ids", []) if str(spot_id).strip()]
        for index, source_id in enumerate(spot_ids):
            for target_id in spot_ids[index + 1 :]:
                relations.append(
                    {
                        "from": source_id,
                        "to": target_id,
                        "relation": "very_close",
                        "hint": "适合安排在同一天",
                    }
                )
                if len(relations) >= 18:
                    return relations
    if len(grouped_spots) > 1:
        first_group = grouped_spots[0].get("spot_ids", [])
        for group in grouped_spots[1:4]:
            other_ids = group.get("spot_ids", [])
            if first_group and other_ids:
                relations.append(
                    {
                        "from": str(first_group[0]),
                        "to": str(other_ids[0]),
                        "relation": "far_or_unknown",
                        "hint": "除非必要，不要强行安排在同一天",
                    }
                )
    return relations


def _weather_summary(weather_constraints: dict[str, Any]) -> str:
    if not isinstance(weather_constraints, dict) or not weather_constraints:
        return "天气约束未知，按常规户外安排"
    risk = str(weather_constraints.get("risk_level") or "unknown")
    rainy_days = _as_short_list(weather_constraints.get("rainy_days"))
    indoor_days = _as_short_list(weather_constraints.get("indoor_preferred_days"))
    condition = str(weather_constraints.get("raw_condition") or "")
    if rainy_days or indoor_days:
        return f"风险{risk}；{','.join(rainy_days or indoor_days)}优先室内/mixed；天气{condition}"
    return f"风险{risk}；适合户外；天气{condition}"


def _as_short_list(value: Any) -> list[str]:
    '''
    把列表规范化一下，去掉空白的项
    '''
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).strip()]


def _constraint_section(travel_task: dict[str, Any], section: str) -> dict[str, Any]:
    constraints = travel_task.get("constraints")
    if isinstance(constraints, dict) and isinstance(constraints.get(section), dict):
        return dict(constraints[section])
    if section == "attractions":
        return {
            "must_visit": _as_short_list(travel_task.get("must_visit")),
            "preferred_types": _as_short_list(travel_task.get("preferences")),
            "avoid": _as_short_list(travel_task.get("avoid")),
            "pace": "normal",
        }
    if section == "general":
        budget_level = travel_task.get("budget_level", "normal")
        return {
            "budget_level": budget_level,
            "travel_style": "budget" if budget_level == "low" else ("comfort" if budget_level in {"high", "luxury"} else "balanced"),
            "special_needs": [],
        }
    return {}


def _must_visit_spot_ids(grouped_spots: list[dict[str, Any]], must_names: list[str]) -> list[str]:
    """从 grouped_spots 中找到 must_visit 对应的 spot_id 列表"""
    if not must_names:
        return []
    ids: list[str] = []
    names_lower = [str(n).strip().lower() for n in must_names if str(n).strip()]
    for spot in grouped_spots:
        if not isinstance(spot, dict):
            continue
        spot_name = str(spot.get("name") or "").lower()
        if any(_must_visit_match(spot_name, mn) for mn in names_lower):
            sid = str(spot.get("id") or "")
            if sid:
                ids.append(sid)
    return ids


def _attraction_must_visit(travel_task: dict[str, Any]) -> list[str]:
    '''
    从task中提取must_visit
    '''
    attractions = _constraint_section(travel_task, "attractions")
    must_visit = attractions.get("must_visit")
    if isinstance(must_visit, list):
        return _as_short_list(must_visit)
    return _as_short_list(travel_task.get("must_visit"))


def _weather_summary(weather_constraints: dict[str, Any]) -> str:
    if not isinstance(weather_constraints, dict) or not weather_constraints:
        return "weather unknown; plan outdoor normally"
    rainy_days = _as_short_list(weather_constraints.get("rainy_days"))
    indoor_days = _as_short_list(weather_constraints.get("indoor_preferred_days"))
    outdoor_days = _as_short_list(weather_constraints.get("outdoor_suitable_days") or weather_constraints.get("outdoor_good_days"))
    condition = str(weather_constraints.get("raw_condition") or "")
    if rainy_days or indoor_days:
        return f"{','.join(rainy_days or indoor_days)} prefer indoor/mixed; weather={condition}"
    return f"{','.join(outdoor_days) or 'most_days'} suitable outdoor; weather={condition}"


def _truncate_text(value: Any, limit: int) -> str:
    text = str(value or "").strip()
    return text[:limit]


def normalize_daily_spot_ids(
    value: Any,
    *,
    compact_spots: list[dict[str, Any]],
    days: int,
    must_visit: list[str],
    weather_constraints: dict[str, Any],
) -> dict[str, list[str]]:
    if not isinstance(value, dict):
        raise ValueError("daily_spot_ids must be a JSON object")
    known_ids = {str(spot.get("id")) for spot in compact_spots if spot.get("id")}
    result: dict[str, list[str]] = {}
    used: set[str] = set()
    for day in range(1, max(1, days) + 1):
        day_key = f"day{day}"
        raw_ids = value.get(day_key)
        if not isinstance(raw_ids, list):
            raw_ids = []
        clean_ids: list[str] = []
        for item in raw_ids:
            spot_id = str(item).strip()
            if spot_id in known_ids and spot_id not in used and spot_id not in clean_ids:
                clean_ids.append(spot_id)
            if len(clean_ids) >= 3:
                break
        used.update(clean_ids)
        result[day_key] = clean_ids

    _add_missing_must_visit(result, compact_spots=compact_spots, must_visit=must_visit)
    _fill_empty_days(result, compact_spots=compact_spots, weather_constraints=weather_constraints)
    return result


def build_rule_daily_spot_ids(
    *,
    compact_spots: list[dict[str, Any]],
    days: int,
    must_visit: list[str],
    weather_constraints: dict[str, Any],
) -> dict[str, list[str]]:
    ranked = sorted(
        compact_spots,
        key=lambda spot: (
            0 if _matches_any_must_visit(spot, must_visit) else 1,
            0 if _is_free_or_low_cost(spot) else 1,
            str(spot.get("area") or ""),
        ),
    )
    result = {f"day{day}": [] for day in range(1, max(1, days) + 1)}
    used: set[str] = set()
    rainy_days = {str(day) for day in weather_constraints.get("rainy_days", [])}
    indoor_days = {str(day) for day in weather_constraints.get("indoor_preferred_days", [])}
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for spot in ranked:
        groups[str(spot.get("area") or "未知区域")].append(spot)
    area_groups = sorted(groups.values(), key=len, reverse=True)
    area_index = 0

    for day in range(1, max(1, days) + 1):
        day_key = f"day{day}"
        prefer_indoor = day_key in rainy_days or day_key in indoor_days
        selected: list[dict[str, Any]] = []
        if prefer_indoor:
            selected = [
                spot
                for spot in ranked
                if str(spot.get("id")) not in used and spot.get("indoor_or_outdoor") in {"indoor", "mixed"}
            ][:3]
        if not selected and area_groups:
            attempts = 0
            while attempts < len(area_groups):
                group = area_groups[area_index % len(area_groups)]
                area_index += 1
                attempts += 1
                selected = [spot for spot in group if str(spot.get("id")) not in used][:3]
                if selected:
                    break
        if not selected:
            selected = [spot for spot in ranked if str(spot.get("id")) not in used][:1]
        result[day_key] = [str(spot.get("id")) for spot in selected if spot.get("id")]
        used.update(result[day_key])

    _add_missing_must_visit(result, compact_spots=compact_spots, must_visit=must_visit)
    _fill_empty_days(result, compact_spots=compact_spots, weather_constraints=weather_constraints)
    return result


def expand_daily_plan_skeleton(
    daily_spot_ids: dict[str, list[str]],
    *,
    compact_spots: list[dict[str, Any]],
    days: int,
    weather_constraints: dict[str, Any],
    travel_task: dict[str, Any] | None = None,
) -> dict[str, Any]:
    spots_by_id = {str(spot.get("id")): spot for spot in compact_spots if spot.get("id")}
    rainy_days = {str(day) for day in weather_constraints.get("rainy_days", [])}
    indoor_days = {str(day) for day in weather_constraints.get("indoor_preferred_days", [])}
    daily_plan: dict[str, Any] = {}
    for day in range(1, max(1, days) + 1):
        day_key = f"day{day}"
        selected = [
            spots_by_id[spot_id]
            for spot_id in daily_spot_ids.get(day_key, [])
            if spot_id in spots_by_id
        ][:3]
        prefer_indoor = day_key in rainy_days or day_key in indoor_days
        daily_plan[day_key] = _format_compact_day_plan(selected, prefer_indoor, travel_task or {})
    return daily_plan


def _format_compact_day_plan(selected: list[dict[str, Any]], prefer_indoor: bool, travel_task: dict[str, Any]) -> dict[str, Any]:
    names = [str(spot.get("name")) for spot in selected if spot.get("name")]
    areas = [str(spot.get("area")) for spot in selected if spot.get("area")]
    area = _dominant_area(areas)
    same_area = bool(area and areas and all(item == area for item in areas))
    general = _constraint_section(travel_task, "general")
    budget_level = str(general.get("budget_level") or travel_task.get("budget_level") or "normal")
    travel_style = str(general.get("travel_style") or "")
    if budget_level == "low":
        notes = ["低预算下优先公共交通和步行"]
    elif budget_level in {"high", "luxury"} or travel_style == "comfort":
        notes = ["舒适优先：节奏放缓，优先选择体验更完整、通勤更顺的安排"]
    else:
        notes = ["兼顾游玩体验和通勤效率"]
    if same_area and len(selected) > 1:
        notes.insert(0, "同一区域景点优先安排在同一天")
    if prefer_indoor:
        notes.append("雨天优先室内或室内外结合景点")
    reservation_required = [str(spot.get("name")) for spot in selected if spot.get("reservation_required") and spot.get("name")]
    spot_details = [
        {
            "spot_id": spot.get("spot_id") or spot.get("id"),
            "name": spot.get("name"),
            "area": spot.get("area"),
            "location": spot.get("location"),
            "address": spot.get("address"),
        }
        for spot in selected
        if spot.get("name")
    ]
    return {
        "theme": "雨天室内安排" if prefer_indoor else f"{area or '景点'}集中游玩",
        "spots": names,
        "spot_details": spot_details,
        "area": area or "待定区域",
        "estimated_visit_time": estimate_visit_time_parts(selected),
        "estimated_ticket_cost": estimate_ticket_cost(selected),
        "reservation_required": reservation_required,
        "notes": notes,
    }


def _add_missing_must_visit(
    daily_spot_ids: dict[str, list[str]],
    *,
    compact_spots: list[dict[str, Any]],
    must_visit: list[str],
) -> None:
    if not must_visit:
        return
    used = {spot_id for ids in daily_spot_ids.values() for spot_id in ids}
    for spot in compact_spots:
        spot_id = str(spot.get("id") or "")
        if not spot_id or spot_id in used or not _matches_any_must_visit(spot, must_visit):
            continue
        target_day = _find_day_with_capacity(daily_spot_ids)
        if target_day:
            daily_spot_ids[target_day].append(spot_id)
            used.add(spot_id)


def _fill_empty_days(
    daily_spot_ids: dict[str, list[str]],
    *,
    compact_spots: list[dict[str, Any]],
    weather_constraints: dict[str, Any],
) -> None:
    used = {spot_id for ids in daily_spot_ids.values() for spot_id in ids}
    pool = [spot for spot in compact_spots if str(spot.get("id")) not in used]
    rainy_days = {str(day) for day in weather_constraints.get("rainy_days", [])}
    indoor_days = {str(day) for day in weather_constraints.get("indoor_preferred_days", [])}
    for day_key, ids in daily_spot_ids.items():
        if ids:
            continue
        prefer_indoor = day_key in rainy_days or day_key in indoor_days
        selected = _pick_next_spot(pool, prefer_indoor=prefer_indoor)
        if selected is None:
            selected = compact_spots[0] if compact_spots else None
        if selected and selected.get("id"):
            spot_id = str(selected["id"])
            ids.append(spot_id)
            used.add(spot_id)
            pool = [spot for spot in pool if str(spot.get("id")) != spot_id]


def _pick_next_spot(pool: list[dict[str, Any]], *, prefer_indoor: bool) -> dict[str, Any] | None:
    if prefer_indoor:
        for spot in pool:
            if spot.get("indoor_or_outdoor") in {"indoor", "mixed"}:
                return spot
    return pool[0] if pool else None


def _find_day_with_capacity(daily_spot_ids: dict[str, list[str]]) -> str | None:
    for day_key, ids in daily_spot_ids.items():
        if len(ids) < 3:
            return day_key
    return None


def _must_visit_match(spot_name: str, must_keyword: str) -> bool:
    """判断景点名称是否匹配用户要求的 must_visit，支持模糊匹配"""
    if must_keyword in spot_name or spot_name in must_keyword:
        return True
    # 去除常见后缀后匹配
    clean = must_keyword
    for suffix in ["基地", "景区", "博物馆", "公园", "遗址", "馆", "园", "寺", "风景区"]:
        if clean.endswith(suffix) and len(clean) > len(suffix) + 2:
            clean = clean[:-len(suffix)]
            break
    return len(clean) >= 3 and clean in spot_name


def _matches_any_must_visit(spot: dict[str, Any], must_visit: list[str]) -> bool:
    name = str(spot.get("name") or "").lower()
    return any(_must_visit_match(name, item.lower()) for item in must_visit if item)


def _is_free_or_low_cost(spot: dict[str, Any]) -> bool:
    ticket = str(spot.get("ticket") or "")
    tags = [str(item) for item in spot.get("tags", [])] if isinstance(spot.get("tags"), list) else []
    return "免费" in ticket or "低价" in tags or "低预算" in tags


def _dominant_area(areas: list[str]) -> str:
    if not areas:
        return ""
    counts: dict[str, int] = {}
    for area in areas:
        counts[area] = counts.get(area, 0) + 1
    return max(counts.items(), key=lambda item: item[1])[0]


def _extract_travel_task(instruction: str, context: dict[str, Any]) -> dict[str, Any]:
    return extract_travel_task_from_context(instruction, context, capability="attraction")

# TODO.确认天气约束的来源
def _extract_weather_constraints(inputs: dict[str, Any]) -> dict[str, Any]:
    upstream = inputs.get("upstream_results", {})
    structured = upstream.get("weather_agent", {}).get("structured", {})
    if isinstance(structured, dict) and isinstance(structured.get("weather_constraints"), dict):
        return structured["weather_constraints"]
    else: return {}


def _extract_origin_city(text: str) -> str | None:
    for city in ["上海", "北京", "广州", "深圳", "杭州", "南京", "成都", "重庆", "武汉", "西安", "苏州", "天津"]:
        if f"从{city}" in text:
            return city
    return None


def _extract_destination_city(text: str) -> str:
    for city in ["北京", "上海", "广州", "深圳", "杭州", "南京", "成都", "重庆", "武汉", "西安", "苏州", "天津"]:
        if f"去{city}" in text or f"到{city}" in text:
            return city
    for city in ["北京", "上海", "广州"]:
        if city in text:
            return city
    return "北京"


def _extract_days(text: str) -> int:
    cn_digits = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7}
    match = re.search(r"(\d+)\s*天", text)
    if match:
        return int(match.group(1))
    for key, value in cn_digits.items():
        if f"{key}天" in text:
            return value
    return 3


def _extract_must_visit(text: str) -> list[str]:
    known_spots = ["天安门广场", "天安门", "故宫", "国家博物馆", "天坛", "颐和园", "圆明园", "什刹海", "南锣鼓巷"]
    return [spot for spot in known_spots if spot in text]


def build_daily_plan_skeleton(*, spots: list[dict[str, Any]], days: int, weather_constraints: dict[str, Any]) -> dict[str, Any]:
    if days <= 0:
        days = 3
    used: set[str] = set()
    area_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for spot in spots:
        area_groups[str(spot.get("area", "其他区域"))].append(spot)
    indoor_preferred_days = set(str(day) for day in weather_constraints.get("indoor_preferred_days", []))
    rainy_days = set(str(day) for day in weather_constraints.get("rainy_days", []))
    daily_plan: dict[str, Any] = {}
    sorted_areas = sorted(area_groups.items(), key=lambda item: len(item[1]), reverse=True)
    area_index = 0
    for day in range(1, days + 1):
        day_key = f"day{day}"
        prefer_indoor = day_key in indoor_preferred_days or day_key in rainy_days
        selected: list[dict[str, Any]] = []
        if prefer_indoor:
            selected = [
                spot
                for spot in spots
                if spot.get("name") not in used and spot.get("indoor_or_outdoor") in {"indoor", "mixed"}
            ][:2]
        if not selected and sorted_areas:
            attempts = 0
            while attempts < len(sorted_areas):
                area, group = sorted_areas[area_index % len(sorted_areas)]
                area_index += 1
                attempts += 1
                selected = [spot for spot in group if spot.get("name") not in used][:3]
                if selected:
                    break
        if not selected:
            selected = [spot for spot in spots if spot.get("name") not in used][:2]
        for spot in selected:
            used.add(str(spot.get("name")))
        daily_plan[day_key] = _format_day_plan(day_key, selected, prefer_indoor)
    return daily_plan


def _format_day_plan(day_key: str, selected: list[dict[str, Any]], prefer_indoor: bool) -> dict[str, Any]:
    spots = [str(spot.get("name", "")) for spot in selected if spot.get("name")]
    areas = [str(spot.get("area", "")) for spot in selected if spot.get("area")]
    area = areas[0] if areas else "待定区域"
    notes: list[str] = []
    reservation_required: list[str] = []
    for spot in selected:
        if spot.get("reservation_required"):
            reservation_required.append(str(spot.get("name")))
            notes.append(f"{spot.get('name')}需要提前预约")
    if prefer_indoor:
        notes.append("当天受天气影响，优先安排室内或室内外结合景点")
    return {
        "theme": "雨天室内安排" if prefer_indoor else f"{area}集中游玩",
        "spots": spots,
        "area": area,
        "reason": "根据预算、天气和区域集中原则安排",
        "estimated_visit_time": estimate_visit_time(selected),
        "estimated_ticket_cost": estimate_ticket_cost(selected),
        "reservation_required": reservation_required,
        "notes": notes,
    }


def estimate_ticket_cost(spots: list[dict[str, Any]]) -> str:
    values = [f"{spot.get('name')}:{spot.get('ticket')}" for spot in spots if spot.get("ticket")]
    return "；".join(values) if values else ""


def estimate_visit_time(spots: list[dict[str, Any]]) -> str:
    total_low = 0
    total_high = 0
    for spot in spots:
        duration = str(spot.get("duration", ""))
        match = re.search(r"(\d+)\s*-\s*(\d+)\s*小时", duration)
        if match:
            total_low += int(match.group(1))
            total_high += int(match.group(2))
        else:
            match = re.search(r"(\d+)\s*小时", duration)
            if match:
                value = int(match.group(1))
                total_low += value
                total_high += value
    if total_low and total_high:
        return f"{total_low}-{total_high}小时"
    return ""


def estimate_visit_time_parts(spots: list[dict[str, Any]]) -> str:
    values = [str(spot.get("duration")) for spot in spots if spot.get("duration")]
    return " + ".join(values) if values else ""


def estimate_ticket_total(daily_plan_skeleton: dict[str, Any]) -> str:
    costs = []
    for day in daily_plan_skeleton.values():
        if isinstance(day, dict) and day.get("estimated_ticket_cost"):
            costs.append(str(day["estimated_ticket_cost"]))
    return "；".join(costs) if costs else ""


def build_summary(travel_task: dict[str, Any], daily_plan_skeleton: dict[str, Any]) -> str:
    city = travel_task.get("destination_city", "目的地")
    days = travel_task.get("days", len(daily_plan_skeleton))
    budget_level = travel_task.get("budget_level", "normal")
    budget_text = "低预算" if budget_level == "low" else "普通预算"
    return f"已根据{budget_text}，为{city}{days}天行程生成结构化景点计划"


def build_summary(travel_task: dict[str, Any], daily_plan_skeleton: dict[str, Any]) -> str:
    city = travel_task.get("destination_city", "目的地")
    days = travel_task.get("days", len(daily_plan_skeleton))
    budget_level = travel_task.get("budget_level", "normal")
    general = _constraint_section(travel_task, "general")
    travel_style = str(general.get("travel_style") or "")
    if budget_level in {"high", "luxury"} or travel_style == "comfort":
        budget_text = "舒适优先"
    elif budget_level == "low":
        budget_text = "低预算"
    else:
        budget_text = "普通预算"
    return f"已根据{budget_text}，为{city}{days}天行程生成结构化景点规划"


def _register_to_registry(host: str, port: int) -> None:
    try:
        registry_url = f"http://{REGISTRY_HOST}:{REGISTRY_PORT}/register"
        payload = AGENTS.get(AGENT_NAME, {}).copy()
        payload["agent_name"] = AGENT_NAME
        payload["host"] = host
        payload["port"] = port
        payload["execute_path"] = "/execute_task"
        req = request.Request(
            registry_url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with request.urlopen(req, timeout=3.0) as response:
            if response.status == 200:
                print(f"{AGENT_NAME} successfully registered to {registry_url}", flush=True)
    except Exception as exc:
        print(f"{AGENT_NAME} failed to register: {exc}", flush=True)


def main() -> None:
    config = AGENTS[AGENT_NAME]
    parser = argparse.ArgumentParser(description="Run Attraction Agent.")
    parser.add_argument("--host", default=config["host"])
    parser.add_argument("--port", type=int, default=int(config["port"]))
    args = parser.parse_args()

    agent = AttractionAgent(host=args.host, port=args.port)
    agent.run()


if __name__ == "__main__":
    main()
