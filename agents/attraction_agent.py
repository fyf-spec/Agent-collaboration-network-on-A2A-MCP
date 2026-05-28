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


from agents.base_agent import BaseAgent
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
    RESULT_ERROR,
    RESULT_SUCCESS,
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
        inputs = context.get("inputs", {})
        upstream_results = inputs.get("upstream_results", {})
        
        mcp_result = call_attraction_mcp(task_id, travel_task)
        spots = mcp_result.get("spots", [])
        days = int(travel_task.get("days") or mcp_result.get("days") or 3)

        try:
            llm_json = llm.chat_json(
                _attraction_selection_prompt(travel_task, upstream_results, spots),
                max_tokens=1100,
                temperature=0.0,
                timeout_seconds=18.0,
            )
            daily_plan_skeleton = _normalize_daily_plan(
                llm_json.get("daily_plan") or llm_json.get("daily_plan_skeleton"),
                spots=spots,
                days=days,
            )
            rejected_spots = llm_json.get("rejected_spots") if isinstance(llm_json.get("rejected_spots"), list) else []
            llm_used = True
        except Exception as exc:
            llm_error = str(exc)
            daily_plan_skeleton = build_daily_plan_skeleton(
                spots=spots,
                days=days,
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
            "structured_result": structured_result,
            "quality": {
                "llm_used": llm_used,
                "llm_error": llm_error,
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
        result_payload = build_result_payload(
            source=AGENT_NAME,
            target=COORDINATOR_NAME,
            task_id=task_id,
            status=RESULT_ERROR,
            result=None,
            error=str(exc),
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
        "budget_level": travel_task.get("budget_level", "normal"),
        "must_visit": travel_task.get("must_visit", []),
        "preferences": travel_task.get("preferences", []),
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
    travel_task: dict[str, Any],
    upstream_results: dict[str, Any],
    spots: list[dict[str, Any]],
) -> str:
    payload = {
        "travel_task": travel_task,
        "upstream_results": upstream_results,
        "attraction_candidates": spots[:14],
        "output_schema": {
            "daily_plan": {
                "day1": {
                    "theme": "不超过20字",
                    "spots": ["景点名"],
                    "area": "区域名",
                    "reason": "不超过35字",
                    "estimated_visit_time": "如 4-6小时",
                    "estimated_ticket_cost": "如 40-60元",
                    "reservation_required": ["景点名"],
                    "notes": ["短提示"],
                }
            },
            "rejected_spots": [{"spot": "景点名", "reason": "短原因"}],
        },
    }
    return "\n".join(
        [
            "你是 Attraction Agent，只做景点筛选和每日分配。",
            "请仔细参考上下文中 upstream_results 内的前置依赖智能体给出的结果，并据此作出合理调整。",
            "根据 travel_task 和 attraction_candidates 输出严格 JSON。",
            "必须满足 must_visit；优先低预算、同区域集中游玩、公共交通方便；雨天优先室内或 mixed 景点。",
            "不要 Markdown，不要解释，不要生成最终旅行攻略。",
            json.dumps(payload, ensure_ascii=False, default=str),
        ]
    )


def _normalize_daily_plan(value: Any, *, spots: list[dict[str, Any]], days: int) -> dict[str, Any]:
    if not isinstance(value, dict):
        return build_daily_plan_skeleton(spots=spots, days=days)
    daily_plan: dict[str, Any] = {}
    known_names = {str(spot.get("name")): spot for spot in spots if spot.get("name")}
    fallback = build_daily_plan_skeleton(spots=spots, days=days)
    for i in range(1, max(1, days) + 1):
        key = f"day{i}"
        raw = value.get(key)
        if not isinstance(raw, dict):
            daily_plan[key] = fallback.get(key, {})
            continue
        spot_names = [str(x) for x in raw.get("spots", []) if str(x) in known_names]
        if not spot_names:
            spot_names = fallback.get(key, {}).get("spots", []) if isinstance(fallback.get(key), dict) else []
        selected = [known_names[name] for name in spot_names if name in known_names]
        reservation_required = raw.get("reservation_required")
        if not isinstance(reservation_required, list):
            reservation_required = [spot.get("name") for spot in selected if spot.get("reservation_required")]
        daily_plan[key] = {
            "theme": str(raw.get("theme") or fallback.get(key, {}).get("theme") or "景点集中游玩"),
            "spots": spot_names,
            "area": str(raw.get("area") or (selected[0].get("area") if selected else fallback.get(key, {}).get("area", "待定区域"))),
            "reason": str(raw.get("reason") or "根据预算、天气和区域集中原则安排"),
            "estimated_visit_time": str(raw.get("estimated_visit_time") or estimate_visit_time(selected)),
            "estimated_ticket_cost": str(raw.get("estimated_ticket_cost") or estimate_ticket_cost(selected)),
            "reservation_required": [str(x) for x in reservation_required],
            "notes": [str(x) for x in raw.get("notes", [])] if isinstance(raw.get("notes"), list) else [],
        }
    return daily_plan





def _extract_travel_task(instruction: str, context: dict[str, Any]) -> dict[str, Any]:
    travel_task = dict(context.get("travel_task") or {})
    inputs = context.get("inputs") or {}
    if not travel_task and isinstance(inputs, dict):
        travel_task = dict(inputs.get("travel_task") or {})
    if "origin_city" not in travel_task:
        origin = _extract_origin_city(instruction)
        if origin:
            travel_task["origin_city"] = origin
    if "destination_city" not in travel_task:
        travel_task["destination_city"] = _extract_destination_city(instruction)
    if "days" not in travel_task:
        travel_task["days"] = _extract_days(instruction)
    if "budget_level" not in travel_task:
        travel_task["budget_level"] = "low" if "低预算" in instruction or "省钱" in instruction else "normal"
    if "transport_preference" not in travel_task and any(word in instruction for word in ["公共交通", "地铁", "公交"]):
        travel_task["transport_preference"] = "public_transport"
    if "must_visit" not in travel_task:
        travel_task["must_visit"] = _extract_must_visit(instruction)
    if "preferences" not in travel_task:
        preferences = ["经典景点"]
        if travel_task.get("budget_level") == "low":
            preferences.append("低预算")
        if travel_task.get("transport_preference") == "public_transport":
            preferences.append("公共交通方便")
        travel_task["preferences"] = preferences
    return travel_task





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


def build_daily_plan_skeleton(*, spots: list[dict[str, Any]], days: int) -> dict[str, Any]:
    if days <= 0:
        days = 3
    daily_plan: dict[str, Any] = {}
    known_names = {str(spot.get("name")): spot for spot in spots if spot.get("name")}
    spots_per_day = max(1, len(spots) // days)

    for i in range(1, days + 1):
        start_idx = (i - 1) * spots_per_day
        end_idx = start_idx + spots_per_day if i < days else len(spots)
        day_spots = spots[start_idx:end_idx]
        
        daily_plan[f"day{i}"] = {
            "theme": "景点集中游玩",
            "spots": [str(s.get("name")) for s in day_spots if s.get("name")],
            "area": str(day_spots[0].get("area")) if day_spots else "待定区域",
            "reason": "根据景点顺序排列",
            "estimated_visit_time": estimate_visit_time(day_spots),
            "estimated_ticket_cost": estimate_ticket_cost(day_spots),
            "reservation_required": [str(s.get("name")) for s in day_spots if s.get("reservation_required")],
            "notes": ["规则回退方案，未参考天气。"],
        }
    return daily_plan




def estimate_ticket_cost(spots: list[dict[str, Any]]) -> str:
    values = [f"{spot.get('name')}:{spot.get('ticket')}" for spot in spots if spot.get("ticket")]
    return "；".join(values) if values else "待确认"


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
    return "待确认"


def estimate_ticket_total(daily_plan_skeleton: dict[str, Any]) -> str:
    costs = []
    for day in daily_plan_skeleton.values():
        if isinstance(day, dict) and day.get("estimated_ticket_cost"):
            costs.append(str(day["estimated_ticket_cost"]))
    return "；".join(costs) if costs else "待确认"


def build_summary(travel_task: dict[str, Any], daily_plan_skeleton: dict[str, Any]) -> str:
    city = travel_task.get("destination_city", "目的地")
    days = travel_task.get("days", len(daily_plan_skeleton))
    budget_level = travel_task.get("budget_level", "normal")
    budget_text = "低预算" if budget_level == "low" else "普通预算"
    return f"已根据{budget_text}，为{city}{days}天行程生成结构化景点计划"


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
