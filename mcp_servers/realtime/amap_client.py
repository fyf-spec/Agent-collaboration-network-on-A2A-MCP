from __future__ import annotations

import json
from typing import Any
from urllib import error, parse, request

from common.config import (
    AMAP_API_BASE_URL,
    AMAP_WEB_KEY,
    MCP_REALTIME_TIMEOUT_SECONDS,
    MCP_TRAFFIC_ROUTE_TIMEOUT_SECONDS,
)
from mcp_servers.realtime.errors import (
    ProviderAuthError,
    ProviderBadResponseError,
    ProviderTimeoutError,
)


class AMapClient:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float | None = None,
    ) -> None:
        # 初始化高德地图客户端
        self.api_key = (api_key if api_key is not None else AMAP_WEB_KEY).strip()
        self.base_url = (base_url or AMAP_API_BASE_URL).strip().rstrip("/")
        self.timeout = float(timeout if timeout is not None else MCP_REALTIME_TIMEOUT_SECONDS)

    def get_weather(self, city_or_adcode: str, *, forecast: bool = False) -> dict[str, Any]:
        # 获取天气信息
        return self._get(
            "/v3/weather/weatherInfo",
            {"city": city_or_adcode, "extensions": "all" if forecast else "base"},
        )

    def search_pois(
        self,
        city: str,
        keywords: str | None = None,
        types: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        # 搜索POI兴趣点
        params: dict[str, Any] = {
            "city": city,
            "offset": max(1, min(int(limit or 20), 25)),
            "page": 1,
            "extensions": "all",
        }
        if keywords:
            params["keywords"] = keywords
        if types:
            params["types"] = types
        return self._get("/v3/place/text", params)

    def search_attractions(
        self,
        city: str,
        preferences: list[str] | None = None,
        limit: int = 30,
    ) -> dict[str, Any]:
        """Search attractions via AMap POI text search.  Preferences are NOT used as keywords to avoid bad results."""
        # 始终用"景点"搜索，避免偏好关键词（如 nature/entertainment）污染搜索结果
        result = self.search_pois(city=city, keywords="景点", types="110000", limit=limit)
        # 如果搜不到，不加类型限制再试
        if not result.get("pois"):
            result = self.search_pois(city=city, keywords="景点", limit=limit)
        # 检查结果是否属于目标城市
        if not _belongs_to_city(result, city):
            result = self.search_pois(city=city, keywords="景点", limit=limit)
        return result

    def search_hotels(
        self, city: str, target_area: str | None = None, limit: int = 20,
        center: str | None = None,
    ) -> dict[str, Any]:
        """Search hotels via AMap POI search.  Supports center-coordinate around-search."""
        # 如果有坐标，用周边搜索
        if center and _looks_number(center.split(",")[0] if "," in center else ""):
            return self._get("/v3/place/around", {
                "location": center,
                "keywords": target_area or "酒店",
                "types": "100000",
                "offset": min(limit, 25),
                "page": 1,
                "extensions": "all",
                "radius": 10000,
            })

        keywords = "酒店"
        if target_area:
            keywords = f"{target_area} 酒店"
        result = self.search_pois(city=city, keywords=keywords, types="100000", limit=limit)
        if target_area and not result.get("pois"):
            result = self.search_pois(city=city, keywords="酒店", types="100000", limit=limit)
        return result

    def get_route(
        self,
        origin: str,
        destination: str,
        city: str | None = None,
        mode: str = "transit",
        origin_location: str | None = None,
        destination_location: str | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        # 获取路线规划
        route_timeout = float(timeout if timeout is not None else MCP_TRAFFIC_ROUTE_TIMEOUT_SECONDS)
        origin_location = self._coerce_location(origin_location) or self._coerce_location(origin) or self.geocode_city_or_address(
            origin,
            city,
            timeout=route_timeout,
        )
        destination_location = self._coerce_location(destination_location) or self._coerce_location(destination) or self.geocode_city_or_address(
            destination,
            city,
            timeout=route_timeout,
        )
        selected_mode = (mode or "transit").strip().lower()
        if selected_mode in {"walk", "walking"}:
            return self._get(
                "/v3/direction/walking",
                {"origin": origin_location, "destination": destination_location},
                timeout=route_timeout,
            )
        if selected_mode in {"drive", "driving", "taxi"}:
            return self._get(
                "/v3/direction/driving",
                {"origin": origin_location, "destination": destination_location, "strategy": 0},
                timeout=route_timeout,
            )
        return self._get(
            "/v3/direction/transit/integrated",
            {
                "origin": origin_location,
                "destination": destination_location,
                "city": city or "",
                "strategy": 0,
            },
            timeout=route_timeout,
        )

    def geocode_city_or_address(self, value: str, city: str | None = None, *, timeout: float | None = None) -> str:
        # 将城市名称或地址解析为经纬度坐标
        text = str(value or "").strip()
        location = self._coerce_location(text)
        if location:
            return location

        poi_result = self._get(
            "/v3/place/text",
            {
                "city": city or "",
                "keywords": text,
                "offset": 1,
                "page": 1,
                "extensions": "base",
            },
            timeout=timeout,
        )
        pois = poi_result.get("pois")
        if isinstance(pois, list) and pois:
            location = pois[0].get("location") if isinstance(pois[0], dict) else None
            if isinstance(location, str) and "," in location:
                return location

        geo_result = self._get("/v3/geocode/geo", {"address": text, "city": city or ""}, timeout=timeout)
        geocodes = geo_result.get("geocodes")
        if isinstance(geocodes, list) and geocodes:
            location = geocodes[0].get("location") if isinstance(geocodes[0], dict) else None
            if isinstance(location, str) and "," in location:
                return location
        raise ProviderBadResponseError(f"AMap could not resolve location: {text}")

    def _coerce_location(self, value: Any) -> str | None:
        # 将逗号分隔的坐标字符串转换为标准格式
        text = str(value or "").strip()
        if "," not in text:
            return None
        left, right = text.split(",", 1)
        if _looks_number(left) and _looks_number(right):
            return text
        return None

    def _get(self, path: str, params: dict[str, Any], *, timeout: float | None = None) -> dict[str, Any]:
        # 发送HTTP GET请求并处理响应
        if not self.api_key:
            raise ProviderAuthError("AMAP_WEB_KEY is required for realtime MCP")

        clean_params = {
            key: str(value)
            for key, value in params.items()
            if value is not None and str(value) != ""
        }
        clean_params["key"] = self.api_key

        url = f"{self.base_url}{path}?{parse.urlencode(clean_params)}"
        request_timeout = float(timeout if timeout is not None else self.timeout)

        # 限流重试：最多3次，指数退避
        import time as time_module
        for attempt in range(3):
            try:
                with request.urlopen(url, timeout=request_timeout) as response:
                    raw_body = response.read().decode("utf-8")
            except TimeoutError as exc:
                raise ProviderTimeoutError(f"AMap request timed out after {request_timeout}s") from exc
            except error.URLError as exc:
                reason = getattr(exc, "reason", exc)
                if isinstance(reason, TimeoutError):
                    raise ProviderTimeoutError(f"AMap request timed out after {request_timeout}s") from exc
                if attempt < 2:
                    time_module.sleep(0.5 * (attempt + 1))
                    continue
                raise ProviderBadResponseError(f"AMap request failed: {type(reason).__name__}") from exc

            try:
                data = json.loads(raw_body)
            except json.JSONDecodeError as exc:
                raise ProviderBadResponseError("AMap response is not valid JSON") from exc
            if not isinstance(data, dict):
                raise ProviderBadResponseError("AMap response body must be an object")
            if data.get("status") != "1":
                info = str(data.get("info") or data.get("infocode") or "")
                # 限流错误 → 等待后重试
                if "EXCEEDED" in info.upper() or "LIMIT" in info.upper():
                    if attempt < 2:
                        wait = (attempt + 1) * 1.0
                        time_module.sleep(wait)
                        continue
                raise ProviderBadResponseError(f"AMap returned status {data.get('status')}: {info}")

            return data

        raise ProviderBadResponseError("AMap request failed after retries")


def _looks_number(value: str) -> bool:
    # 判断字符串是否为有效数字
    try:
        float(value.strip())
        return True
    except ValueError:
        return False


def _belongs_to_city(result: dict[str, Any], city: str) -> bool:
    """检查 AMap 返回的 POI 是否属于目标城市（防止高德无提示地跨城市返回错误数据）"""
    pois = result.get("pois") if isinstance(result.get("pois"), list) else []
    if not pois:
        return True
    samples = [p for p in pois[:3] if isinstance(p, dict)]
    if not samples:
        return True
    # 取前3个POI的cityname/pname，看是否包含目标城市名
    wrong = 0
    for p in samples:
        cn = str(p.get("cityname") or "")
        pn = str(p.get("pname") or "")
        if city not in cn and city not in pn:
            wrong += 1
    return wrong < len(samples)  # 至少一个匹配就算通过
