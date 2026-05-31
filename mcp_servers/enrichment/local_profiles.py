from __future__ import annotations

import json
from pathlib import Path
from typing import Any


PROFILE_DIR = Path(__file__).resolve().parent


def enrich_attraction(city: str, spot: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    return _enrich_item("attractions", city, spot, id_key="spot_id")


def enrich_hotel(city: str, hotel: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    return _enrich_item("hotels", city, hotel, id_key="hotel_id")


def _enrich_item(section: str, city: str, item: dict[str, Any], *, id_key: str) -> tuple[dict[str, Any], dict[str, str]]:
    profile = _find_profile(section, city, item, id_key=id_key)
    if not profile:
        return item, {}

    enriched = dict(item)
    field_sources: dict[str, str] = {}
    for key, value in profile.items():
        if key in {"name", "poi_id", "aliases"}:
            continue
        if enriched.get(key) in (None, "", []):
            enriched[key] = value
            field_sources[key] = "local_profile"

    if field_sources:
        source = dict(enriched.get("data_source") or {})
        source["provider"] = "amap+local_profile" if source.get("provider") == "amap" else "local_profile"
        existing = source.get("field_sources") if isinstance(source.get("field_sources"), dict) else {}
        source["field_sources"] = {**existing, **field_sources}
        missing = source.get("missing_fields") if isinstance(source.get("missing_fields"), list) else []
        source["missing_fields"] = [field for field in missing if field not in field_sources]
        enriched["data_source"] = source

    return enriched, field_sources


def _find_profile(section: str, city: str, item: dict[str, Any], *, id_key: str) -> dict[str, Any] | None:
    profiles = _load_profiles().get(section, {})
    city_profiles = profiles.get(city) or profiles.get(str(city).replace("市", "")) or []
    item_id = str(item.get(id_key) or "").strip()
    item_name = str(item.get("name") or "").strip()
    item_area = str(item.get("area") or "").strip()

    for profile in city_profiles:
        if not isinstance(profile, dict):
            continue
        if item_id and item_id == str(profile.get("poi_id") or "").strip():
            return profile

    for profile in city_profiles:
        if not isinstance(profile, dict):
            continue
        names = [str(profile.get("name") or "").strip()]
        aliases = profile.get("aliases")
        if isinstance(aliases, list):
            names.extend(str(alias).strip() for alias in aliases if str(alias).strip())
        if any(name and (name == item_name or name in item_name or item_name in name) for name in names):
            return profile

    if section == "hotels":
        defaults = profiles.get("_area_defaults", {}).get(city) or profiles.get("_area_defaults", {}).get(str(city).replace("市", "")) or []
        for profile in defaults:
            area = str(profile.get("area") or "").strip()
            if area and (area in item_area or area in item_name):
                return profile
        if defaults and isinstance(defaults[0], dict):
            return defaults[0]

    return None


def _load_profiles() -> dict[str, Any]:
    data: dict[str, Any] = {}
    for filename in ("attraction_profiles.json", "hotel_profiles.json"):
        path = PROFILE_DIR / filename
        if not path.exists():
            continue
        with path.open("r", encoding="utf-8") as file:
            loaded = json.load(file)
        if isinstance(loaded, dict):
            data.update(loaded)
    return data
