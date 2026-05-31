from __future__ import annotations

from datetime import datetime
from typing import Any


UNKNOWN_VALUES = {
    "",
    "null",
    "none",
    "unknown",
    "unspecified",
    "n/a",
    "na",
    "pending",
    "未指定",
    "待确认",
    "未知",
}


def clean_text(value: Any) -> str:
    return str(value or "").strip()


def is_unknown(value: Any) -> bool:
    text = clean_text(value)
    return text.lower() in UNKNOWN_VALUES or text in UNKNOWN_VALUES


def is_iso_date(value: Any) -> bool:
    text = clean_text(value)
    if len(text) < 10:
        return False
    try:
        datetime.strptime(text[:10], "%Y-%m-%d")
    except ValueError:
        return False
    return True


def iso_date_or_empty(value: Any) -> str:
    text = clean_text(value)
    return text[:10] if is_iso_date(text) else ""


def normalize_budget_level(value: Any, *, default: str = "normal") -> str:
    text = clean_text(value).lower()
    mapping = {
        "budget": "low",
        "cheap": "low",
        "low_budget": "low",
        "low": "low",
        "normal": "normal",
        "medium": "normal",
        "balanced": "normal",
        "high": "high",
        "luxury": "high",
        "premium": "high",
        "unlimited": "high",
        "no_limit": "high",
    }
    return mapping.get(text, default)


def normalize_travel_style(value: Any, *, budget_level: str = "normal") -> str:
    text = clean_text(value).lower()
    mapping = {
        "budget": "budget",
        "comfort": "comfort",
        "comfortable": "comfort",
        "premium": "comfort",
        "luxury": "comfort",
        "relaxed": "comfort",
        "balanced": "balanced",
        "normal": "balanced",
    }
    if text in mapping:
        return mapping[text]
    if budget_level == "low":
        return "budget"
    if budget_level == "high":
        return "comfort"
    return "balanced"


def normalize_transport_preference(value: Any, *, default: str = "normal") -> str:
    text = clean_text(value).lower()
    mapping = {
        "public": "public_transport",
        "public_transport": "public_transport",
        "metro": "public_transport",
        "subway": "public_transport",
        "bus": "public_transport",
        "fast": "fastest",
        "fastest": "fastest",
        "cheap": "cheapest",
        "cheapest": "cheapest",
        "taxi": "taxi",
        "normal": "normal",
        "balanced": "normal",
    }
    return mapping.get(text, default)


def display_budget_level(value: Any) -> str:
    budget = normalize_budget_level(value)
    return {
        "low": "低预算",
        "high": "预算充足、舒适优先",
        "normal": "普通预算",
    }.get(budget, "普通预算")


def display_transport_preference(value: Any) -> str:
    preference = normalize_transport_preference(value)
    return {
        "public_transport": "公共交通优先",
        "fastest": "速度优先",
        "cheapest": "费用优先",
        "taxi": "打车优先",
        "normal": "按用户偏好",
    }.get(preference, "按用户偏好")
