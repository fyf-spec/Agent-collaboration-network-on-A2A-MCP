"""Runtime configuration for the local A2A coordinator demo."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parent.parent

# ── 自动加载 .env ──────────────────────────────────────────────
# 所有 import common.config 的进程都会自动读取项目根目录的 .env 文件，
# 无需在各处手动调用 load_dotenv()。
_load_dotenv_path = PROJECT_ROOT / ".env"
if _load_dotenv_path.exists():
    load_dotenv(_load_dotenv_path)
# ────────────────────────────────────────────────────────────────

COORDINATOR_NAME = "coordinator"
COORDINATOR_HOST = "127.0.0.1"
COORDINATOR_PORT = 9000
COORDINATOR_A2A_TCP_HOST = "127.0.0.1"
COORDINATOR_A2A_TCP_PORT = 9001

REGISTRY_HOST = "127.0.0.1"
REGISTRY_PORT = 7000

# 备用注册中心
BACKUP_REGISTRY_HOST = "127.0.0.1"
BACKUP_REGISTRY_PORT = 7001

def _env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


DEFAULT_TASK_TIMEOUT_SECONDS = float(os.environ.get("DEFAULT_TASK_TIMEOUT_SECONDS", 120.0))
MAX_TASK_TIMEOUT_SECONDS = float(os.environ.get("MAX_TASK_TIMEOUT_SECONDS", 900.0))
DISPATCH_HTTP_TIMEOUT_SECONDS = float(os.environ.get("DISPATCH_HTTP_TIMEOUT_SECONDS", 5.0))
A2A_TCP_TIMEOUT_SECONDS = float(os.environ.get("A2A_TCP_TIMEOUT_SECONDS", 3.0))
MCP_HTTP_TIMEOUT_SECONDS = float(os.environ.get("MCP_HTTP_TIMEOUT_SECONDS", 3.0))

A2A_REALTIME_MCP_ENABLED = _env_bool("A2A_REALTIME_MCP_ENABLED", False)
AMAP_WEB_KEY = os.environ.get("AMAP_WEB_KEY", "").strip()
AMAP_API_BASE_URL = os.environ.get("AMAP_API_BASE_URL", "https://restapi.amap.com").strip().rstrip("/")
OPEN_METEO_API_BASE_URL = os.environ.get("OPEN_METEO_API_BASE_URL", "https://api.open-meteo.com").strip().rstrip("/")
OPEN_METEO_MAX_FORECAST_DAYS = int(os.environ.get("OPEN_METEO_MAX_FORECAST_DAYS", 16))
MCP_REALTIME_TIMEOUT_SECONDS = float(os.environ.get("MCP_REALTIME_TIMEOUT_SECONDS", 5.0))
MCP_REALTIME_FALLBACK_TO_MOCK = _env_bool("MCP_REALTIME_FALLBACK_TO_MOCK", True)
MCP_TRAFFIC_REALTIME_ENABLED = _env_bool("MCP_TRAFFIC_REALTIME_ENABLED", True)
MCP_TRAFFIC_MAX_WORKERS = int(os.environ.get("MCP_TRAFFIC_MAX_WORKERS", 4))
MCP_TRAFFIC_ROUTE_TIMEOUT_SECONDS = float(os.environ.get("MCP_TRAFFIC_ROUTE_TIMEOUT_SECONDS", 1.5))
MCP_TRAFFIC_MAX_SEGMENTS = int(os.environ.get("MCP_TRAFFIC_MAX_SEGMENTS", 8))
MCP_GATEWAY_UPSTREAM_TIMEOUT_SECONDS = float(os.environ.get("MCP_GATEWAY_UPSTREAM_TIMEOUT_SECONDS", 2.5))

LOG_FILE = PROJECT_ROOT / "logs" / "demo_log.jsonl"

MCP_GATEWAY = {
    "name": "mcp_gateway",
    "host": "127.0.0.1",
    "port": 8100,
    "path": "/",
    "enabled": True,
    "cache_ttl_seconds": 300.0,  # 默认TTL，被 per_method_ttl 覆盖
    "per_method_ttl_seconds": {
        # 天气数据每天变化，短TTL；其他数据稳定，长TTL
        "get_weather": 86400.0,       # 1天
        "get_packing_list": 86400.0,  # 依赖天气，同样1天
        "get_routes": 2592000.0,     # 30天
        "search_hotels": 2592000.0,  # 30天
        "search_attractions": 2592000.0,  # 30天
        "get_intercity_transport": 2592000.0,  # 30天
    },
    "max_concurrent_per_method": 3,
    "rate_limit_wait_seconds": 0.2,
    "coalesce_wait_seconds": 5.0,
    "upstream_timeout_seconds": MCP_GATEWAY_UPSTREAM_TIMEOUT_SECONDS,
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
        "extra_methods": ["get_routes", "get_intercity_transport"],
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
        "capabilities": ["hotel.query", "accommodation", "hotel.selection"],
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
        "port": 9060,
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
