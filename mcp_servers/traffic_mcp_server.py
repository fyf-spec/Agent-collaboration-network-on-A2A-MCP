from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import sys
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


from common.config import (
    A2A_REALTIME_MCP_ENABLED,
    MCP_REALTIME_FALLBACK_TO_MOCK,
    MCP_SERVERS,
    MCP_TRAFFIC_MAX_SEGMENTS,
    MCP_TRAFFIC_MAX_WORKERS,
    MCP_TRAFFIC_REALTIME_ENABLED,
    MCP_TRAFFIC_ROUTE_TIMEOUT_SECONDS,
)
from mcp_servers.base_mcp_server import MCPTool, run_mcp_server
from mcp_servers.mock_data import (
    get_route as get_mock_route,
    get_routes as get_mock_routes,
    get_transport as get_mock_transport,
    get_traffic as get_mock_traffic,
)
from mcp_servers.realtime.amap_client import AMapClient
from mcp_servers.realtime.normalizers import attach_mock_source, normalize_route, realtime_source


def get_route(
    city: str = "北京",
    origin: str = "",
    destination: str = "",
    preference: str = "public_transport",
    **kwargs: object,
) -> dict[str, object]:
    origin_location = str(kwargs.get("origin_location") or "").strip()
    destination_location = str(kwargs.get("destination_location") or "").strip()
    if not A2A_REALTIME_MCP_ENABLED or not MCP_TRAFFIC_REALTIME_ENABLED:
        return attach_mock_source(
            get_mock_route(city=city, origin=origin, destination=destination, preference=preference, **kwargs),
            fallback_used=False,
        )
    try:
        mode = _route_mode(preference)
        data = AMapClient().get_route(
            origin=origin,
            destination=destination,
            city=city,
            mode=mode,
            origin_location=origin_location,
            destination_location=destination_location,
            timeout=MCP_TRAFFIC_ROUTE_TIMEOUT_SECONDS,
        )
        return normalize_route(data, city=city, origin=origin, destination=destination, preference=preference, mode=mode)
    except Exception as exc:
        if not MCP_REALTIME_FALLBACK_TO_MOCK:
            raise
        result = get_mock_route(city=city, origin=origin, destination=destination, preference=preference, **kwargs)
        return attach_mock_source(result, fallback_used=True, fallback_reason=type(exc).__name__)


def get_routes(
    city: str = "北京",
    segments: list[dict[str, object]] | None = None,
    preference: str = "public_transport",
    **kwargs: object,
) -> dict[str, object]:
    if not A2A_REALTIME_MCP_ENABLED or not MCP_TRAFFIC_REALTIME_ENABLED:
        return attach_mock_source(
            get_mock_routes(city=city, segments=segments, preference=preference, **kwargs),
            fallback_used=False,
        )
    clean_segments = [segment for segment in (segments or []) if isinstance(segment, dict)][: max(1, MCP_TRAFFIC_MAX_SEGMENTS)]
    if not clean_segments:
        return attach_mock_source(get_mock_routes(city=city, segments=segments, preference=preference, **kwargs), fallback_used=True, fallback_reason="empty_segments")

    routes: list[dict[str, Any]] = [{} for _ in clean_segments]
    with ThreadPoolExecutor(max_workers=max(1, MCP_TRAFFIC_MAX_WORKERS)) as executor:
        futures = {
            executor.submit(_route_for_segment, city, segment, preference): index
            for index, segment in enumerate(clean_segments)
        }
        for future in as_completed(futures):
            routes[futures[future]] = future.result()

    fallback_count = sum(1 for route in routes if _route_fallback_used(route))
    if fallback_count >= len(routes):
        result = get_mock_routes(city=city, segments=clean_segments, preference=preference, **kwargs)
        return attach_mock_source(result, fallback_used=True, fallback_reason="all route segments fallback")

    if fallback_count:
        return {
            "city": city,
            "preference": preference,
            "routes": routes,
            "data_source": {
                "provider": "amap+mock",
                "realtime": True,
                "fallback_used": True,
                "fallback_reason": "partial route fallback",
            },
        }
    return {
        "city": city,
        "preference": preference,
        "routes": routes,
        "data_source": realtime_source(missing_fields=[]),
    }


