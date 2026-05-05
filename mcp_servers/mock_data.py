"""Mock environment data used by the JSON-RPC MCP servers."""

from __future__ import annotations

from typing import Any


WEATHER_DATA = {
    "北京": {"city": "北京", "date": "明天", "temp": "15-24°C", "condition": "晴", "wind": "东北风 2 级"},
    "上海": {"city": "上海", "date": "明天", "temp": "18-25°C", "condition": "多云", "wind": "东南风 3 级"},
    "广州": {"city": "广州", "date": "明天", "temp": "22-29°C", "condition": "小雨", "wind": "南风 2 级"},
}

TRAFFIC_DATA = {
    "北京": {
        "city": "北京",
        "route": "地铁 4 号线 -> 2 号线",
        "status": "早高峰局部拥堵",
        "duration": "约 45 分钟",
    },
    "上海": {
        "city": "上海",
        "route": "地铁 2 号线 -> 10 号线",
        "status": "主干道通行正常",
        "duration": "约 38 分钟",
    },
    "广州": {
        "city": "广州",
        "route": "地铁 3 号线 -> 1 号线",
        "status": "雨天车速偏慢",
        "duration": "约 50 分钟",
    },
}


def get_weather(city: str) -> dict[str, Any]:
    return WEATHER_DATA.get(city, WEATHER_DATA["北京"])


def get_traffic(city: str) -> dict[str, Any]:
    return TRAFFIC_DATA.get(city, TRAFFIC_DATA["北京"])
