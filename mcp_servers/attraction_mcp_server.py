from __future__ import annotations

import argparse
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


from common.config import A2A_REALTIME_MCP_ENABLED, MCP_REALTIME_FALLBACK_TO_MOCK, MCP_SERVERS
from mcp_servers.base_mcp_server import MCPTool, run_mcp_server
from mcp_servers.mock_data import search_attractions as search_mock_attractions
from mcp_servers.realtime.amap_client import AMapClient
from mcp_servers.realtime.normalizers import attach_mock_source, normalize_attractions


def search_attractions(
    city: str = "北京",
    days: int = 3,
    budget_level: str = "normal",
    must_visit: list[str] | None = None,
    preferences: list[str] | None = None,
    requested_fields: list[str] | None = None,
    **kwargs: object,
) -> dict[str, object]:
    if not A2A_REALTIME_MCP_ENABLED:
        return attach_mock_source(
            search_mock_attractions(
                city=city,
                days=days,
                budget_level=budget_level,
                must_visit=must_visit,
                preferences=preferences,
                requested_fields=requested_fields,
                **kwargs,
            ),
            fallback_used=False,
        )
    try:
        data = AMapClient().search_attractions(city=city, preferences=preferences, limit=30)
        return normalize_attractions(
            data,
            city=city,
            days=days,
            budget_level=budget_level,
            must_visit=must_visit,
            preferences=preferences,
            limit=30,
        )
    except Exception as exc:
        if not MCP_REALTIME_FALLBACK_TO_MOCK:
            raise
        result = search_mock_attractions(
            city=city,
            days=days,
            budget_level=budget_level,
            must_visit=must_visit,
            preferences=preferences,
            requested_fields=requested_fields,
            **kwargs,
        )
        return attach_mock_source(result, fallback_used=True, fallback_reason=type(exc).__name__)


def main() -> None:
    config = MCP_SERVERS["attraction"]
    parser = argparse.ArgumentParser(description="Run Attraction MCP Server.")
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
                handler=search_attractions,
                description="Return realtime AMap attraction data with mock fallback.",
            )
        },
    )


if __name__ == "__main__":
    main()
