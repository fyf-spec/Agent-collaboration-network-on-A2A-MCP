from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from mcp_servers.enrichment.local_profiles import enrich_attraction, enrich_hotel


def realtime_source(*, missing_fields: list[str] | None = None) -> dict[str, Any]:
    return {
        "provider": "amap",
        "realtime": True,
        "fallback_used": False,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "missing_fields": missing_fields or [],
    }


def mock_source(*, fallback_used: bool, fallback_reason: str | None = None) -> dict[str, Any]:
    source: dict[str, Any] = {
        "provider": "mock",
        "realtime": False,
        "fallback_used": fallback_used,
    }
    if fallback_reason:
        source["fallback_reason"] = fallback_reason
    return source


def normalize_weather(
    data: dict[str, Any],
    *,
    requested_city: str,
    date: str | None = None,
    days: int = 1,
) -> dict[str, Any]:
    forecasts = data.get("forecasts")
    if isinstance(forecasts, list) and forecasts and isinstance(forecasts[0], dict):
        return _normalize_weather_forecast(data, requested_city=requested_city, date=date, days=days)

    lives = data.get("lives")
    if not isinstance(lives, list) or not lives or not isinstance(lives[0], dict):
        raise ValueError("AMap weather response missing lives")
    live = lives[0]
    missing = _missing(live, ["weather", "temperature", "winddirection", "windpower", "reporttime"])
    return {
        "city": live.get("city") or requested_city,
        "requested_city": requested_city,
        "date": date or live.get("reporttime"),
        "condition": live.get("weather"),
        "temp": _format_temperature(live.get("temperature")),
        "wind": _format_wind(live.get("winddirection"), live.get("windpower")),
        "humidity": live.get("humidity"),
        "adcode": live.get("adcode"),
        "province": live.get("province"),
        "reporttime": live.get("reporttime"),
        "fallback_used": False,
        "data_source": realtime_source(missing_fields=missing),
    }


def normalize_open_meteo_weather(
    data: dict[str, Any],
    *,
    requested_city: str,
    date: str | None = None,
    days: int = 1,
    start_offset: int = 0,
) -> dict[str, Any]:
    daily = data.get("daily")
    if not isinstance(daily, dict):
        raise ValueError("Open-Meteo response missing daily forecast")
    dates = daily.get("time")
    codes = daily.get("weather_code")
    temp_max = daily.get("temperature_2m_max")
    temp_min = daily.get("temperature_2m_min")
    precip = daily.get("precipitation_probability_max")
    if not isinstance(dates, list) or not dates:
        raise ValueError("Open-Meteo response missing daily time")

    day_count = max(1, int(days or 1))
    offset = max(0, int(start_offset or 0))
    forecast_days = []
    for index, forecast_date in enumerate(dates[offset : offset + day_count], start=1):
        source_index = offset + index - 1
        code = _list_get(codes, source_index)
        condition = _open_meteo_condition(code)
        forecast_days.append(
            {
                "day": f"day{index}",
                "date": forecast_date,
                "condition": condition,
                "weather_code": code,
                "temp_min": _format_temperature(_list_get(temp_min, source_index)),
                "temp_max": _format_temperature(_list_get(temp_max, source_index)),
                "precipitation_probability": _list_get(precip, source_index),
                "wind": None,
            }
        )
    if not forecast_days:
        raise ValueError("Open-Meteo response contains no usable daily forecast")

    first = forecast_days[0]
    source = realtime_source(missing_fields=[])
    source["provider"] = "open-meteo"
    source["max_forecast_days"] = 16
    return {
        "city": requested_city,
        "requested_city": requested_city,
        "date": date if _is_displayable_date(date) else first.get("date"),
        "condition": first.get("condition"),
        "temp": _temperature_range(first.get("temp_min"), first.get("temp_max")),
        "wind": first.get("wind"),
        "humidity": None,
        "forecast_days": forecast_days,
        "fallback_used": False,
        "data_source": source,
    }


