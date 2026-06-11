"""Helpers for selecting direct MCP versus MCP Gateway endpoints."""

from __future__ import annotations

from typing import Any

from common.config import MCP_GATEWAY, MCP_SERVERS


def mcp_gateway_enabled() -> bool:
    return bool(MCP_GATEWAY.get("enabled", True))


def mcp_endpoint_for_server(server_key: str) -> tuple[str, str, bool]:
    """Return (url, network_target, gateway_used) for an MCP server key."""
    server = MCP_SERVERS[server_key]
    if mcp_gateway_enabled():
        return _gateway_url(), str(MCP_GATEWAY["name"]), True
    return _server_url(server), str(server["name"]), False


def _gateway_url() -> str:
    return f"http://{MCP_GATEWAY['host']}:{MCP_GATEWAY['port']}{MCP_GATEWAY.get('path', '/')}"


def _server_url(server: dict[str, Any]) -> str:
    return f"http://{server['host']}:{server['port']}{server.get('path', '/')}"
