from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from common.config import A2A_REALTIME_MCP_ENABLED, MCP_REALTIME_FALLBACK_TO_MOCK, MCP_SERVERS
from mcp_servers.base_mcp_server import MCPTool, run_mcp_server
from mcp_servers.mock_data import search_hotels as search_mock_hotels
from mcp_servers.realtime.amap_client import AMapClient
from mcp_servers.realtime.normalizers import attach_mock_source, normalize_hotels


def search_hotels(
    city: str = "北京",
    preferred_areas: list[str] | None = None,
    target_area: str | None = None,
    budget_level: str = "normal",
    days: int = 3,
    daily_plan: dict[str, object] | None = None,
    preferences: list[str] | None = None,
    area_selection: dict[str, object] | None = None,
    requested_fields: list[str] | None = None,
    **kwargs: object,
) -> dict[str, object]:
    if not A2A_REALTIME_MCP_ENABLED:
        return attach_mock_source(
            search_mock_hotels(
                city=city,
                preferred_areas=preferred_areas,
                target_area=target_area,
                budget_level=budget_level,
                days=days,
                daily_plan=daily_plan,
                preferences=preferences,
                area_selection=area_selection,
                requested_fields=requested_fields,
                **kwargs,
            ),
            fallback_used=False,
        )
    try:
        amap = AMapClient()
        # 把 target_area 转成坐标，用于周边搜索
        center: str | None = None
        if target_area:
            try:
                center = amap.geocode_city_or_address(f"{city}{target_area}")
            except Exception:
                center = None

        # 泛搜（有坐标用周边搜索，没有用文本搜索）
        data = amap.search_hotels(city=city, target_area=target_area, limit=20, center=center)

        # 按预算档次做精确搜索
        tier_keywords = _budget_tier_keywords(budget_level, preferences)
        tier_data: dict[str, Any] | None = None
        if tier_keywords:
            try:
                tier_data = amap.search_pois(
                    city=city, keywords=tier_keywords, types="100000", limit=10,
                )
            except Exception:
                tier_data = None

        # 合并：精确搜结果插入最前面
        all_pois = list(data.get("pois", []) if isinstance(data.get("pois"), list) else [])
        if isinstance(tier_data, dict):
            seen_ids = {str(p.get("id") or "") for p in all_pois if isinstance(p, dict)}
            for p in (tier_data.get("pois") or []) if isinstance(tier_data.get("pois"), list) else []:
                if not isinstance(p, dict):
                    continue
                pid = str(p.get("id") or "")
                if pid and pid not in seen_ids:
                    seen_ids.add(pid)
                    all_pois.insert(0, p)

        return normalize_hotels(
            {"pois": all_pois},
            city=city,
            days=days,
            budget_level=budget_level,
            target_area=target_area,
            preferred_areas=preferred_areas,
            area_selection=area_selection,
            limit=20,
        )
    except Exception as exc:
        if not MCP_REALTIME_FALLBACK_TO_MOCK:
            raise
        result = search_mock_hotels(
            city=city,
            preferred_areas=preferred_areas,
            target_area=target_area,
            budget_level=budget_level,
            days=days,
            daily_plan=daily_plan,
            preferences=preferences,
            area_selection=area_selection,
            requested_fields=requested_fields,
            **kwargs,
        )
        return attach_mock_source(result, fallback_used=True, fallback_reason=type(exc).__name__)


def _budget_tier_keywords(budget_level: str, preferences: list[str] | None) -> str | None:
    """按预算档次返回 AMap 搜索关键词。三档：经济型 / 舒适型 / 高端轻奢型"""
    level = str(budget_level).strip().lower()
    prefs_lower = [str(p).strip().lower() for p in (preferences or [])]

    # 显式偏好优先
    if any(p in {"luxury", "五星级", "5-star", "5star", "奢华", "五星", "高端", "顶级"} for p in prefs_lower):
        return "五星级酒店"
    if any(p in {"economy", "经济", "便宜", "低价", "穷游"} for p in prefs_lower):
        return "经济型酒店"

    # 按 budget_level
    if level in {"high", "luxury"}:
        return "五星级酒店"
    elif level == "low":
        return "经济型酒店"
    elif level == "normal":
        return "舒适型酒店"

    return None


def main() -> None:
    config = MCP_SERVERS["hotel"]
    parser = argparse.ArgumentParser(description="Run Hotel MCP Server.")
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
                handler=search_hotels,
                description="Return realtime AMap hotel candidates with mock fallback.",
            )
        },
    )


if __name__ == "__main__":
    main()
