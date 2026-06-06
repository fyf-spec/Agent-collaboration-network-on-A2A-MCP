"""Runtime mode helpers shared by local demo processes."""

from __future__ import annotations

import os
from collections.abc import Mapping, MutableMapping


TRUE_VALUES = {"1", "true", "yes", "on"}
FALSE_VALUES = {"0", "false", "no", "off"}


def optional_env_bool(name: str, environ: Mapping[str, str] | None = None) -> bool | None:
    source = os.environ if environ is None else environ
    value = source.get(name)
    if value is None:
        return None
    normalized = value.strip().lower()
    if normalized in TRUE_VALUES:
        return True
    if normalized in FALSE_VALUES:
        return False
    return None


def env_bool(name: str, default: bool = False, environ: Mapping[str, str] | None = None) -> bool:
    value = optional_env_bool(name, environ)
    return default if value is None else value


def llm_enabled(environ: Mapping[str, str] | None = None, *, default: bool = True) -> bool:
    explicit = optional_env_bool("A2A_USE_LLM", environ)
    if explicit is not None:
        return explicit

    legacy_enabled = optional_env_bool("A2A_LLM_ENABLED", environ)
    if legacy_enabled is not None:
        return legacy_enabled

    legacy_fast_mode = optional_env_bool("A2A_DEMO_FAST", environ)
    if legacy_fast_mode is not None:
        return not legacy_fast_mode

    return default


def no_llm_mode_enabled(environ: Mapping[str, str] | None = None) -> bool:
    return not llm_enabled(environ)


def runtime_mode_name(environ: Mapping[str, str] | None = None) -> str:
    return "llm" if llm_enabled(environ) else "no-llm"


def configure_runtime_env(env: MutableMapping[str, str], *, use_llm: bool) -> None:
    value = "1" if use_llm else "0"
    env["A2A_USE_LLM"] = value
    env["A2A_LLM_ENABLED"] = value
    env["A2A_DEMO_FAST"] = "0" if use_llm else "1"
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUNBUFFERED", "1")
