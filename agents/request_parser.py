from __future__ import annotations

from datetime import date, timedelta
import re
from typing import Any


CN_DIGITS = {
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
}

KNOWN_CITIES = [
    "北京",
    "上海",
    "广州",
    "深圳",
    "杭州",
    "南京",
    "成都",
    "重庆",
    "武汉",
    "西安",
    "苏州",
    "天津",
    "黄山",
    "泰州",
    "柳州",
]


def extract_travel_task_from_payload(task_payload: dict[str, Any], *, capability: str = "") -> dict[str, Any]:
    context = task_payload.get("context") if isinstance(task_payload, dict) else {}
    return extract_travel_task_from_context(
        str(task_payload.get("instruction") or ""),
        context if isinstance(context, dict) else {},
        capability=capability,
    )


def extract_travel_task_from_context(
    instruction: str,
    context: dict[str, Any] | None = None,
    *,
    capability: str = "",
) -> dict[str, Any]:
    context = context or {}
    existing = _existing_task(context)
    if existing:
        existing.setdefault("_parser", "agent_context_travel_task")
        return _ensure_defaults(existing, instruction, capability=capability)

    text = _request_text(instruction, context)
    origin_city = _extract_origin_city(text)
    destination_city = _extract_destination_city(text, origin_city=origin_city)
    days = _extract_days(text)
    start_date, date_text = _extract_start_date(text)
    budget_level = _extract_budget_level(text)
    transport_preference = _extract_transport_preference(text)
    must_visit = _extract_must_visit(text)
    preferences = _extract_preferences(text, budget_level=budget_level, transport_preference=transport_preference)

    task = {
        "origin_city": origin_city,
        "destination_city": destination_city,
        "city": destination_city,
        "days": days,
        "start_date": start_date or "未指定",
        "date_text": date_text,
        "budget_level": budget_level,
        "transport_preference": transport_preference,
        "must_visit": must_visit,
        "preferences": preferences,
        "avoid": [],
        "raw_constraints": text,
        "constraints": {
            "attractions": {
                "must_visit": must_visit,
                "preferred_types": preferences,
                "avoid": [],
                "pace": "normal",
            },
            "traffic": {
                "preference": transport_preference,
                "avoid": [],
                "max_transfer": None,
                "walking_tolerance": "normal",
            },
            "hotel": {
                "preferred_features": _hotel_preferences(text),
                "preferred_area": None,
                "hotel_type": _hotel_type(text),
            },
            "general": {
                "budget_level": budget_level,
                "travel_style": "budget" if budget_level == "low" else ("comfort" if budget_level in {"high", "luxury"} else "balanced"),
                "special_needs": _special_needs(text),
            },
        },
        "_parser": "agent_local_request_parser",
        "_parsed_by_capability": capability,
    }
    return task


def city_from_request(instruction: str, context: dict[str, Any] | None = None) -> str:
    task = extract_travel_task_from_context(instruction, context or {})
    return str(task.get("destination_city") or task.get("city") or "未指定").strip() or "未指定"


def _existing_task(context: dict[str, Any]) -> dict[str, Any]:
    if isinstance(context.get("travel_task"), dict):
        return dict(context["travel_task"])
    inputs = context.get("inputs") or {}
    if isinstance(inputs, dict) and isinstance(inputs.get("travel_task"), dict):
        return dict(inputs["travel_task"])
    return {}


def _ensure_defaults(task: dict[str, Any], instruction: str, *, capability: str) -> dict[str, Any]:
    text = _request_text(instruction, {})
    if _is_unknown(task.get("origin_city")):
        task["origin_city"] = _extract_origin_city(text)
    if _is_unknown(task.get("destination_city")):
        task["destination_city"] = _extract_destination_city(text, origin_city=task.get("origin_city"))
    if _is_unknown(task.get("city")):
        task["city"] = task.get("destination_city")
    if _is_unknown(task.get("days")):
        task["days"] = _extract_days(text)
    start_date, date_text = _extract_start_date(text)
    if _is_unknown(task.get("start_date")):
        task["start_date"] = start_date or "未指定"
    if _is_unknown(task.get("date_text")):
        task["date_text"] = date_text
    if _is_unknown(task.get("budget_level")):
        task["budget_level"] = _extract_budget_level(text)
    if _is_unknown(task.get("transport_preference")):
        task["transport_preference"] = _extract_transport_preference(text)
    if _is_unknown(task.get("must_visit")):
        task["must_visit"] = _extract_must_visit(text)
    if _is_unknown(task.get("preferences")):
        task["preferences"] = _extract_preferences(
            text,
            budget_level=str(task.get("budget_level") or "normal"),
            transport_preference=str(task.get("transport_preference") or "normal"),
        )
    if _is_unknown(task.get("raw_constraints")):
        task["raw_constraints"] = text
    task.setdefault("_parsed_by_capability", capability)
    return task