def build_far_future_weather_result(
    *,
    requested_city: str,
    date: str | None,
    days: int,
    reason: str,
) -> dict[str, Any]:
    day_count = max(1, int(days or 1))
    source = realtime_source(missing_fields=["forecast"])
    source["provider"] = "open-meteo"
    source["realtime"] = False
    source["fallback_used"] = False
    source["max_forecast_days"] = 16
    source["forecast_unavailable_reason"] = reason
    forecast_days = [
        {
            "day": f"day{index}",
            "date": None,
            "condition": "时间太远，无法准确预测",
            "outdoor_suitable": False,
            "indoor_preferred": False,
            "needs_weather_recheck": True,
            "note": "超过天气 API 可准确预报范围，请临近出行前再确认天气",
        }
        for index in range(1, day_count + 1)
    ]
    return {
        "city": requested_city,
        "requested_city": requested_city,
        "date": date if _is_displayable_date(date) else None,
        "condition": "时间太远，无法准确预测",
        "temp": None,
        "wind": None,
        "humidity": None,
        "forecast_days": forecast_days,
        "forecast_unavailable": True,
        "forecast_unavailable_reason": reason,
        "fallback_used": False,
        "data_source": source,
    }


def _normalize_weather_forecast(
    data: dict[str, Any],
    *,
    requested_city: str,
    date: str | None,
    days: int,
) -> dict[str, Any]:
    forecasts = data.get("forecasts")
    if not isinstance(forecasts, list) or not forecasts or not isinstance(forecasts[0], dict):
        raise ValueError("AMap weather response missing forecasts")
    forecast = forecasts[0]
    casts = forecast.get("casts")
    if not isinstance(casts, list) or not casts:
        raise ValueError("AMap weather forecast response missing casts")

    day_count = max(1, int(days or 1))
    forecast_days = []
    missing: list[str] = []
    for index, cast in enumerate(casts[:day_count], start=1):
        if not isinstance(cast, dict):
            continue
        missing.extend(_missing(cast, ["date", "dayweather", "nightweather", "daytemp", "nighttemp", "daywind", "daypower"]))
        forecast_days.append(
            {
                "day": f"day{index}",
                "date": cast.get("date"),
                "week": cast.get("week"),
                "condition": cast.get("dayweather") or cast.get("nightweather"),
                "day_weather": cast.get("dayweather"),
                "night_weather": cast.get("nightweather"),
                "temp_min": _format_temperature(cast.get("nighttemp")),
                "temp_max": _format_temperature(cast.get("daytemp")),
                "wind": _format_wind(cast.get("daywind"), cast.get("daypower")),
                "night_wind": _format_wind(cast.get("nightwind"), cast.get("nightpower")),
            }
        )
    if not forecast_days:
        raise ValueError("AMap weather forecast response contains no usable casts")

    first = forecast_days[0]
    return {
        "city": forecast.get("city") or requested_city,
        "requested_city": requested_city,
        "date": date or first.get("date"),
        "condition": first.get("condition"),
        "temp": _temperature_range(first.get("temp_min"), first.get("temp_max")),
        "wind": first.get("wind"),
        "humidity": None,
        "adcode": forecast.get("adcode"),
        "province": forecast.get("province"),
        "reporttime": forecast.get("reporttime"),
        "forecast_days": forecast_days,
        "fallback_used": False,
        "data_source": realtime_source(missing_fields=sorted(set(missing))),
    }


def normalize_attractions(
    data: dict[str, Any],
    *,
    city: str,
    days: int,
    budget_level: str,
    must_visit: list[str] | None,
    preferences: list[str] | None,
    limit: int,
) -> dict[str, Any]:
    spots = []
    enriched_fields: list[str] = []
    for poi in _pois(data)[:limit]:
        spot, fields = enrich_attraction(city, _normalize_spot(poi))
        spots.append(spot)
        enriched_fields.extend(fields)
    if not spots:
        raise ValueError("AMap attraction response contains no pois")
    return {
        "city": city,
        "requested_city": city,
        "fallback_used": False,
        "days": days,
        "budget_level": budget_level,
        "must_visit": must_visit or [],
        "preferences": preferences or [],
        "spots": spots,
        "data_source": _merged_top_source(enriched_fields),
    }


