"""Mock environment data used by the JSON-RPC MCP servers."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


DEFAULT_CITY = "北京"
DEFAULT_DATE = "明天"

WEATHER_DATA = {
    "北京": {
        "city": "北京",
        "date": "明天",
        "temp": "15°C",
        "condition": "晴",
        "wind": "微风",
    },
    "上海": {
        "city": "上海",
        "date": "明天",
        "temp": "18°C",
        "condition": "多云",
        "wind": "东南风 3 级",
    },
    "广州": {
        "city": "广州",
        "date": "明天",
        "temp": "24°C",
        "condition": "小雨",
        "wind": "南风 2 级",
    },
}

TRANSPORT_DATA = {
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


def get_weather(city: str = DEFAULT_CITY, date: str = DEFAULT_DATE, **_: Any) -> dict[str, Any]:
    data = _lookup(WEATHER_DATA, city)
    data["date"] = date or data.get("date", DEFAULT_DATE)
    return data


def get_transport(city: str = DEFAULT_CITY, date: str = DEFAULT_DATE, **_: Any) -> dict[str, Any]:
    data = _lookup(TRANSPORT_DATA, city)
    data["date"] = date or DEFAULT_DATE
    return data


def get_traffic(city: str = DEFAULT_CITY, date: str = DEFAULT_DATE, **kwargs: Any) -> dict[str, Any]:
    """Backward-compatible alias for older local config/tests."""
    return get_transport(city=city, date=date, **kwargs)


def _lookup(dataset: dict[str, dict[str, Any]], city: str) -> dict[str, Any]:
    normalized_city = (city or DEFAULT_CITY).strip()
    data = dataset.get(normalized_city) or dataset[DEFAULT_CITY]
    result = deepcopy(data)
    result["requested_city"] = normalized_city
    result["fallback_used"] = normalized_city not in dataset
    return result
