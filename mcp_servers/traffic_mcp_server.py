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
        },
    )


if __name__ == "__main__":
    main()