def normalize_hotels(
    data: dict[str, Any],
    *,
    city: str,
    days: int,
    budget_level: str,
    target_area: str | None,
    preferred_areas: list[str] | None,
    area_selection: dict[str, Any] | None,
    limit: int,
) -> dict[str, Any]:
    hotels = []
    enriched_fields: list[str] = []
    for poi in _pois(data)[:limit]:
        hotel, fields = enrich_hotel(city, _normalize_hotel(poi))
        hotels.append(hotel)
        enriched_fields.extend(fields)
    if not hotels:
        raise ValueError("AMap hotel response contains no pois")
    return {
        "city": city,
        "requested_city": city,
        "fallback_used": False,
        "days": days,
        "budget_level": budget_level,
        "target_area": target_area or "",
        "preferred_areas": preferred_areas or [],
        "area_selection": area_selection or {},
        "area_filter_fallback": False,
        "hotels": hotels,
        "data_source": _merged_top_source(enriched_fields),
    }


def normalize_route(
    data: dict[str, Any],
    *,
    city: str,
    origin: str,
    destination: str,
    preference: str,
    mode: str,
) -> dict[str, Any]:
    candidates = _route_candidates(data, mode=mode)
    if not candidates:
        raise ValueError("AMap route response contains no route candidates")
    return {
        "city": city,
        "requested_city": city,
        "fallback_used": False,
        "origin": origin,
        "destination": destination,
        "preference": preference,
        "same_area": False,
        "candidates": candidates,
        "data_source": realtime_source(missing_fields=[]),
    }


def attach_mock_source(result: dict[str, Any], *, fallback_used: bool, fallback_reason: str | None = None) -> dict[str, Any]:
    enriched = dict(result)
    enriched["data_source"] = mock_source(fallback_used=fallback_used, fallback_reason=fallback_reason)
    return enriched


def _merged_top_source(enriched_fields: list[str]) -> dict[str, Any]:
    source = realtime_source(missing_fields=[])
    if enriched_fields:
        source["provider"] = "amap+local_profile"
        source["field_sources"] = {field: "local_profile" for field in sorted(set(enriched_fields))}
    return source


def _pois(data: dict[str, Any]) -> list[dict[str, Any]]:
    pois = data.get("pois")
    if not isinstance(pois, list):
        return []
    return [item for item in pois if isinstance(item, dict)]


def _normalize_spot(poi: dict[str, Any]) -> dict[str, Any]:
    missing = ["ticket", "duration", "open_time", "reservation_required", "indoor_or_outdoor", "nearest_subway"]
    return {
        "spot_id": poi.get("id"),
        "name": poi.get("name"),
        "area": poi.get("adname") or poi.get("address"),
        "ticket": None,
        "duration": None,
        "open_time": None,
        "reservation_required": None,
        "indoor_or_outdoor": None,
        "nearest_subway": None,
        "tags": _tags_from_poi(poi),
        "address": poi.get("address"),
        "location": poi.get("location"),
        "type": poi.get("type"),
        "tel": poi.get("tel"),
        "data_source": realtime_source(missing_fields=missing),
    }


def _normalize_hotel(poi: dict[str, Any]) -> dict[str, Any]:
    missing = ["price_per_night", "nearest_subway", "pros", "cons"]
    return {
        "hotel_id": poi.get("id"),
        "name": poi.get("name"),
        "area": poi.get("adname") or poi.get("address"),
        "price_per_night": None,
        "type": poi.get("type") or "hotel",
        "nearest_subway": None,
        "tags": _tags_from_poi(poi),
        "pros": [],
        "cons": [],
        "address": poi.get("address"),
        "location": poi.get("location"),
        "tel": poi.get("tel"),
        "data_source": realtime_source(missing_fields=missing),
    }


def _route_candidates(data: dict[str, Any], *, mode: str) -> list[dict[str, Any]]:
    route = data.get("route")
    if not isinstance(route, dict):
        return []
    if "transits" in route:
        return [_transit_candidate(item) for item in route.get("transits", [])[:3] if isinstance(item, dict)]
    if "paths" in route:
        return [_path_candidate(item, mode=mode) for item in route.get("paths", [])[:3] if isinstance(item, dict)]
    if "paths" in data:
        return [_path_candidate(item, mode=mode) for item in data.get("paths", [])[:3] if isinstance(item, dict)]
    return []


