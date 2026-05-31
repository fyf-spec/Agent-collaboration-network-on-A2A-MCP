from __future__ import annotations

import argparse
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from common.config import A2A_REALTIME_MCP_ENABLED, MCP_REALTIME_FALLBACK_TO_MOCK, MCP_SERVERS
from mcp_servers.base_mcp_server import MCPTool, run_mcp_server
from mcp_servers.mock_data import search_hotels as search_mock_hotels
from mcp_servers.realtime.amap_client import AMapClient
from mcp_servers.realtime.normalizers import attach_mock_source, normalize_hotels


def search_hotels(
    city: str = "北京",
    preferred_areas: list[str] | None = None,
    target_area: str | None = None,
    budget_level: str = "normal",
    days: int = 3,
    daily_plan: dict[str, object] | None = None,
    preferences: list[str] | None = None,
    area_selection: dict[str, object] | None = None,
    requested_fields: list[str] | None = None,
    **kwargs: object,
) -> dict[str, object]:
    if not A2A_REALTIME_MCP_ENABLED:
        return attach_mock_source(
            search_mock_hotels(
                city=city,
                preferred_areas=preferred_areas,
                target_area=target_area,
                budget_level=budget_level,
                days=days,
                daily_plan=daily_plan,
                preferences=preferences,
                area_selection=area_selection,
                requested_fields=requested_fields,
                **kwargs,
            ),
            fallback_used=False,
        )
    try:
        data = AMapClient().search_hotels(city=city, target_area=target_area, limit=20)
        return normalize_hotels(
            data,
            city=city,
            days=days,
            budget_level=budget_level,
            target_area=target_area,
            preferred_areas=preferred_areas,
            area_selection=area_selection,
            limit=20,
        )
    except Exception as exc:
        if not MCP_REALTIME_FALLBACK_TO_MOCK:
            raise
        result = search_mock_hotels(
            city=city,
            preferred_areas=preferred_areas,
            target_area=target_area,
            budget_level=budget_level,
            days=days,
            daily_plan=daily_plan,
            preferences=preferences,
            area_selection=area_selection,
            requested_fields=requested_fields,
            **kwargs,
        )
        return attach_mock_source(result, fallback_used=True, fallback_reason=type(exc).__name__)


def main() -> None:
    config = MCP_SERVERS["hotel"]
    parser = argparse.ArgumentParser(description="Run Hotel MCP Server.")
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
            config["method"]: MCPTool(
                name=config["method"],
                handler=search_hotels,
                description="Return realtime AMap hotel candidates with mock fallback.",
            )
        },
    )


if __name__ == "__main__":
    main()
