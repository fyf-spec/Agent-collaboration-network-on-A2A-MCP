from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any
from urllib import request


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")


from common.config import MCP_GATEWAY
from common.http_client import HttpJsonClientError, post_json
from scripts.start_all import run_services


def main() -> None:
    gateway_url = f"http://{MCP_GATEWAY['host']}:{MCP_GATEWAY['port']}{MCP_GATEWAY.get('path', '/')}"
    metrics_url = f"http://{MCP_GATEWAY['host']}:{MCP_GATEWAY['port']}/metrics"

    # This demo only needs Weather MCP and MCP Gateway. It avoids Agent/Coordinator/LLM
    # so cache behavior is easy to observe and repeat.
    exclude = [
        "registry_center",
        "traffic_mcp_server",
        "weather_agent",
        "traffic_agent",
        "coordinator",
    ]

    with run_services(exclude=exclude):
        time.sleep(1.0)

        first_payload = {
            "jsonrpc": "2.0",
            "id": "cache-demo-1",
            "method": "get_weather",
            "params": {"city": "北京"},
        }
        second_payload = {
            "jsonrpc": "2.0",
            "id": "cache-demo-2",
            "method": "get_weather",
            "params": {"city": "北京"},
        }

        try:
            print("====== MCP Gateway Cache Demo ======")
            print(f"Gateway URL: {gateway_url}")
            print("Sending two identical get_weather requests with different JSON-RPC ids...\n")

            first = post_json(gateway_url, first_payload, timeout=5.0)
            second = post_json(gateway_url, second_payload, timeout=5.0)
            metrics = _get_json(metrics_url, timeout=5.0)

            print("First response:")
            print(json.dumps(first.data, ensure_ascii=False, indent=2))
            print("\nSecond response:")
            print(json.dumps(second.data, ensure_ascii=False, indent=2))

            metric_body = metrics.get("metrics", {})
            print("\nGateway metrics:")
            print(f"- total_requests: {metric_body.get('total_requests')}")
            print(f"- upstream_calls: {metric_body.get('upstream_calls')}")
            print(f"- cache_hits: {metric_body.get('cache_hits')}")
            print(f"- cache_misses: {metric_body.get('cache_misses')}")
            print(f"- error_count: {metric_body.get('error_count')}")

            weather_stats = metric_body.get("method_stats", {}).get("get_weather", {})
            if weather_stats:
                print("\nMethod stats for get_weather:")
                print(json.dumps(weather_stats, ensure_ascii=False, indent=2))

            print("\nExpected cache effect:")
            print("- total_requests should be 2")
            print("- upstream_calls should be 1")
            print("- cache_hits should be 1")

        except HttpJsonClientError as exc:
            print(f"HTTP Request Error: {exc}")
        except Exception as exc:
            print(f"Unknown Error: {exc}")


def _get_json(url: str, *, timeout: float) -> dict[str, Any]:
    http_request = request.Request(url, method="GET", headers={"Accept": "application/json"})
    with request.urlopen(http_request, timeout=timeout) as response:
        raw_body = response.read().decode("utf-8")
    data = json.loads(raw_body)
    if not isinstance(data, dict):
        raise ValueError("GET response body must be a JSON object")
    return data


if __name__ == "__main__":
    main()