def _route_for_segment(city: str, segment: dict[str, object], preference: str) -> dict[str, Any]:
    origin = str(segment.get("origin_name") or segment.get("origin") or "")
    destination = str(segment.get("destination_name") or segment.get("destination") or "")
    segment_preference = str(segment.get("mode") or preference)
    try:
        route = get_route(
            city=city,
            origin=origin,
            destination=destination,
            preference=segment_preference,
            origin_location=segment.get("origin_location"),
            destination_location=segment.get("destination_location"),
        )
        route["segment_id"] = segment.get("segment_id")
        return route
    except Exception as exc:
        if not MCP_REALTIME_FALLBACK_TO_MOCK:
            raise
        return _mock_route_for_segment(city, segment, preference, type(exc).__name__)


def _mock_route_for_segment(city: str, segment: dict[str, object], preference: str, reason: str) -> dict[str, Any]:
    origin = str(segment.get("origin_name") or segment.get("origin") or "")
    destination = str(segment.get("destination_name") or segment.get("destination") or "")
    result = get_mock_route(city=city, origin=origin, destination=destination, preference=preference)
    result["segment_id"] = segment.get("segment_id")
    return attach_mock_source(result, fallback_used=True, fallback_reason=reason)


def _route_fallback_used(route: dict[str, Any]) -> bool:
    source = route.get("data_source")
    return isinstance(source, dict) and bool(source.get("fallback_used"))


def get_transport(city: str = "北京", date: str = "明天", **kwargs: object) -> dict[str, object]:
    result = get_mock_transport(city=city, date=date, **kwargs)
    return attach_mock_source(
        result,
        fallback_used=A2A_REALTIME_MCP_ENABLED,
        fallback_reason="transport_status_not_implemented" if A2A_REALTIME_MCP_ENABLED else None,
    )


def get_traffic(city: str = "北京", date: str = "明天", **kwargs: object) -> dict[str, object]:
    result = get_mock_traffic(city=city, date=date, **kwargs)
    return attach_mock_source(
        result,
        fallback_used=A2A_REALTIME_MCP_ENABLED,
        fallback_reason="transport_status_not_implemented" if A2A_REALTIME_MCP_ENABLED else None,
    )


def _route_mode(preference: str) -> str:
    value = (preference or "").strip().lower()
    if value in {"walk", "walking"}:
        return "walking"
    if value in {"drive", "driving", "taxi"}:
        return "driving"
    return "transit"


