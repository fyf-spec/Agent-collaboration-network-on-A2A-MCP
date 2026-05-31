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
        self.api_key = (api_key if api_key is not None else AMAP_WEB_KEY).strip()
        self.base_url = (base_url or AMAP_API_BASE_URL).strip().rstrip("/")
        self.timeout = float(timeout if timeout is not None else MCP_REALTIME_TIMEOUT_SECONDS)

    def get_weather(self, city_or_adcode: str, *, forecast: bool = False) -> dict[str, Any]:
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
        keywords = " ".join(str(item) for item in preferences or [] if str(item).strip()) or "景点"
        return self.search_pois(city=city, keywords=keywords, types="110000", limit=limit)

    def search_hotels(self, city: str, target_area: str | None = None, limit: int = 20) -> dict[str, Any]:
        keywords = "酒店"
        if target_area:
            keywords = f"{target_area} 酒店"
        return self.search_pois(city=city, keywords=keywords, types="100000", limit=limit)

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
        text = str(value or "").strip()
        if "," not in text:
            return None
        left, right = text.split(",", 1)
        if _looks_number(left) and _looks_number(right):
            return text
        return None

    def _get(self, path: str, params: dict[str, Any], *, timeout: float | None = None) -> dict[str, Any]:
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
        try:
            with request.urlopen(url, timeout=request_timeout) as response:
                raw_body = response.read().decode("utf-8")
        except TimeoutError as exc:
            raise ProviderTimeoutError(f"AMap request timed out after {request_timeout}s") from exc
        except error.URLError as exc:
            reason = getattr(exc, "reason", exc)
            if isinstance(reason, TimeoutError):
                raise ProviderTimeoutError(f"AMap request timed out after {request_timeout}s") from exc
            raise ProviderBadResponseError(f"AMap request failed: {type(reason).__name__}") from exc

        try:
            data = json.loads(raw_body)
        except json.JSONDecodeError as exc:
            raise ProviderBadResponseError("AMap response is not valid JSON") from exc
        if not isinstance(data, dict):
            raise ProviderBadResponseError("AMap response body must be an object")
        if data.get("status") != "1":
            info = data.get("info") or data.get("infocode") or "unknown provider error"
            raise ProviderBadResponseError(f"AMap returned status {data.get('status')}: {info}")

        return data


def _looks_number(value: str) -> bool:
    try:
        float(value.strip())
        return True
    except ValueError:
        return False
