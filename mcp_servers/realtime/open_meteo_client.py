from __future__ import annotations

import json
from typing import Any
from urllib import error, parse, request

from common.config import MCP_REALTIME_TIMEOUT_SECONDS, OPEN_METEO_API_BASE_URL
from mcp_servers.realtime.errors import ProviderBadResponseError, ProviderTimeoutError


class OpenMeteoClient:
    def __init__(self, *, base_url: str | None = None, timeout: float | None = None) -> None:
        self.base_url = (base_url or OPEN_METEO_API_BASE_URL).strip().rstrip("/")
        self.timeout = float(timeout if timeout is not None else MCP_REALTIME_TIMEOUT_SECONDS)

    def get_forecast(self, *, latitude: float, longitude: float, days: int) -> dict[str, Any]:
        return self._get(
            "/v1/forecast",
            {
                "latitude": latitude,
                "longitude": longitude,
                "daily": ",".join(
                    [
                        "weather_code",
                        "temperature_2m_max",
                        "temperature_2m_min",
                        "precipitation_probability_max",
                    ]
                ),
                "forecast_days": max(1, min(int(days or 1), 16)),
                "timezone": "Asia/Shanghai",
            },
        )

    def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.base_url}{path}?{parse.urlencode({k: str(v) for k, v in params.items()})}"
        try:
            with request.urlopen(url, timeout=self.timeout) as response:
                raw_body = response.read().decode("utf-8")
        except TimeoutError as exc:
            raise ProviderTimeoutError(f"Open-Meteo request timed out after {self.timeout}s") from exc
        except error.URLError as exc:
            reason = getattr(exc, "reason", exc)
            if isinstance(reason, TimeoutError):
                raise ProviderTimeoutError(f"Open-Meteo request timed out after {self.timeout}s") from exc
            raise ProviderBadResponseError(f"Open-Meteo request failed: {type(reason).__name__}") from exc

        try:
            data = json.loads(raw_body)
        except json.JSONDecodeError as exc:
            raise ProviderBadResponseError("Open-Meteo response is not valid JSON") from exc
        if not isinstance(data, dict):
            raise ProviderBadResponseError("Open-Meteo response body must be an object")
        if data.get("error"):
            raise ProviderBadResponseError(f"Open-Meteo returned error: {data.get('reason') or data.get('error')}")
        return data
