from __future__ import annotations

import json
import os
from pathlib import Path
import sys
from typing import Any

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def main() -> None:
    load_dotenv(PROJECT_ROOT / ".env")
    os.environ["A2A_REALTIME_MCP_ENABLED"] = "true"

    from mcp_servers.attraction_mcp_server import search_attractions
    from mcp_servers.hotel_mcp_server import search_hotels
    from mcp_servers.traffic_mcp_server import get_routes
    from mcp_servers.weather_mcp_server import get_weather

    has_key = bool(os.getenv("AMAP_WEB_KEY", "").strip())
    print("Realtime MCP smoke test")
    print(f"AMAP_WEB_KEY configured: {has_key}")
    if not has_key:
        raise RuntimeError("AMAP_WEB_KEY is required for realtime MCP smoke test")

    traffic_segments = [
        {
            "origin": "Hangzhou lakeside hotel",
            "origin_name": "Hangzhou lakeside hotel",
            "origin_location": "120.163093,30.258394",
            "destination": "West Lake",
            "destination_name": "West Lake",
            "destination_location": "120.143222,30.236064",
        },
        {
            "origin": "West Lake",
            "origin_name": "West Lake",
            "origin_location": "120.143222,30.236064",
            "destination": "Lingyin Temple",
            "destination_name": "Lingyin Temple",
            "destination_location": "120.100936,30.240826",
        },
    ]
    checks = {
        "weather": get_weather(city="杭州", date="今天", days=3),
        "attraction": search_attractions(city="杭州", days=2, preferences=["西湖"], limit=10),
        "hotel": search_hotels(city="杭州", target_area="西湖", days=2),
        "traffic": get_routes(city="杭州", segments=traffic_segments, preference="walking"),
    }

    for name, result in checks.items():
        source = _source(result)
        print(f"- {name}: provider={source.get('provider')} realtime={source.get('realtime')}")
        if name == "weather":
            forecast_days = result.get("forecast_days") if isinstance(result.get("forecast_days"), list) else []
            print(f"  forecast_days={len(forecast_days)}")
        if name == "attraction":
            spots = result.get("spots", []) if isinstance(result.get("spots"), list) else []
            print(f"  spots={len(spots)}")
        if name == "hotel":
            hotels = result.get("hotels", []) if isinstance(result.get("hotels"), list) else []
            print(f"  hotels={len(hotels)}")
        if name == "traffic":
            routes = result.get("routes", []) if isinstance(result.get("routes"), list) else []
            for index, route in enumerate(routes, start=1):
                route_source = _source(route)
                print(
                    f"  segment {index}: provider={route_source.get('provider')} "
                    f"realtime={route_source.get('realtime')}"
                )

    weather_source = _source(checks["weather"])
    assert weather_source.get("provider") in {"amap", "open-meteo"}, f"weather did not use a realtime weather provider: {json.dumps(weather_source, ensure_ascii=False)}"
    assert weather_source.get("realtime") is True, "weather did not report realtime=true"
    assert checks["weather"].get("forecast_days"), "weather did not return forecast_days"

    for name in ("attraction", "hotel"):
        source = _source(checks[name])
        assert source.get("provider") in {"amap", "amap+local_profile"}, f"{name} did not use AMap: {json.dumps(source, ensure_ascii=False)}"
        assert source.get("realtime") is True, f"{name} did not report realtime=true"

    traffic_source = _source(checks["traffic"])
    assert traffic_source.get("provider") in {"amap", "amap+mock"}, f"traffic did not use realtime route layer: {json.dumps(traffic_source, ensure_ascii=False)}"
    assert traffic_source.get("realtime") is True, "traffic did not report realtime=true"
    print("OK: weather, attraction, hotel and traffic route layer returned realtime-capable data.")
    print("Note: cache is intentionally not tested here; caching will be handled by MCP Gateway later.")


def _source(result: dict[str, Any]) -> dict[str, Any]:
    source = result.get("data_source")
    if isinstance(source, dict):
        return source
    return {}


if __name__ == "__main__":
    main()
