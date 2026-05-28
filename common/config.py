"""Runtime configuration for the local A2A coordinator demo."""

from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent

COORDINATOR_NAME = "coordinator"
COORDINATOR_HOST = "127.0.0.1"
COORDINATOR_PORT = 9000
COORDINATOR_A2A_TCP_HOST = "127.0.0.1"
COORDINATOR_A2A_TCP_PORT = 9001

REGISTRY_HOST = "127.0.0.1"
REGISTRY_PORT = 7000

DEFAULT_TASK_TIMEOUT_SECONDS = 120.0
MAX_TASK_TIMEOUT_SECONDS = 900.0
DISPATCH_HTTP_TIMEOUT_SECONDS = 5.0
A2A_TCP_TIMEOUT_SECONDS = 3.0
MCP_HTTP_TIMEOUT_SECONDS = 3.0

LOG_FILE = PROJECT_ROOT / "logs" / "demo_log.jsonl"

MCP_GATEWAY = {
    "name": "mcp_gateway",
    "host": "127.0.0.1",
    "port": 8100,
    "path": "/",
    "enabled": True,
    "cache_ttl_seconds": 10.0,
    "max_concurrent_per_method": 3,
    "rate_limit_wait_seconds": 0.2,
    "coalesce_wait_seconds": 5.0,
    "upstream_timeout_seconds": 2.5,
    "circuit_failure_threshold": 3,
    "circuit_cooldown_seconds": 10.0,
}

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
        "method": "get_route",
        "extra_methods": ["get_intercity_transport"],
    },
    "attraction": {
        "name": "attraction_mcp_server",
        "host": "127.0.0.1",
        "port": 8003,
        "path": "/",
        "method": "search_attractions",
    },
    "hotel": {
        "name": "hotel_mcp_server",
        "host": "127.0.0.1",
        "port": 8004,
        "path": "/",
        "method": "search_hotels",
    },
    "packing": {
        "name": "packing_mcp_server",
        "host": "127.0.0.1",
        "port": 8005,
        "path": "/",
        "method": "get_packing_list",
    },
}

AGENTS = {
    "weather_agent": {
        "host": "127.0.0.1",
        "port": 9010,
        "protocol": "tcp",
        "execute_path": "/execute_task",
        "enabled": True,
        "capabilities": ["weather.query", "weather.forecast"],
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
    "attraction_agent": {
        "host": "127.0.0.1",
        "port": 9030,
        "protocol": "tcp",
        "execute_path": "/execute_task",
        "enabled": True,
        "capabilities": ["attraction.query", "attraction.plan"],
        "keywords": [
            "attraction",
            "spot",
            "scenic",
            "place",
            "景点",
            "游玩",
            "故宫",
            "天安门",
            "博物馆",
            "门票",
            "预约",
            "开放时间",
        ],
    },
    "hotel_agent": {
        "host": "127.0.0.1",
        "port": 9040,
        "protocol": "tcp",
        "execute_path": "/execute_task",
        "enabled": True,
        "capabilities": ["hotel.query", "hotel.selection"],
        "keywords": [
            "hotel",
            "accommodation",
            "stay",
            "住宿",
            "酒店",
            "旅馆",
            "青旅",
            "民宿",
            "住哪里",
            "住宿区域",
        ],
    },
    "traffic_agent": {
        "host": "127.0.0.1",
        "port": 9020,
        "protocol": "tcp",
        "execute_path": "/execute_task",
        "enabled": True,
        "capabilities": ["traffic.query", "route.selection", "intercity.transport"],
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
    "packing_agent": {
        "host": "127.0.0.1",
        "port": 9050,
        "protocol": "tcp",
        "execute_path": "/execute_task",
        "enabled": True,
        "capabilities": ["packing.list", "preparation"],
        "keywords": [
            "packing",
            "luggage",
            "preparation",
            "行李",
            "准备",
            "带什么",
            "清单",
            "衣物",
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
