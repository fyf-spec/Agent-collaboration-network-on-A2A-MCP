"""Mock environment data used by the JSON-RPC MCP servers.

This file intentionally keeps mock data local and deterministic. MCP servers
expose these functions over HTTP JSON-RPC; Agents should treat MCP as a tool
service, not as local helper calls.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


DEFAULT_CITY = "北京"
DEFAULT_DATE = "明天"

WEATHER_DATA = {
    "北京": {"city": "北京", "date": "明天", "temp": "15°C", "condition": "晴", "wind": "微风"},
    "上海": {"city": "上海", "date": "明天", "temp": "18°C", "condition": "多云", "wind": "东南风 3 级"},
    "广州": {"city": "广州", "date": "明天", "temp": "24°C", "condition": "小雨", "wind": "南风 2 级"},
    "杭州": {"city": "杭州", "date": "明天", "temp": "19°C", "condition": "多云", "wind": "东风 2 级"},
    "南京": {"city": "南京", "date": "明天", "temp": "20°C", "condition": "晴到多云", "wind": "东南风 2 级"},
}

TRANSPORT_DATA = {
    "北京": {"city": "北京", "route": "地铁 4 号线 -> 2 号线", "status": "早高峰局部拥堵", "duration": "约 45 分钟"},
    "上海": {"city": "上海", "route": "地铁 2 号线 -> 10 号线", "status": "主干道通行正常", "duration": "约 38 分钟"},
    "广州": {"city": "广州", "route": "地铁 3 号线 -> 1 号线", "status": "雨天车速偏慢", "duration": "约 50 分钟"},
    "杭州": {"city": "杭州", "route": "地铁 1 号线/2 号线/5 号线按景区就近换乘", "status": "主城区通行正常，西湖周边建议步行接驳", "duration": "约 30-50 分钟"},
    "南京": {"city": "南京", "route": "地铁 1 号线/2 号线/3 号线按景点换乘", "status": "主干线路通行正常，夫子庙秦淮河周边建议步行", "duration": "约 30-55 分钟"},
}

ATTRACTION_DATA = {
    "北京": [
        {
            "name": "天安门广场",
            "area": "天安门-故宫区域",
            "ticket": "免费",
            "duration": "1-2小时",
            "open_time": "全天",
            "reservation_required": True,
            "indoor_or_outdoor": "outdoor",
            "nearest_subway": "天安门东/天安门西",
            "tags": ["经典景点", "免费", "地标", "必去"],
        },
        {
            "name": "故宫",
            "area": "天安门-故宫区域",
            "ticket": "40-60元",
            "duration": "3-4小时",
            "open_time": "08:30-17:00",
            "reservation_required": True,
            "indoor_or_outdoor": "mixed",
            "nearest_subway": "天安门东/天安门西",
            "tags": ["经典景点", "历史", "必去"],
        },
        {
            "name": "国家博物馆",
            "area": "天安门-故宫区域",
            "ticket": "免费",
            "duration": "2-3小时",
            "open_time": "09:00-17:00",
            "reservation_required": True,
            "indoor_or_outdoor": "indoor",
            "nearest_subway": "天安门东",
            "tags": ["室内", "雨天备选", "免费", "博物馆"],
        },
        {
            "name": "景山公园",
            "area": "天安门-故宫区域",
            "ticket": "2-10元",
            "duration": "1-2小时",
            "open_time": "06:30-21:00",
            "reservation_required": False,
            "indoor_or_outdoor": "outdoor",
            "nearest_subway": "中国美术馆/南锣鼓巷",
            "tags": ["低价", "观景", "户外"],
        },
        {
            "name": "前门",
            "area": "天坛-前门区域",
            "ticket": "免费",
            "duration": "1-2小时",
            "open_time": "全天",
            "reservation_required": False,
            "indoor_or_outdoor": "outdoor",
            "nearest_subway": "前门",
            "tags": ["免费", "老北京街区", "公共交通方便"],
        },
        {
            "name": "大栅栏",
            "area": "天坛-前门区域",
            "ticket": "免费",
            "duration": "1-2小时",
            "open_time": "全天",
            "reservation_required": False,
            "indoor_or_outdoor": "outdoor",
            "nearest_subway": "前门",
            "tags": ["免费", "老北京街区", "低预算"],
        },
        {
            "name": "天坛",
            "area": "天坛-前门区域",
            "ticket": "15-34元",
            "duration": "2-3小时",
            "open_time": "06:00-22:00",
            "reservation_required": False,
            "indoor_or_outdoor": "outdoor",
            "nearest_subway": "天坛东门",
            "tags": ["经典景点", "低价", "户外"],
        },
        {
            "name": "什刹海",
            "area": "什刹海-南锣鼓巷区域",
            "ticket": "免费",
            "duration": "2-3小时",
            "open_time": "全天",
            "reservation_required": False,
            "indoor_or_outdoor": "outdoor",
            "nearest_subway": "什刹海",
            "tags": ["免费", "休闲", "低预算"],
        },
        {
            "name": "南锣鼓巷",
            "area": "什刹海-南锣鼓巷区域",
            "ticket": "免费",
            "duration": "1-2小时",
            "open_time": "全天",
            "reservation_required": False,
            "indoor_or_outdoor": "outdoor",
            "nearest_subway": "南锣鼓巷",
            "tags": ["免费", "街区", "低预算"],
        },
        {
            "name": "圆明园",
            "area": "海淀西北区域",
            "ticket": "10-25元",
            "duration": "2-3小时",
            "open_time": "07:00-19:30",
            "reservation_required": False,
            "indoor_or_outdoor": "outdoor",
            "nearest_subway": "圆明园",
            "tags": ["低价", "户外", "历史"],
        },
        {
            "name": "颐和园",
            "area": "海淀西北区域",
            "ticket": "20-60元",
            "duration": "3-4小时",
            "open_time": "06:00-20:00",
            "reservation_required": False,
            "indoor_or_outdoor": "outdoor",
            "nearest_subway": "北宫门",
            "tags": ["经典景点", "户外", "园林"],
        },
    ],
    "上海": [
        {
            "name": "外滩",
            "area": "黄浦江沿线",
            "ticket": "免费",
            "duration": "1-2小时",
            "open_time": "全天",
            "reservation_required": False,
            "indoor_or_outdoor": "outdoor",
            "nearest_subway": "南京东路",
            "tags": ["免费", "经典景点", "地标"],
        }
    ],
    "广州": [
        {
            "name": "广州塔",
            "area": "珠江新城-广州塔区域",
            "ticket": "外观免费，登塔另收费",
            "duration": "1-2小时",
            "open_time": "09:30-22:30",
            "reservation_required": False,
            "indoor_or_outdoor": "mixed",
            "nearest_subway": "广州塔",
            "tags": ["地标", "经典景点"],
        }
    ],
    "杭州": [
        {
            "name": "西湖",
            "area": "湖滨-西湖边",
            "ticket": "免费",
            "duration": "3-4小时",
            "open_time": "全天",
            "reservation_required": False,
            "indoor_or_outdoor": "outdoor",
            "nearest_subway": "龙翔桥/凤起路",
            "tags": ["经典景点", "免费", "地标", "必去", "公共交通方便"],
        },
        {
            "name": "灵隐寺",
            "area": "灵隐-西湖西线",
            "ticket": "45-75元",
            "duration": "2-3小时",
            "open_time": "07:00-18:00",
            "reservation_required": False,
            "indoor_or_outdoor": "mixed",
            "nearest_subway": "黄龙体育中心换乘公交",
            "tags": ["经典景点", "历史", "寺庙", "mixed"],
        },
        {
            "name": "河坊街",
            "area": "吴山-河坊街区域",
            "ticket": "免费",
            "duration": "1-2小时",
            "open_time": "全天",
            "reservation_required": False,
            "indoor_or_outdoor": "outdoor",
            "nearest_subway": "定安路",
            "tags": ["免费", "街区", "低预算", "公共交通方便"],
        },
        {
            "name": "西溪湿地",
            "area": "西溪湿地区域",
            "ticket": "70-80元",
            "duration": "3-4小时",
            "open_time": "08:00-17:30",
            "reservation_required": False,
            "indoor_or_outdoor": "outdoor",
            "nearest_subway": "西溪湿地南",
            "tags": ["自然", "经典景点", "户外"],
        },
        {
            "name": "京杭大运河",
            "area": "拱宸桥-运河区域",
            "ticket": "免费",
            "duration": "2-3小时",
            "open_time": "全天",
            "reservation_required": False,
            "indoor_or_outdoor": "outdoor",
            "nearest_subway": "拱宸桥东",
            "tags": ["免费", "历史", "低预算", "公共交通方便"],
        },
        {
            "name": "雷峰塔",
            "area": "湖滨-西湖边",
            "ticket": "40元",
            "duration": "1-2小时",
            "open_time": "08:00-20:00",
            "reservation_required": False,
            "indoor_or_outdoor": "mixed",
            "nearest_subway": "龙翔桥换乘公交",
            "tags": ["经典景点", "历史", "西湖周边"],
        },
    ],
    "南京": [
        {
            "name": "中山陵",
            "area": "钟山风景区",
            "ticket": "免费",
            "duration": "2-3小时",
            "open_time": "08:30-17:00",
            "reservation_required": True,
            "indoor_or_outdoor": "outdoor",
            "nearest_subway": "苜蓿园换乘景区交通",
            "tags": ["经典景点", "免费", "历史", "必去"],
        },
        {
            "name": "夫子庙",
            "area": "夫子庙-秦淮河区域",
            "ticket": "免费",
            "duration": "1-2小时",
            "open_time": "全天",
            "reservation_required": False,
            "indoor_or_outdoor": "outdoor",
            "nearest_subway": "夫子庙",
            "tags": ["经典景点", "免费", "街区", "公共交通方便"],
        },
        {
            "name": "秦淮河",
            "area": "夫子庙-秦淮河区域",
            "ticket": "河岸免费，游船另收费",
            "duration": "1-2小时",
            "open_time": "全天",
            "reservation_required": False,
            "indoor_or_outdoor": "outdoor",
            "nearest_subway": "夫子庙/武定门",
            "tags": ["经典景点", "夜景", "低预算"],
        },
        {
            "name": "明孝陵",
            "area": "钟山风景区",
            "ticket": "70元",
            "duration": "2-3小时",
            "open_time": "06:30-18:30",
            "reservation_required": False,
            "indoor_or_outdoor": "outdoor",
            "nearest_subway": "苜蓿园",
            "tags": ["经典景点", "历史", "户外"],
        },
        {
            "name": "南京博物院",
            "area": "中山东路-博物院区域",
            "ticket": "免费",
            "duration": "2-4小时",
            "open_time": "09:00-17:00",
            "reservation_required": True,
            "indoor_or_outdoor": "indoor",
            "nearest_subway": "博物院周边地铁站",
            "tags": ["室内", "雨天备选", "免费", "博物馆"],
        },
        {
            "name": "总统府",
            "area": "新街口-总统府区域",
            "ticket": "35元",
            "duration": "1-2小时",
            "open_time": "08:30-17:00",
            "reservation_required": False,
            "indoor_or_outdoor": "mixed",
            "nearest_subway": "大行宫",
            "tags": ["经典景点", "历史", "公共交通方便"],
        },
    ],
}


def get_weather(city: str = DEFAULT_CITY, date: str = DEFAULT_DATE, **_: Any) -> dict[str, Any]:
    data = _lookup(WEATHER_DATA, city)
    data["date"] = date or data.get("date", DEFAULT_DATE)
    return data

def get_packing_list(city: str = DEFAULT_CITY, days: int = 3, temperature: str = "", condition: str = "", **_: Any) -> dict[str, Any]:
    """Return mock packing list based on destination and weather."""
    normalized_city = (city or DEFAULT_CITY).strip()
    
    base_items = [
        {"category": "证件", "items": ["身份证", "学生证/优惠证件"], "reason": "出行必备"},
        {"category": "洗漱用品", "items": ["牙刷", "毛巾", "护肤品"], "reason": "日常所需"},
        {"category": "电子产品", "items": ["手机", "充电器", "充电宝"], "reason": "保持联系与记录"}
    ]
    
    clothing_items = ["内衣裤", "袜子"]
    if "冷" in condition or "雪" in condition or (temperature and any(int(t) < 10 for t in __import__("re").findall(r"-?\d+", temperature))):
        clothing_items.extend(["羽绒服", "保暖内衣", "围巾", "手套"])
        clothing_reason = "天气寒冷，需注意保暖"
    elif "热" in condition or (temperature and any(int(t) > 28 for t in __import__("re").findall(r"-?\d+", temperature))):
        clothing_items.extend(["短袖", "短裤", "防晒衣"])
        clothing_reason = "天气炎热，需透气和防晒"
    else:
        clothing_items.extend(["长袖", "薄外套", "长裤"])
        clothing_reason = "气温适中，建议洋葱式穿衣"
        
    base_items.append({"category": "衣物", "items": clothing_items, "reason": clothing_reason})
    
    if "雨" in condition:
        base_items.append({"category": "雨具", "items": ["雨伞", "雨衣", "防水鞋套"], "reason": "预报有雨"})
    elif "晴" in condition or "太阳" in condition:
        base_items.append({"category": "防晒", "items": ["太阳伞", "墨镜", "防晒霜"], "reason": "预报晴天，注意防晒"})
        
    return {
        "city": normalized_city,
        "days": days,
        "weather_condition_used": condition,
        "temperature_used": temperature,
        "packing_list": base_items
    }


def get_transport(city: str = DEFAULT_CITY, date: str = DEFAULT_DATE, **_: Any) -> dict[str, Any]:
    data = _lookup(TRANSPORT_DATA, city)
    data["date"] = date or DEFAULT_DATE
    return data


def get_traffic(city: str = DEFAULT_CITY, date: str = DEFAULT_DATE, **kwargs: Any) -> dict[str, Any]:
    """Backward-compatible alias for older local config/tests."""
    return get_transport(city=city, date=date, **kwargs)


def search_attractions(
    city: str = DEFAULT_CITY,
    days: int = 3,
    budget_level: str = "normal",
    must_visit: list[str] | None = None,
    preferences: list[str] | None = None,
    requested_fields: list[str] | None = None,
    **_: Any,
) -> dict[str, Any]:
    """Return mock attraction data, ranked by must-visit, budget and preference hints."""
    normalized_city = (city or DEFAULT_CITY).strip()
    spots: list[dict[str, Any]] = deepcopy(ATTRACTION_DATA.get(normalized_city) or ATTRACTION_DATA[DEFAULT_CITY])

    must_visit = must_visit or []
    preferences = preferences or []
    requested_fields = requested_fields or []

    def score(spot: dict[str, Any]) -> int:
        value = 0
        name = str(spot.get("name", ""))
        area = str(spot.get("area", ""))
        tags = spot.get("tags", [])
        ticket = str(spot.get("ticket", ""))

        for item in must_visit:
            item = str(item)
            if item and (item in name or name in item):
                value += 100

        if budget_level == "low":
            if "免费" in ticket:
                value += 30
            if "低价" in tags:
                value += 20

        for pref in preferences:
            pref = str(pref)
            if pref and (pref in tags or pref in name or pref in area):
                value += 10

        if "经典景点" in tags:
            value += 5
        return value

    spots.sort(key=score, reverse=True)

    if requested_fields:
        keep = set(requested_fields) | {"name", "area", "tags"}
        spots = [{key: value for key, value in spot.items() if key in keep} for spot in spots]

    return {
        "city": normalized_city if normalized_city in ATTRACTION_DATA else DEFAULT_CITY,
        "requested_city": normalized_city,
        "fallback_used": normalized_city not in ATTRACTION_DATA,
        "days": days,
        "budget_level": budget_level,
        "must_visit": must_visit,
        "preferences": preferences,
        "spots": spots,
    }


def get_route(
    city: str = DEFAULT_CITY,
    origin: str = "",
    destination: str = "",
    preference: str = "public_transport",
    **_: Any,
) -> dict[str, Any]:
    """Return mock candidate routes between two attractions."""
    normalized_city = (city or DEFAULT_CITY).strip()
    origin = (origin or "出发地").strip()
    destination = (destination or "目的地").strip()
    same_area = _same_area(normalized_city, origin, destination)

    if same_area:
        candidates = [
            {
                "mode": "walk",
                "route": "步行前往",
                "duration_minutes": 12,
                "cost_yuan": 0,
                "walk_minutes": 12,
                "transfers": 0,
                "note": "同一区域景点，步行成本最低",
            },
            {
                "mode": "taxi",
                "route": "打车短途直达",
                "duration_minutes": 8,
                "cost_yuan": 18,
                "walk_minutes": 2,
                "transfers": 0,
                "note": "最快但不符合低预算优先",
            },
        ]
    else:
        candidates = [
            {
                "mode": "subway",
                "route": _generic_subway_route(normalized_city, origin, destination),
                "duration_minutes": 38,
                "cost_yuan": 5,
                "walk_minutes": 12,
                "transfers": 1,
                "note": "公共交通优先，费用低且稳定",
            },
            {
                "mode": "bus",
                "route": "公交换乘方案",
                "duration_minutes": 52,
                "cost_yuan": 2,
                "walk_minutes": 15,
                "transfers": 1,
                "note": "费用最低，但时间更长",
            },
            {
                "mode": "taxi",
                "route": "打车直达",
                "duration_minutes": 28,
                "cost_yuan": 42,
                "walk_minutes": 3,
                "transfers": 0,
                "note": "最快但费用较高",
            },
        ]

    return {
        "city": normalized_city,
        "requested_city": normalized_city,
        "fallback_used": normalized_city not in ATTRACTION_DATA,
        "origin": origin,
        "destination": destination,
        "preference": preference,
        "same_area": same_area,
        "candidates": candidates,
    }


def get_routes(
    city: str = DEFAULT_CITY,
    segments: list[dict[str, Any]] | None = None,
    preference: str = "public_transport",
    **_: Any,
) -> dict[str, Any]:
    segments = segments or []
    routes = []
    for segment in segments:
        if not isinstance(segment, dict):
            continue
        routes.append(
            get_route(
                city=city,
                origin=str(segment.get("origin", "")),
                destination=str(segment.get("destination", "")),
                preference=preference,
            )
        )
    return {"city": city, "preference": preference, "routes": routes}


def _lookup(dataset: dict[str, dict[str, Any]], city: str) -> dict[str, Any]:
    normalized_city = (city or DEFAULT_CITY).strip()
    data = dataset.get(normalized_city) or dataset[DEFAULT_CITY]
    result = deepcopy(data)
    result["requested_city"] = normalized_city
    result["fallback_used"] = normalized_city not in dataset
    return result


def _spot_area(city: str, spot_name: str) -> str | None:
    spots = ATTRACTION_DATA.get(city, [])
    for spot in spots:
        name = str(spot.get("name", ""))
        if spot_name == name or spot_name in name or name in spot_name:
            return str(spot.get("area", ""))

    # Hotel names can also be route endpoints after Hotel Agent is inserted
    # before Traffic Agent. HOTEL_DATA is defined later in this module; the
    # lookup happens at runtime after module initialization, so globals() is safe.
    hotel_data = globals().get("HOTEL_DATA", {})
    hotels = hotel_data.get(city, []) if isinstance(hotel_data, dict) else []
    for hotel in hotels:
        if not isinstance(hotel, dict):
            continue
        name = str(hotel.get("name", ""))
        area = str(hotel.get("area", ""))
        if spot_name == name or spot_name in name or name in spot_name:
            return area
    return None


def _same_area(city: str, origin: str, destination: str) -> bool:
    origin_area = _spot_area(city, origin)
    dest_area = _spot_area(city, destination)
    return bool(origin_area and dest_area and origin_area == dest_area)


def _generic_subway_route(city: str, origin: str, destination: str) -> str:
    if city == "北京":
        return "地铁为主，按地图选择最近站点换乘"
    if city == "上海":
        return "地铁 2/10 号线等市区线路换乘"
    if city == "广州":
        return "地铁 3/1 号线等市区线路换乘"
    if city == "杭州":
        return "地铁 1/2/5 号线结合公交或步行接驳"
    if city == "南京":
        return "地铁 1/2/3 号线结合景区步行接驳"
    return "城市轨道交通换乘"


HOTEL_DATA = {
    "北京": [
        {
            "name": "前门轻居酒店",
            "area": "天坛-前门区域",
            "price_per_night": 230,
            "type": "经济型酒店",
            "nearest_subway": "前门",
            "tags": ["低预算", "地铁方便", "近天安门", "老城核心"],
            "pros": ["靠近前门、大栅栏和天安门", "步行和地铁都方便", "适合第一次来北京"],
            "cons": ["核心区房间可能偏小"],
        },
        {
            "name": "王府井青年旅舍",
            "area": "天安门-故宫区域",
            "price_per_night": 160,
            "type": "青旅/床位",
            "nearest_subway": "王府井/金鱼胡同",
            "tags": ["低预算", "近故宫", "公共交通方便", "性价比"],
            "pros": ["靠近故宫和天安门", "价格低", "适合低预算"],
            "cons": ["私密性较弱", "舒适度一般"],
        },
        {
            "name": "东直门便捷酒店",
            "area": "东直门-雍和宫区域",
            "price_per_night": 260,
            "type": "经济型酒店",
            "nearest_subway": "东直门",
            "tags": ["交通枢纽", "地铁方便", "机场线方便"],
            "pros": ["地铁线路多", "去机场方便", "换乘便利"],
            "cons": ["离天安门和故宫不是最近"],
        },
        {
            "name": "西直门地铁站酒店",
            "area": "西直门-动物园区域",
            "price_per_night": 240,
            "type": "经济型酒店",
            "nearest_subway": "西直门",
            "tags": ["地铁枢纽", "去海淀方便", "公共交通方便"],
            "pros": ["去颐和园、圆明园方向方便", "多线路换乘"],
            "cons": ["距离天安门核心区略远"],
        },
        {
            "name": "海淀圆明园学生公寓式酒店",
            "area": "海淀西北区域",
            "price_per_night": 210,
            "type": "公寓式酒店",
            "nearest_subway": "圆明园",
            "tags": ["低预算", "近圆明园", "安静"],
            "pros": ["去圆明园、颐和园方便", "价格相对低"],
            "cons": ["去天安门和故宫较远"],
        },
    ],
    "上海": [
        {
            "name": "人民广场经济酒店",
            "area": "人民广场-南京路区域",
            "price_per_night": 260,
            "type": "经济型酒店",
            "nearest_subway": "人民广场",
            "tags": ["地铁方便", "市中心", "低预算"],
            "pros": ["地铁换乘方便", "靠近南京路"],
            "cons": ["热门区域价格波动大"],
        }
    ],
    "杭州": [
        {
            "name": "湖滨西湖便捷酒店",
            "area": "湖滨-西湖边",
            "price_per_night": 280,
            "type": "经济型酒店",
            "nearest_subway": "龙翔桥",
            "tags": ["地铁方便", "近西湖", "公共交通方便", "市中心"],
            "pros": ["步行可到西湖湖滨", "地铁和公交换乘方便", "适合首次游览杭州"],
            "cons": ["热门区域节假日价格可能上涨"],
        },
        {
            "name": "武林广场地铁酒店",
            "area": "武林广场",
            "price_per_night": 240,
            "type": "经济型酒店",
            "nearest_subway": "武林广场",
            "tags": ["低预算", "地铁方便", "公共交通方便", "市中心"],
            "pros": ["多条线路换乘方便", "前往西湖和运河都较顺路", "价格相对稳妥"],
            "cons": ["距离湖滨核心景观需短途地铁或公交"],
        },
        {
            "name": "杭州东站轻住酒店",
            "area": "杭州东站附近",
            "price_per_night": 220,
            "type": "经济型酒店",
            "nearest_subway": "火车东站",
            "tags": ["低预算", "高铁方便", "地铁方便"],
            "pros": ["适合高铁往返", "地铁接入主城区方便", "价格较低"],
            "cons": ["去西湖和灵隐寺通勤时间稍长"],
        },
    ],
    "南京": [
        {
            "name": "新街口地铁精选酒店",
            "area": "新街口",
            "price_per_night": 260,
            "type": "经济型酒店",
            "nearest_subway": "新街口",
            "tags": ["地铁方便", "市中心", "公共交通方便"],
            "pros": ["地铁换乘便利", "前往总统府、夫子庙和钟山都较均衡", "餐饮选择多"],
            "cons": ["核心商圈价格可能波动"],
        },
        {
            "name": "夫子庙秦淮客栈",
            "area": "夫子庙-秦淮河区域",
            "price_per_night": 230,
            "type": "经济型客栈",
            "nearest_subway": "夫子庙",
            "tags": ["低预算", "近夫子庙", "地铁方便", "公共交通方便"],
            "pros": ["夜游秦淮河方便", "步行覆盖夫子庙周边", "适合低预算"],
            "cons": ["热门街区夜间可能较热闹"],
        },
        {
            "name": "南京南站便捷酒店",
            "area": "南京南站附近",
            "price_per_night": 210,
            "type": "经济型酒店",
            "nearest_subway": "南京南站",
            "tags": ["低预算", "高铁方便", "地铁方便"],
            "pros": ["高铁抵离方便", "价格相对低", "地铁进城线路明确"],
            "cons": ["距离主要景点通勤时间略长"],
        },
    ],
}


def search_hotels(
    city: str = DEFAULT_CITY,
    preferred_areas: list[str] | None = None,
    target_area: str | None = None,
    budget_level: str = "normal",
    days: int = 3,
    daily_plan: dict[str, Any] | None = None,
    preferences: list[str] | None = None,
    area_selection: dict[str, Any] | None = None,
    requested_fields: list[str] | None = None,
    **_: Any,
) -> dict[str, Any]:
    """Return mock hotels for the selected area, then rank by budget and transport.

    Hotel Agent v11 first asks the LLM to choose a target accommodation area,
    then calls this MCP method to retrieve hotels in that area. If the exact
    target area has no candidates in the mock dataset, we fall back to globally
    ranked candidates but mark area_filter_fallback=True.
    """
    normalized_city = (city or DEFAULT_CITY).strip()
    all_hotels: list[dict[str, Any]] = deepcopy(HOTEL_DATA.get(normalized_city) or HOTEL_DATA[DEFAULT_CITY])
    preferred_areas = preferred_areas or []
    preferences = preferences or []
    requested_fields = requested_fields or []
    target_area = (target_area or (preferred_areas[0] if preferred_areas else "") or "").strip()

    plan_areas: list[str] = []
    if isinstance(daily_plan, dict):
        for item in daily_plan.values():
            if isinstance(item, dict) and item.get("area"):
                plan_areas.append(str(item.get("area")))
    all_area_hints = ([target_area] if target_area else []) + preferred_areas + plan_areas

    def area_matches(hotel: dict[str, Any], area_hint: str) -> bool:
        area = str(hotel.get("area", ""))
        hint = str(area_hint or "")
        if not area or not hint:
            return False
        return area == hint or area in hint or hint in area

    if target_area:
        filtered_hotels = [hotel for hotel in all_hotels if area_matches(hotel, target_area)]
    else:
        filtered_hotels = []

    area_filter_fallback = False
    hotels = filtered_hotels
    if not hotels:
        area_filter_fallback = bool(target_area)
        hotels = all_hotels

    def score(hotel: dict[str, Any]) -> int:
        value = 0
        area = str(hotel.get("area", ""))
        tags = [str(x) for x in hotel.get("tags", [])]
        price = int(hotel.get("price_per_night") or 9999)

        if target_area and area_matches(hotel, target_area):
            value += 100

        for hint in all_area_hints:
            hint = str(hint)
            if not hint:
                continue
            if hint == area or area in hint or hint in area:
                value += 35
            elif any(part and part in hint for part in area.split("-")[:1]):
                value += 12

        if budget_level == "low":
            value += max(0, 80 - price // 5)
            if "低预算" in tags or "性价比" in tags:
                value += 25

        if "公共交通方便" in preferences or "地铁方便" in preferences:
            if "地铁方便" in tags or "公共交通方便" in tags or "地铁枢纽" in tags:
                value += 20

        if "近天安门" in tags or "近故宫" in tags:
            value += 10
        return value

    hotels.sort(key=score, reverse=True)

    if requested_fields:
        keep = set(requested_fields) | {"name", "area", "price_per_night", "nearest_subway", "tags"}
        hotels = [{key: value for key, value in hotel.items() if key in keep} for hotel in hotels]

    return {
        "city": normalized_city,
        "requested_city": normalized_city,
        "fallback_used": normalized_city not in HOTEL_DATA,
        "days": days,
        "budget_level": budget_level,
        "target_area": target_area,
        "preferred_areas": preferred_areas,
        "area_selection": area_selection or {},
        "area_filter_fallback": area_filter_fallback,
        "hotels": hotels,
    }
