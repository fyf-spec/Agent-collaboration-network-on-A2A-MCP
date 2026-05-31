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
    # 取更多原始 POI，给过滤留余量
    raw_pois = _pois(data)[: max(limit * 3, 60)]
    must_names_lower = [str(n).strip().lower() for n in (must_visit or []) if str(n).strip()]
    scored: list[tuple[int, dict[str, Any], list[str]]] = []
    for poi in raw_pois:
        spot, fields = enrich_attraction(city, _normalize_spot(poi))
        score = _score_spot(spot)
        # must_visit 加分（不会被过滤误杀）
        spot_name = str(spot.get("name") or "").lower()
        if any(mn in spot_name for mn in must_names_lower):
            score = max(score, 6)
        scored.append((score, spot, fields))

    # 按分数降序排序
    scored.sort(key=lambda x: x[0], reverse=True)

    # ── 先做名称前缀聚类（合并同景区子区域），再做坐标去重 ──
    all_spots = [spot for _, spot, _ in scored if _score_spot(spot) >= 2]
    clustered = _cluster_by_name_prefix(all_spots)

    spots: list[dict[str, Any]] = []
    enriched_fields: list[str] = []
    seen_locations: set[str] = set()
    for spot in clustered:
        loc = str(spot.get("location") or "")
        if loc and loc in seen_locations:
            continue
        seen_locations.add(loc)
        spots.append(spot)
        if len(spots) >= limit:
            break

    # 重新收集 enriched_fields
    enriched_fields = [str(spot.get("data_source", "")) for spot in spots]

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
    biz = poi.get("biz_ext") if isinstance(poi.get("biz_ext"), dict) else {}
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
        "keytag": poi.get("keytag") or "",
        "rating": biz.get("rating") or "",
        "data_source": realtime_source(missing_fields=missing),
    }


def _score_spot(spot: dict[str, Any]) -> int:
    """综合评分（0~10）：用户评分 + 景区等级 - 展品/低质惩罚"""
    score = 4  # 基础分

    # ── 用户评分 ──
    rating_str = str(spot.get("rating") or "").strip()
    if rating_str:
        try:
            rating = float(rating_str)
            if rating >= 4.5:
                score += 3
            elif rating >= 4.0:
                score += 2
            elif rating >= 3.0:
                score += 1
        except (ValueError, TypeError):
            pass

    # ── 景区等级 ──
    keytag = str(spot.get("keytag") or "").strip()
    if "5A" in keytag:
        score += 3
    elif "4A" in keytag:
        score += 2
    elif "特色景区" in keytag or "国家级" in keytag:
        score += 1

    # ── 展品/低质关键词惩罚 ──
    name = str(spot.get("name") or "")
    if _is_likely_exhibit(name):
        score -= 6

    # ── 名字过长（展品/冷门场馆特征）──
    if len(name) >= 14:
        score -= 2
    elif len(name) >= 10:
        score -= 1

    return max(0, min(score, 10))


def _is_likely_exhibit(name: str) -> bool:
    """检测是否为博物馆展品、动物园分区、游乐场小展区等非真实景点"""
    import re
    patterns = [
        # 博物馆展品类
        r"仿",           # 仿制品
        r"插屏|宝座|屏风|花瓶|瓷器|玉器|铜器|陶俑|壁画",
        r"-\d{2,}型",    # 型号编号，如 024型潜艇
        r"展厅|展品|陈列|馆藏|第\d+号",
        r"模型|复制|仿制",
        # 动物园/植物园内小展区
        r"恐龙|爬行|两栖|昆虫|蝴蝶|热带鱼|企鹅|北极|熊猫馆",
        # 游乐园/主题场馆的小分区
        r"世界$|乐园$|王国$",
        # 非景点类
        r"官兵一致|军训|拓展|团建|真人CS",
    ]
    return any(re.search(p, name) for p in patterns)


