from __future__ import annotations

import argparse
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


from common.config import MCP_SERVERS
from mcp_servers.base_mcp_server import MCPTool, run_mcp_server
from mcp_servers.mock_data import get_transport


def main() -> None:
    config = MCP_SERVERS["traffic"]
    parser = argparse.ArgumentParser(description="Run Traffic MCP Server.")
    parser.add_argument("--host", default=config["host"])
    parser.add_argument("--port", type=int, default=config["port"])
    args = parser.parse_args()

    run_mcp_server(
        name=config["name"],
        host=args.host,
        port=args.port,
        tools={
            config["method"]: MCPTool(
                name=config["method"],
                handler=get_transport,
                description="Return mock transport data for a city.",
            )
        },
    )


if __name__ == "__main__":
    main()
