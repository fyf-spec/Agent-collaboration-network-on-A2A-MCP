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


def _build_intercity_table() -> dict[tuple[str, str], dict[str, object]]:
    """构建主要城市之间的城际交通数据表。key=(出发,到达)按双向存储。"""
    import itertools
    # 城市间的大致高铁距离（km）→ 用于推算时长和票价
    distances: dict[tuple[str, str], int] = {
        ("北京","上海"):1318, ("北京","广州"):2298, ("北京","深圳"):2400,
        ("北京","杭州"):1470, ("北京","南京"):1020, ("北京","成都"):1870,
        ("北京","重庆"):1860, ("北京","武汉"):1230, ("北京","西安"):1130,
        ("北京","苏州"):1220, ("北京","天津"):120,
        ("上海","广州"):1780, ("上海","深圳"):1750, ("上海","杭州"):170,
        ("上海","南京"):300, ("上海","成都"):2060, ("上海","重庆"):1900,
        ("上海","武汉"):830, ("上海","西安"):1430, ("上海","苏州"):84,
        ("上海","天津"):1230,
        ("广州","深圳"):130, ("广州","杭州"):1450, ("广州","南京"):1560,
        ("广州","成都"):1800, ("广州","重庆"):1650, ("广州","武汉"):1060,
        ("广州","西安"):1750, ("广州","苏州"):1600, ("广州","天津"):2200,
        ("杭州","南京"):260, ("杭州","成都"):1900, ("杭州","武汉"):750,
        ("杭州","西安"):1400,
        ("南京","成都"):1660, ("南京","武汉"):520, ("南京","西安"):1100,
        ("成都","重庆"):310, ("成都","武汉"):1140, ("成都","西安"):740,
        ("深圳","杭州"):1400, ("深圳","成都"):1900, ("武汉","西安"):740,
    }
    lookup: dict[tuple[str, str], int] = {}
    for (a,b), d in distances.items():
        lookup[(a,b)] = d
        lookup[(b,a)] = d

    table: dict[tuple[str, str], dict[str, object]] = {}
    major_cities = ["北京","上海","广州","深圳","杭州","南京","成都","重庆","武汉","西安","苏州","天津"]
    for o, d in itertools.permutations(major_cities, 2):
        km = lookup.get((o,d))
        if km is None:
            continue
        h, m = divmod(int(km / 280 * 60), 60)
        dur = f"约{h}小时{m}分钟" if m else f"约{h}小时"
        g_price = [max(100, int(km * 0.42)), max(150, int(km * 0.48))]
        d_price = [max(80, int(km * 0.28)), max(120, int(km * 0.35))]
        table[(o,d)] = {
            "recommended": {"mode":"高铁二等座","duration":dur,"cost_yuan_range":g_price,"reason":"高铁时刻稳定、舒适度较高"},
            "alternatives": [
                {"mode":"动车/普速","duration":dur.replace("小时","-") + "小时" if "小时" in dur else dur,"cost_yuan_range":d_price,"reason":"更省钱但耗时略长"},
                {"mode":"飞机经济舱","duration":f"约{max(1,km//800)}-{max(2,km//600)}小时飞行，不含机场通勤","cost_yuan_range":[max(300,int(km*0.55)),max(500,int(km*0.9))],"reason":"可能更快，但机场通勤增加总时长"},
            ],
        }
    return table


def get_intercity_transport(
    origin_city: str,
    destination_city: str,
    budget_level: str = "normal",
    transport_preference: str = "public_transport",
) -> dict[str, object]:
    origin = (origin_city or "上海").strip()
    destination = (destination_city or "北京").strip()
    options_by_od: dict[tuple[str, str], dict[str, object]] = _build_intercity_table()
    selected = options_by_od.get((origin, destination))
    fallback_used = selected is None
    if selected is None:
        # 用 AMap 驾车 API 获取真实跨城距离
        real_distance_km: float | None = None
        try:
            from mcp_servers.realtime.amap_client import AMapClient
            amap = AMapClient()
            drive = amap.get_route(origin=origin, destination=destination, mode="driving")
            paths = (drive.get("route") or {}).get("paths") if isinstance(drive, dict) else None
            if isinstance(paths, list) and paths:
                dist_m = _safe_int(paths[0].get("distance"), default=0)
                if dist_m > 0:
                    real_distance_km = dist_m / 1000.0
        except Exception:
            real_distance_km = None

        if real_distance_km and real_distance_km > 10:
            km = real_distance_km
            h = int(km / 280)
            m = int((km % 280) / 280 * 60)
            dur = f"约{h}小时{m}分钟" if m else f"约{h}小时"
            g_price = [max(100, int(km * 0.42)), max(150, int(km * 0.48))]
            fly_dur = f"约{max(1,int(km/750))}-{max(2,int(km/600))}小时飞行"
            fly_price = [max(300, int(km * 0.55)), max(500, int(km * 0.9))]
            selected = {
                "recommended": {"mode": "高铁二等座", "duration": dur, "cost_yuan_range": g_price,
                    "reason": f"基于驾车距离{int(km)}km推算，实际以铁路时刻表为准"},
                "alternatives": [
                    {"mode": "动车/普速", "duration": f"约{h+1}-{h+3}小时", "cost_yuan_range": [max(80,int(km*0.28)), max(120,int(km*0.35))],
                        "reason": "更省钱但耗时更长"},
                    {"mode": "飞机经济舱", "duration": f"{fly_dur}，不含机场通勤", "cost_yuan_range": fly_price,
                        "reason": "飞行时间短但机场通勤增加总时长"},
                ],
            }
        else:
            selected = {
                "recommended": {
                    "mode": "高铁/动车二等座",
                    "duration": "按实际城市距离确认",
                    "cost_yuan_range": [100, 800],
                    "reason": "出发前请查询12306或航司确认实时班次和票价",
                },
                "alternatives": [],
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