def get_intercity_transport(
    origin_city: str,
    destination_city: str,
    budget_level: str = "normal",
    transport_preference: str = "public_transport",
) -> dict[str, object]:
    origin = (origin_city or "上海").strip()
    destination = (destination_city or "北京").strip()
    options_by_od: dict[tuple[str, str], dict[str, object]] = {
        ("上海", "北京"): {
            "recommended": {
                "mode": "高铁二等座",
                "duration": "约4.5-6小时",
                "cost_yuan_range": [550, 650],
                "reason": "时间稳定、舒适度较高，适合五天行程",
            },
            "alternatives": [
                {
                    "mode": "普速火车硬卧/硬座",
                    "duration": "约12-15小时",
                    "cost_yuan_range": [150, 350],
                    "reason": "更省钱但耗时较长",
                },
                {
                    "mode": "高铁二等座",
                    "duration": "约4.5-6小时",
                    "cost_yuan_range": [550, 650],
                    "reason": "时间稳定、舒适度较高，适合五天行程",
                },
                {
                    "mode": "飞机经济舱",
                    "duration": "约2-2.5小时飞行时间，不含机场通勤",
                    "cost_yuan_range": [500, 1000],
                    "reason": "可能更快，但价格和机场通勤波动较大",
                },
            ],
        },
        ("上海", "杭州"): {
            "recommended": {
                "mode": "高铁二等座",
                "duration": "约1小时",
                "cost_yuan_range": [60, 100],
                "reason": "班次密集、耗时短，适合低成本周边出行",
            },
            "alternatives": [
                {
                    "mode": "动车/城际二等座",
                    "duration": "约1-1.5小时",
                    "cost_yuan_range": [50, 90],
                    "reason": "价格接近高铁，可按发车时间选择",
                },
                {
                    "mode": "长途汽车",
                    "duration": "约2.5-3小时",
                    "cost_yuan_range": [70, 110],
                    "reason": "可作为车票紧张时的备选",
                },
            ],
        },
        ("上海", "南京"): {
            "recommended": {
                "mode": "高铁二等座",
                "duration": "约1-1.5小时",
                "cost_yuan_range": [140, 180],
                "reason": "沪宁高铁班次密集，时间稳定",
            },
            "alternatives": [
                {
                    "mode": "动车二等座",
                    "duration": "约1.5-2.5小时",
                    "cost_yuan_range": [95, 150],
                    "reason": "更省钱但耗时略长",
                },
                {
                    "mode": "长途汽车",
                    "duration": "约4小时",
                    "cost_yuan_range": [100, 140],
                    "reason": "适合作为铁路票紧张时备选",
                },
            ],
        },
    }
    selected = options_by_od.get((origin, destination))
    fallback_used = selected is None
    if selected is None:
        selected = {
            "recommended": {
                "mode": "高铁/动车二等座",
                "duration": "约1-6小时，按实际城市距离确认",
                "cost_yuan_range": [100, 600],
                "reason": "未知城市组合使用通用铁路估算，出行前需确认实时班次",
            },
            "alternatives": [
                {
                    "mode": "普速火车或长途汽车",
                    "duration": "按实际线路确认",
                    "cost_yuan_range": [50, 400],
                    "reason": "更省钱但耗时通常更长",
                }
            ],
        }
    recommended = dict(selected["recommended"])
    alternatives = list(selected["alternatives"])
    if budget_level in {"high", "luxury"} or transport_preference == "taxi":
        recommended = _comfort_intercity_option(origin, destination, recommended)
        alternatives = [recommended] + [item for item in alternatives if item.get("mode") != recommended.get("mode")]
    return {
        "origin_city": origin,
        "destination_city": destination,
        "fallback_used": fallback_used,
        "recommended_option": recommended,
        "alternatives": alternatives,
        "preference": transport_preference,
        "cost_note": "价格为示例估算，不代表实时票价",
    }


def _comfort_intercity_option(origin: str, destination: str, default: dict[str, object]) -> dict[str, object]:
    if (origin, destination) == ("上海", "北京"):
        return {
            "mode": "高铁一等座/商务座",
            "duration": "约4.5-6小时",
            "cost_yuan_range": [950, 2200],
            "reason": "无预算上限且舒适优先，座位空间和乘坐体验更好",
        }
    option = dict(default)
    mode = str(option.get("mode") or "")
    if "二等座" in mode:
        option["mode"] = mode.replace("二等座", "一等座")
        costs = option.get("cost_yuan_range")
        if isinstance(costs, list) and len(costs) >= 2:
            option["cost_yuan_range"] = [int(float(costs[0]) * 1.6), int(float(costs[1]) * 2.2)]
        option["reason"] = "舒适优先，选择更宽敞的一等座方案"
    return option


def main() -> None:
    config = MCP_SERVERS["traffic"]

    parser = argparse.ArgumentParser(description="Run Traffic MCP Server.")
    parser.add_argument("--host", default=config["host"])
    parser.add_argument("--port", type=int, default=config["port"])
    parser.add_argument("--delay", type=float, default=0.0)
    args = parser.parse_args()

    run_mcp_server(
        name=config["name"],
        host=args.host,
        port=args.port,
        delay=args.delay,
        tools={
            "get_route": MCPTool(
                name="get_route",
                handler=get_route,
                description="Return candidate routes between two attractions.",
            ),
            "get_routes": MCPTool(
                name="get_routes",
                handler=get_routes,
                description="Return candidate routes for multiple attraction segments.",
            ),
            "get_transport": MCPTool(
                name="get_transport",
                handler=get_transport,
                description="Return city-level mock transport data.",
            ),
            "get_traffic": MCPTool(
                name="get_traffic",
                handler=get_traffic,
                description="Backward-compatible alias for city-level transport data.",
            ),
            "get_intercity_transport": MCPTool(
                name="get_intercity_transport",
                handler=get_intercity_transport,
                description="Return intercity transport options between origin and destination cities.",
            ),
        },
    )


if __name__ == "__main__":
    main()
