from __future__ import annotations

import argparse
from datetime import date as date_type, datetime
from pathlib import Path
import sys
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


from common.config import (
    A2A_REALTIME_MCP_ENABLED,
    MCP_GATEWAY_UPSTREAM_TIMEOUT_SECONDS,
    MCP_HTTP_TIMEOUT_SECONDS,
    MCP_REALTIME_FALLBACK_TO_MOCK,
    MCP_REALTIME_TIMEOUT_SECONDS,
    MCP_SERVERS,
    OPEN_METEO_MAX_FORECAST_DAYS,
)
from common.http_client import retry_call
from mcp_servers.base_mcp_server import MCPTool, run_mcp_server
from mcp_servers.mock_data import get_weather as get_mock_weather
from mcp_servers.realtime.amap_client import AMapClient
from mcp_servers.realtime.normalizers import (
    attach_mock_source,
    build_far_future_weather_result,
    normalize_open_meteo_weather,
)
from mcp_servers.realtime.open_meteo_client import OpenMeteoClient


def get_weather(city: str = "北京", date: str = "", days: int = 1, **kwargs: object) -> dict[str, object]:
    # 获取实时天气数据，支持 mock 回退
    if not A2A_REALTIME_MCP_ENABLED:
        return attach_mock_source(get_mock_weather(city=city, date=date, **kwargs), fallback_used=False)
    try:
        day_count = max(1, int(days or 1))
        requested_date = _clean_date_label(date)
        target_date = _resolve_target_date(requested_date)
        start_offset = 0
        if target_date is not None:
            days_until = (target_date - date_type.today()).days
            if days_until >= OPEN_METEO_MAX_FORECAST_DAYS:
                return build_far_future_weather_result(
                    requested_city=city,
                    date=target_date.isoformat(),
                    days=day_count,
                    reason=f"target date is {days_until} days away; Open-Meteo forecast limit is {OPEN_METEO_MAX_FORECAST_DAYS} days",
                )
            if days_until < 0:
                return build_far_future_weather_result(
                    requested_city=city,
                    date=target_date.isoformat(),
                    days=day_count,
                    reason="target date is in the past; realtime forecast is unavailable",
                )
            start_offset = days_until

        # 实时 API 调用必须早于 Agent/Gateway 超时完成，失败后直接降级 Mock。
        realtime_budget = max(
            1.0,
            min(float(MCP_HTTP_TIMEOUT_SECONDS), float(MCP_GATEWAY_UPSTREAM_TIMEOUT_SECONDS)) - 1.0,
        )
        provider_timeout = max(
            1.0,
            min(float(MCP_REALTIME_TIMEOUT_SECONDS), realtime_budget / 3.0),
        )

        def _realtime_weather_call():
            location = AMapClient(timeout=provider_timeout).geocode_city_or_address(city, timeout=provider_timeout)
            longitude, latitude = _parse_amap_location(location)
            request_days = min(OPEN_METEO_MAX_FORECAST_DAYS, start_offset + day_count)
            data = OpenMeteoClient(timeout=provider_timeout).get_forecast(
                latitude=latitude,
                longitude=longitude,
                days=request_days,
            )
            return normalize_open_meteo_weather(
                data, requested_city=city, date=requested_date, days=day_count, start_offset=start_offset,
            )
        return retry_call(_realtime_weather_call, retries=0, sleep_seconds=0.0)
    except Exception as exc:
        if not MCP_REALTIME_FALLBACK_TO_MOCK:
            raise
        result = get_mock_weather(city=city, date=date, **kwargs)
        return attach_mock_source(result, fallback_used=True, fallback_reason=type(exc).__name__)


def _clean_date_label(value: Any) -> str | None:
    # 清洗日期标签，去除无效或未指定的值
    text = str(value or "").strip()
    if text.lower() in {"", "unspecified", "unknown", "none", "null"} or text in {"未指定", "待确认"}:
        return None
    return text


def _resolve_target_date(date_label: str | None) -> date_type | None:
    # 将日期字符串解析为 date 对象
    text = str(date_label or "").strip()
    if not text:
        return None
    for token in text.replace("/", "-").split():
        try:
            return datetime.strptime(token[:10], "%Y-%m-%d").date()
        except ValueError:
            pass
    return None


def _parse_amap_location(location: str) -> tuple[float, float]:
    # 解析高德地图定位字符串为经纬度浮点数
    longitude_text, latitude_text = location.split(",", 1)
    return float(longitude_text), float(latitude_text)


def main() -> None:
    # 启动天气 MCP 服务
    config = MCP_SERVERS["weather"]
    parser = argparse.ArgumentParser(description="Run Weather MCP Server.")
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
                handler=get_weather,
                description="Return Open-Meteo weather forecast after AMap geocoding.",
            )
        },
    )


if __name__ == "__main__":
    main()