def _is_unknown(value: Any) -> bool:
    if value in (None, "", [], {}):
        return True
    if isinstance(value, str):
        return value.strip().lower() in {
            "",
            "未指定",
            "待确认",
            "目的地",
            "出发地",
            "unknown",
            "unspecified",
            "none",
            "null",
        }
    return False


def _request_text(instruction: str, context: dict[str, Any]) -> str:
    request = context.get("request") if isinstance(context, dict) else {}
    pieces = [instruction]
    if isinstance(request, dict):
        pieces.extend(
            str(request.get(key) or "")
            for key in ("original_instruction", "node_goal", "agent_instruction")
        )
    return " ".join(piece.strip() for piece in pieces if piece and piece.strip())


def _extract_origin_city(text: str) -> str | None:
    match = re.search(r"从([\u4e00-\u9fa5]{2,12}?)(?:去|到|出发|$|[，,。；;\s])", text)
    if match:
        return match.group(1).strip()
    return None


def _extract_destination_city(text: str, *, origin_city: Any = None) -> str:
    for candidate in _destination_candidates(text):
        destination = _clean_place(candidate)
        if not destination or destination == origin_city:
            continue
        destination_city = _city_from_place(destination)
        if destination_city and destination_city != origin_city:
            return destination_city
        return destination

    fallback_city = _city_from_place(text)
    if fallback_city and fallback_city != origin_city:
        return fallback_city
    return "未指定"


def _clean_place(value: str) -> str:
    text = value.strip()
    text = re.sub(r"^(?:一下|一趟|一次)", "", text)
    text = re.sub(r"(?:玩|游玩|逛|看看|看|住|待)(?:\d+|[一二两三四五六七八九十])?天?.*$", "", text)
    text = re.sub(r"(?:的)?(?:\d+|[一二两三四五六七八九十])天.*$", "", text)
    text = re.sub(r"(?:的)?(?:低预算|高预算|穷游|省钱|经济|舒适|豪华).*$", "", text)
    text = re.sub(r"(?:的)?(?:旅行|旅游|行程|计划).*$", "", text)
    text = re.sub(r"\u7684$", "", text)
    text = re.sub(r"(?:附近|周边)$", "", text)
    return text.strip()