def _transit_candidate(item: dict[str, Any]) -> dict[str, Any]:
    segments = item.get("segments") if isinstance(item.get("segments"), list) else []
    names: list[str] = []
    pending_walk_meters = 0
    transfers = 0
    for segment in segments:
        if not isinstance(segment, dict):
            continue
        bus = segment.get("bus")
        buslines = bus.get("buslines") if isinstance(bus, dict) else None
        if isinstance(buslines, list) and buslines:
            first = buslines[0]
            if isinstance(first, dict) and first.get("name"):
                if pending_walk_meters >= 150:
                    names.append(f"步行约{pending_walk_meters}m")
                pending_walk_meters = 0
                names.append(_short_route_line_name(str(first["name"])))
                transfers += 1
        walking = segment.get("walking")
        if isinstance(walking, dict) and walking.get("distance"):
            try:
                pending_walk_meters += int(float(walking.get("distance") or 0))
            except (TypeError, ValueError):
                pass
    if pending_walk_meters >= 150:
        names.append(f"步行约{pending_walk_meters}m")
    return {
        "mode": "subway",
        "route": " -> ".join(names) or "AMap transit route",
        "duration_minutes": _seconds_to_minutes(item.get("duration")),
        "cost_yuan": _number_or_none(item.get("cost")),
        "walk_minutes": _meters_to_walk_minutes(item.get("walking_distance")),
        "transfers": max(0, transfers - 1),
        "note": "AMap realtime route candidate",
    }


def _short_route_line_name(name: str) -> str:
    text = name.strip()
    text = text.split("(")[0].split("（")[0]
    text = re.sub(r"\([^)]*\)", "", text)
    text = re.sub(r"（[^）]*）", "", text)
    return text.strip(" )）")


def _path_candidate(item: dict[str, Any], *, mode: str) -> dict[str, Any]:
    return {
        "mode": "walk" if mode in {"walk", "walking"} else "taxi",
        "route": item.get("strategy") or "AMap route",
        "duration_minutes": _seconds_to_minutes(item.get("duration")),
        "cost_yuan": _number_or_none(item.get("tolls")) or 0,
        "walk_minutes": _seconds_to_minutes(item.get("duration")) if mode in {"walk", "walking"} else 0,
        "transfers": 0,
        "note": "AMap realtime route candidate",
    }


def _tags_from_poi(poi: dict[str, Any]) -> list[str]:
    result = []
    for key in ("type", "business_area"):
        value = poi.get(key)
        if isinstance(value, str) and value:
            result.extend(part for part in value.split(";") if part)
    return result


def _missing(source: dict[str, Any], fields: list[str]) -> list[str]:
    return [field for field in fields if source.get(field) in (None, "", [])]


def _format_temperature(value: Any) -> str | None:
    if value in (None, ""):
        return None
    return f"{value}C"


def _format_wind(direction: Any, power: Any) -> str | None:
    if direction in (None, "") and power in (None, ""):
        return None
    if power in (None, ""):
        return str(direction)
    return f"{direction}{power}级"


def _temperature_range(low: Any, high: Any) -> str | None:
    if low in (None, "") and high in (None, ""):
        return None
    if low in (None, ""):
        return str(high)
    if high in (None, ""):
        return str(low)
    return f"{low}-{high}"


def _seconds_to_minutes(value: Any) -> int | None:
    number = _number_or_none(value)
    if number is None:
        return None
    return max(1, round(number / 60))


def _meters_to_walk_minutes(value: Any) -> int | None:
    number = _number_or_none(value)
    if number is None:
        return None
    return max(1, round(number / 80))


def _number_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _list_get(value: Any, index: int) -> Any:
    if isinstance(value, list) and 0 <= index < len(value):
        return value[index]
    return None


def _is_displayable_date(value: str | None) -> bool:
    text = str(value or "").strip().lower()
    return bool(text and text not in {"unspecified", "unknown", "none", "null", "未指定", "待确认"})


def _open_meteo_condition(code: Any) -> str:
    try:
        value = int(code)
    except (TypeError, ValueError):
        return "天气待确认"
    mapping = {
        0: "晴",
        1: "大部晴朗",
        2: "局部多云",
        3: "阴",
        45: "雾",
        48: "雾凇",
        51: "小毛毛雨",
        53: "毛毛雨",
        55: "较强毛毛雨",
        61: "小雨",
        63: "中雨",
        65: "大雨",
        71: "小雪",
        73: "中雪",
        75: "大雪",
        80: "小阵雨",
        81: "阵雨",
        82: "强阵雨",
        95: "雷暴",
        96: "雷暴伴小冰雹",
        99: "雷暴伴冰雹",
    }
    return mapping.get(value, "天气待确认")
