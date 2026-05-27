from __future__ import annotations

import argparse
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


from common.config import MCP_SERVERS
from mcp_servers.base_mcp_server import MCPTool, run_mcp_server
from mcp_servers.mock_data import get_route, get_routes, get_transport, get_traffic


def get_intercity_transport(
    origin_city: str,
    destination_city: str,
    budget_level: str = "normal",
    transport_preference: str = "public_transport",
) -> dict[str, object]:
    recommended = {
        "mode": "高铁二等座",
        "duration": "约4.5-6小时",
        "cost_yuan_range": [550, 650],
        "reason": "时间稳定、舒适度较高，适合五天行程",
    }
    return {
        "origin_city": origin_city,
        "destination_city": destination_city,
        "recommended_option": recommended,
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
        "preference": transport_preference,
        "cost_note": "价格为示例估算，不代表实时票价",
    }


def main() -> None:
    config = MCP_SERVERS["traffic"]

    parser = argparse.ArgumentParser(description="Run Traffic MCP Server.")
    parser.add_argument("--host", default=config["host"])
    parser.add_argument("--port", type=int, default=config["port"])
    parser.add_argument("--delay", type=float, default=0.0, help="Artificial delay in seconds")
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
