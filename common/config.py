"""Runtime configuration for the local A2A coordinator demo."""

from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent

COORDINATOR_NAME = "coordinator"
COORDINATOR_HOST = "127.0.0.1"
COORDINATOR_PORT = 9000

DEFAULT_TASK_TIMEOUT_SECONDS = 10.0
MAX_TASK_TIMEOUT_SECONDS = 60.0
DISPATCH_HTTP_TIMEOUT_SECONDS = 3.0
MCP_HTTP_TIMEOUT_SECONDS = 3.0

LOG_FILE = PROJECT_ROOT / "logs" / "demo_log.jsonl"

MCP_SERVERS = {
    "weather": {
        "name": "weather_mcp_server",
        "host": "127.0.0.1",
        "port": 8001,
        "path": "/",
        "method": "get_weather",
    },
    "traffic": {
        "name": "traffic_mcp_server",
        "host": "127.0.0.1",
        "port": 8002,
        "path": "/",
        "method": "get_transport",
    },
}

AGENTS = {
    "weather_agent": {
        "host": "127.0.0.1",
        "port": 9010,
        "execute_path": "/execute_task",
        "enabled": True,
        "capabilities": ["weather"],
        "keywords": [
            "weather",
            "temperature",
            "rain",
            "snow",
            "wind",
            "forecast",
            "天气",
            "气温",
            "温度",
            "下雨",
            "降雨",
            "雨",
            "雪",
            "晴",
            "阴",
            "风",
            "预报",
        ],
    },
    "traffic_agent": {
        "host": "127.0.0.1",
        "port": 9020,
        "execute_path": "/execute_task",
        "enabled": True,
        "capabilities": ["traffic", "transport"],
        "keywords": [
            "traffic",
            "transport",
            "route",
            "subway",
            "train",
            "flight",
            "bus",
            "drive",
            "交通",
            "路况",
            "路线",
            "出行",
            "通勤",
            "地铁",
            "公交",
            "火车",
            "高铁",
            "航班",
            "机票",
            "开车",
            "打车",
        ],
    },
}

TRAVEL_KEYWORDS = [
    "travel",
    "trip",
    "itinerary",
    "plan",
    "旅行",
    "旅游",
    "出游",
    "行程",
    "攻略",
    "游玩",
    "安排",
    "方案",
]