def _cluster_by_name_prefix(spots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """把同景区的子区域（如 西湖-断桥残雪、西湖-花港观鱼）合并为一个条目"""
    if len(spots) <= 1:
        return spots

    # 按名称长度排序，短名在前（主景区通常名短）
    indexed = list(enumerate(spots))
    indexed.sort(key=lambda x: len(str(x[1].get("name") or "")))

    merged_flags: set[int] = set()
    result: list[dict[str, Any]] = []

    for i, short_spot in indexed:
        if i in merged_flags:
            continue
        short_name = str(short_spot.get("name") or "").strip()
        if len(short_name) < 4:  # 太短的名字不可靠
            result.append(short_spot)
            merged_flags.add(i)
            continue

        sub_names: list[str] = []
        best_spot = dict(short_spot)
        best_score = _score_spot(short_spot)

        for j, other_spot in indexed:
            if j == i or j in merged_flags:
                continue
            other_name = str(other_spot.get("name") or "").strip()
            # 检查 other 是否是 short 的子区域：other 以 short_name 开头，且后面跟着分隔符
            if other_name.startswith(short_name) and len(other_name) > len(short_name):
                suffix = other_name[len(short_name):]
                if suffix and suffix[0] in ("-", "·", "(", "（", "—"):
                    clean_suffix = suffix.lstrip("-·(（— ").rstrip(")）")
                    if clean_suffix and len(clean_suffix) >= 2:
                        sub_names.append(clean_suffix)
                    merged_flags.add(j)
                    # 合并评分和等级（取最高）
                    other_score = _score_spot(other_spot)
                    if other_score > best_score:
                        best_score = other_score
                        # 保留主名称但更新评分来源
                        best_spot["rating"] = other_spot.get("rating") or best_spot.get("rating")
                        best_spot["keytag"] = other_spot.get("keytag") or best_spot.get("keytag")

        if sub_names:
            existing_tags = best_spot.get("tags") or []
            if isinstance(existing_tags, list):
                existing_tags = [t for t in existing_tags if not t.startswith("含:")]
                existing_tags.append(f"含:{','.join(sub_names[:6])}")  # 最多保留6个子区域名
                best_spot["tags"] = existing_tags

        result.append(best_spot)
        merged_flags.add(i)

    # 恢复原始顺序（按分数）
    result.sort(key=lambda s: _score_spot(s), reverse=True)
    return result


def _normalize_hotel(poi: dict[str, Any]) -> dict[str, Any]:
    missing = ["price_per_night", "nearest_subway", "pros", "cons"]
    typecode = str(poi.get("typecode") or "")
    hotel_type = str(poi.get("type") or "")
    return {
        "hotel_id": poi.get("id"),
        "name": poi.get("name"),
        "area": poi.get("adname") or poi.get("address"),
        "price_per_night": _infer_hotel_price(typecode, hotel_type),
        "type": hotel_type,
        "nearest_subway": None,
        "tags": _tags_from_poi(poi),
        "pros": [],
        "cons": [],
        "address": poi.get("address"),
        "location": poi.get("location"),
        "tel": poi.get("tel"),
        "data_source": realtime_source(missing_fields=["nearest_subway", "pros", "cons"]),
    }


def _infer_hotel_price(typecode: str, hotel_type: str) -> str | None:
    """根据高德类型编码推算酒店价格区间。AMap 不返回真实房价，此为估算。"""
    tc = typecode.strip()
    ht = hotel_type.lower()

    # 五星级 / 豪华型
    if tc in ("100102", "100103") or any(w in ht for w in ["五星", "豪华", "奢华", "5星"]):
        return "800-2500"
    # 四星级 / 高档型
    if tc == "100101" or any(w in ht for w in ["四星", "高档", "4星"]):
        return "400-800"
    # 三星级 / 舒适型
    if tc == "100100" or any(w in ht for w in ["三星", "舒适", "商务", "3星"]):
        return "250-500"
    # 经济型 / 青年旅舍
    if any(w in ht for w in ["经济", "青年", "背包", "快捷", "民宿", "公寓"]):
        return "100-300"
    # 无法判断
    return "价格待确认"


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
    is_drive = mode not in {"walk", "walking"}
    # AMap driving routes don't have taxi fare — estimate from distance
    cost = _number_or_none(item.get("tolls")) or 0
    if is_drive and cost == 0:
        distance_m = _safe_int(item.get("distance"), default=0)
        if distance_m > 0:
            km = max(1, distance_m / 1000)
            # 起步价12元 + 2.3元/km
            cost = round(12 + km * 2.3, 1)
    return {
        "mode": "taxi" if is_drive else "walk",
        "route": item.get("strategy") or "AMap route",
        "duration_minutes": _seconds_to_minutes(item.get("duration")),
        "cost_yuan": cost,
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