def _city_from_place(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""

    for city in KNOWN_CITIES:
        if city in text:
            return city

    match = re.search(r"([\u4e00-\u9fa5]{2,12}?)(?:市|城区|区)", text)
    if match:
        city = match.group(1).strip()
        if city.endswith("州") or city.endswith("京") or city.endswith("海") or city.endswith("圳") or city.endswith("庆"):
            return city

    return ""


def _destination_candidates(text: str) -> list[str]:
    candidates: list[str] = []

    for match in re.finditer(r"(?:去|到|前往)(.{0,40})", text):
        window = str(match.group(1) or "").strip()
        if window:
            candidates.append(window)

    precise = re.search(r"(?:去|到)([\u4e00-\u9fa5]{2,18}?)(?:玩|旅游|旅行|看看|看|住|待|逛|$|[，,。；;\s])", text)
    if precise:
        candidates.append(str(precise.group(1) or "").strip())

    generic = re.search(r"([\u4e00-\u9fa5]{2,12})(?:玩|旅游|旅行)(?:\d+|[一二两三四五六七八九十])?天", text)
    if generic:
        candidates.append(str(generic.group(1) or "").strip())

    # 去重，保持原有优先级
    return list(dict.fromkeys(item for item in candidates if item))


def _extract_days(text: str) -> int:
    match = re.search(r"(\d+)\s*天", text)
    if match:
        return max(1, int(match.group(1)))
    for key, value in CN_DIGITS.items():
        if f"{key}天" in text:
            return value
    return 3


def _extract_start_date(text: str) -> tuple[str | None, str | None]:
    today = date.today()
    for phrase, offset in (("后天", 2), ("明天", 1), ("今天", 0)):
        if phrase in text:
            return (today + timedelta(days=offset)).isoformat(), phrase

    match = re.search(r"(\d{1,2})月(初|中旬|中|底|末)?", text)
    if not match:
        return None, None
    month = max(1, min(12, int(match.group(1))))
    qualifier = match.group(2) or ""
    day = 1
    if qualifier in {"中", "中旬"}:
        day = 15
    elif qualifier in {"底", "末"}:
        day = 25
    year = today.year
    if month < today.month:
        year += 1
    return date(year, month, day).isoformat(), match.group(0)


def _extract_budget_level(text: str) -> str:
    if any(word in text for word in ("穷游", "低预算", "省钱", "便宜", "经济")):
        return "low"
    if any(word in text for word in ("高端", "豪华", "舒适", "不差钱")):
        return "high"
    return "normal"


def _extract_transport_preference(text: str) -> str:
    if any(word in text for word in ("地铁", "公交", "公共交通")):
        return "public_transport"
    if any(word in text for word in ("打车", "出租", "网约车")):
        return "taxi"
    if any(word in text for word in ("自驾", "开车")):
        return "drive"
    return "normal"


def _extract_must_visit(text: str) -> list[str]:
    result: list[str] = []
    match = re.search(
        r"(?:必须|一定|务必|想|希望)(?:要)?去([\u4e00-\u9fa5A-Za-z0-9、/和与 ]{2,40}?)(?:看看|看|玩|参观|$|[，,。；;])",
        text,
    )
    if match:
        phrase = match.group(1).strip()
        if not any(word in phrase for word in ("著名景点", "经典景点", "热门景点")):
            result.extend(item.strip() for item in re.split(r"[、/和与\s]+", phrase) if item.strip())
    known_spots = [
        "天安门广场",
        "天安门",
        "故宫",
        "国家博物馆",
        "天坛",
        "颐和园",
        "圆明园",
        "什刹海",
        "南锣鼓巷",
        "总统府",
        "夫子庙",
        "秦淮河",
        "南京博物院",
        "西湖",
        "灵隐寺",
        "雷峰塔",
        "断桥",
        "西溪湿地",
    ]
    result.extend(spot for spot in known_spots if spot in text)
    return list(dict.fromkeys(result))


def _extract_preferences(text: str, *, budget_level: str, transport_preference: str) -> list[str]:
    preferences: list[str] = []
    if any(word in text for word in ("著名景点", "经典景点", "热门景点")):
        preferences.append("经典景点")
    if any(word in text for word in ("博物馆", "历史", "文化")):
        preferences.append("历史文化")
    if any(word in text for word in ("自然", "山水", "公园")):
        preferences.append("自然风光")
    if budget_level == "low":
        preferences.append("低预算")
    if transport_preference == "public_transport":
        preferences.append("公共交通方便")
    return list(dict.fromkeys(preferences or ["经典景点"]))


def _hotel_preferences(text: str) -> list[str]:
    result: list[str] = []
    if any(word in text for word in ("地铁", "公交", "公共交通")):
        result.append("靠近公共交通")
    if any(word in text for word in ("便宜", "穷游", "低预算", "经济")):
        result.append("经济型")
    return result


def _hotel_type(text: str) -> str | None:
    if any(word in text for word in ("青旅", "青年旅舍")):
        return "hostel"
    if any(word in text for word in ("经济", "快捷", "便宜", "穷游")):
        return "budget"
    return None


def _special_needs(text: str) -> list[str]:
    result: list[str] = []
    if "老人" in text:
        result.append("老人同行")
    if "孩子" in text or "儿童" in text:
        result.append("儿童同行")
    if "无障碍" in text:
        result.append("无障碍")
    return result
